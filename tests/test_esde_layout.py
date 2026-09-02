"""ES-DE layout: where the gamelist and the media live.

Two roots, one sub-folder naming:

* **ES-DE installed** (``config.metadata.esde_root``) -- gamelist in
  ``<root>/gamelists/<system>/``, media in ``<root>/downloaded_media/<system>/``,
  both outside the ROM tree;
* **no ES-DE** (the default) -- gamelist in ``<SYS>/gamelist.xml``, media under
  ``<SYS>/media/``.

The sub-folder names are ES-DE's either way (``covers/``, ``screenshots/``,
``videos/``, ``marquees/``, ``fanart/``), so the two layouts differ only in the
root and a card can move between them without renaming anything.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config, MetadataConfig
from retrostation.core.model import Game
from retrostation.data.media import media_dirs_for, resolve_assets
from retrostation.data.sources.esde import ESDESource
from retrostation.data.systems import esde_system_name


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (16, 16), (200, 120, 40, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


PS_GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game>
    <path>./灵魂能力.chd</path>
    <name>灵魂能力</name>
    <desc>3D 武器格斗。</desc>
    <playcount>2</playcount>
  </game>
</gameList>
"""

FC_GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game>
    <path>./魂斗罗.nes</path>
    <name>魂斗罗</name>
  </game>
</gameList>
"""


class TestSystemName:
    """ES-DE spells systems its own way; the firmware spells them differently."""

    def test_known_systems_are_translated(self) -> None:
        assert esde_system_name("ps") == "psx"
        assert esde_system_name("fc") == "nes"
        assert esde_system_name("sfc") == "snes"
        assert esde_system_name("md") == "megadrive"

    def test_firmware_upper_case_still_resolves(self) -> None:
        assert esde_system_name("PS") == "psx"
        assert esde_system_name("FC") == "nes"

    def test_unmapped_keys_fall_back_to_themselves(self) -> None:
        assert esde_system_name("gb") == "gb"
        assert esde_system_name("未知平台") == "未知平台"


class TestWithoutEsdeRoot:
    """The default: everything stays inside the ROM directory."""

    def test_media_root_is_the_rom_directorys_media(self, platform) -> None:
        dirs = media_dirs_for(platform, Config(), "FC")
        assert dirs.by_kind["cover"] == platform.rom_root / "FC" / "media" / "covers"
        assert dirs.by_kind["video"] == platform.rom_root / "FC" / "media" / "videos"
        assert dirs.by_kind["logo"] == platform.rom_root / "FC" / "media" / "marquees"
        assert dirs.by_kind["screenshot"] == platform.rom_root / "FC" / "media" / "screenshots"
        assert dirs.by_kind["fanart"] == platform.rom_root / "FC" / "media" / "fanart"

    def test_media_is_found_in_the_esde_subfolders(self, platform, rom_root: Path) -> None:
        media = rom_root / "FC" / "media"
        files = {
            "covers/魂斗罗.png": png_bytes(),
            "screenshots/魂斗罗.png": png_bytes(),
            "videos/魂斗罗.mp4": b"mp4",
            "marquees/魂斗罗.png": png_bytes(),
            "fanart/魂斗罗.jpg": b"jpg",
        }
        for relative, payload in files.items():
            target = media / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

        game = Game.from_rom("FC", rom_root / "FC" / "魂斗罗.nes")
        resolved = resolve_assets(game, media_dirs_for(platform, Config(), "FC"))

        assert resolved.asset("cover") == (media / "covers" / "魂斗罗.png").resolve()
        assert resolved.asset("screenshot") == (media / "screenshots" / "魂斗罗.png").resolve()
        assert resolved.asset("video") == (media / "videos" / "魂斗罗.mp4").resolve()
        assert resolved.asset("logo") == (media / "marquees" / "魂斗罗.png").resolve()
        assert resolved.asset("fanart") == (media / "fanart" / "魂斗罗.jpg").resolve()

    def test_the_esde_layout_beats_the_older_imgs_convention(self, platform, rom_root: Path) -> None:
        """``media/covers/`` is the new convention; ``Imgs/`` is only a fallback.

        The fixture already ships ``FC/Imgs/魂斗罗.png``.
        """
        covers = rom_root / "FC" / "media" / "covers"
        covers.mkdir(parents=True)
        (covers / "魂斗罗.png").write_bytes(png_bytes())

        game = Game.from_rom("FC", rom_root / "FC" / "魂斗罗.nes")
        resolved = resolve_assets(game, media_dirs_for(platform, Config(), "FC"))
        assert resolved.asset("cover").parent.name == "covers"

    def test_gamelist_is_read_from_the_rom_directory(self, rom_root: Path) -> None:
        (rom_root / "FC" / "gamelist.xml").write_text(FC_GAMELIST, encoding="utf-8")
        source = ESDESource()
        assert source.detect(rom_root / "FC") is True
        assert "魂斗罗.nes" in source.load(rom_root / "FC")


class TestWithEsdeRoot:
    """ES-DE's own trees, keyed by ES-DE's own system names."""

    @pytest.fixture
    def esde_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "ES-DE"
        (root / "gamelists" / "psx").mkdir(parents=True)
        (root / "downloaded_media" / "psx").mkdir(parents=True)
        return root

    def test_gamelist_comes_from_the_esde_tree(self, rom_root: Path, esde_root: Path) -> None:
        (esde_root / "gamelists" / "psx" / "gamelist.xml").write_text(PS_GAMELIST, encoding="utf-8")
        source = ESDESource(esde_root)
        system_dir = rom_root / "PS"
        system_dir.mkdir()

        assert source.detect(system_dir) is True
        assert set(source.load(system_dir)) == {"灵魂能力.chd"}

    def test_save_writes_back_where_it_read(self, rom_root: Path, esde_root: Path) -> None:
        target = esde_root / "gamelists" / "psx" / "gamelist.xml"
        target.write_text(PS_GAMELIST, encoding="utf-8")
        source = ESDESource(esde_root)
        system_dir = rom_root / "PS"
        system_dir.mkdir()

        entries = source.load(system_dir)
        game = source.to_game("PS", system_dir / "灵魂能力.chd", entries["灵魂能力.chd"])
        game.play_count = 7
        entries["灵魂能力.chd"] = source.to_raw(game, entries["灵魂能力.chd"])
        source.save(system_dir, entries)

        assert "<playcount>7</playcount>" in target.read_text(encoding="utf-8")
        assert (esde_root / "gamelists" / "psx" / "gamelist.xml.bak").is_file()
        # No second copy is invented next to the ROMs.
        assert not (system_dir / "gamelist.xml").exists()

    def test_an_existing_rom_directory_gamelist_still_wins(
        self, rom_root: Path, esde_root: Path
    ) -> None:
        """A card that only ever had ``<SYS>/gamelist.xml`` keeps using it."""
        system_dir = rom_root / "PS"
        system_dir.mkdir()
        (system_dir / "gamelist.xml").write_text(PS_GAMELIST, encoding="utf-8")

        source = ESDESource(esde_root)
        assert source.gamelist_path(system_dir) == system_dir / "gamelist.xml"

    def test_media_comes_from_downloaded_media(self, platform, esde_root: Path) -> None:
        covers = esde_root / "downloaded_media" / "psx" / "covers"
        covers.mkdir(parents=True)
        (covers / "灵魂能力.png").write_bytes(png_bytes())
        videos = esde_root / "downloaded_media" / "psx" / "videos"
        videos.mkdir(parents=True)
        (videos / "灵魂能力.mp4").write_bytes(b"mp4")

        config = Config(metadata=MetadataConfig(esde_root=str(esde_root)))
        dirs = media_dirs_for(platform, config, "PS")
        assert dirs.by_kind["cover"] == covers

        game = Game.from_rom("PS", platform.rom_root / "PS" / "灵魂能力.chd")
        resolved = resolve_assets(game, dirs)
        assert resolved.asset("cover") == (covers / "灵魂能力.png").resolve()
        assert resolved.asset("video") == (videos / "灵魂能力.mp4").resolve()

    def test_rom_directory_media_is_still_a_fallback(self, platform, rom_root: Path, esde_root: Path) -> None:
        """With a shared ES-DE tree, ``<SYS>/media/`` is still worth looking in."""
        covers = rom_root / "PS" / "media" / "covers"
        covers.mkdir(parents=True)
        (covers / "灵魂能力.png").write_bytes(png_bytes())

        config = Config(metadata=MetadataConfig(esde_root=str(esde_root)))
        dirs = media_dirs_for(platform, config, "PS")
        game = Game.from_rom("PS", rom_root / "PS" / "灵魂能力.chd")
        resolved = resolve_assets(game, dirs)
        assert resolved.asset("cover") == (covers / "灵魂能力.png").resolve()

    def test_relative_media_paths_also_resolve_against_the_esde_tree(
        self, rom_root: Path, esde_root: Path
    ) -> None:
        """Scrapers disagree on what a gamelist path is relative to: try both."""
        covers = esde_root / "downloaded_media" / "psx" / "covers"
        covers.mkdir(parents=True)
        (covers / "灵魂能力.png").write_bytes(png_bytes())

        gamelist = """<?xml version="1.0"?>
<gameList>
  <game>
    <path>./灵魂能力.chd</path>
    <name>灵魂能力</name>
    <cover>./covers/灵魂能力.png</cover>
  </game>
</gameList>
"""
        (esde_root / "gamelists" / "psx" / "gamelist.xml").write_text(gamelist, encoding="utf-8")

        source = ESDESource(esde_root)
        system_dir = rom_root / "PS"
        system_dir.mkdir()
        entry = source.load(system_dir)["灵魂能力.chd"]
        game = source.to_game("PS", system_dir / "灵魂能力.chd", entry)
        assert game.asset("cover") == (covers / "灵魂能力.png").resolve()
