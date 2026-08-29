"""Media resolution, thumbnail cache and scanner tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.core.model import Game
from retrostation.data.library import Library
from retrostation.data.media import media_dirs_for, resolve_assets
from retrostation.data.scanner import (
    LibraryIndex,
    accepted_extensions,
    is_rom,
    scan_library,
    scan_system,
    signature,
)


def make_game(system: str, filename: str, **kwargs) -> Game:
    kwargs.setdefault("key", f"{system}/{filename}")
    kwargs.setdefault("path", Path(f"/x/{system}/{filename}"))
    kwargs.setdefault("name", Path(filename).stem)
    return Game(**kwargs)


def png_bytes(color=(200, 120, 40, 255), size=(32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class TestResolutionOrder:
    def test_source_paths_win(self, platform, rom_root: Path) -> None:
        dirs = media_dirs_for(platform, Config(), "FC")
        explicit = Path("/elsewhere/cover.png")
        game = make_game("FC", "魂斗罗.nes")
        game.set_asset("cover", explicit)

        resolved = resolve_assets(game, dirs)
        assert resolved.asset("cover") == explicit

    def test_our_own_directories(self, platform, rom_root: Path) -> None:
        game = make_game("FC", "魂斗罗.nes")
        resolved = resolve_assets(game, media_dirs_for(platform, Config(), "FC"))
        assert resolved.asset("cover") == (rom_root / "FC" / "Imgs" / "魂斗罗.png").resolve()

    def test_format_default_directories(self, platform, rom_root: Path) -> None:
        (rom_root / "FC" / "media" / "covers").mkdir(parents=True)
        (rom_root / "FC" / "media" / "covers" / "冒險島 [T-Eng].png").write_bytes(png_bytes())

        game = make_game("FC", "冒險島 [T-Eng].nes")
        resolved = resolve_assets(game, media_dirs_for(platform, Config(), "FC"))
        assert resolved.asset("cover") is not None
        assert resolved.asset("cover").name == "冒險島 [T-Eng].png"

    def test_missing_everything_yields_none(self, platform, rom_root: Path) -> None:
        game = make_game("FC", "不存在的游戏.nes")
        resolved = resolve_assets(game, media_dirs_for(platform, Config(), "FC"))
        assert resolved.asset("cover") is None
        assert resolved.asset("video") is None

    def test_alternate_suffixes(self, platform, rom_root: Path) -> None:
        (rom_root / "FC" / "video").mkdir()
        (rom_root / "FC" / "video" / "魂斗罗.webm").write_bytes(b"x")
        game = make_game("FC", "魂斗罗.nes")
        resolved = resolve_assets(game, media_dirs_for(platform, Config(), "FC"))
        assert resolved.asset("video") is not None


class TestThumbnailCache:
    def test_creates_cached_file_next_to_source(self, platform, rom_root: Path) -> None:
        from retrostation.data.media import ThumbnailCache

        source = rom_root / "FC" / "Imgs" / "魂斗罗.png"
        cache = ThumbnailCache(platform, rom_root / ".thumbs")

        bitmap = cache.get("cover", source, 84, 30)
        assert bitmap is not None

        # The cache lives in a dot-directory next to the artwork, so the ROM
        # scanner never mistakes it for media.
        cached = list((source.parent / ".cache").iterdir())
        assert len(cached) == 1
        assert cached[0].parent.name == ".cache"

    def test_second_hit_uses_the_same_file(self, platform, rom_root: Path) -> None:
        from retrostation.data.media import ThumbnailCache

        source = rom_root / "FC" / "Imgs" / "魂斗罗.png"
        cache = ThumbnailCache(platform, rom_root / ".thumbs")
        assert cache.get("cover", source, 84, 30) is not None
        assert cache.get("cover", source, 84, 30) is not None
        assert len(list((source.parent / ".cache").iterdir())) == 1

    def test_memory_is_bounded(self, platform, rom_root: Path) -> None:
        from retrostation.data.media import ThumbnailCache

        source = rom_root / "FC" / "Imgs" / "魂斗罗.png"
        cache = ThumbnailCache(platform, rom_root / ".thumbs")
        for width in range(60, 140):
            cache.get("cover", source, width, 30)
        assert len(cache._memory) <= 40  # noqa: SLF001 - testing the LRU itself

    def test_missing_source_is_none(self, platform, rom_root: Path) -> None:
        from retrostation.data.media import ThumbnailCache

        cache = ThumbnailCache(platform, rom_root / ".thumbs")
        assert cache.get("cover", rom_root / "FC" / "Imgs" / "nope.png", 84, 30) is None

    def test_disabled_cache_writes_nothing(self, platform, rom_root: Path) -> None:
        from retrostation.data.media import ThumbnailCache

        source = rom_root / "FC" / "Imgs" / "魂斗罗.png"
        cache = ThumbnailCache(platform, rom_root / ".thumbs", enabled=False)
        assert cache.get("cover", source, 84, 30) is not None
        assert not (source.parent / ".cache").exists()


class TestPlaceholder:
    def test_deterministic(self, platform) -> None:
        from retrostation.data.media import placeholder_bitmap

        first = placeholder_bitmap(platform, "FC/魂斗罗.nes", 64, 64)
        second = placeholder_bitmap(platform, "FC/魂斗罗.nes", 64, 64)
        assert list(first.getdata()) == list(second.getdata())

    def test_different_seeds_differ(self, platform) -> None:
        from retrostation.data.media import placeholder_bitmap

        first = placeholder_bitmap(platform, "FC/a.nes", 64, 64)
        second = placeholder_bitmap(platform, "SFC/b.sfc", 64, 64)
        assert list(first.getdata()) != list(second.getdata())


class TestScanner:
    def test_extension_matching(self) -> None:
        extensions = accepted_extensions("FC")
        assert is_rom("魂斗罗.nes", extensions)
        assert is_rom("game.ZIP", extensions)  # case insensitive
        assert not is_rom("README.txt", extensions)

    def test_hidden_and_cache_files_skipped(self) -> None:
        extensions = accepted_extensions("FC")
        assert not is_rom(".hidden.nes", extensions)
        assert not is_rom(".cache/junk.nes", extensions)
        assert not is_rom("Imgs/x.nes", extensions)

    def test_unknown_system_gets_generic_extensions(self) -> None:
        extensions = accepted_extensions("SOMETHINGNEW")
        assert is_rom("game.bin", extensions)
        assert is_rom("game.zip", extensions)

    def test_scan_system_is_sorted(self, platform) -> None:
        roms = scan_system(platform, "FC")
        names = [rom.name for rom in roms]
        assert names == sorted(names, key=str.casefold)
        assert "README.txt" not in names
        assert "魂斗罗.nes" in names

    def test_scan_library_skips_hidden_systems(self, platform) -> None:
        result = scan_library(platform, Config())
        assert "FC" in result.systems
        assert "GBA" in result.systems
        assert "APPS" not in result.systems
        assert result.total_roms == 4


class TestLibraryIndex:
    def test_round_trip(self, tmp_path: Path, rom_root: Path) -> None:
        from retrostation.data.scanner import Rom

        roms = [Rom("a.nes", rom_root / "FC" / "a.nes", 10, 1.0)]
        sig = signature(roms)

        index = LibraryIndex(tmp_path / "index.json")
        index.put("FC", sig, roms)
        index.flush()

        reloaded = LibraryIndex(tmp_path / "index.json")
        assert reloaded.get("FC", sig) == roms
        assert reloaded.get("FC", "changed") is None

    def test_version_mismatch_discards(self, tmp_path: Path) -> None:
        path = tmp_path / "index.json"
        path.write_text('{"version": 1, "systems": {}}', encoding="utf-8")
        assert LibraryIndex(path).get("FC", "x") is None


class TestLibraryFacade:
    def test_load_games_merges_metadata(self, platform, rom_root: Path) -> None:
        library = Library(platform, Config())
        library.scan()
        fc = library.load_games("FC")

        names = [game.name for game in fc.games]
        assert "超级马力欧兄弟" in names
        assert "魂斗罗" in names

    def test_games_are_sorted_and_complete(self, platform, rom_root: Path) -> None:
        library = Library(platform, Config())
        library.scan()
        fc = library.load_games("FC")
        keys = [game.key for game in fc.games]
        assert len(keys) == 3
        assert keys == sorted(keys, key=str.casefold)

    def test_save_state_creates_gamelist_when_missing(self, platform, rom_root: Path) -> None:
        """First save must create gamelist.xml, not fall back to a sidecar."""
        config = Config()
        config.metadata.primary_write_source = "esde"
        library = Library(platform, config)
        library.scan()

        game = library.load_games("FC").games[0]
        game.play_count = 5
        game.favorite = True
        assert library.save_state(game, "FC") is True

        gamelist = rom_root / "FC" / "gamelist.xml"
        assert gamelist.is_file()
        text = gamelist.read_text(encoding="utf-8")
        assert "<playcount>5</playcount>" in text
        assert "<favorite>true</favorite>" in text

    def test_sidecar_used_when_primary_source_not_writable(self, platform, rom_root: Path) -> None:
        config = Config()
        config.metadata.primary_write_source = "pegasus"  # read-only by design
        library = Library(platform, config)
        library.scan()
        game = library.load_games("FC").games[0]
        game.play_count = 3

        assert library.save_state(game, "FC") is True
        assert (rom_root / "FC" / ".retrostation" / "state.json").is_file()
        assert not (rom_root / "FC" / "gamelist.xml").exists()

    def test_read_only_config_writes_nothing(self, platform, rom_root: Path) -> None:
        config = Config()
        config.metadata.read_only = True
        library = Library(platform, config)
        library.scan()
        game = library.load_games("FC").games[0]

        assert library.save_state(game, "FC") is False
        assert not (rom_root / "FC" / ".retrostation").exists()

    def test_aggregates(self, platform, rom_root: Path) -> None:
        library = Library(platform, Config())
        library.scan()
        # ALL loads every system on demand; UNKNOWN is not an aggregate at all.
        assert len(library.aggregate("ALL")) == 4
        assert library.aggregate("FAV") == []
        assert library.aggregate("UNKNOWN") == []

    def test_aggregate_favourite_after_write(self, platform, rom_root: Path) -> None:
        config = Config()
        script = rom_root / "RA_launch.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        config.launcher.ra_script = str(script)

        library = Library(platform, config)
        library.scan()
        game = library.load_games("FC").games[0]
        game.favorite = True
        library.save_state(game, "FC")

        assert len(library.aggregate("FAV")) == 1
