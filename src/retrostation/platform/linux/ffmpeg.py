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
    return _picked_env("video", "rawvideo")


def audio_env() -> dict[str, str]:
    """Environment for the soundtrack decoder -- same hijack, other half.

    :func:`decoder_env` exists because the stock launcher's library path hides
    ``rawvideo``.  The very same cut-down libavformat has no ``s16le`` either,
    so a decoder spawned the naive way dies the instant it is asked for PCM --
    and with its stderr discarded that reads as "this clip has no soundtrack",
    which is how previews stayed silent on one device for weeks.
    """
    return _picked_env("audio", "s16le")


#: Decided environments, by role ("video" / "audio"); see :func:`_picked_env`.
_ENV_CACHE: dict[str, dict[str, str]] = {}


def _picked_env(role: str, muxer: str) -> dict[str, str]:
    """The environment for ``role``, cached once it has been decided.

    Only a *decided* answer is cached.  A probe that could not run -- the
    machine is busy, most often just after a game has closed -- is not an
    answer, and caching it used to pin the decoder to the hijacked library
    path for the rest of the session: every clip afterwards failed with
    "not a suitable output format".  Undecided, we hand back the clean
    environment (the one that behaves like a shell) and try again next time.
    """
    cached = _ENV_CACHE.get(role)
    if cached is not None:
        return cached
    clean = {key: value for key, value in os.environ.items() if key != "LD_LIBRARY_PATH"}
    available = _has_muxer(clean, muxer)
    if available is None:
        return clean
    env = clean if available else dict(os.environ)
    _ENV_CACHE[role] = env
    return env


def _has_muxer(env: dict[str, str], name: str) -> bool | None:
    """Whether the decoder under ``env`` lists ``name`` among its muxers.

    ``None`` when it could not be asked -- see :func:`_picked_env` for why
    that is not the same as ``False``.
    """
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-muxers"],
            capture_output=True, timeout=_PROBE_TIMEOUT, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("muxer probe failed: %s", exc)
        return None
    text = result.stdout.decode("utf-8", "replace")
    if not text.strip():
        return None
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
