"""Bottom-screen video playback (DESIGN §6.5).

The UI never waits for a frame: a daemon thread pulls bitmaps out of a
platform-provided :class:`~retrostation.platform.base.VideoPipe` and keeps only
the newest one, so a slow decoder costs smoothness (dropped frames) instead of
input latency.

Four rules keep this from eating the handheld:

* **debounce** -- the decoder only starts once the selection has been stable for
  :attr:`VideoSettings.debounce`.  Scrolling through 500 ROMs must not spawn
  500 ffmpeg processes.
* **pace** -- the pumping thread sleeps to the configured fps.  Draining the
  pipe as fast as possible would make ffmpeg decode at 5x realtime and pin a
  core for no visible benefit.
* **degrade** -- a pipe that yields nothing within :attr:`VideoSettings.stale`
  seconds is blacklisted for the session and the UI keeps showing the cover.
  A missing ``ffmpeg`` disables the feature entirely after a few attempts.
* **stop** -- :meth:`VideoPlayer.stop` runs before a game is launched and when
  the frontend exits: an inherited ffmpeg would keep burning CPU behind the
  emulator (DESIGN §8.1 step ③).  Stopping *because the selection changed* is
  deliberately different -- it is handed to a background thread.  Measured on
  the RG DS, ffmpeg ignores SIGTERM for about a second while it is mid-read on
  the SD card, and waiting for that on the UI thread froze the whole frontend
  on every game switch.  :meth:`VideoPlayer.stop` still waits for those
  background teardowns, so nothing survives a launch.

Sound rides along with the pictures: starting a clip also asks the platform for
an :class:`~retrostation.platform.base.AudioPipe`, and it is closed by the very
same teardown -- so a game switch cannot leave a soundtrack playing behind the
emulator, and a platform with no audio support simply stays silent.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..core.config import Config
from ..core.model import ASSET_VIDEO, Game
from ..platform.base import AudioPipe, Platform, VideoPipe

log = logging.getLogger(__name__)

#: How many files may fail to open before we stop trying altogether.  A missing
#: decoder returns ``None`` for *every* file, and probing device after device is
#: pure waste.
_UNSUPPORTED_AFTER = 3

#: Seconds the next clip waits for the outgoing soundtrack to let go of the
#: card.  The teardown is nearly always instantaneous -- the fade-out is 40 ms
#: -- so this is only paid when a switch really does overlap, and it is far
#: cheaper than the alternative: losing the race means silence.
_AUDIO_HANDOVER = 0.5


@dataclass(frozen=True)
class VideoSettings:
    """Decode target and the two timing knobs that protect the frame budget."""

    width: int = 288
    height: int = 216
    fps: int = 15
    #: Seconds the selection must stay put before we spawn a decoder.
    debounce: float = 0.25
    #: Seconds without a single frame before a file is treated as broken.
    stale: float = 3.0
    #: Whether the clip's soundtrack is played.  ``False`` keeps the preview
    #: silent, which is all it ever was before sound existed.
    sound: bool = True
    #: Preview volume, 0.0-1.0.  A game clip at full scale is startling on a
    #: handheld speaker, so the factory default is well below it.
    volume: float = 0.7

    @classmethod
    def from_config(cls, config: Config) -> VideoSettings:
        width, height = config.video_size
        return cls(
            width=int(width),
            height=int(height),
            fps=int(config.video_fps),
            sound=bool(config.video_sound),
            volume=max(0.0, min(1.0, config.video_volume / 100.0)),
        )

    def resized(self, width: int, height: int) -> VideoSettings:
        """Same settings, decoded straight at the size the UI will draw."""
        if width <= 0 or height <= 0 or (width, height) == (self.width, self.height):
            return self
        return replace(self, width=int(width), height=int(height))


class VideoPlayer:
    """Plays the video of whichever game the cursor has settled on."""

    def __init__(
        self,
        platform: Platform,
        settings: VideoSettings,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self._platform = platform
        self._settings = settings
        self._clock = clock
        self._sleep = sleep

        self._lock = threading.RLock()
        #: What the UI wants, and since when (debounce bookkeeping).
        self._wanted: tuple[str, Path] | None = None
        self._wanted_since = 0.0
        #: What is actually decoding.
        self._current: tuple[str, Path] | None = None
        self._pipe: VideoPipe | None = None
        #: The clip's soundtrack, owned by whatever :meth:`_start_locked` got
        #: from the platform.  ``None`` on every platform that cannot play it.
        self._audio: AudioPipe | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._generation = 0
        #: Decoders being torn down in the background.  Joined before the device
        #: is handed over to a game, so nothing survives under the emulator.
        self._reapers: list[threading.Thread] = []
        #: Teardown of the outgoing soundtrack, kept separately from the video
        #: reapers because the next clip's sound has to wait for exactly this
        #: one -- see :meth:`_start_locked`.
        self._audio_reaper: threading.Thread | None = None

        self._frame = None
        self._frame_seq = 0
        self._frames_decoded = 0
        self._duration = 0.0
        self._started_at = 0.0

        self._failed: set[str] = set()
        self._null_opens = 0
        self._enabled = True
        #: Terminal: set by :meth:`close` (launch / shutdown), never undone.
        self._closed = False

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(
        self,
        *,
        size: tuple[int, int] | None = None,
        enabled: bool | None = None,
        sound: bool | None = None,
        volume: float | None = None,
    ) -> None:
        """Set the decode size, switch playback on/off, or change the sound.

        Decoding at the exact size the media box will draw skips a per-frame
        LANCZOS resize in the canvas (measurable at 15 fps on this CPU).

        Volume is retuned on the clip that is sounding rather than left for the
        next selection -- but *without* rebuilding the pipe, since a rebuild has
        to wait for the audio device and that wait lands on the UI thread as a
        stutter.  The rocker would otherwise either appear dead or cost a hitch
        every time it moves.
        """
        with self._lock:
            if size is not None:
                self._settings = self._settings.resized(*size)
            if sound is not None and sound != self._settings.sound:
                self._settings = replace(self._settings, sound=sound)
                if not sound:
                    self._drop_audio()
                elif self._current is not None:
                    self._open_audio(self._current[1])
            if volume is not None and volume != self._settings.volume:
                self._settings = replace(self._settings, volume=volume)
                if self._audio is not None:
                    self._audio.set_volume(volume)

        if enabled is None or self._closed or enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.stop()

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #

    def select(self, game: Game | None) -> None:
        """Declare the current selection; called once per frame."""
        target: tuple[str, Path] | None = None
        if game is not None and self._enabled:
            path = game.asset(ASSET_VIDEO)
            if path is not None:
                target = (game.key, Path(path))

        now = self._clock()
        with self._lock:
            if target != self._wanted:
                self._wanted = target
                self._wanted_since = now
            if target is None or target[0] != (self._current[0] if self._current else None):
                # Never show the previous game's frame over the new one.
                self._frame = None
                self._frames_decoded = 0
        self.update(now)

    def update(self, now: float | None = None) -> None:
        """Start, stop or leave alone the decoder.  Idempotent per frame."""
        now = self._clock() if now is None else now
        with self._lock:
            wanted = self._wanted
            running = self._current

            if wanted is None or wanted[0] in self._failed or not self._enabled:
                if running is not None:
                    self._stop_locked(reap=False)
                return

            if running is not None:
                if running[0] == wanted[0]:
                    self._check_staleness(now)
                    return
                self._stop_locked(reap=False)

            if now - self._wanted_since >= self._settings.debounce:
                self._start_locked(wanted, now)

    def frame(self) -> object | None:
        """The newest decoded bitmap for the current game, or ``None``."""
        with self._lock:
            if self._current is None:
                return None
            return self._frame

    @property
    def frame_seq(self) -> int:
        """Increments on every published frame -- the UI redraws when it moves."""
        with self._lock:
            return self._frame_seq

    def next_frame_in(self) -> float | None:
        """Seconds until the pump is expected to publish, or ``None`` when idle.

        The UI loop uses this to keep a slow top-screen repaint out of the way
        of a frame that is about to land: drawing the top panel costs ~39 ms on
        the handheld, which is most of a 66 ms video frame interval.
        """
        with self._lock:
            if self._current is None or not self._frames_decoded:
                return None
            interval = 1.0 / max(1, self._settings.fps)
            return max(0.0, self._last_frame_at + interval - self._clock())

    def is_playing(self, key: str | None = None) -> bool:
        with self._lock:
            if self._current is None or self._frame is None:
                return False
            return key is None or self._current[0] == key

    def is_pending(self, key: str) -> bool:
        """A clip for ``key`` has been asked for but has not produced a frame.

        A decoder needs the debounce interval plus a frame or two before there
        is anything to show.  Without this the UI falls back to cover art for
        that moment, so browsing a list flashes a cover on every game before
        its clip arrives.
        """
        with self._lock:
            if not self._enabled or key in self._failed:
                return False
            if self._current is not None:
                return self._current[0] == key and self._frame is None
            return self._wanted is not None and self._wanted[0] == key

    def progress(self) -> float | None:
        """Position through the (looping) clip, 0..1, or ``None`` if unknown."""
        with self._lock:
            if self._current is None or self._frame is None or self._duration <= 0:
                return None
            elapsed = max(0.0, self._clock() - self._started_at)
        return (elapsed % self._duration) / self._duration

    def stop(self) -> None:
        """Stop decoding and reap the thread.  Safe to call twice.

        This is the *synchronous* path: it waits for the decoder to be gone,
        because its callers are about to hand the device over (launch, exit).
        A plain selection change goes through :meth:`update`, which tears the
        old decoder down in the background instead.
        """
        with self._lock:
            thread = self._stop_locked(reap=True)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._join_reapers()

    def close(self) -> None:
        """Stop for good: launching a game or shutting the frontend down.

        After this the decoder never restarts, even if the UI keeps asking --
        an ``execv`` that failed must not leave an ffmpeg behind the game.
        """
        self._closed = True
        self._enabled = False
        self.stop()

    # ------------------------------------------------------------------ #
    # Internals (all called with the lock held, except the pump)
    # ------------------------------------------------------------------ #

    def _drop_audio(self) -> None:
        """Stop the soundtrack without touching the pictures.

        Runs with the lock held or on the UI thread: changing the volume
        replaces the player outright, and the card can only be held by one.
        """
        audio, self._audio = self._audio, None
        if audio is None:
            return
        reaper = threading.Thread(
            target=self._close_pipes, args=(None, audio),
            name="retrostation-audio-reap", daemon=True,
        )
        self._reapers.append(reaper)
        self._audio_reaper = reaper
        reaper.start()

    def _open_audio(self, path: Path) -> None:
        """Start the soundtrack for ``path``; silence is an acceptable outcome."""
        if not self._settings.sound:
            return
        # Take the card over from the clip being replaced: it cannot be opened
        # twice, and the one that loses the race stays silent on screen.
        handover = self._audio_reaper
        if handover is not None:
            handover.join(timeout=_AUDIO_HANDOVER)
            self._audio_reaper = None
        try:
            self._audio = self._platform.open_audio_pipe(path, volume=self._settings.volume)
        except Exception:  # noqa: BLE001 - silence is an acceptable outcome
            log.debug("no audio for %s", path, exc_info=True)

    def _start_locked(self, target: tuple[str, Path], now: float) -> None:
        key, path = target
        settings = self._settings
        try:
            pipe = self._platform.open_video_pipe(
                path, width=settings.width, height=settings.height, fps=settings.fps
            )
        except Exception:  # noqa: BLE001 - a bad plugin must not kill the UI
            log.exception("video decoder crashed for %s", path)
            pipe = None

        if pipe is None:
            self._null_opens += 1
            self._failed.add(key)
            if self._null_opens >= _UNSUPPORTED_AFTER:
                self._enabled = False
                log.info("video disabled: %d files could not be decoded", self._null_opens)
            return

        self._generation += 1
        generation = self._generation
        self._current = target
        self._pipe = pipe
        self._audio = None
        self._frames_decoded = 0
        self._duration = 0.0
        self._started_at = now
        self._last_frame_at = now
        stop = threading.Event()
        self._stop_event = stop

        # Sound comes up last: it waits for the outgoing clip to let go of the
        # card, and failing to get it must never stop the pictures.
        self._open_audio(path)

        self._thread = threading.Thread(
            target=self._pump,
            args=(pipe, generation, stop),
            name=f"retrostation-video-{generation}",
            daemon=True,
        )
        self._thread.start()

    def _stop_locked(self, *, reap: bool) -> threading.Thread | None:
        """Terminate the decoder; returns the thread the *caller* must join.

        ``reap`` closes the pipe on this thread.  It is only wanted when we are
        handing the device over: making the frame loop wait for a process that
        is already dying froze the UI for a second per switch on the RG DS,
        where ffmpeg can ignore SIGTERM for that long.
        """
        self._generation += 1
        pipe, self._pipe = self._pipe, None
        audio, self._audio = self._audio, None
        stop, self._stop_event = self._stop_event, None
        thread, self._thread = self._thread, None
        self._current = None
        self._frame = None
        self._frames_decoded = 0
        self._duration = 0.0

        if stop is not None:
            stop.set()

        # Sound goes on its own thread even when the pictures do not: the next
        # clip's player must be able to wait for this one alone.
        if audio is not None:
            if reap:
                self._close_pipes(None, audio)
                self._audio_reaper = None
            else:
                self._audio_reaper = threading.Thread(
                    target=self._close_pipes, args=(None, audio),
                    name="retrostation-audio-reap", daemon=True,
                )
                self._reapers.append(self._audio_reaper)
                self._audio_reaper.start()

        if pipe is not None:
            if reap:
                self._close_pipes(pipe, None)
            else:
                reaper = threading.Thread(
                    target=self._close_pipes, args=(pipe, None),
                    name="retrostation-video-reap", daemon=True,
                )
                self._reapers.append(reaper)
                reaper.start()
        return thread

    @staticmethod
    def _close_pipes(pipe: VideoPipe | None, audio: AudioPipe | None) -> None:
        """Tear one clip's decoders down; never raises, never leaks a process.

        Both halves go through the same reaper on purpose: a game switch that
        closed the pictures but not the sound would leave a clip playing behind
        the emulator, which is exactly what DESIGN §8.1 step ③ forbids.
        """
        for target in (pipe, audio):
            if target is None:
                continue
            try:
                target.close()  # terminates ffmpeg, which unblocks read_frame()
            except Exception:  # noqa: BLE001 - never leak a process over a bug
                log.debug("closing a media pipe failed", exc_info=True)

    def _join_reapers(self) -> None:
        """Wait for decoders still being torn down in the background."""
        reapers, self._reapers = self._reapers, []
        for thread in reapers:
            thread.join(timeout=1.0)

    def _check_staleness(self, now: float) -> None:
        """A pipe that produced nothing is broken: blacklist it (DESIGN §14)."""
        if self._frames_decoded or self._current is None:
            return
        if now - self._started_at < self._settings.stale:
            return
        log.info("video produced no frames within %.1fs: %s", self._settings.stale, self._current[1])
        self._failed.add(self._current[0])
        self._stop_locked(reap=False)

    def _pump(self, pipe: VideoPipe, generation: int, stop: threading.Event) -> None:
        """Read frames at a steady pace and publish only the newest one."""
        interval = 1.0 / max(1, self._settings.fps)
        try:
            duration = float(getattr(pipe, "duration", 0.0) or 0.0)
            with self._lock:
                if self._generation == generation:
                    self._duration = duration
        except Exception:  # noqa: BLE001 - duration is a nicety, never fatal
            log.debug("duration probe failed", exc_info=True)

        next_at = self._clock()
        try:
            while not stop.is_set():
                frame = pipe.read_frame()
                if frame is None:
                    break
                with self._lock:
                    if self._generation != generation:
                        return
                    self._frame = frame
                    self._frame_seq += 1
                    self._frames_decoded += 1
                    self._last_frame_at = self._clock()
                next_at += interval
                delay = next_at - self._clock()
                if delay <= 0:
                    next_at = self._clock()  # fell behind: drop the backlog
                else:
                    self._sleep(delay)
        except Exception:  # noqa: BLE001 - a dying codec must not kill the app
            log.debug("video pump stopped", exc_info=True)
        finally:
            with self._lock:
                if self._generation == generation:
                    # Stream ended or decoder died: hold the last picture and
                    # let the UI keep showing it until the selection changes.
                    self._thread = None
