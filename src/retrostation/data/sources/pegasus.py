"""Pegasus frontend metadata source (``metadata.pegasus.txt``).

Read-only by design: Pegasus has no representation for ``favorite``,
``playcount`` or ``lastplayed``, so Retrostation never writes this file and
keeps frontend state in the ES-DE gamelist (or in the sidecar fallback).

Format notes that actually matter when parsing:

* keys are case-insensitive and lowercase-normalised here;
* a value continues onto the next line when that line starts with whitespace;
* a line containing a single ``.`` inserts an empty line inside a value;
* ``file:`` may repeat (multi-disc games) -- the first one is the key.
"""

from __future__ import annotations

import os
from pathlib import Path

from ...core.model import (
    ASSET_COVER,
    ASSET_FANART,
    ASSET_LOGO,
    ASSET_SCREENSHOT,
    ASSET_VIDEO,
    Game,
    PartialDate,
)
from .base import MetadataSource, RawEntry

FILENAME = "metadata.pegasus.txt"

#: Pegasus asset key -> internal asset kind.  First match wins per kind.
_ASSET_KEYS: dict[str, str] = {
    "boxfront": ASSET_COVER,
    "box2d": ASSET_COVER,
    "marquee": ASSET_LOGO,
    "wheel": ASSET_LOGO,
    "screenshot": ASSET_SCREENSHOT,
    "titlescreen": ASSET_SCREENSHOT,
    "video": ASSET_VIDEO,
    "videos": ASSET_VIDEO,
    "fanart": ASSET_FANART,
}

#: Collection-level keys that describe the folder rather than a game.
_COLLECTION_KEYS = frozenset(
    {"collection", "extension", "directory", "command", "launch", "ignore-file", "ignore-ext", "shortname"}
)


class PegasusSource(MetadataSource):
    """Reads ``<system>/metadata.pegasus.txt``."""

    name = "pegasus"
    display_name = "Pegasus"
    writable = False
    priority = 20

    FILENAME = FILENAME

    def __init__(self, filename: str = FILENAME) -> None:
        self._filename = filename

    # ------------------------------------------------------------------ #
    # Discovery / reading
    # ------------------------------------------------------------------ #

    def detect(self, system_dir: Path) -> bool:
        return (system_dir / self._filename).is_file()

    def load(self, system_dir: Path) -> dict[str, RawEntry]:
        path = system_dir / self._filename
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}

        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0

        blocks = self._parse_blocks(text)
        entries: dict[str, RawEntry] = {}
        for block in blocks:
            entry = self._to_entry(block, modified)
            if entry is None:
                continue
            # Index the entry under *every* file it lists, pointing at the same
            # RawEntry object.  That way each variant ROM finds its metadata, and
            # the loader can collapse them into one game (see ``build_games``).
            for name in entry.files:
                entries.setdefault(name, entry)
        return entries

    # -- parsing ---------------------------------------------------------- #

    @staticmethod
    def _parse_blocks(text: str) -> list[tuple[dict[str, str], list[str]]]:
        """Split the file into ``(fields, files)`` blocks.

        Handles the two ways Pegasus lists ROMs for one game (both occur in the
        wild, and PegasusConverter parses both):

        * repeated ``file:`` lines -- one per ROM;
        * a single ``files:`` line with comma-separated ROMs, optionally
          continued on indented following lines.

        A ``game:`` line starts a new block; a block with no ``file``/``files``
        (collection-level metadata) yields an empty ``files`` list and is
        dropped later.
        """
        blocks: list[tuple[dict[str, str], list[str]]] = []
        fields: dict[str, str] = {}
        files: list[str] = []
        current_key: str | None = None
        in_files = False

        def flush() -> None:
            if fields or files:
                blocks.append((dict(fields), list(files)))

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                # A blank line ends a value continuation; a comment is ignored.
                if not stripped:
                    current_key = None
                    in_files = False
                continue

            # Continuation line (leading whitespace) of the current value.
            if line[0] in " \t" and current_key is not None:
                body = stripped
                if body == ".":
                    body = ""
                if in_files:
                    files.extend(p for p in body.split(",") if p.strip())
                elif current_key == "description":
                    fields[current_key] = f"{fields.get(current_key, '')}\n{body}"
                elif current_key not in ("game",):
                    # Unknown field continued onto the next line.
                    fields[current_key] = f"{fields.get(current_key, '')}\n{body}"
                continue

            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if not key:
                continue

            if key == "game":
                flush()
                fields, files = {}, []
                current_key = "game"
                in_files = False
                fields["game"] = value
            elif key in ("file", "files"):
                current_key = "file"
                in_files = True
                files.extend(p for p in value.split(",") if p.strip())
                # Keep the first spelling for provenance/round-trip.
                fields.setdefault("file", value)
            elif key.startswith("assets."):
                fields[key] = value
                current_key = None
                in_files = False
            else:
                current_key = key
                in_files = False
                if key in fields:
                    fields[key] = f"{fields[key]}\n{value}"  # repeated key
                else:
                    fields[key] = value

        flush()
        return blocks

    def _to_entry(self, block: tuple[dict[str, str], list[str]], modified: float) -> RawEntry | None:
        fields, files = block
        files = [name.strip() for name in files if name.strip()]
        if not files:
            return None  # a block without a file is collection metadata

        key = files[0]
        media: dict[str, str] = {}
        for name, value in fields.items():
            if not name.startswith("assets."):
                continue
            asset_key = name[len("assets."):].replace("_", "").replace("-", "").lower()
            kind = _ASSET_KEYS.get(asset_key)
            first = value.splitlines()[0].strip() if value else ""
            if kind and first and kind not in media:
                media[kind] = first

        return RawEntry(
            key=key,
            fields=dict(fields),
            media=media,
            modified=modified,
            files=list(files),
        )

    # ------------------------------------------------------------------ #
    # Conversion
    # ------------------------------------------------------------------ #

    def to_game(self, system: str, rom: Path, raw: RawEntry) -> Game:
        game = Game.from_rom(system, rom)
        f = raw.fields

        game.name = (f.get("game") or "").splitlines()[0].strip() or rom.stem
        game.sortname = (f.get("sortby") or "").strip() or None
        game.summary = (f.get("summary") or "").strip()
        game.description = (f.get("description") or "").strip() or game.summary
        game.rating = self._rating(f.get("rating"))
        game.release = PartialDate.parse(f.get("release"))
        game.developer = self._first_line(f.get("developer"))
        game.publisher = self._first_line(f.get("publisher"))
        game.genres = self._split_list(f.get("genre"))
        game.tags = self._split_list(f.get("tags"))
        game.players = self._first_line(f.get("players"))

        base = rom.parent
        for kind, text in raw.media.items():
            if text:
                game.set_asset(kind, self._resolve(base, text, rom))

        game.sources[self.name] = self._filename
        return game

    def to_raw(self, game: Game, previous: RawEntry | None) -> RawEntry:
        # Read-only source: rebuilding an entry is only useful for round-trip
        # tests, so we simply reuse what we parsed.
        return previous or RawEntry(key=game.path.name, missing=True)

    def save(self, system_dir: Path, entries: dict[str, RawEntry]) -> None:  # pragma: no cover
        raise super().save(system_dir, entries)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _first_line(value: object) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        first = text.splitlines()[0].strip()
        return first or None

    @staticmethod
    def _resolve(base: Path, text: str, rom: Path) -> Path | None:
        """Pegasus resolves relative paths against the collection directory.

        Only existing files are returned: a stale ``assets.`` line must not
        shadow our own media directories (DESIGN §6.8.5 level 2).
        """
        text = text.strip()
        if not text:
            return None
        path = Path(text)
        if path.is_absolute() or text.startswith("~"):
            expanded = Path(os.path.expanduser(text))
            return expanded if expanded.is_file() else None

        for candidate in (base / path, base / path.name):
            if candidate.is_file():
                return candidate.resolve()
        return None
