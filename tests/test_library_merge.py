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
