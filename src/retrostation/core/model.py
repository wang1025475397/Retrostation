"""Canonical game model.

This module is deliberately free of any knowledge about *where* metadata comes
from (``gamelist.xml``, ``metadata.pegasus.txt``, ...) and *how* it is drawn
(SDL, PIL, Compose, ...).  Everything above the metadata sources — the UI, the
scanner, the launcher — speaks only in terms of :class:`Game`.

That indirection is what lets us add a new metadata format by touching a single
file, and reuse the very same model on the planned Android port.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Media kinds
# --------------------------------------------------------------------------- #

ASSET_COVER = "cover"
ASSET_LOGO = "logo"
ASSET_SCREENSHOT = "screenshot"
ASSET_VIDEO = "video"
ASSET_FANART = "fanart"

#: Ordered by display importance.  ``assets`` dicts are keyed by these values.
ASSET_KEYS: tuple[str, ...] = (
    ASSET_COVER,
    ASSET_LOGO,
    ASSET_SCREENSHOT,
    ASSET_VIDEO,
    ASSET_FANART,
)


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #

# ES-DE writes ``19850913T000000``, Pegasus writes ``1985-09-13`` and both
# allow partial values (``1985``, ``1985-09``).  A plain ``datetime.date``
# cannot hold those, hence this little type.


@dataclass(frozen=True)
class PartialDate:
    """A calendar date that may be missing its month and/or day."""

    year: int
    month: int | None = None
    day: int | None = None

    @classmethod
    def parse(cls, raw: object) -> PartialDate | None:
        """Parse the common frontend date formats.

        Accepts ``19850913T000000``, ``1985-09-13``, ``1985-09``, ``1985`` and
        ``datetime``/``date`` objects.  Returns ``None`` for anything that does
        not contain at least a usable year.
        """
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return cls(raw.year, raw.month, raw.day)
        if isinstance(raw, PartialDate):
            return raw

        text = str(raw).strip()
        if not text:
            return None

        # ES-DE: 19850913T000000 -> keep only the date part before the 'T'.
        text = text.split("T", 1)[0]

        # Pegasus / ISO: 1985-09-13 or 1985-09
        if "-" in text:
            parts = text.split("-")
        elif len(text) == 8 and text.isdigit():
            parts = [text[0:4], text[4:6], text[6:8]]
        else:
            parts = [text]

        parts = [p for p in parts if p]
        try:
            year = int(parts[0])
        except (ValueError, IndexError):
            return None
        if year <= 0:
            return None

        month = cls._bounded(parts, 1, 1, 12)
        day = cls._bounded(parts, 2, 1, 31)
        if month is None:
            day = None
        return cls(year, month, day)

    @staticmethod
    def _bounded(parts: list[str], index: int, low: int, high: int) -> int | None:
        try:
            value = int(parts[index])
        except (ValueError, IndexError):
            return None
        return value if low <= value <= high else None

    @property
    def year_only(self) -> bool:
        return self.month is None

    def __str__(self) -> str:
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


# --------------------------------------------------------------------------- #
# Game
# --------------------------------------------------------------------------- #


def game_key(system: str, rom: Path) -> str:
    """Stable, filesystem-independent primary key for a ROM.

    Uses the file *name* (not the full path) so that moving the ROM root or
    switching between SD cards does not invalidate every favourite.
    """
    return f"{system}/{rom.name}"


@dataclass
class Game:
    """A single game, normalised across every metadata source."""

    # -- identity ---------------------------------------------------------- #
    key: str
    path: Path
    name: str = ""
    #: Extra ROM files that belong to the *same* game (Pegasus blocks may list
    #: several ``file:`` lines for one title -- region/revision variants).  The
    #: primary ``path`` is what launches; these exist so the game shows up once
    #: instead of once per file.
    variants: list[Path] = field(default_factory=list)

    # -- descriptive metadata ---------------------------------------------- #
    sortname: str | None = None
    summary: str = ""
    description: str = ""
    #: Normalised to 0.0 - 1.0 regardless of what the source used.
    rating: float | None = None
    release: PartialDate | None = None
    developer: str | None = None
    publisher: str | None = None
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    players: str | None = None

    # -- state owned by the frontend --------------------------------------- #
    favorite: bool = False
    play_count: int = 0
    last_played: datetime | None = None
    completed: bool = False
    hidden: bool = False

    # -- media ------------------------------------------------------------- #
    #: key from :data:`ASSET_KEYS` -> absolute path (or ``None`` when absent)
    assets: dict[str, Path | None] = field(default_factory=dict)

    # -- provenance -------------------------------------------------------- #
    #: source name -> name of the file it was read from
    sources: dict[str, str] = field(default_factory=dict)
    #: Anything we do not understand, kept verbatim so that a round-trip
    #: through Retrostation never destroys data written by Skraper/Pegasus.
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Constructors
    # ------------------------------------------------------------------ #

    @classmethod
    def from_rom(cls, system: str, rom: Path) -> Game:
        """Minimal game synthesised from a ROM file (no metadata present)."""
        return cls(key=game_key(system, rom), path=rom, name=rom.stem)

    def copy(self, **changes: Any) -> Game:
        """Return a modified copy (keeps ``assets``/``extra`` from being shared).

        Hand-rolled instead of :func:`dataclasses.replace`, which costs ~0.5 ms
        per call -- opening a 600-ROM system copies every game once, and that
        was 300 ms of the platform switch.
        """
        changes.setdefault("assets", dict(self.assets))
        changes.setdefault("extra", dict(self.extra))
        clone = Game.__new__(Game)
        clone.__dict__.update(self.__dict__)
        for key, value in changes.items():
            if key not in _GAME_FIELDS:
                raise TypeError(f"Game has no field {key!r}")
            setattr(clone, key, value)
        return clone

    # ------------------------------------------------------------------ #
    # Derived accessors
    # ------------------------------------------------------------------ #

    @property
    def display_name(self) -> str:
        """Name to render; never empty."""
        return self.name or self.path.stem

    @property
    def is_multi(self) -> bool:
        """True when the game bundles more than one ROM file."""
        return bool(self.variants)

    @property
    def sort_key(self) -> str:
        return self.sortname or self.display_name

    @property
    def rating_stars(self) -> int:
        """Rating expressed as 0-5 whole stars."""
        if self.rating is None:
            return 0
        return max(0, min(5, round(self.rating * 5)))

    @property
    def blurb(self) -> str:
        """Short single paragraph for the bottom screen, with sane fallback."""
        return (self.summary or self.description or "").strip()

    def asset(self, kind: str) -> Path | None:
        return self.assets.get(kind)

    def set_asset(self, kind: str, value: Path | None) -> None:
        if kind not in ASSET_KEYS:
            raise ValueError(f"unknown asset kind {kind!r}; expected one of {ASSET_KEYS}")
        self.assets[kind] = value

    def has_asset(self, kind: str) -> bool:
        return self.assets.get(kind) is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Game {self.key!r} name={self.display_name!r}>"


#: Field names of :class:`Game`; :meth:`Game.copy` uses them to reject typos.
_GAME_FIELDS = frozenset(field.name for field in fields(Game))
