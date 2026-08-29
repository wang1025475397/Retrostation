"""Artwork provider.

Screens never decode images themselves -- decoding is the slow part of a frame
and belongs behind a cache.  :class:`ArtProvider` wraps the library's on-disk
thumbnail cache and hands out ready-to-draw bitmaps, falling back to the
deterministic placeholder so a missing cover still renders something.
"""

from __future__ import annotations

from pathlib import Path

from ..core.model import ASSET_COVER, ASSET_LOGO, Game
from ..data.library import Library
from ..data.media import placeholder_bitmap
from ..platform.base import Platform


class ArtProvider:
    """Cached artwork lookup used by every screen."""

    def __init__(self, library: Library, platform: Platform) -> None:
        self._library = library
        self._platform = platform

    # ------------------------------------------------------------------ #

    def thumbnail(self, game: Game, width: int, height: int, *, prefer_logo: bool = False) -> object | None:
        """Scaled artwork for ``game``, or ``None`` when there is none."""
        kind = ASSET_LOGO if prefer_logo else ASSET_COVER
        path = game.asset(kind)
        if path is None:
            return None
        return self._library.thumbnail(kind, game, width, height)

    def placeholder(self, seed: str, width: int, height: int) -> object:
        return placeholder_bitmap(self._platform, seed, width, height)

    def has_cover(self, game: Game) -> bool:
        path = game.asset(ASSET_COVER)
        return bool(path) and Path(path).is_file()
