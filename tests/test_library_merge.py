"""A metadata source's data must reach the games the UI actually browses.

Regression: ``build_games`` keys its result by :func:`game_key`, which is
system-prefixed (``"FC/超级马力欧兄弟.nes"``), while ``Library.load_games``
looked the bare file name up.  Every lookup missed, so every game fell back to
``Game.from_rom`` -- no names, no descriptions, no ratings, no media, from any
source, on every system.  Found by copying a 天马 (Pegasus) folder onto the
device: the metadata parsed fine and still showed nothing.
"""

from __future__ import annotations

from pathlib import Path

from retrostation.core.config import Config
from retrostation.data.library import Library
from tests.conftest import FakePlatform

GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game>
    <path>./超级马力欧兄弟.nes</path>
    <name>超级马力欧兄弟</name>
    <desc>经典平台跳跃。</desc>
    <rating>0.85</rating>
  </game>
</gameList>
"""


def _library(root: Path) -> Library:
    (root / "FC" / "gamelist.xml").write_text(GAMELIST, encoding="utf-8")
    platform = FakePlatform(root)
    library = Library(platform, Config())
    library.scan()
    return library


class TestMetadataReachesGames:
    def test_descriptive_fields_survive(self, rom_root: Path) -> None:
        games = _library(rom_root).resolve_all("FC")
        mario = next(game for game in games if game.path.name == "超级马力欧兄弟.nes")

        assert mario.name == "超级马力欧兄弟"
        assert mario.blurb == "经典平台跳跃。"
        assert mario.rating is not None

    def test_provenance_is_recorded(self, rom_root: Path) -> None:
        """The source is remembered, which is how the bug showed up in a probe."""
        games = _library(rom_root).resolve_all("FC")
        mario = next(game for game in games if game.path.name == "超级马力欧兄弟.nes")

        assert mario.sources == {"esde": "gamelist.xml"}

    def test_roms_without_metadata_still_appear(self, rom_root: Path) -> None:
        """A partial gamelist must not hide the ROMs it does not mention."""
        games = _library(rom_root).resolve_all("FC")

        assert len(games) == 3
        unnamed = next(game for game in games if game.path.name == "魂斗罗.nes")
        assert unnamed.name == "魂斗罗"
        assert unnamed.sources == {}


class TestSavingState:
    """Saving one game's state must not cost the rest of the system theirs."""

    MANY = """<?xml version="1.0"?>
<gameList>
  <game>
    <path>./超级马力欧兄弟.nes</path>
    <name>超级马力欧兄弟</name>
    <desc>经典平台跳跃。</desc>
    <rating>0.85</rating>
  </game>
  <game>
    <path>./魂斗罗.nes</path>
    <name>魂斗罗</name>
    <genre>射击</genre>
  </game>
  <game>
    <path>./冒險島 [T-Eng].nes</path>
    <name>冒險島</name>
    <developer>Hudson</developer>
  </game>
</gameList>
"""

    def _library(self, root: Path) -> Library:
        library = Library(FakePlatform(root), Config())
        library.scan()
        return library

    def test_favouriting_one_game_keeps_the_other_entries(self, rom_root: Path) -> None:
        """Regression: saving only the one entry wiped every other game."""
        (rom_root / "FC" / "gamelist.xml").write_text(self.MANY, encoding="utf-8")
        library = self._library(rom_root)

        games = library.resolve_all("FC")
        mario = next(game for game in games if game.path.name == "超级马力欧兄弟.nes")
        mario.favorite = True

        assert library.save_state(mario, "FC") is True

        text = (rom_root / "FC" / "gamelist.xml").read_text(encoding="utf-8")
        assert "<name>魂斗罗</name>" in text
        assert "<name>冒險島</name>" in text
        assert "<developer>Hudson</developer>" in text  # untouched fields too
        assert "<favorite>true</favorite>" in text

    def test_every_game_is_still_there_after_a_save(self, rom_root: Path) -> None:
        (rom_root / "FC" / "gamelist.xml").write_text(self.MANY, encoding="utf-8")
        first = self._library(rom_root)
        mario = next(g for g in first.resolve_all("FC") if g.path.name == "超级马力欧兄弟.nes")
        mario.favorite = True
        first.save_state(mario, "FC")

        again = self._library(rom_root).resolve_all("FC")
        names = {game.name for game in again}
        assert names == {"超级马力欧兄弟", "魂斗罗", "冒險島"}

    def test_a_brand_new_gamelist_is_well_formed(self, rom_root: Path) -> None:
        """A card with no gamelist at all: the one we create must be usable."""
        library = self._library(rom_root)
        contra = next(g for g in library.resolve_all("FC") if g.path.name == "魂斗罗.nes")
        contra.favorite = True

        assert library.save_state(contra, "FC") is True

        text = (rom_root / "FC" / "gamelist.xml").read_text(encoding="utf-8")
        assert text.startswith("<?xml")
        assert "<gameList>" in text
        assert "<path>./魂斗罗.nes</path>" in text
        assert "<favorite>true</favorite>" in text

    def test_a_new_entry_reads_like_esde_wrote_it(self, rom_root: Path) -> None:
        """Alphabetical order would put <broken> first and bury <path>."""
        library = self._library(rom_root)
        contra = next(g for g in library.resolve_all("FC") if g.path.name == "魂斗罗.nes")
        contra.favorite = True
        contra.play_count = 2

        library.save_state(contra, "FC")

        text = (rom_root / "FC" / "gamelist.xml").read_text(encoding="utf-8")
        assert text.find("<path>") < text.find("<name>") < text.find("<playcount>")


class TestPegasusReachesGames:
    PEGASUS = """collection: FC
extensions: nes

game: 魂斗罗
file: 魂斗罗.nes
developer: Konami
description: 1987 年的跑射游戏。
"""

    def test_pegasus_metadata_survives(self, rom_root: Path) -> None:
        """The 天马 / Pegasus layout: same code path, same bug."""
        (rom_root / "FC" / "metadata.pegasus.txt").write_text(self.PEGASUS, encoding="utf-8")
        games = _library(rom_root).resolve_all("FC")
        contra = next(game for game in games if game.path.name == "魂斗罗.nes")

        assert contra.blurb == "1987 年的跑射游戏。"
        assert contra.developer == "Konami"
        assert contra.sources.get("pegasus") == "metadata.pegasus.txt"
