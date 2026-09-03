"""Library facade -- the single object the UI talks to.

Responsibilities, in one place so the UI never has to know how they fit
together:

* scanning (``scanner.py``),
* metadata loading and merging across sources (``sources/``),
* media resolution (``media.py``),
* the aggregates shown on the home carousel (ALL / FAV / RECENT).

Everything is loaded lazily per system: startup lists the directories, and the
first time the user opens FC we parse 515 gamelist entries then and there.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Config
from ..core.model import ASSET_VIDEO, Game, game_key
from ..platform.base import Platform
from . import sources as source_registry
from .media import MediaDirs, ThumbnailCache, media_dirs_for, resolve_assets
from .scanner import Rom, ScanResult, scan_library, scan_system
from .systems import AGGREGATES, lookup

log = logging.getLogger(__name__)


@dataclass
class SystemLibrary:
    """Everything the UI needs about one system."""

    key: str
    roms: list[Rom] = field(default_factory=list)
    games: list[Game] = field(default_factory=list)
    media_dirs: MediaDirs | None = None
    loaded: bool = False
    #: Asset paths are static for the lifetime of a process.  Resolving them
    #: once matters: ``Session.games()`` runs several times *per frame*, and
    #: re-probing every candidate file for 500 ROMs was measurable.
    media_resolved: bool = False

    def game_at(self, index: int) -> Game | None:
        if 0 <= index < len(self.games):
            return self.games[index]
        return None

    def index_of(self, game_key: str) -> int:
        for position, game in enumerate(self.games):
            if game.key == game_key:
                return position
        return -1


class Library:
    """Owns the scan result and hands out :class:`SystemLibrary` objects."""

    def __init__(self, platform: Platform, config: Config) -> None:
        self._platform = platform
        self._config = config
        self._lock = threading.RLock()
        self._roms: dict[str, list[Rom]] = {}
        self._systems: dict[str, SystemLibrary] = {}
        self._thumbnails = ThumbnailCache(
            platform,
            platform.config_dir / "thumbnails",
            enabled=config.thumbnail_cache,
        )
        self.last_scan: ScanResult | None = None

    # ------------------------------------------------------------------ #
    # Scanning
    # ------------------------------------------------------------------ #

    def _index_path(self) -> Path:
        """Where this card's scan cache lives.

        One file per ROM root.  The cards are separate libraries, and a shared
        ``index.json`` would hand the card you just switched to the previous
        card's listing -- which is exactly what ``cached_only`` paints on the
        first frame, so you would see the wrong systems until the background
        scan caught up.
        """
        root = self._platform.rom_root
        stem = root.parent.name or "root"
        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:6]
        return self._platform.config_dir / f"index-{stem}-{digest}.json"

    def scan(self, *, on_progress=None, cached_only: bool = False) -> ScanResult:
        """Scan the ROM root.  Safe to call again; it merges, not replaces.

        ``cached_only`` reads the index instead of listing the tree, so the
        first frame can come up populated; see
        :func:`~retrostation.data.scanner.scan_library`.
        """
        result = scan_library(
            self._platform,
            self._config,
            on_progress=on_progress,
            cached_only=cached_only,
            index_path=self._index_path(),
        )
        with self._lock:
            self.last_scan = result
            self._roms = result.systems
            # Every SystemLibrary memoises its games, and a re-scan may have
            # changed any of them: dropping only the systems that disappeared
            # left the rest showing the previous listing.
            self._systems.clear()
        log.info(
            "library scan: %d systems, %d ROMs in %.2fs (%d cached / %d rescanned)",
            len(result.systems), result.total_roms, result.duration, result.cached, result.rescanned,
        )
        return result

    def system_keys(self) -> list[str]:
        """System keys that actually contain ROMs, in home-page order."""
        order = {key: index for index, (key, _label, _zh) in enumerate(AGGREGATES)}
        with self._lock:
            keys = [
                key
                for key in self._roms
                if self._roms[key] and not lookup(key).hidden
            ]
        keys.sort(key=lambda key: (1, lookup(key).order, key))
        return keys

    def rom_count(self, system_key: str) -> int:
        with self._lock:
            return len(self._roms.get(system_key, []))

    # ------------------------------------------------------------------ #
    # Per-system access
    # ------------------------------------------------------------------ #

    def system(self, system_key: str) -> SystemLibrary:
        """A cached :class:`SystemLibrary`; metadata loads on first use."""
        with self._lock:
            library = self._systems.get(system_key)
            if library is None:
                library = SystemLibrary(key=system_key, roms=self._roms.get(system_key, []))
                self._systems[system_key] = library
        return library

    def load_games(self, system_key: str) -> SystemLibrary:
        """Parse metadata + resolve media for one system (idempotent)."""
        library = self.system(system_key)
        if library.loaded:
            return library

        roms = list(library.roms)
        if not roms:
            library.roms = scan_system(self._platform, system_key)
            roms = library.roms

        system_dir = self._platform.rom_root / system_key
        definition = lookup(system_key)

        try:
            bundles = source_registry.load_system(
                system_dir,
                names=self._config.metadata.sources,
                esde_root=self._config.metadata.esde_root,
            )
            games, variant_keys = source_registry.build_games(
                system_key, [rom.path for rom in roms], system_dir, bundles
            )
        except Exception:  # noqa: BLE001 - metadata must never break browsing
            log.exception("metadata load failed for %s; falling back to filenames", system_key)
            games = {rom.name: Game.from_rom(system_key, rom.path) for rom in roms}
            variant_keys = set()

        # Keep the ROM's on-disk order (already sorted by the scanner).
        ordered: list[Game] = []
        for rom in roms:
            key = game_key(system_key, rom.path)
            # A multi-file Pegasus block collapses into its primary Game; the
            # other files (its variants) are skipped so the title shows once.
            if key in variant_keys:
                continue
            # ``build_games`` keys by ``game_key``, which is system-prefixed;
            # looking a bare file name up here silently dropped every source's
            # metadata and left the library with filename-only games.
            game = games.get(key)
            if game is None:
                game = Game.from_rom(system_key, rom.path)
            ordered.append(game)

        library.games = self._filter_and_sort(ordered, system_key)
        library.media_dirs = media_dirs_for(self._platform, self._config, system_key)
        library.loaded = True
        return library

    def resolve_media(self, game: Game, system_key: str) -> Game:
        """Fill asset paths that metadata did not provide."""
        library = self.system(system_key)
        if library.media_dirs is None:
            library.media_dirs = media_dirs_for(self._platform, self._config, system_key)
        return resolve_assets(game, library.media_dirs)

    def resolve_all(self, system_key: str) -> list[Game]:
        """Resolve media for every game in a system, updating the cache in place.

        Runs once per system; afterwards the resolved list is returned as-is.
        """
        library = self.load_games(system_key)
        if library.media_resolved:
            return library.games
        if library.media_dirs is None:
            library.media_dirs = media_dirs_for(self._platform, self._config, system_key)
        library.games = [resolve_assets(game, library.media_dirs) for game in library.games]
        library.media_resolved = True
        return library.games

    # ------------------------------------------------------------------ #
    # Aggregates
    # ------------------------------------------------------------------ #

    def aggregate(self, key: str) -> list[Game]:
        """ALL / FAV / RECENT views, loading systems on demand.

        The first call parses metadata for every system (a few seconds on the
        device); afterwards everything is cached in the SystemLibrary objects.
        """
        if key not in {name for name, _l, _z in AGGREGATES}:
            return []
        games: list[Game] = []
        for system_key in self.system_keys():
            if lookup(system_key).is_standalone:
                continue
            # resolve_all 而非 load_games：聚合视图的预览也要有解析过的封面
            # 路径，否则预览全部落到「无封面」占位。
            games.extend(self.resolve_all(system_key))
        if key == "FAV":
            games = [game for game in games if game.favorite]
        elif key == "RECENT":
            games = [game for game in games if game.last_played is not None]
            games.sort(key=lambda game: game.last_played, reverse=True)
            games = games[:30]
        else:
            games.sort(key=lambda game: game.sort_key.casefold())
        return games

    # ------------------------------------------------------------------ #
    # Write-back
    # ------------------------------------------------------------------ #

    def save_state(self, game: Game, system_key: str) -> bool:
        """Persist ``favorite`` / ``playcount`` / ``lastplayed`` for one game.

        Returns True when something was written.  See DESIGN §6.8.4 for the
        rules: only the primary write source is touched.
        """
        metadata = self._config.metadata
        if metadata.read_only:
            return False

        system_dir = self._platform.rom_root / system_key
        source = source_registry.source_by_name(metadata.primary_write_source, metadata.esde_root)
        if source is None or not source.writable:
            return self._save_sidecar(game, system_key)

        # NOTE: ``detect()`` is deliberately not required here -- the very
        # first save is what creates ``gamelist.xml`` on a card that never had
        # one (DESIGN §6.4, level 4 of the lookup order).
        entries = source.load(system_dir)
        try:
            entries[game.path.name] = source.to_raw(game, entries.get(game.path.name))
            # Write the whole set back, never just this one entry: ``save``
            # rebuilds the document from what it is handed, so a single entry
            # would silently delete every other game in the system.
            source.save(system_dir, entries)
        except Exception:  # noqa: BLE001 - a failed save must not kill the app
            log.exception("failed to write metadata for %s", game.key)
            return False
        return True

    def _save_sidecar(self, game: Game, system_key: str) -> bool:
        """Last resort when no writable source exists."""
        if not self._config.metadata.sidecar_fallback:
            return False
        directory = self._platform.rom_root / system_key / ".retrostation"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "key": game.key,
                "favorite": game.favorite,
                "playcount": game.play_count,
                "lastplayed": game.last_played.isoformat() if game.last_played else None,
            }
            target = directory / "state.json"
            target.write_text(repr(payload), encoding="utf-8")
        except OSError:
            return False
        return True

    # ------------------------------------------------------------------ #

    def thumbnail(self, kind: str, game: Game, width: int, height: int):
        """A cached scaled bitmap, or ``None``."""
        source = game.asset(kind)
        if source is None:
            return None
        return self._thumbnails.get(kind, source, width, height)

    def has_video(self, game: Game) -> bool:
        return game.has_asset(ASSET_VIDEO)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _filter_and_sort(games: list[Game], system_key: str) -> list[Game]:
        """Hide entries marked hidden, and sort by display name."""
        visible = [game for game in games if not game.hidden]
        visible.sort(key=lambda game: game.sort_key.casefold())
        return visible
