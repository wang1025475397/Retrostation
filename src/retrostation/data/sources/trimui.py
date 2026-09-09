"""TrimUI image folders: ``<card>/Imgs/<SYSTEM>/<rom name>.png``.

The stock TrimUI / CrossMix launcher keeps cover art in an ``Imgs`` folder
sitting **next to** ``Roms`` -- ``/mnt/SDCARD/Imgs/FC/1942 (Japan, USA).png``
answers ``/mnt/SDCARD/Roms/FC/1942 (Japan, USA).zip`` -- one flat folder per
system, image file named after the ROM minus its extension.

This is a *media-only* source: it carries no metadata fields, only the cover,
so it never competes with ES-DE / Pegasus on content -- it fills the gaps they
leave, and its ``detect()`` is False on any card without the folder, which
makes it free everywhere else.
"""

from __future__ import annotations

from pathlib import Path

from ...core.model import ASSET_COVER, Game
from .base import MetadataSource, RawEntry

#: Extensions the stock launcher uses for cover art.
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"})


class TrimuiSource(MetadataSource):
    """Covers from the firmware's own ``Imgs`` tree."""

    name = "trimui"
    display_name = "TrimUI image folders"
    writable = False
    priority = 300  # after ES-DE / Pegasus: only fill the gaps they leave
    media_only = True

    # -- discovery -------------------------------------------------------- #

    @staticmethod
    def images_dir(system_dir: Path) -> Path:
        """``Roms/<SYS>`` -> the sibling-of-``Roms`` ``Imgs/<SYS>``."""
        return system_dir.parents[1] / "Imgs" / system_dir.name

    def detect(self, system_dir: Path) -> bool:
        return self.images_dir(system_dir).is_dir()

    # -- reading ---------------------------------------------------------- #

    def load(self, system_dir: Path) -> dict[str, RawEntry]:
        """Match every ROM against its same-stem image.

        Entries are keyed by ROM *file name* because that is what
        :func:`~retrostation.data.sources.build_games` matches on; ROMs the
        directory listing turns up but the scanner filters out (wrong
        extension) simply never match, and a surplus image costs one dict
        slot -- so no extension table is duplicated here.
        """
        images = self.images_dir(system_dir)
        by_stem: dict[str, Path] = {}
        for path in sorted(images.iterdir()):
            if path.is_file() and path.suffix.lower() in _IMAGE_EXTS:
                by_stem.setdefault(path.stem, path)

        entries: dict[str, RawEntry] = {}
        for rom in sorted(system_dir.iterdir()):
            if not rom.is_file():
                continue
            image = by_stem.get(rom.stem)
            if image is not None:
                entries[rom.name] = RawEntry(key=rom.name, media={ASSET_COVER: str(image)})
        return entries

    # -- conversion ------------------------------------------------------- #

    def to_game(self, system: str, rom: Path, raw: RawEntry) -> Game:
        game = Game.from_rom(system, rom)
        cover = raw.media.get(ASSET_COVER)
        if cover:
            # Assets are Paths downstream (media.py stats them); the raw media
            # dict is strings, exactly as every other source stores it.
            game.set_asset(ASSET_COVER, Path(cover))
        return game

    def to_raw(self, game: Game, previous: RawEntry | None) -> RawEntry:
        """Never called (:attr:`writable` is False); satisfies the interface."""
        return RawEntry(key=game.path.name, missing=previous is None)
