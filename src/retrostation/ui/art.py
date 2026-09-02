"""Artwork provider.

Screens never decode images themselves -- decoding is the slow part of a frame
and belongs behind a cache.  :class:`ArtProvider` wraps the library's on-disk
thumbnail cache and hands out ready-to-draw bitmaps, falling back to the
deterministic placeholder so a missing cover still renders something.
"""

from __future__ import annotations

from pathlib import Path

from ..core.model import ASSET_COVER, ASSET_FANART, ASSET_LOGO, ASSET_SCREENSHOT, Game
from ..data.library import Library
from ..data.media import cover_bitmap, placeholder_bitmap
from ..platform.base import Platform
from .platform_art import PlatformArt


#: How many panel-sized backdrops to hold.  Each is a full-screen RGBA bitmap,
#: so four is already a few megabytes -- past that, re-decoding is cheaper than
#: the memory, and a player scrolling fast only ever sees the newest few.
_BACKDROP_LIMIT = 4


class ArtProvider:
    """Cached artwork lookup used by every screen."""

    def __init__(self, library: Library, platform: Platform,
                 platform_art: PlatformArt | None = None) -> None:
        self._library = library
        self._platform = platform
        #: Artwork shipped with the app (one background + logo per platform),
        #: kept apart from the per-game media the library manages.
        self.platform_art = platform_art if platform_art is not None else PlatformArt(platform)
        #: Generated placeholders are deterministic, so drawing one costs a
        #: gradient loop -- cheap once, noticeable ten times a frame.
        self._placeholders: dict[tuple, object] = {}
        #: Panel-sized backdrop per ``(game key, width, height)``.
        self._backdrops: dict[tuple, object] = {}

    # ------------------------------------------------------------------ #

    def thumbnail(self, game: Game, width: int, height: int, *, prefer_logo: bool = False) -> object | None:
        """Scaled artwork for ``game``, or ``None`` when there is none."""
        kind = ASSET_LOGO if prefer_logo else ASSET_COVER
        path = game.asset(kind)
        if path is None:
            return None
        return self._library.thumbnail(kind, game, width, height)

    def backdrop(self, game: Game, width: int, height: int) -> object | None:
        """Panel-filling art to sit behind the game page, or ``None``.

        Fanart is what every other frontend uses for this, and a screenshot is
        the stand-in when a game has none.  A 天马 pack calls those same two
        assets ``background`` and ``screenshot`` -- which is precisely where the
        media scanner already files them -- so both layouts land here without
        any special case.
        """
        key = (game.key, width, height)
        if key in self._backdrops:
            return self._backdrops[key]

        bitmap = None
        for kind in (ASSET_FANART, ASSET_SCREENSHOT):
            if game.asset(kind) is None:
                continue
            scaled = self._library.thumbnail(kind, game, width, height)
            if scaled is not None:
                bitmap = cover_bitmap(scaled, width, height)
                break

        if len(self._backdrops) >= _BACKDROP_LIMIT:
            self._backdrops.clear()
        self._backdrops[key] = bitmap
        return bitmap

    def placeholder(self, seed: str, width: int, height: int) -> object:
        key = (seed, width, height)
        bitmap = self._placeholders.get(key)
        if bitmap is None:
            bitmap = placeholder_bitmap(self._platform, seed, width, height)
            if len(self._placeholders) >= 64:
                self._placeholders.clear()
            self._placeholders[key] = bitmap
        return bitmap

    def has_cover(self, game: Game) -> bool:
        path = game.asset(ASSET_COVER)
        return bool(path) and Path(path).is_file()

    # -- shipped platform artwork ---------------------------------------- #

    def platform_background(self, key: str, width: int, height: int) -> object | None:
        """Square art for a platform card, or ``None`` when we ship none."""
        return self.platform_art.background(key, width, height)

    def platform_logo(self, key: str, width: int, height: int) -> object | None:
        """The platform's logo, alpha preserved, or ``None``."""
        return self.platform_art.logo(key, width, height)
