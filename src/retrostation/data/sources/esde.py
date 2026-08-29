"""ES-DE / EmulationStation ``gamelist.xml`` source.

The contract here is stricter than "just parse XML": this file is *shared*
state that Skraper, ES-DE and Batocera also write to.  Therefore:

* **unknown elements are preserved verbatim and in order** -- losing a
  ``<fanart>`` or a custom tag because we did not model it is data loss;
* writes are **atomic** (temp file + ``os.replace``) and leave one ``.bak``;
* only the fields we actually own are touched; everything else is replayed
  from the stored element list.
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
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

# --------------------------------------------------------------------------- #
# Field mapping
# --------------------------------------------------------------------------- #

#: Internal asset kind -> ES-DE element names, in priority order.
_MEDIA_TAGS: dict[str, tuple[str, ...]] = {
    ASSET_COVER: ("cover", "image", "thumbnail"),
    ASSET_LOGO: ("marquee", "wheel"),
    ASSET_SCREENSHOT: ("screenshot", "titleshot"),
    ASSET_VIDEO: ("video",),
    ASSET_FANART: ("fanart",),
}
_MEDIA_BY_TAG: dict[str, str] = {
    tag: kind for kind, tags in _MEDIA_TAGS.items() for tag in tags
}

#: Elements we model.  Everything else is passed through untouched.
_KNOWN_TAGS = frozenset(
    {
        "path", "name", "sortname", "desc", "rating", "releasedate",
        "developer", "publisher", "genre", "players",
        "playcount", "lastplayed", "favorite", "completed", "hidden",
        "kidgame", "broken",
        *_MEDIA_BY_TAG.keys(),
    }
)

#: Provenance elements copied into ``Game.extra``.
_PROVENANCE_TAGS = ("id", "source", "hash", "region", "lang", "romtype")


def _rom_name(path_text: str) -> str:
    """``./Foo.nes`` -> ``Foo.nes`` (ES-DE writes relative paths)."""
    return Path(path_text.strip()).name


def _datetime(value: str) -> datetime | None:
    """Parse ES-DE ``YYYYMMDDThhmmss`` (and plain ISO, to be forgiving)."""
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed = PartialDate.parse(text)
    if parsed and parsed.month and parsed.day:
        try:
            return datetime(parsed.year, parsed.month, parsed.day)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Child:
    """One child element of ``<game>``, kept verbatim for the round-trip."""

    tag: str
    text: str
    attribs: dict[str, str]


@dataclass
class _GameElement:
    """Everything we know about one ``<game>`` element, in document order."""

    children: list[_Child] = field(default_factory=list)
    #: ``id`` / ``source`` / ``hash`` live in attributes, not child elements.
    attribs: dict[str, str] = field(default_factory=dict)


class ESDESource(MetadataSource):
    """Reads and writes ``<system>/gamelist.xml``."""

    name = "esde"
    display_name = "ES-DE / EmulationStation"
    writable = True
    priority = 10

    FILENAME = "gamelist.xml"

    # ------------------------------------------------------------------ #
    # Discovery / reading
    # ------------------------------------------------------------------ #

    def detect(self, system_dir: Path) -> bool:
        return (system_dir / self.FILENAME).is_file()

    def load(self, system_dir: Path) -> dict[str, RawEntry]:
        path = system_dir / self.FILENAME
        try:
            tree = ET.parse(path)
        except (OSError, ET.ParseError):
            return {}  # unreadable gamelist must not break the library

        root = tree.getroot()
        if root.tag not in ("gameList", "gamecollection"):
            return {}

        modified = 0.0
        try:
            modified = path.stat().st_mtime
        except OSError:
            pass

        entries: dict[str, RawEntry] = {}
        for element in root.findall("game"):
            raw = self._parse_game(element, modified)
            if raw and raw.key:
                entries.setdefault(raw.key, raw)
        return entries

    def _parse_game(self, element: ET.Element, modified: float) -> RawEntry | None:
        children = [
            _Child(tag=child.tag, text=(child.text or "").strip(), attribs=dict(child.attrib))
            for child in element
        ]
        path_text = next((child.text for child in children if child.tag == "path"), "")
        if not path_text:
            return None  # a <game> without <path> is unusable

        key = _rom_name(path_text)
        # Every child is kept in ``fields`` -- including ones we do not model --
        # so provenance lookups (``<hash>``) work without special-casing.
        # Writing back still only touches known tags; the rest are replayed
        # verbatim from ``opaque``.
        fields: dict[str, str] = {child.tag: child.text for child in children}
        attribs = {str(k): str(v) for k, v in element.attrib.items()}
        fields.update(attribs)

        media: dict[str, str] = {}
        for child in children:
            kind = _MEDIA_BY_TAG.get(child.tag)
            if kind and child.text and kind not in media:
                media[kind] = child.text

        opaque = _GameElement(children=children, attribs=attribs)
        return RawEntry(key=key, fields=fields, media=media, opaque=opaque, modified=modified)

    # ------------------------------------------------------------------ #
    # Conversion
    # ------------------------------------------------------------------ #

    def to_game(self, system: str, rom: Path, raw: RawEntry) -> Game:
        desc = str(raw.get("desc", "") or "")
        game = Game.from_rom(system, rom)

        game.name = str(raw.get("name", "") or "").strip() or rom.stem
        game.sortname = str(raw.get("sortname", "") or "").strip() or None
        # ES-DE keeps one free-text blob; the first line reads fine as a blurb.
        game.summary = desc.strip().splitlines()[0] if desc.strip() else ""
        game.description = desc.strip()
        game.rating = self._rating(raw.get("rating"))
        game.release = PartialDate.parse(raw.get("releasedate"))
        game.developer = str(raw.get("developer", "") or "").strip() or None
        game.publisher = str(raw.get("publisher", "") or "").strip() or None
        game.genres = self._split_list(raw.get("genre"))
        game.players = str(raw.get("players", "") or "").strip() or None

        game.favorite = self._bool(raw.get("favorite"))
        game.play_count = self._int(raw.get("playcount"), 0)
        game.last_played = _datetime(str(raw.get("lastplayed", "") or ""))
        game.completed = self._bool(raw.get("completed"))
        game.hidden = self._bool(raw.get("hidden"))

        # ES-DE media paths are relative to the ROM's own directory.
        for kind, text in raw.media.items():
            if not text:
                continue
            game.set_asset(kind, _resolve_path(rom.parent, text))

        for tag in _PROVENANCE_TAGS:
            value = str(raw.get(tag, "") or "").strip()
            if value:
                game.extra[tag] = value

        game.sources[self.name] = self.FILENAME
        return game

    def to_raw(self, game: Game, previous: RawEntry | None) -> RawEntry:
        fields = dict(previous.fields) if previous else {}
        fields.update(
            {
                "path": previous.fields["path"] if previous and "path" in previous.fields else f"./{game.path.name}",
                "name": game.display_name,
                "desc": game.description or game.summary,
                "favorite": "true" if game.favorite else "false",
                "playcount": str(game.play_count),
                "lastplayed": game.last_played.strftime("%Y%m%dT%H%M%S") if game.last_played else "",
            }
        )
        if game.sortname:
            fields["sortname"] = game.sortname
        if game.rating is not None:
            fields["rating"] = f"{game.rating:.2f}"
        if game.release:
            fields["releasedate"] = _esde_date(game.release)
        if game.developer:
            fields["developer"] = game.developer
        if game.publisher:
            fields["publisher"] = game.publisher
        if game.genres:
            fields["genre"] = ", ".join(game.genres)
        if game.players:
            fields["players"] = game.players
        if game.completed:
            fields["completed"] = "true"
        if game.hidden:
            fields["hidden"] = "true"

        media = dict(previous.media) if previous else {}
        for kind in (ASSET_COVER, ASSET_LOGO, ASSET_VIDEO, ASSET_SCREENSHOT, ASSET_FANART):
            asset = game.asset(kind)
            if asset is not None:
                media[kind] = f"./{_relative(asset, game.path.parent)}"

        opaque = previous.opaque if previous else _GameElement()
        return RawEntry(key=game.path.name, fields=fields, media=media, opaque=opaque)

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    def save(self, system_dir: Path, entries: dict[str, RawEntry]) -> None:
        target = system_dir / self.FILENAME
        backup = system_dir / f"{self.FILENAME}.bak"

        root = ET.Element("gameList")
        for key in sorted(entries):
            root.append(self._build_game_element(entries[key]))

        _indent(root)
        tree = ET.ElementTree(root)

        system_dir.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            try:
                backup.write_bytes(target.read_bytes())
            except OSError:
                pass  # a failed backup must not block the save

        payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(system_dir), prefix=".gamelist-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _build_game_element(self, raw: RawEntry) -> ET.Element:
        """Rebuild one ``<game>``, replaying unknown children in their order."""
        opaque: _GameElement = raw.opaque if isinstance(raw.opaque, _GameElement) else _GameElement()
        element = ET.Element("game", dict(opaque.attribs))
        written: set[str] = set()

        for child in opaque.children:
            if child.tag in _KNOWN_TAGS:
                value = raw.fields.get(child.tag)
                if value is None or value == "":
                    continue  # field was cleared; drop it
                ET.SubElement(element, child.tag, attrib=dict(child.attribs)).text = str(value)
                written.add(child.tag)
            else:
                ET.SubElement(element, child.tag, attrib=dict(child.attribs)).text = child.text
                written.add(child.tag)

        # Media paths live in raw.media but are written under their ES-DE tag.
        for kind, tags in _MEDIA_TAGS.items():
            for tag in tags:
                if tag in written:
                    break
                value = raw.media.get(kind)
                if value:
                    ET.SubElement(element, tag).text = value
                    written.add(tag)
                    break

        # Newly added fields go last, in a stable order.
        for tag in sorted(_KNOWN_TAGS - set(written)):
            value = raw.fields.get(tag)
            if value not in (None, ""):
                ET.SubElement(element, tag).text = str(value)

        return element


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve_path(base: Path, text: str) -> Path | None:
    """Resolve a media path the way ES-DE does, keeping sub-directories.

    Returns ``None`` for paths that do not exist on disk: a stale gamelist
    entry must not shadow our own media directories (DESIGN §6.8.5 level 2).
    """
    text = text.strip()
    if not text:
        return None
    if text.startswith("~"):
        expanded = Path(os.path.expanduser(text))
        return expanded if expanded.is_file() else None

    path = Path(text)
    resolved = path if path.is_absolute() else (base / path).resolve()
    return resolved if resolved.is_file() else None


def _relative(asset: Path, rom_dir: Path) -> str:
    """Best-effort relative path for writing back."""
    try:
        return str(asset.relative_to(rom_dir))
    except ValueError:
        return str(asset)


def _esde_date(release: PartialDate) -> str:
    month = release.month or 1
    day = release.day or 1
    return f"{release.year:04d}{month:02d}{day:02d}T000000"


def _indent(element: ET.Element, level: int = 0) -> None:
    """Two-space indentation, matching what ES-DE writes."""
    padding = "\n" + "  " * level
    inner = "\n" + "  " * (level + 1)
    if len(element):
        if not element.text or not element.text.strip():
            element.text = inner
        for child in element:
            _indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = inner
        if not element[-1].tail or not element[-1].tail.strip():
            element[-1].tail = padding
    elif element.text is None:
        element.text = ""
