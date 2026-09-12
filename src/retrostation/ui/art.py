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
        #: ``(game key, width, height)`` already found to carry no fanart or
        #: screenshot at all, so the panel stops asking to have it decoded.
        self._backdrop_missing: set[tuple] = set()

    # ------------------------------------------------------------------ #

    def thumbnail(self, game: Game, width: int, height: int, *,
                  prefer_logo: bool = False, cover: bool = False) -> object | None:
        """Scaled artwork for ``game``, or ``None`` when it cannot be shown yet.

        The frame loop never decodes: it takes what the warm thread has already
        cached and leaves the rest to the warm-up that :meth:`prefetch` fills
        from the frame loop's idle pass.  ``None`` therefore means
        either "this game has no such artwork" or "not ready yet"; the screens
        draw the same empty plate for both, and the panel is repainted once the
        warm-up lands (see the epoch check in ``App._draw``).
        """
        kind = ASSET_LOGO if prefer_logo else ASSET_COVER
        if game.asset(kind) is None:
            return None
        bitmap = self._library.thumbnail_cached(kind, game, width, height, cover=cover)
        if bitmap is None:
            # Nothing anywhere: decode it now.  Waiting for the warm-up instead
            # means a placeholder that, on a long list, can take minutes to be
            # replaced -- indistinguishable from "the covers are missing".  The
            # warm-up still pre-builds what the cursor is about to reach, so this
            # is the one-off case, not the steady state.
            bitmap = self._library.thumbnail(kind, game, width, height, cover=cover)
        return bitmap

    def backdrop(self, game: Game, width: int, height: int) -> object | None:
        """Panel-filling art to sit behind the game page, or ``None``.

        Fanart is what every other frontend uses for this, and a screenshot is
        the stand-in when a game has none.  A 天马 pack calls those same two
        assets ``background`` and ``screenshot`` -- which is precisely where the
        media scanner already files them -- so both layouts land here without
        any special case.

        Never decodes (see :meth:`thumbnail`).  A miss is deliberately *not*
        cached: the next frame retries and hits as soon as the warm thread is
        done, which is what makes the art fade in instead of stalling a frame.
        """
        key = (game.key, width, height)
        cached = self._backdrops.get(key)
        if cached is not None or key in self._backdrop_missing:
            return cached

        pending = False
        for kind in (ASSET_FANART, ASSET_SCREENSHOT):
            if game.asset(kind) is None:
                continue
            scaled = self._library.thumbnail_cached(kind, game, width, height)
            if scaled is None:
                pending = True
                continue
            bitmap = cover_bitmap(scaled, width, height)
            if len(self._backdrops) >= _BACKDROP_LIMIT:
                self._backdrops.clear()
            self._backdrops[key] = bitmap
            return bitmap

        if pending:
            # Same as :meth:`thumbnail`: decode rather than show nothing, and let
            # the warm-up cover the cases this one did not (see ``prefetch``).
            for kind in (ASSET_FANART, ASSET_SCREENSHOT):
                if game.asset(kind) is None:
                    continue
                scaled = self._library.thumbnail(kind, game, width, height)
                if scaled is not None:
                    bitmap = cover_bitmap(scaled, width, height)
                    if len(self._backdrops) >= _BACKDROP_LIMIT:
                        self._backdrops.clear()
                    self._backdrops[key] = bitmap
                    return bitmap
            return None
        # Every kind is absent: remember that, so the panel is not asked again.
        if len(self._backdrop_missing) >= _BACKDROP_LIMIT:
            self._backdrop_missing.clear()
        self._backdrop_missing.add(key)
        return None

    def placeholder(self, seed: str, width: int, height: int) -> object:
        key = (seed, width, height)
        bitmap = self._placeholders.get(key)
        if bitmap is None:
            bitmap = placeholder_bitmap(self._platform, seed, width, height)
            if len(self._placeholders) >= 64:
                self._placeholders.clear()
            self._placeholders[key] = bitmap
        return bitmap

    def prefetch(self, game: Game, slots) -> bool:
        """Queue ``game``'s artwork for every slot in ``slots``.

        A slot is ``(kind, width, height, cover)`` -- the same triple the
        cache keys on, so the warm-up produces exactly the files the screen
        will ask for.  Slots are grouped by kind first because one source has
        to be decoded once no matter how many sizes it feeds.

        Returns ``True`` when there is nothing more to do for this game --
        queued, or no artwork to queue -- and ``False`` only when the warm-up
        queue is full.  The caller has to be able to tell those apart: a full
        queue means "ask again in a moment", a game with no cover means
        "never ask again".
        """
        grouped: dict[str, list[tuple[int, int, bool]]] = {}
        for kind, width, height, cover in slots:
            grouped.setdefault(kind, []).append((width, height, cover))

        for kind, sizes in grouped.items():
            path = game.asset(kind)
            if path is None:
                continue
            if not self._library.warm_thumbnails(Path(path), sizes):
                return False
        return True

    def set_prefetch(self, active: bool) -> None:
        """Let the warm-up thread run, or hold it while the player moves."""
        self._library.set_thumbnail_warm(active)

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
