"""Metadata source plug-in layer (DESIGN §6.8).

A *source* knows one metadata format.  Everything above this layer speaks only
in terms of :class:`~retrostation.core.model.Game`, so adding Pegasus (or any
future format) is a matter of writing one module and registering it.

Merge rules, all of them deliberate:

* **first wins** for descriptive fields -- the earliest source in
  ``config.metadata.sources`` wins, later sources only fill gaps;
* **most recent wins** for frontend-owned state (``favorite`` / ``play_count``
  / ``last_played``), decided by the source file's mtime;
* media paths are merged per asset kind with the same first-wins rule.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from ...core.model import ASSET_KEYS, Game, PartialDate, game_key


class UnsupportedWrite(RuntimeError):
    """Raised when a read-only source is asked to save."""

    def __init__(self, source_name: str) -> None:
        super().__init__(f"metadata source {source_name!r} is read-only")


# --------------------------------------------------------------------------- #
# Raw entry
# --------------------------------------------------------------------------- #


@dataclass
class RawEntry:
    """A game's metadata exactly as a source stores it.

    ``fields`` holds values this source understands, ``opaque`` anything the
    source needs for a lossless round-trip (ES-DE keeps the ordered XML
    children there).  ``missing`` marks an entry the source does not actually
    have -- the scanner synthesises those so that every ROM gets a Game.
    """

    key: str
    fields: dict[str, Any] = field(default_factory=dict)
    media: dict[str, str] = field(default_factory=dict)
    #: Every ROM file this entry covers.  Most sources describe one file, so
    #: this defaults to ``[key]``; Pegasus blocks that list several ``file:``
    #: lines carry all of them here, which is what drives multi-file grouping.
    files: list[str] = field(default_factory=list)
    opaque: Any = None
    modified: float = 0.0
    missing: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)


# --------------------------------------------------------------------------- #
# Source interface
# --------------------------------------------------------------------------- #


class MetadataSource(abc.ABC):
    """One metadata format."""

    #: Stable identifier, used in ``config.metadata.sources``.
    name: str = ""
    #: Human-readable name for the settings page.
    display_name: str = ""
    #: Whether :meth:`save` may be called.
    writable: bool = False
    #: Lower number = higher priority when merging.
    priority: int = 100

    # -- discovery -------------------------------------------------------- #

    @abc.abstractmethod
    def detect(self, system_dir: Path) -> bool:
        """Whether ``system_dir`` carries this source's metadata."""

    # -- reading ---------------------------------------------------------- #

    @abc.abstractmethod
    def load(self, system_dir: Path) -> dict[str, RawEntry]:
        """Read every entry, keyed by ROM file name.

        Must never raise for a corrupt file: return whatever parsed and log
        the problem.  A broken metadata file must not break the library.
        """

    # -- writing ---------------------------------------------------------- #

    def save(self, system_dir: Path, entries: Mapping[str, RawEntry]) -> None:
        """Write entries back.  Only meaningful when :attr:`writable`."""
        raise UnsupportedWrite(self.name)

    # -- conversion ------------------------------------------------------- #

    @abc.abstractmethod
    def to_game(self, system: str, rom: Path, raw: RawEntry) -> Game:
        """Normalise a raw entry into the canonical model."""

    @abc.abstractmethod
    def to_raw(self, game: Game, previous: RawEntry | None) -> RawEntry:
        """Prepare a raw entry for writing back."""

    # -- helpers for subclasses ------------------------------------------- #

    @staticmethod
    def _split_list(value: object, separators: str = ",/") -> list[str]:
        """Split a multi-value field, dropping empties and duplicates."""
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            items = [str(v).strip() for v in value]
        else:
            items = [p.strip() for p in re.split(f"[{re.escape(separators)}]", str(value))]
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            if item and item.lower() not in seen:
                seen.add(item.lower())
                ordered.append(item)
        return ordered

    @staticmethod
    def _rating(value: object) -> float | None:
        """Normalise a rating to 0.0-1.0, accepting '80%' and '0.8'."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        try:
            if text.endswith("%"):
                number = float(text[:-1]) / 100.0
            else:
                number = float(text)
        except ValueError:
            return None
        return max(0.0, min(1.0, number))

    @staticmethod
    def _bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _int(value: object, default: int = 0) -> int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default


# --------------------------------------------------------------------------- #
# Merging
# --------------------------------------------------------------------------- #

#: Fields where "most recently written" beats source priority.
_STATE_FIELDS = ("favorite", "play_count", "last_played", "completed")


def merge_games(candidates: list[Game]) -> Game:
    """Merge one ROM's metadata from several sources into a single :class:`Game`.

    ``candidates`` must already be ordered by source priority (see
    :attr:`MetadataSource.priority`).
    """
    if not candidates:
        raise ValueError("merge_games() needs at least one candidate")
    if len(candidates) == 1:
        return candidates[0]

    merged = candidates[0].copy()

    for other in candidates[1:]:
        _fill_blanks(merged, other)

        # Frontend-owned state: newest file wins, not source priority.
        for name in _STATE_FIELDS:
            mine = getattr(merged, name)
            theirs = getattr(other, name)
            if theirs is None or theirs is False or theirs == 0:
                continue
            if _newer(other, merged):
                setattr(merged, name, theirs)

        merged.sources.update(other.sources)
        merged.extra.update(other.extra)

    return merged


def _fill_blanks(target: Game, donor: Game) -> None:
    """Copy descriptive fields that the higher-priority source left empty."""
    simple = ("sortname", "summary", "description", "developer", "publisher", "players")
    for name in simple:
        if not getattr(target, name) and getattr(donor, name):
            setattr(target, name, getattr(donor, name))

    if target.rating is None and donor.rating is not None:
        target.rating = donor.rating
    if target.release is None and donor.release is not None:
        target.release = donor.release
    if not target.genres and donor.genres:
        target.genres = list(donor.genres)
    if not target.tags and donor.tags:
        target.tags = list(donor.tags)

    for kind in ASSET_KEYS:
        if not target.has_asset(kind) and donor.has_asset(kind):
            target.set_asset(kind, donor.asset(kind))


def _newer(candidate: Game, incumbent: Game) -> bool:
    """True when ``candidate``'s state looks more recent than ``incumbent``'s."""
    theirs = candidate.last_played
    mine = incumbent.last_played
    if theirs is not None and (mine is None or theirs > mine):
        return True
    if theirs is None and mine is None:
        return candidate.play_count > incumbent.play_count
    return False


def build_game(system: str, rom: Path) -> Game:
    """Baseline game for a ROM with no metadata at all."""
    return Game.from_rom(system, rom)


def with_release(game: Game, raw_value: object) -> Game:
    """Helper for sources that parse dates at conversion time."""
    parsed = PartialDate.parse(raw_value)
    return replace(game, release=parsed) if parsed else game


def key_for(system: str, rom: Path) -> str:
    """Re-exported for convenience of source implementations."""
    return game_key(system, rom)
