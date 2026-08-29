"""Canonical model tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.core.model import ASSET_KEYS, Game, PartialDate, game_key


class TestPartialDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("19850913T000000", "1985-09-13"),
            ("1985-09-13", "1985-09-13"),
            ("1985-09", "1985-09"),
            ("1985", "1985"),
            ("19850913", "1985-09-13"),
            ("", None),
            ("garbage", None),
            ("0000", None),
        ],
    )
    def test_parse(self, raw, expected):
        parsed = PartialDate.parse(raw)
        assert (str(parsed) if parsed else None) == expected

    def test_out_of_range_components_are_dropped(self):
        assert str(PartialDate.parse("1985-13-40")) == "1985"

    def test_accepts_datetime(self):
        from datetime import datetime

        parsed = PartialDate.parse(datetime(1994, 12, 3, 8, 30))
        assert parsed == PartialDate(1994, 12, 3)

    def test_year_only_flag(self):
        assert PartialDate(1985).year_only is True
        assert PartialDate(1985, 9).year_only is False

    def test_month_without_day_drops_day(self):
        assert PartialDate.parse("1985-09-13") == PartialDate(1985, 9, 13)
        assert PartialDate.parse("1985-99-13").day is None


class TestGame:
    def test_key_is_filename_based(self):
        assert game_key("FC", Path("/mnt/mmc/Roms/FC/魂斗罗.nes")) == "FC/魂斗罗.nes"
        assert game_key("FC", Path("/mnt/sdcard/Roms/FC/魂斗罗.nes")) == "FC/魂斗罗.nes"

    def test_from_rom_uses_stem_as_name(self):
        game = Game.from_rom("FC", Path("/x/超级马力欧兄弟.nes"))
        assert game.display_name == "超级马力欧兄弟"
        assert game.key == "FC/超级马力欧兄弟.nes"

    def test_rating_stars(self):
        assert Game(key="k", path=Path("x")).rating_stars == 0
        assert Game(key="k", path=Path("x"), rating=0.8).rating_stars == 4
        assert Game(key="k", path=Path("x"), rating=0.44).rating_stars == 2

    def test_sort_key_prefers_sortname(self):
        game = Game(key="k", path=Path("x/b.nes"), name="b", sortname="a")
        assert game.sort_key == "a"

    def test_asset_accessors(self):
        game = Game(key="k", path=Path("x"))
        assert game.has_asset("cover") is False

        game.set_asset("cover", Path("/tmp/a.png"))
        assert game.has_asset("cover") is True
        assert game.asset("cover") == Path("/tmp/a.png")

        with pytest.raises(ValueError):
            game.set_asset("boxart", Path("/tmp/a.png"))

    def test_copy_isolates_mutable_containers(self):
        original = Game(key="k", path=Path("x"))
        original.set_asset("cover", Path("a.png"))
        original.extra["id"] = "1"

        clone = original.copy(name="other")
        clone.set_asset("cover", Path("b.png"))
        clone.extra["id"] = "2"

        assert original.asset("cover") == Path("a.png")
        assert original.extra["id"] == "1"

    def test_asset_keys_are_stable(self):
        assert ASSET_KEYS[0] == "cover"
        assert ASSET_KEYS[1] == "logo"
        assert "video" in ASSET_KEYS
