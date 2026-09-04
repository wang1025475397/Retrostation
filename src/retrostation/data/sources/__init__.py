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


def SOURCES(esde_root: str | Path | None = None) -> list[MetadataSource]:  # noqa: N802
    """All registered sources, ordered by priority.

    ``esde_root`` is the player's ES-DE folder (the one holding ``gamelists/``
    and ``downloaded_media/``); it is handed to the sources that understand it
    and ignored by the rest.  ``None`` means "no ES-DE installed", in which
    case everything is read from inside the ROM directories.
    """
    return sorted((ESDESource(esde_root), PegasusSource()), key=lambda s: s.priority)


def source_by_name(name: str, esde_root: str | Path | None = None) -> MetadataSource | None:
    for source in SOURCES(esde_root):
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


def load_system(
    system_dir: Path,
    names: list[str] | None = None,
    esde_root: str | Path | None = None,
) -> list[_SourceBundle]:
    """Load every enabled source for one system directory.

    A source that fails to parse is skipped, never fatal: one corrupt
    ``metadata.pegasus.txt`` must not hide the gamelist data.
    """
    bundles: list[_SourceBundle] = []
    for source in SOURCES(esde_root):
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
) -> tuple[dict[str, Game], set[str]]:
    """Merge every source into one :class:`Game` per logical game.

    Returns ``(games, variant_keys)``:

    * ``games`` maps ``game_key`` -> :class:`Game` for every entry that should
      appear in the library -- one per ROM, or one per multi-file Pegasus block;
    * ``variant_keys`` are the ``game_key``\\ s of ROM files that belong to a
      multi-file block but are not its primary file.  The caller drops them so
      a Pegasus ``game:`` block listing several ``file:`` lines shows up once,
      with the other files collected in :attr:`Game.variants`.

    ROMs absent from every source still get a minimal Game built from the file
    name, so the library is always complete.
    """
    rom_index = {rom.name: rom for rom in roms}
    # File names a metadata source (Pegasus ``ignore-files:``) asks to hide.
    # Lower-cased so the pack's spelling need not match the on-disk name.
    ignored: set[str] = set()
    for bundle in bundles:
        ignored.update(bundle.source.ignored_files(system_dir))
    ignored_lower = {name.lower() for name in ignored}
    if ignored_lower:
        rom_index = {
            name: rom for name, rom in rom_index.items()
            if name.lower() not in ignored_lower
        }
    games: dict[str, Game] = {}
    variant_keys: set[str] = set()

    # Per-ROM matches across all sources, by file name.
    matches: dict[str, list[tuple[MetadataSource, RawEntry]]] = {}
    for rom in roms:
        found = [
            (bundle.source, bundle.entries[rom.name])
            for bundle in bundles
            if rom.name in bundle.entries
        ]
        if found:
            matches[rom.name] = found

    # Group ROMs covered by a single multi-file entry (e.g. a Pegasus block
    # listing several ``file:`` lines).  They collapse into one Game.
    groups: dict[int, list[str]] = {}
    for bundle in bundles:
        for name, entry in bundle.entries.items():
            file_list = getattr(entry, "files", None)
            if file_list and len(file_list) > 1:
                groups.setdefault(id(entry), []).append(name)

    for names in groups.values():
        primary_name = next((n for n in names if n in matches), names[0])
        primary_rom = rom_index.get(primary_name)
        if primary_rom is None:
            continue
        found = matches[primary_name]
        candidates = [source.to_game(system, primary_rom, raw) for source, raw in found]
        game = merge_games(candidates) if len(candidates) > 1 else candidates[0]
        variants = [rom_index[n] for n in names if n != primary_name and n in rom_index]
        game = game.copy(variants=variants)
        key = game_key(system, primary_rom)
        games[key] = game
        for source, raw in found:
            if not raw.missing:
                game.sources.setdefault(source.name, system_dir.name)
        for name in names:
            if name != primary_name and name in rom_index:
                variant_keys.add(game_key(system, rom_index[name]))

    # Every other ROM -> its own Game (or a multi-file ROM with no grouping).
    for rom in roms:
        if rom.name.lower() in ignored_lower:
            continue
        key = game_key(system, rom)
        if key in games or key in variant_keys:
            continue
        found = matches.get(rom.name)
        if found:
            candidates = [source.to_game(system, rom, raw) for source, raw in found]
            game = merge_games(candidates) if len(candidates) > 1 else candidates[0]
        else:
            game = Game.from_rom(system, rom)
        games[key] = game
        if found:
            for source, raw in found:
                if not raw.missing:
                    game.sources.setdefault(source.name, system_dir.name)

    return games, variant_keys


def collect_state(entries: dict[str, RawEntry]) -> dict[str, RawEntry]:
    """Filter entries down to the ones worth writing back."""
    return {key: entry for key, entry in entries.items() if not entry.missing}


def asset_kinds() -> tuple[str, ...]:
    """Re-exported so callers do not import the model for one constant."""
    return ASSET_KEYS
