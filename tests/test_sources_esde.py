"""ES-DE ``gamelist.xml`` source tests.

The critical property under test: a load -> save round-trip must not lose
anything we did not model, because that file is shared with Skraper / ES-DE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.data.sources.esde import ESDESource

GAMELIST = """<?xml version="1.0"?>
<gameList>
  <game id="1234" source="ScreenScraper">
    <path>./超级马力欧兄弟.nes</path>
    <name>超级马力欧兄弟</name>
    <desc>经典平台跳跃。
1985 年发售。</desc>
    <rating>0.850000</rating>
    <releasedate>19850913T000000</releasedate>
    <developer>任天堂</developer>
    <publisher>任天堂</publisher>
    <genre>平台跳跃</genre>
    <players>1-2</players>
    <playcount>23</playcount>
    <lastplayed>20260820T193000</lastplayed>
    <favorite>true</favorite>
    <cover>./Imgs/超级马力欧兄弟.png</cover>
    <marquee>./logo/超级马力欧兄弟.png</marquee>
    <video>./video/超级马力欧兄弟.mp4</video>
    <fanart>./media/fanart/超级马力欧兄弟.jpg</fanart>
    <customTag keep-me="1">不要丢我</customTag>
    <hash>abcdef</hash>
  </game>
  <game>
    <path>./魂斗罗.nes</path>
    <name>魂斗罗</name>
  </game>
  <game>
    <name>没有 path 的条目应当被忽略</name>
  </game>
</gameList>
"""


@pytest.fixture
def system_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "FC"
    directory.mkdir()
    (directory / "gamelist.xml").write_text(GAMELIST, encoding="utf-8")
    (directory / "超级马力欧兄弟.nes").write_bytes(b"nes")
    (directory / "魂斗罗.nes").write_bytes(b"nes")

    # The media the gamelist points at must exist: non-existent paths are
    # dropped on purpose so they cannot shadow our own directories.
    (directory / "Imgs").mkdir()
    (directory / "Imgs" / "超级马力欧兄弟.png").write_bytes(b"\x89PNG fake")
    (directory / "logo").mkdir()
    (directory / "logo" / "超级马力欧兄弟.png").write_bytes(b"\x89PNG fake")
    (directory / "video").mkdir()
    (directory / "video" / "超级马力欧兄弟.mp4").write_bytes(b"mp4")
    (directory / "media" / "fanart").mkdir(parents=True)
    (directory / "media" / "fanart" / "超级马力欧兄弟.jpg").write_bytes(b"jpg")
    return directory


class TestLoad:
    def test_detect(self, system_dir: Path) -> None:
        assert ESDESource().detect(system_dir) is True
        assert ESDESource().detect(system_dir.parent) is False

    def test_keys_are_rom_file_names(self, system_dir: Path) -> None:
        entries = ESDESource().load(system_dir)
        assert set(entries) == {"超级马力欧兄弟.nes", "魂斗罗.nes"}

    def test_descriptive_fields(self, system_dir: Path) -> None:
        source = ESDESource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)

        assert game.name == "超级马力欧兄弟"
        assert game.summary == "经典平台跳跃。"
        assert "1985 年发售。" in game.description
        assert game.rating == pytest.approx(0.85)
        assert str(game.release) == "1985-09-13"
        assert game.developer == "任天堂"
        assert game.genres == ["平台跳跃"]
        assert game.players == "1-2"
        assert game.play_count == 23
        assert game.favorite is True
        assert game.last_played is not None and game.last_played.year == 2026

    def test_media_paths_are_resolved(self, system_dir: Path) -> None:
        source = ESDESource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)

        # Sub-directories must survive: ./media/fanart/x.jpg is not ./x.jpg.
        assert game.asset("cover") == (system_dir / "Imgs" / "超级马力欧兄弟.png").resolve()
        assert game.asset("logo") == (system_dir / "logo" / "超级马力欧兄弟.png").resolve()
        assert game.asset("video") == (system_dir / "video" / "超级马力欧兄弟.mp4").resolve()
        assert game.asset("fanart") == (system_dir / "media" / "fanart" / "超级马力欧兄弟.jpg").resolve()

    def test_media_paths_that_do_not_exist_are_dropped(self, system_dir: Path) -> None:
        """A stale gamelist entry must not shadow our own media directories."""
        source = ESDESource()
        entries = source.load(system_dir)
        entry = entries["魂斗罗.nes"]
        entry.media = {"cover": "./Imgs/missing.png"}

        game = source.to_game("FC", system_dir / "魂斗罗.nes", entry)
        assert game.asset("cover") is None

    def test_provenance_kept_in_extra(self, system_dir: Path) -> None:
        source = ESDESource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)
        # id / source come from element attributes, hash from a child element.
        assert game.extra["id"] == "1234"
        assert game.extra["source"] == "ScreenScraper"
        assert game.extra["hash"] == "abcdef"

    def test_minimal_entry_still_works(self, system_dir: Path) -> None:
        source = ESDESource()
        entry = source.load(system_dir)["魂斗罗.nes"]
        game = source.to_game("FC", system_dir / "魂斗罗.nes", entry)
        assert game.name == "魂斗罗"
        assert game.rating is None
        assert game.favorite is False


class TestRoundTrip:
    def test_unknown_elements_and_order_survive(self, system_dir: Path, tmp_path: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)

        # Touch the state fields, leave everything else alone.
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entries["超级马力欧兄弟.nes"])
        game.play_count = 99
        entries["超级马力欧兄弟.nes"] = source.to_raw(game, entries["超级马力欧兄弟.nes"])
        source.save(system_dir, entries)

        reloaded = source.load(system_dir)
        text = (system_dir / "gamelist.xml").read_text(encoding="utf-8")

        # Unknown tag and its attribute are still there, still in place.
        assert "<customTag keep-me=\"1\">不要丢我</customTag>" in text
        assert reloaded["超级马力欧兄弟.nes"].get("hash") == "abcdef"
        assert reloaded["超级马力欧兄弟.nes"].get("id") == "1234"
        # Order is preserved: hash comes after the custom tag, as in the source.
        assert text.find("<customTag") < text.find("<hash>")

    def test_state_fields_are_updated(self, system_dir: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entries["超级马力欧兄弟.nes"])
        game.play_count = 99
        game.favorite = False
        entries["超级马力欧兄弟.nes"] = source.to_raw(game, entries["超级马力欧兄弟.nes"])
        source.save(system_dir, entries)

        reloaded = source.load(system_dir)
        assert reloaded["超级马力欧兄弟.nes"].get("playcount") == "99"
        assert reloaded["超级马力欧兄弟.nes"].get("favorite") == "false"

    def test_backup_is_created(self, system_dir: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)
        source.save(system_dir, entries)
        assert (system_dir / "gamelist.xml.bak").is_file()

    def test_new_entry_is_appended(self, system_dir: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)
        fresh = source.to_raw(
            ESDESource().to_game(
                "FC", system_dir / "魂斗罗.nes", entries["魂斗罗.nes"]
            ),
            None,
        )
        source.save(system_dir, {**entries, "魂斗罗.nes": fresh})

        reloaded = source.load(system_dir)
        assert "魂斗罗.nes" in reloaded
        assert reloaded["魂斗罗.nes"].get("name") == "魂斗罗"

    def test_cleared_fields_are_dropped(self, system_dir: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entries["超级马力欧兄弟.nes"])
        game.description = ""
        game.summary = ""  # desc falls back to summary, so both must go
        entries["超级马力欧兄弟.nes"] = source.to_raw(game, entries["超级马力欧兄弟.nes"])
        source.save(system_dir, entries)

        text = (system_dir / "gamelist.xml").read_text(encoding="utf-8")
        assert "<desc>" not in text
        assert "<name>" in text  # untouched fields survive

    def test_desc_falls_back_to_summary(self, system_dir: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)
        game = source.to_game("FC", system_dir / "魂斗罗.nes", entries["魂斗罗.nes"])
        game.summary = "单行简介"
        raw = source.to_raw(game, entries["魂斗罗.nes"])
        assert raw.fields["desc"] == "单行简介"

    def test_corrupt_file_is_ignored(self, tmp_path: Path) -> None:
        directory = tmp_path / "FC"
        directory.mkdir()
        (directory / "gamelist.xml").write_text("<gameList><game>", encoding="utf-8")
        assert ESDESource().load(directory) == {}


class TestFormatting:
    def test_rating_written_with_two_decimals(self, system_dir: Path) -> None:
        source = ESDESource()
        entries = source.load(system_dir)
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entries["超级马力欧兄弟.nes"])
        game.rating = 0.4
        raw = source.to_raw(game, entries["超级马力欧兄弟.nes"])
        assert raw.fields["rating"] == "0.40"

    def test_partial_date_written_as_first_of_month(self, system_dir: Path) -> None:
        from retrostation.core.model import PartialDate

        source = ESDESource()
        entries = source.load(system_dir)
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entries["超级马力欧兄弟.nes"])
        game.release = PartialDate(1985)
        raw = source.to_raw(game, entries["超级马力欧兄弟.nes"])
        assert raw.fields["releasedate"] == "19850101T000000"


class TestPlayerCount:
    """ES-DE writes ``<players>``; a fair few scrapers write ``<player>``."""

    def _system(self, tmp_path: Path, extra: str) -> tuple[Path, Path]:
        directory = tmp_path / "FC"
        directory.mkdir()
        (directory / "gamelist.xml").write_text(
            '<?xml version="1.0"?>\n<gameList>\n  <game>\n'
            '    <path>./魂斗罗.nes</path>\n    <name>魂斗罗</name>\n'
            f"{extra}\n  </game>\n</gameList>\n",
            encoding="utf-8",
        )
        rom = directory / "魂斗罗.nes"
        rom.write_bytes(b"nes")
        return directory, rom

    def test_plural_is_read(self, tmp_path: Path) -> None:
        directory, rom = self._system(tmp_path, "    <players>1-2</players>")
        source = ESDESource()
        entry = source.load(directory)["魂斗罗.nes"]
        assert source.to_game("FC", rom, entry).players == "1-2"

    def test_singular_is_read(self, tmp_path: Path) -> None:
        """The bug: a scraper writing ``<player>`` left the field empty."""
        directory, rom = self._system(tmp_path, "    <player>1-2</player>")
        source = ESDESource()
        entry = source.load(directory)["魂斗罗.nes"]
        assert source.to_game("FC", rom, entry).players == "1-2"

    def test_the_original_spelling_is_written_back(self, tmp_path: Path) -> None:
        directory, rom = self._system(tmp_path, "    <player>1-2</player>")
        source = ESDESource()

        entries = source.load(directory)
        game = source.to_game("FC", rom, entries["魂斗罗.nes"])
        game.players = "1-4"
        entries["魂斗罗.nes"] = source.to_raw(game, entries["魂斗罗.nes"])
        source.save(directory, entries)

        text = (directory / "gamelist.xml").read_text(encoding="utf-8")
        assert "<player>1-4</player>" in text
        # No second spelling is invented next to the one that was there.
        assert "<players>" not in text

    def test_clearing_drops_every_spelling(self, tmp_path: Path) -> None:
        directory, rom = self._system(tmp_path, "    <player>1-2</player>")
        source = ESDESource()

        entries = source.load(directory)
        game = source.to_game("FC", rom, entries["魂斗罗.nes"])
        game.players = None
        entries["魂斗罗.nes"] = source.to_raw(game, entries["魂斗罗.nes"])
        source.save(directory, entries)

        assert "<player>" not in (directory / "gamelist.xml").read_text(encoding="utf-8")
