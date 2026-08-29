"""Font discovery and caching.

Fonts are cached per ``(path, size)``: constructing a TrueType font is
measurable frame-rate loss if done per draw call.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import ImageFont

#: Where we look, in order.  Includes desktop OS fonts so the app can be
#: developed and unit-tested off-device.
_CANDIDATE_DIRS: tuple[str, ...] = (
    "/usr/share/fonts/source-han-sans-cn",      # RG DS (measured)
    "/usr/share/fonts/truetype/dejavu",         # Debian/Buildroot
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/truetype/noto",
    "C:/Windows/Fonts",                          # Windows development
    "/System/Library/Fonts",                     # macOS development
)

_CANDIDATE_NAMES: tuple[str, ...] = (
    "SourceHanSansCN-Regular.otf",
    "SourceHanSansCN-Regular.ttf",
    "NotoSansCJK-Regular.ttc",
    "msyh.ttc",
    "simhei.ttf",
    "DejaVuSans.ttf",
    "PingFang.ttc",
)

_FALLBACK_SIZE = 16


class FontBook:
    """Resolve and cache fonts; falls back to PIL's bitmap font."""

    def __init__(self, search_dirs: tuple[str, ...] | None = None) -> None:
        env_override = os.environ.get("RETROSTATION_FONT")
        self._dirs: tuple[Path, ...] = (
            (Path(env_override).parent,)
            if env_override
            else tuple(Path(d) for d in (search_dirs or _CANDIDATE_DIRS))
        )
        self._preferred = Path(env_override) if env_override else None
        self._resolved: Path | None | None = None
        self._cache: dict[int, ImageFont.FreeTypeFont] = {}
        self._bitmap_font: ImageFont.ImageFont | None = None

    # ------------------------------------------------------------------ #

    def _path(self) -> Path | None:
        """Locate a usable font file, once."""
        if self._resolved is not None:
            return self._resolved

        if self._preferred is not None and self._preferred.is_file():
            self._resolved = self._preferred
            return self._resolved

        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for name in _CANDIDATE_NAMES:
                candidate = directory / name
                if candidate.is_file():
                    self._resolved = candidate
                    return self._resolved
            # Last resort inside the directory: any font at all.
            for pattern in ("*.otf", "*.ttf", "*.ttc"):
                for candidate in sorted(directory.glob(pattern)):
                    self._resolved = candidate
                    return self._resolved

        self._resolved = None
        return None

    # ------------------------------------------------------------------ #

    def get(self, size: int) -> ImageFont.ImageFont:
        """A font at ``size`` points, cached."""
        size = max(8, int(size))
        path = self._path()
        if path is None:
            if self._bitmap_font is None:
                self._bitmap_font = ImageFont.load_default()
            return self._bitmap_font

        cached = self._cache.get(size)
        if cached is None:
            try:
                cached = ImageFont.truetype(str(path), size)
            except OSError:
                if self._bitmap_font is None:
                    self._bitmap_font = ImageFont.load_default()
                return self._bitmap_font
            self._cache[size] = cached
        return cached

    @property
    def path(self) -> Path | None:
        return self._path()
