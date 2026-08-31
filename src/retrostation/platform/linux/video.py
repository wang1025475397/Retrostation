"""ffmpeg raw-frame pipe (DESIGN §6.5).

Measured on the RG DS (rk3568, ffmpeg 4.4.4, **no** hardware decode):
288x216@15fps costs ~19% of one core, which is why frames are decoded small
and at a fixed rate, and why ``scale`` uses ``fast_bilinear``.

The pipe is deliberately dumb: it produces frames as fast as it is asked to
and knows nothing about timing.  Pacing is the caller's job
(:class:`~retrostation.data.video.VideoPlayer`), because a reader that drains
the pipe as fast as possible would make ffmpeg decode at 5x realtime and pin a
core for no reason.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path

from ..base import VideoPipe

log = logging.getLogger(__name__)

#: Overridable so tests and odd firmwares can point at another binary.
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

_availability: dict[str, bool] = {}

#: Seconds a decoder gets to exit politely before it is killed.
_TERMINATE_GRACE = 0.15
#: Seconds to reap after SIGKILL -- only to avoid zombies, not to wait for I/O.
_KILL_GRACE = 0.2


def available(executable: str = FFMPEG) -> bool:
    """Whether the binary exists.  Cached: this is asked once per video."""
    cached = _availability.get(executable)
    if cached is None:
        cached = shutil.which(executable) is not None
        _availability[executable] = cached
    return cached


def forget_availability() -> None:
    """Drop the cache (tests, or a firmware that installs ffmpeg at runtime)."""
    _availability.clear()


class FFmpegPipe(VideoPipe):
    """One ``ffmpeg`` process writing rgb24 frames to a pipe."""

    def __init__(
        self,
        path: Path,
        *,
        width: int,
        height: int,
        fps: int,
        executable: str = FFMPEG,
        probe: str = FFPROBE,
    ) -> None:
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError(f"invalid video target {width}x{height}@{fps}")

        self._path = Path(path)
        self.size = (int(width), int(height))
        self._frame_bytes = int(width) * int(height) * 3
        self._probe = probe
        self._duration = -1.0

        command = build_command(path, width=width, height=height, fps=fps, executable=executable)
        log.debug("video pipe: %s", " ".join(command))
        self._proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self._frame_bytes * 2,
        )

    # -- VideoPipe -------------------------------------------------------- #

    @property
    def duration(self) -> float:
        """Clip length from ``ffprobe``; ``0`` when it cannot be determined.

        Called once by the pumping thread, never by the UI thread: a cold probe
        costs tens of milliseconds on the device's SD card.
        """
        if self._duration >= 0:
            return self._duration
        self._duration = 0.0
        if not available(self._probe):
            return 0.0
        command = [
            self._probe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(self._path),
        ]
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command, capture_output=True, timeout=5, check=False,
            )
            self._duration = max(0.0, float((result.stdout or b"").strip() or 0.0))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.debug("duration probe failed for %s: %s", self._path, exc)
        return self._duration

    def read_frame(self) -> object | None:
        """One RGBA bitmap, or ``None`` at end of stream / after ``close``."""
        from PIL import Image

        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        try:
            data = proc.stdout.read(self._frame_bytes)
        except (ValueError, OSError):
            # close() raced us and closed the pipe underneath the read.
            return None
        if not data or len(data) < self._frame_bytes:
            return None
        return Image.frombytes("RGB", self.size, data).convert("RGBA")

    def close(self) -> None:
        """Stop the decoder and reap the process.  Both waits are short.

        Measured on the RG DS: while ffmpeg is mid-read on the SD card it
        routinely ignores SIGTERM for the full second, and this used to sit in
        ``wait(timeout=1.0)`` for every one of those -- on the UI thread, so the
        whole frontend froze for a second on each game switch.  SIGKILL lands
        immediately; the short wait after it only reaps the corpse.
        """
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=_TERMINATE_GRACE)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    with suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=_KILL_GRACE)
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            log.debug("ffmpeg shutdown failed: %s", exc)
        finally:
            stream = proc.stdout
            if stream is not None and not stream.closed:
                with suppress(OSError):  # the reader may hold it
                    stream.close()


def build_command(
    path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    executable: str = FFMPEG,
) -> list[str]:
    """The ffmpeg argv.

    A list, never a string: ROM and video names on this device are Chinese and
    must survive without a shell in between (DESIGN §14).

    * ``-stream_loop -1`` -- loop forever, like every other frontend;
    * ``-an -sn -dn``     -- we only ever draw pictures;
    * ``fps=`` first      -- drop to our target rate *before* scaling, so the
      frames we are going to throw away never get scaled;
    * ``scale=...decrease`` + ``pad`` -- keep the aspect ratio *and* the fixed
      frame size, so a portrait NDS clip is letterboxed instead of stretched.

    Note there is deliberately **no input** ``-r``.  Placed before ``-i`` it
    does not drop frames, it retimes them: it tells the demuxer the file runs
    at our rate, so a 30 fps clip is reinterpreted as 15 fps and plays at half
    speed (a 60 fps one at a quarter).  Dropping frames to hit a rate is the
    ``fps`` filter's job -- it keeps the clip's real duration.
    """
    return [
        executable,
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-stream_loop", "-1",
        "-i", str(path),
        "-an", "-sn", "-dn",
        "-vf",
        f"fps={fps},"
        f"scale={width}:{height}:flags=fast_bilinear:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "-pix_fmt", "rgb24",
        "-f", "rawvideo", "-",
    ]
