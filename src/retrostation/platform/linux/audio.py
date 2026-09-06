"""Clip soundtrack over ALSA (DESIGN §6.5).

The video pipe has carried pictures only since the first version -- ``-an``
throws the audio away -- because there was nowhere to put it.  Sound needs a
decoder and somewhere to send the result:

    ffmpeg (decode the track to s16le PCM) -> aplay (ALSA)

**Two processes, not one with a second output pipe.**  ffmpeg's output stage is
single-threaded, so a reader that only drains the video at 15 fps would stall
the audio behind it and the track would stutter.  Two decoders cost a little
more CPU -- an AAC track is ~2% of a core against ~19% for the video -- and
both streams then flow independently.

**Timing is deliberately loose.**  The pump shows frames at a fixed rate, so a
clip lasts exactly as long as the file; aplay plays PCM at 44100 Hz, so the
track lasts exactly as long as the file.  Both loop on the same boundary, so
they stay together without a shared clock -- which is all a few-second preview
needs, and far cheaper than a real A/V sync loop on this CPU.
"""

from __future__ import annotations

import contextlib
import logging
import math
import queue
import shutil
import functools
import struct
import subprocess
import threading
import time
from pathlib import Path

from . import ffmpeg as ffmpeg_codec

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
APLAY = "aplay"

#: Verified on the RG DS: ``aplay`` accepts S16_LE / 44100 Hz / stereo and the
#: card is free, because Retrostation only ever asks SDL for VIDEO.
RATE = 44100
CHANNELS = 2

#: One hop of PCM -- ~23 ms at 44.1 kHz.  Small enough to stop promptly, large
#: enough that the forwarding thread is not awake every few milliseconds.
_CHUNK = 4096

#: Seconds to ramp down before stopping.  Cutting a PCM stream mid-sample is a
#: step from full scale to zero: a click on every game switch.
_FADE_SECONDS = 0.04
_FADE_STEPS = 4

#: Buffer time handed to ``aplay``, in microseconds.  ALSA's default is tuned
#: for uninterrupted music -- several hundred milliseconds deep -- and that is
#: pure latency for a click that has to land *with* the button.
_SFX_BUFFER_US = 20_000

#: Seconds a process gets to exit politely before it is killed.
#:
#: Every one of these timeouts sits between one clip and the next: the card is
#: not free until the whole teardown finishes, so a generous wait here is paid
#: for by the following clip losing its sound.
_TERMINATE_GRACE = 0.1
#: Seconds ``aplay`` is given to play out its buffer after stdin is closed.
#:
#: Deliberately short.  This card cannot be opened twice -- a second aplay gets
#: ``Device or resource busy`` -- so holding it for the sake of a few buffered
#: milliseconds is what cost the *next* clip its sound entirely.  The fade-out
#: above already covers the click that draining was there to avoid.
_DRAIN_SECONDS = 0.1

#: How long we wait after starting ``aplay`` before trusting that it has the
#: card.  It fails immediately when it does not ("Device or resource busy"), so
#: a tenth of a second tells the two cases apart.
_OPEN_SETTLE = 0.12
#: Further attempts at getting the card, and the pause between them: the clip
#: being replaced may still be tearing down when this one starts.
_OPEN_RETRIES = 2
_OPEN_RETRY_DELAY = 0.15


def available(*, player: str = APLAY, executable: str = FFMPEG) -> bool:
    """Whether both halves of the chain exist on this device."""
    return shutil.which(executable) is not None and shutil.which(player) is not None


def _scaled(chunk: bytes, volume: float) -> bytes:
    """Apply ``volume`` (0..1) to a block of s16le samples.

    ``audioop`` is the right tool but it is gone in Python 3.13, so a missing
    module degrades to full volume rather than to silence -- a loud preview is
    annoying, a silent one looks broken.
    """
    if volume >= 0.999:
        return chunk
    try:
        import audioop

        return audioop.mul(chunk, 2, volume)
    except Exception:  # noqa: BLE001 - any failure means "play it as-is"
        return chunk


@functools.lru_cache(maxsize=1)
def _supports_buffer_time(player: str) -> bool:
    """Whether this ``aplay`` understands ``-B`` (buffer time in µs).

    Assumed nowhere.  alsa-utils has had the option for years, but a firmware
    shipping its own player need not, and an unrecognised option stops the
    player before it plays a single sample -- so on such a machine the sound
    would be missing entirely rather than merely a little late.  Probed once
    and cached; when the option is absent the card's own default is used.
    """
    try:
        result = subprocess.run([player, "--help"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    # Usage goes to stdout here and to stderr on other builds; check both.
    text = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
    return "-B" in text


def _reap_process(proc: subprocess.Popen) -> None:
    """Dispose of a process that never got going, or has already gone."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=_TERMINATE_GRACE)
    for stream in (proc.stdout, proc.stdin):
        if stream is not None and not stream.closed:
            with contextlib.suppress(OSError):
                stream.close()


class _AudioSink:
    """The single ``aplay`` that everything plays through.

    The card cannot be opened twice, so a clip's soundtrack and the button
    blips share one player.  Two players would have to hand the card over on
    every switch -- a teardown, then an open, with a "busy" window in between
    that has to be retried -- and that window is precisely the lag a click
    cannot afford.  One player opened on first use and then kept means a blip
    is just a write into an already-running process.
    """

    def __init__(self, *, player: str = APLAY, rate: int = RATE,
                 channels: int = CHANNELS) -> None:
        self._command = [player, "-q", "-f", "S16_LE", "-r", str(int(rate)),
                         "-c", str(int(channels))]
        if _supports_buffer_time(player):
            self._command += ["-B", str(_SFX_BUFFER_US)]
        self._command.append("-")
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._warned = False
        #: Set by :meth:`release`; see there for why a write may not reopen.
        self._released = False

    def start(self) -> bool:
        """Open the card; ``False`` when somebody else is holding it."""
        with self._lock:
            self._released = False
            return self._start_locked()

    def write(self, pcm: bytes) -> bool:
        """Hand PCM to the player.  Never raises; ``False`` when it cannot."""
        if not pcm:
            return True
        with self._lock:
            if self._released:
                return False
            if self._proc is None and not self._start_locked():
                return False
            return self._write_locked(pcm)

    def release(self) -> None:
        """Let go of the card -- a game is about to want it.

        Nothing takes it back until :meth:`start` is called again.  Without
        that, the clip still fading out would reopen ALSA a few milliseconds
        later and the game would start silent.
        """
        with self._lock:
            self._released = True
            self._close_locked()

    # -- internals, all with the lock held ------------------------------- #

    def _start_locked(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if self._proc is not None:
            self._close_locked()
        for attempt in range(_OPEN_RETRIES + 1):
            try:
                candidate = subprocess.Popen(
                    self._command, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                return False
            time.sleep(_OPEN_SETTLE)
            if candidate.poll() is None:
                self._proc = candidate
                return True
            _reap_process(candidate)
            if attempt < _OPEN_RETRIES:
                time.sleep(_OPEN_RETRY_DELAY)
        if not self._warned:
            self._warned = True
            log.info("sound card is busy: previews and button sounds stay silent")
        return False

    def _write_locked(self, pcm: bytes) -> bool:
        proc = self._proc
        if proc is None:
            return False
        try:
            stdin = proc.stdin
            if stdin is None or stdin.closed:
                return False
            stdin.write(pcm)
            stdin.flush()
            return True
        except (OSError, ValueError):
            self._close_locked()
            return False

    def _close_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(OSError, ValueError):
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            if proc.poll() is None:
                proc.wait(timeout=_DRAIN_SECONDS)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=_TERMINATE_GRACE)


_SINK: _AudioSink | None = None


def audio_sink() -> _AudioSink:
    """The process-wide sink, created on first use."""
    global _SINK
    if _SINK is None:
        _SINK = _AudioSink()
    return _SINK


class AlsaAudioPipe:
    """Plays one clip's soundtrack and nothing else.

    Not a :class:`~retrostation.platform.base.AudioPipe` subclass on purpose:
    the platform hands these out, and keeping the concrete class free of the
    ABC means it can be constructed (and torn down) in isolation by tests.
    """

    def __init__(
        self,
        path: Path,
        *,
        volume: float = 1.0,
        executable: str = FFMPEG,
        player: str = APLAY,
        rate: int = RATE,
        channels: int = CHANNELS,
    ) -> None:
        self._rate = int(rate)
        self._channels = int(channels)
        self._frame_bytes = self._channels * 2
        # Keep whole frames in every hop: a partial sample would shift the
        # stereo image for the rest of the stream.
        self._chunk = _CHUNK - (_CHUNK % self._frame_bytes)
        self._volume = max(0.0, min(1.0, float(volume)))
        self._stop = threading.Event()

        decode = [
            executable, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-stream_loop", "-1",
            "-i", str(path),
            "-vn", "-sn", "-dn",
            "-f", "s16le", "-ar", str(self._rate), "-ac", str(self._channels),
            "-",
        ]
        # The clean environment matters as much here as it does for the
        # pictures: the stock launcher's LD_LIBRARY_PATH hides ``s16le``, and a
        # decoder that cannot write PCM exits before the pump ever reads a byte
        # (see ``ffmpeg.audio_env``).
        self._ffmpeg = subprocess.Popen(
            decode, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=ffmpeg_codec.audio_env(),
        )

        # The player is shared with the button blips (see ``_AudioSink``), so
        # this only has to make sure the card is ours at all -- not win it from
        # another player of ours.
        if not audio_sink().start():
            # Nothing to play through: leave no decoder behind either, and let
            # the platform hand back "no pipe" so the preview stays silent.
            _reap_process(self._ffmpeg)
            self._ffmpeg = None
            raise OSError(f"audio device busy for {path}")

        self._thread = threading.Thread(
            target=self._pump, name="retrostation-audio", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------------ #

    def set_volume(self, volume: float) -> None:
        """Retune what is already playing instead of rebuilding the chain.

        Swapping the pipe for a new one would mean waiting for the card a
        second time, and that wait lands on the UI thread: the pictures stutter
        every time the rocker moves.  The pump reads this on its next hop, so
        the change is audible within one 23 ms buffer and costs nothing.
        """
        self._volume = max(0.0, min(1.0, float(volume)))

    def _pump(self) -> None:
        source = self._ffmpeg.stdout
        if source is None:
            return
        sink = audio_sink()
        try:
            while not self._stop.is_set():
                chunk = source.read(self._chunk)
                if not chunk:
                    break
                if not sink.write(_scaled(chunk, self._volume)):
                    break
            self._fade_out(source)
        except (OSError, ValueError):
            pass  # a dying decoder must not take the app with it

    def _fade_out(self, source) -> None:
        """Ramp the last few hops down, then end on silence."""
        sink = audio_sink()
        for step in range(_FADE_STEPS):
            chunk = source.read(self._chunk)
            if not chunk:
                break
            level = self._volume * max(0.0, 1.0 - (step + 1) / _FADE_STEPS)
            if not sink.write(_scaled(chunk, level)):
                return
        sink.write(b"\x00" * self._chunk)

    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Stop playback.  Safe to call twice; never raises."""
        if self._stop.is_set():
            return
        self._stop.set()
        # The pump owns the fade-out, so give it the few milliseconds that
        # takes before falling back to signals.
        self._thread.join(timeout=_FADE_SECONDS + 0.15)

        # The source is done with.  The player is shared with the button blips
        # and outlives this clip: closing it would cost the next click its
        # latency, so only the decoder goes.
        self._stop_process(self._ffmpeg, grace=_TERMINATE_GRACE)
        self._close_streams()

    @staticmethod
    def _stop_process(proc: subprocess.Popen | None, *, grace: float) -> None:
        """Ask one process to stop, then make it stop."""
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=grace)
        except (OSError, subprocess.SubprocessError):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                proc.kill()
                proc.wait(timeout=grace)

    def _close_streams(self) -> None:
        for proc in (self._ffmpeg,):
            if proc is None:
                continue
            for stream in (proc.stdout, proc.stdin):
                if stream is not None and not stream.closed:
                    with contextlib.suppress(OSError):
                        stream.close()


# --------------------------------------------------------------------------- #
# Button sound effects
# --------------------------------------------------------------------------- #

#: The three UI blips the frontend asks for.
SFX_MOVE = "move"
SFX_CONFIRM = "confirm"
SFX_BACK = "back"

#: ``(start Hz, end Hz, seconds)`` per blip.  Short and mid-high on purpose:
#: the speaker is tiny, so anything long or low only rattles it.
_SFX_SHAPES: dict[str, tuple[float, float, float]] = {
    SFX_MOVE: (880.0, 880.0, 0.035),
    SFX_CONFIRM: (660.0, 990.0, 0.070),
    SFX_BACK: (520.0, 330.0, 0.060),
}


def sfx_pcm(kind: str, *, volume: float = 1.0, rate: int = RATE,
            channels: int = CHANNELS) -> bytes:
    """S16_LE samples for one blip; empty for an unknown kind.

    Synthesised rather than shipped as files: three WAVs would put binaries in
    the bundle (and in git) for about 150 ms of tone between them, and there
    is no sample library on the device to match anyway.

    Cached because these fire on every keypress: rendering three thousand
    samples through ``struct.pack`` each time costs milliseconds that land
    directly on the button's response.
    """
    level = round(max(0.0, min(1.0, float(volume))), 3)
    key = (kind, level, int(rate), int(channels))
    cached = _SFX_CACHE.get(key)
    if cached is not None:
        return cached

    shape = _SFX_SHAPES.get(kind)
    if shape is None:
        return b""
    start, end, seconds = shape
    frames = max(1, int(rate * seconds))
    peak = 32767.0 * level
    layout = struct.Struct(f"<{int(channels)}h")
    out = bytearray()
    for index in range(frames):
        progress = index / frames
        # Fast attack, quick decay: starting or stopping a tone at full scale
        # is a click, and that is exactly what these are meant to replace.
        envelope = min(1.0, progress / 0.08) * (1.0 - progress) ** 2
        freq = start + (end - start) * progress
        value = int(peak * envelope * math.sin(2 * math.pi * freq * index / rate))
        out += layout.pack(*([value] * int(channels)))
    _SFX_CACHE[key] = bytes(out)
    return _SFX_CACHE[key]


#: Rendered blips, keyed by ``(kind, volume, rate, channels)``; see above.
_SFX_CACHE: dict[tuple, bytes] = {}


class SfxPlayer:
    """Short UI blips, written to the shared :class:`_AudioSink`.

    No player of its own: the card is exclusive, so a second one would have to
    win the card back from the clip's on every switch, and that handover is the
    one thing a click cannot wait for.  Sharing the sink makes a blip just a
    few kilobytes into an ``aplay`` that is already running -- no open, no
    teardown, nothing in between.

    It still runs on a thread of its own, purely so a keypress never waits on
    that write.
    """

    def __init__(
        self,
        *,
        volume: float = 1.0,
        rate: int = RATE,
        channels: int = CHANNELS,
    ) -> None:
        self._rate = int(rate)
        self._channels = int(channels)
        self._volume = max(0.0, min(1.0, float(volume)))
        self._enabled = True
        self._queue: queue.Queue[str] = queue.Queue(maxsize=8)
        self._thread = threading.Thread(
            target=self._run, name="retrostation-sfx", daemon=True
        )
        self._thread.start()

    # -- control --------------------------------------------------------- #

    def configure(self, *, enabled: bool | None = None,
                  volume: float | None = None) -> None:
        if enabled is not None:
            self._enabled = bool(enabled)
        if volume is not None:
            self._volume = max(0.0, min(1.0, float(volume)))

    def play(self, kind: str) -> None:
        """Queue one blip.  Never blocks, never raises."""
        if not self._enabled:
            return
        try:
            self._queue.put_nowait(kind)
        except queue.Full:
            pass  # dropped rather than delayed

    def release(self) -> None:
        """Let go of the card: a game is about to want it."""
        audio_sink().release()

    def close(self) -> None:
        """Stop playing; safe to call twice."""
        self._enabled = False

    # -- worker ---------------------------------------------------------- #

    def _run(self) -> None:
        sink = audio_sink()
        while True:
            kind = self._queue.get()
            if not self._enabled:
                continue
            # start() also clears "released", so blips come back by themselves
            # once the card is ours again (after a resident game, say).
            if not sink.start():
                continue
            sink.write(sfx_pcm(kind, volume=self._volume, rate=self._rate,
                               channels=self._channels))
