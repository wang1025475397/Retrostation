"""Image decoding through ffmpeg.

Pillow on the RG DS cannot open JPEG *at all*: it is linked against libjpeg 9
headers while the system only ships libjpeg 6b, so every ``Image.open()`` on a
.jpg dies with ``Wrong JPEG library version: library is 62, caller expects 90``.
Covers are very often JPEG -- an entire NDS set can be -- so without this those
games would show nothing but the generated placeholder.

ffmpeg is already on the device for video playback, decodes these files in well
under 100 ms, and scales in the same pass, which saves us a resize afterwards.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"

#: Extensions whose failure we treat as "Pillow cannot do this, try ffmpeg".
#: A PNG that fails to open is genuinely broken; a JPEG that fails to open on
#: this device is expected.
RECOVERABLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".jpe", ".jfif"})


def available() -> bool:
    """Whether an ffmpeg binary can be found."""
    return shutil.which(FFMPEG) is not None


def is_recoverable(path: Path) -> bool:
    """Whether a decode failure on ``path`` is worth retrying with ffmpeg."""
    return path.suffix.lower() in RECOVERABLE_SUFFIXES


def transcode(source: Path, target: Path, width: int, height: int) -> bool:
    """Decode ``source`` into ``target``, scaled to fit inside ``width x height``.

    Two device quirks shape this command:

    * the muxer must be inferred from ``target``'s extension -- passing ``-f``
      fails with "not a suitable output format" on this build;
    * the webp encoder is compiled out, so PNG is the only usable output.

    Returns ``True`` when ``target`` was written.
    """
    if not available() or width <= 0 or height <= 0:
        return False
    if target.suffix.lower() != ".png":
        log.debug("ffmpeg fallback writes PNG only, not %s", target.suffix)
        return False

    # Same rule as fit_bitmap(): fit inside the box at the source's own aspect
    # ratio, upscaling included -- a slot that is larger than the artwork has
    # to be filled, not left with a border around a small picture.
    scale = f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease"
    command = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source),
        "-frames:v", "1",
        "-vf", scale,
        str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ffmpeg fallback failed for %s: %s", source, exc)
        return False

    if result.returncode != 0 or not target.is_file():
        log.debug("ffmpeg could not decode %s: %s", source,
                  result.stderr.decode("utf-8", "replace")[:200])
        return False
    return True
