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
import shutil
import subprocess
import threading
import time
from pathlib import Path

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
        play = [player, "-q", "-f", "S16_LE", "-r", str(self._rate),
                "-c", str(self._channels), "-"]

        self._ffmpeg = subprocess.Popen(
            decode, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

        # Getting the card can fail even though both binaries exist: the clip we
        # are replacing may still be letting go of it.  aplay says so at once
        # and exits, so retry a couple of times before settling for silence.
        self._aplay = None
        for attempt in range(_OPEN_RETRIES + 1):
            candidate = subprocess.Popen(
                play, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(_OPEN_SETTLE)
            if candidate.poll() is None:
                self._aplay = candidate
                break
            self._reap(candidate)
            if attempt < _OPEN_RETRIES:
                time.sleep(_OPEN_RETRY_DELAY)

        if self._aplay is None:
            # Nothing to play through: leave no decoder behind either, and let
            # the platform hand back "no pipe" so the preview stays silent.
            self._reap(self._ffmpeg)
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

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        """Dispose of a process that never got going, or has already gone."""
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=_TERMINATE_GRACE)
        for stream in (proc.stdout, proc.stdin):
            if stream is not None and not stream.closed:
                with contextlib.suppress(OSError):
                    stream.close()

    def _pump(self) -> None:
        source = self._ffmpeg.stdout
        sink = self._aplay.stdin
        if source is None or sink is None:
            return
        try:
            while not self._stop.is_set():
                chunk = source.read(self._chunk)
                if not chunk:
                    break
                sink.write(_scaled(chunk, self._volume))
            self._fade_out(source, sink)
            with contextlib.suppress(OSError):
                sink.flush()
        except (OSError, ValueError):
            pass  # a dying decoder must not take the app with it
        finally:
            # Closing aplay's stdin is what makes it stop: it plays what it has
            # and exits, which is quieter than killing it mid-buffer.
            with contextlib.suppress(OSError):
                sink.close()

    def _fade_out(self, source, sink) -> None:
        """Ramp the last few hops down, then end on silence."""
        for step in range(_FADE_STEPS):
            chunk = source.read(self._chunk)
            if not chunk:
                break
            level = self._volume * max(0.0, 1.0 - (step + 1) / _FADE_STEPS)
            try:
                sink.write(_scaled(chunk, level))
            except OSError:
                return
        with contextlib.suppress(OSError):
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

        # The source is done with; the player is not, see below.
        self._stop_process(self._ffmpeg, grace=_TERMINATE_GRACE)

        # aplay drains what it already holds once stdin is closed, and it needs
        # a moment for that: killing it straight away is what turns a game
        # switch into a click.  Measured here, the buffer is a couple hundred
        # milliseconds of audio.
        aplay = self._aplay
        if aplay is not None and aplay.poll() is None:
            try:
                aplay.wait(timeout=_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                self._stop_process(aplay, grace=_TERMINATE_GRACE)

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
        for proc in (self._ffmpeg, self._aplay):
            if proc is None:
                continue
            for stream in (proc.stdout, proc.stdin):
                if stream is not None and not stream.closed:
                    with contextlib.suppress(OSError):
                        stream.close()
