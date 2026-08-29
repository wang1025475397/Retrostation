"""Media resolution and thumbnail cache.

Resolution order (DESIGN §6.8.5), cheapest first:

1. a path a metadata source already resolved (nothing to do),
2. this app's own directories -- ``Imgs/`` / ``video/`` / ``logo/`` from
   ``config.media_dirs``, which is where tiny-scraper already writes covers,
3. the conventional directories of the metadata formats we read
   (ES-DE ``media/...``, Pegasus ``assets/...``),
4. nothing -- callers fall back to a programmatic placeholder.

Thumbnails are written next to the originals in a dot-directory so the ROM
scanner never mistakes them for media.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config
from ..core.model import ASSET_COVER, ASSET_FANART, ASSET_KEYS, ASSET_LOGO, ASSET_SCREENSHOT, ASSET_VIDEO, Game
from ..platform.base import Platform

log = logging.getLogger(__name__)

#: Candidate directories per format, checked after our own convention.
_FORMAT_DIRS: dict[str, dict[str, tuple[str, ...]]] = {
    "esde": {
        ASSET_COVER: ("media/covers", "media/box2d", "media/2dbox"),
        ASSET_LOGO: ("media/marquees", "media/wheel"),
        ASSET_SCREENSHOT: ("media/screenshots", "media/titleshots"),
        ASSET_VIDEO: ("media/videos",),
        ASSET_FANART: ("media/fanart",),
    },
    "pegasus": {
        ASSET_COVER: ("assets/box_front", "assets/box2d"),
        ASSET_LOGO: ("assets/marquee", "assets/wheel"),
        ASSET_SCREENSHOT: ("assets/screenshot", "assets/titlescreen"),
        ASSET_VIDEO: ("assets/videos", "assets/video"),
        ASSET_FANART: ("assets/fanart",),
    },
}

#: Files tried for each kind, in order.  Videos are separate on purpose.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".avi")


def _suffixes(kind: str) -> tuple[str, ...]:
    return _VIDEO_SUFFIXES if kind == ASSET_VIDEO else _IMAGE_SUFFIXES


@dataclass(frozen=True)
class MediaDirs:
    """Resolved media directories for one system."""

    root: Path
    by_kind: dict[str, Path]
    format_dirs: tuple[Path, ...]

    def candidates(self, kind: str, stem: str) -> list[Path]:
        """Every path that could hold ``kind`` media for a ROM named ``stem``."""
        out: list[Path] = []
        directory = self.by_kind.get(kind)
        if directory is not None:
            out.extend(directory / f"{stem}{suffix}" for suffix in _suffixes(kind))
        out.extend(directory / f"{stem}{suffix}" for directory in self.format_dirs for suffix in _suffixes(kind))
        return out


def media_dirs_for(platform: Platform, config: Config, system_key: str) -> MediaDirs:
    """Build the directory set for one system."""
    root = platform.rom_root / system_key
    by_kind = {
        kind: root / config.media_dirs.get(kind, kind)
        for kind in ASSET_KEYS
        if config.media_dirs.get(kind)
    }

    format_dirs: list[Path] = []
    for source_name, table in _FORMAT_DIRS.items():
        for kind, dirs in table.items():
            target = by_kind.get(kind)
            for directory in dirs:
                path = root / directory
                if path.is_dir() and (target is None or path != target):
                    format_dirs.append(path)
    return MediaDirs(root=root, by_kind=by_kind, format_dirs=tuple(dict.fromkeys(format_dirs)))


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def resolve_assets(game: Game, dirs: MediaDirs) -> Game:
    """Fill in the asset kinds the metadata sources did not provide.

    Returns a new :class:`Game`; existing (source-provided) paths win.
    """
    resolved = dict(game.assets)
    stem = game.path.stem

    for kind in ASSET_KEYS:
        if resolved.get(kind):
            continue
        for candidate in dirs.candidates(kind, stem):
            if candidate.is_file():
                resolved[kind] = candidate
                break
        else:
            resolved[kind] = None

    return game.copy(assets=resolved)


def placeholder_bitmap(platform: Platform, seed: str, width: int, height: int) -> object:
    """Deterministic gradient tile used when a game has no artwork.

    Mirrors the prototype's ``coverStyle()`` so the two look the same.
    """
    from PIL import Image, ImageDraw

    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    hue_a = digest[0] * 360 // 255
    hue_b = (hue_a + 40 + digest[1] % 120) % 360

    def hsl(hue: int, sat: float, light: float) -> tuple[int, int, int]:
        import colorsys

        r, g, b = colorsys.hls_to_rgb(hue / 360.0, light, sat)
        return (round(r * 255), round(g * 255), round(b * 255))

    top = hsl(hue_a, 0.46, 0.26)
    bottom = hsl(hue_a, 0.52, 0.14)
    accent = hsl(hue_b, 0.72, 0.58)

    image = Image.new("RGB", (max(1, width), max(1, height)), top)
    draw = ImageDraw.Draw(image)
    for row in range(height):
        t = row / max(1, height - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        draw.line([(0, row), (width, row)], fill=color)

    motif = digest[2] % 4
    cx, cy = int(width * 0.66), int(height * 0.30)
    radius = min(width, height) // 5
    if motif == 0:
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=accent)
    elif motif == 1:
        draw.polygon([(cx, cy - radius), (cx + radius, cy + radius), (cx - radius, cy + radius)], fill=accent)
    elif motif == 2:
        draw.rectangle([cx - radius, cy - radius // 2, cx + radius, cy + radius // 2], fill=accent)
    else:
        draw.ellipse(
            [int(width * 0.30) - radius, int(height * 0.72) - radius,
             int(width * 0.30) + radius, int(height * 0.72) + radius],
            fill=accent,
        )
    return image


# --------------------------------------------------------------------------- #
# Thumbnail cache
# --------------------------------------------------------------------------- #


class ThumbnailCache:
    """On-disk JPEG/PNG cache for scaled-down artwork.

    Keyed by ``(kind, source file mtime, size)`` so editing a cover invalidates
    its thumbnails without any bookkeeping.
    """

    def __init__(self, platform: Platform, root: Path, enabled: bool = True) -> None:
        self._platform = platform
        self._root = Path(root)
        self._enabled = enabled
        self._memory: dict[tuple[str, int, int, int], object] = {}
        self._memory_order: list[tuple[str, int, int, int]] = []

    # ------------------------------------------------------------------ #

    def get(self, kind: str, source: Path, width: int, height: int) -> object | None:
        """A scaled bitmap for ``source``, or ``None`` if it cannot be made."""
        if not source.is_file():
            return None
        key = (str(source), width, height, int(source.stat().st_mtime))

        cached = self._memory.get(key)
        if cached is not None:
            return cached

        bitmap = self._decode(source, width, height)
        if bitmap is None:
            return None

        self._remember(key, bitmap)
        return bitmap

    def _decode(self, source: Path, width: int, height: int) -> object | None:
        disk = self._disk_path(source, width, height)
        if disk is not None and disk.is_file():
            try:
                return self._platform.load_image(disk)
            except OSError:
                disk.unlink(missing_ok=True)

        try:
            original = self._platform.load_image(source)
        except OSError:
            return None

        scaled = _fit(original, width, height)
        if disk is not None and self._enabled:
            self._store(disk, scaled)
        return scaled

    def _disk_path(self, source: Path, width: int, height: int) -> Path | None:
        """``Imgs/.cache/<hash>_<w>x<h>.jpg``; ``None`` when caching is off."""
        if not self._enabled:
            return None
        digest = hashlib.sha1(f"{source}|{width}x{height}".encode("utf-8")).hexdigest()[:16]
        suffix = ".jpg" if source.suffix.lower() not in (".png",) else ".png"
        directory = source.parent / ".cache"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return directory / f"{digest}_{width}x{height}{suffix}"

    def _store(self, target: Path, bitmap: object) -> None:
        try:
            from PIL import Image

            image: Image.Image = bitmap  # type: ignore[assignment]
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (20, 20, 20))
                background.paste(image, mask=image.split()[-1])
                background.save(target, quality=86)
            else:
                image.save(target, quality=86)
        except (OSError, ValueError) as exc:  # pragma: no cover - best effort
            log.debug("thumbnail write failed for %s: %s", target, exc)

    # -- memory LRU ------------------------------------------------------- #

    def _remember(self, key: tuple[str, int, int, int], bitmap: object) -> None:
        limit = 40
        self._memory[key] = bitmap
        self._memory_order.append(key)
        while len(self._memory_order) > limit:
            oldest = self._memory_order.pop(0)
            self._memory.pop(oldest, None)

    def clear_memory(self) -> None:
        self._memory.clear()
        self._memory_order.clear()


def _fit(bitmap: object, width: int, height: int) -> object:
    """Scale to fit inside ``width x height``, never upscaling."""
    from PIL import Image

    image: Image.Image = bitmap  # type: ignore[assignment]
    scale = min(width / image.width, height / image.height, 1.0)
    if scale < 1.0:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    return image
