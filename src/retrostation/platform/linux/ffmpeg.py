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

import functools
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"

#: How long the version probe below may take before we give up on ffmpeg.
_PROBE_TIMEOUT = 10.0

#: Extensions whose failure we treat as "Pillow cannot do this, try ffmpeg".
#: A PNG that fails to open is genuinely broken; a JPEG that fails to open on
#: this device is expected.
RECOVERABLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".jpe", ".jfif"})


@functools.lru_cache(maxsize=4)
def runs(executable: str) -> bool:
    """Whether ``executable`` is installed **and can start**.

    ``which()`` alone is not enough.  One of these devices has an ffmpeg whose
    ``libfontconfig.so.1`` resolves to a build too old for the libpangoft2 it
    was linked against, so the binary is found, spawned, and dies on startup
    with ``symbol lookup error`` -- every time something asked for a video,
    with nothing on screen to say why.  Asking it for its version once costs a
    few milliseconds and turns that into an ordinary "no video, show the
    cover".

    Cached because this is asked before every clip: a broken build is not going
    to fix itself inside one process, and PATH does not move either.
    """
    if shutil.which(executable) is None:
        return False
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-version"],
            capture_output=True, timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s cannot be started: %s", executable, exc)
        return False
    if result.returncode != 0:
        log.warning(
            "%s is present but exits with %d: %s",
            executable, result.returncode,
            result.stderr.decode("utf-8", "replace").strip()[:200],
        )
        return False
    return True


def available() -> bool:
    """Whether an ffmpeg binary is installed and can be run."""
    return runs(FFMPEG)


@functools.lru_cache(maxsize=1)
def decoder_env() -> dict[str, str]:
    """Environment to spawn the decoder with: the system's own libraries.

    The stock launcher exports ``LD_LIBRARY_PATH=/usr/lib32:/usr/lib:...``, and
    ``/usr/lib`` holds a cut-down ``libavformat`` -- measured on the device:
    **ten** muxers against the system library's 183, with no ``rawvideo``
    among them.  ffmpeg then dies with "Requested output format 'rawvideo' is
    not a suitable output format", which reads like a broken clip rather than
    a hijacked library, and it does it identically for every file.

    The binary resolves its own dependencies through its RPATH, so dropping
    the override is what makes it behave the way it already does in a shell.

    Verified rather than assumed: the clean environment is only used when the
    decoder then still reports the muxer we ask for, so a device that really
    does keep its libraries somewhere unusual is left alone.
    """
    clean = {key: value for key, value in os.environ.items() if key != "LD_LIBRARY_PATH"}
    return clean if _has_muxer(clean, "rawvideo") else dict(os.environ)


def _has_muxer(env: dict[str, str], name: str) -> bool:
    """Whether the decoder under ``env`` lists ``name`` among its muxers."""
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-muxers"],
            capture_output=True, timeout=_PROBE_TIMEOUT, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("muxer probe failed: %s", exc)
        return False
    text = result.stdout.decode("utf-8", "replace")
    return any(
        len(parts) > 1 and parts[1] == name
        for parts in (line.split() for line in text.splitlines())
    )


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
        result = subprocess.run(command, capture_output=True, timeout=60,
                                env=decoder_env())
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ffmpeg fallback failed for %s: %s", source, exc)
        return False

    if result.returncode != 0 or not target.is_file():
        log.debug("ffmpeg could not decode %s: %s", source,
                  result.stderr.decode("utf-8", "replace")[:200])
        return False
    return True
