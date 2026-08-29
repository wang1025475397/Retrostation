"""Source registry and the orchestration that ties sources together.

Adding a metadata format means: write ``sources/<name>.py`` implementing
:class:`MetadataSource`, then add one line to :data:`SOURCES`.  Nothing else in
the codebase changes -- that is the entire point of this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core.model import ASSET_KEYS, Game, game_key
from .base import MetadataSource, RawEntry, UnsupportedWrite, merge_games
from .esde import ESDESource
from .pegasus import PegasusSource

__all__ = [
    "MetadataSource",
    "RawEntry",
    "UnsupportedWrite",
    "SOURCES",
    "source_by_name",
    "load_system",
    "build_games",
]


def SOURCES() -> list[MetadataSource]:  # noqa: N802 - reads like a constant
    """All registered sources, ordered by priority."""
    return sorted((ESDESource(), PegasusSource()), key=lambda s: s.priority)


def source_by_name(name: str) -> MetadataSource | None:
    for source in SOURCES():
        if source.name == name:
            return source
    return None


# --------------------------------------------------------------------------- #
# Per-system loading
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SourceBundle:
    """One source's entries for a system, sorted into priority order."""

    source: MetadataSource
    entries: dict[str, RawEntry]


def load_system(system_dir: Path, names: list[str] | None = None) -> list[_SourceBundle]:
    """Load every enabled source for one system directory.

    A source that fails to parse is skipped, never fatal: one corrupt
    ``metadata.pegasus.txt`` must not hide the gamelist data.
    """
    bundles: list[_SourceBundle] = []
    for source in SOURCES():
        if names and source.name not in names:
            continue
        if not source.detect(system_dir):
            continue
        bundles.append(_SourceBundle(source=source, entries=source.load(system_dir)))
    return bundles


def build_games(
    system: str,
    roms: list[Path],
    system_dir: Path,
    bundles: list[_SourceBundle],
) -> dict[str, Game]:
    """Merge every source into one :class:`Game` per ROM.

    ROMs absent from every source still get a minimal Game built from the file
    name, so the library is always complete.
    """
    games: dict[str, Game] = {}
    for rom in roms:
        key = game_key(system, rom)
        candidates = [
            bundle.source.to_game(system, rom, bundle.entries[rom.name])
            for bundle in bundles
            if rom.name in bundle.entries
        ]
        games[key] = merge_games(candidates) if candidates else Game.from_rom(system, rom)

        # Remember which file each source came from, for provenance/debugging.
        for bundle in bundles:
            entry = bundle.entries.get(rom.name)
            if entry is not None and not entry.missing:
                games[key].sources.setdefault(bundle.source.name, system_dir.name)
    return games


def collect_state(entries: dict[str, RawEntry]) -> dict[str, RawEntry]:
    """Filter entries down to the ones worth writing back."""
    return {key: entry for key, entry in entries.items() if not entry.missing}


def asset_kinds() -> tuple[str, ...]:
    """Re-exported so callers do not import the model for one constant."""
    return ASSET_KEYS
