"""Pegasus ``metadata.pegasus.txt`` source tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.data.sources.base import UnsupportedWrite
from retrostation.data.sources.pegasus import PegasusSource
from retrostation.data.sources import load_system, build_games

METADATA = """# a comment, ignored
collection: NES
extension: nes

game: 超级马力欧兄弟
file: 超级马力欧兄弟.nes
developer: 任天堂
publisher: 任天堂
genre: 平台跳跃, 动作
tags: 经典, 单人
release: 1985-09-13
players: 1-2
rating: 80%
summary: 横版卷轴平台跳跃的开山之作。
description: 由宫本茂设计的经典作品，
 定义了横版平台跳跃这一游戏类型。
 .
 至今仍是游戏设计教材级范例。
assets.boxFront: Imgs/超级马力欧兄弟.png
assets.marquee: logo/超级马力欧兄弟.png
assets.video: video/超级马力欧兄弟.mp4

game: 魂斗罗
file: 魂斗罗.nes
file: 魂斗罗 (Track 1).nes
rating: 65%
"""


@pytest.fixture
def system_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "FC"
    directory.mkdir()
    (directory / "metadata.pegasus.txt").write_text(METADATA, encoding="utf-8")
    (directory / "超级马力欧兄弟.nes").write_bytes(b"nes")
    (directory / "魂斗罗.nes").write_bytes(b"nes")
    (directory / "Imgs").mkdir()
    (directory / "Imgs" / "超级马力欧兄弟.png").write_bytes(b"png")
    return directory


class TestParsing:
    def test_detect(self, system_dir: Path) -> None:
        assert PegasusSource().detect(system_dir) is True

    def test_key_is_first_file(self, system_dir: Path) -> None:
        entries = PegasusSource().load(system_dir)
        # Every file listed by a ``file:`` line is indexed (same RawEntry), so
        # each variant ROM can find its metadata.
        assert set(entries) == {"超级马力欧兄弟.nes", "魂斗罗.nes", "魂斗罗 (Track 1).nes"}
        # And the entry remembers all of them.
        assert entries["魂斗罗 (Track 1).nes"].files == ["魂斗罗.nes", "魂斗罗 (Track 1).nes"]

    def test_rating_percent_is_normalised(self, system_dir: Path) -> None:
        source = PegasusSource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)
        assert game.rating == pytest.approx(0.80)

    def test_multiline_description_with_empty_line(self, system_dir: Path) -> None:
        source = PegasusSource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)

        assert "定义了横版平台跳跃这一游戏类型。" in game.description
        assert "\n\n" in game.description  # the lone "." became a blank line
        assert game.summary == "横版卷轴平台跳跃的开山之作。"

    def test_release_and_lists(self, system_dir: Path) -> None:
        source = PegasusSource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)

        assert str(game.release) == "1985-09-13"
        assert game.genres == ["平台跳跃", "动作"]
        assert game.tags == ["经典", "单人"]
        assert game.players == "1-2"

    def test_assets_are_mapped(self, system_dir: Path) -> None:
        source = PegasusSource()
        entry = source.load(system_dir)["超级马力欧兄弟.nes"]
        game = source.to_game("FC", system_dir / "超级马力欧兄弟.nes", entry)

        # Only Imgs/ exists on disk; logo/ and video/ do not, so those two
        # must come back as None and let the media layer resolve them instead.
        assert game.asset("cover") == (system_dir / "Imgs" / "超级马力欧兄弟.png").resolve()
        assert game.asset("logo") is None
        assert game.asset("video") is None

    def test_read_only(self, system_dir: Path) -> None:
        source = PegasusSource()
        assert source.writable is False
        with pytest.raises(UnsupportedWrite):
            source.save(system_dir, {})

    def test_collection_block_is_not_a_game(self, tmp_path: Path) -> None:
        directory = tmp_path / "FC"
        directory.mkdir()
        (directory / "metadata.pegasus.txt").write_text(
            "collection: NES\nextension: nes\n", encoding="utf-8"
        )
        assert PegasusSource().load(directory) == {}

    def test_unreadable_file_is_ignored(self, tmp_path: Path) -> None:
        directory = tmp_path / "FC"
        directory.mkdir()
        (directory / "metadata.pegasus.txt").write_bytes(b"\xff\xfe\x00bad")
        assert PegasusSource().load(directory) == {}


class TestMultiFileGrouping:
    """A ``game:`` block may list several ``file:`` lines (region/revision
    variants).  They must collapse into ONE library entry, not N filename games.

    This is the bug the user hit: ``FBNEO`` folders where Asterix ships as
    ``asterix.zip`` / ``asterixaad.zip`` / ``asterixj.zip`` etc. were all shown
    separately and only the first got its metadata.
    """

    METADATA = """game: Asterix
file: asterix.zip
file: asterixaad.zip
file: asterixj.zip
summary: 高卢英雄闯关。
assets.boxFront: Imgs/asterix.png
"""

    @pytest.fixture
    def system_dir(self, tmp_path: Path) -> Path:
        directory = tmp_path / "FBNEO"
        directory.mkdir()
        (directory / "metadata.pegasus.txt").write_text(self.METADATA, encoding="utf-8")
        (directory / "asterix.zip").write_bytes(b"x")
        (directory / "asterixaad.zip").write_bytes(b"x")
        (directory / "asterixj.zip").write_bytes(b"x")
        (directory / "Imgs").mkdir()
        (directory / "Imgs" / "asterix.png").write_bytes(b"png")
        return directory

    def test_one_entry_per_block(self, system_dir: Path) -> None:
        roms = [system_dir / n for n in ("asterix.zip", "asterixaad.zip", "asterixj.zip")]
        bundles = load_system(system_dir)
        games, variant_keys = build_games("FBNEO", roms, system_dir, bundles)

        # One game, keyed by the primary (first-listed) file.
        assert set(games) == {"FBNEO/asterix.zip"}
        game = games["FBNEO/asterix.zip"]
        assert game.display_name == "Asterix"
        assert game.blurb == "高卢英雄闯关。"
        # The other two files are its variants, reported so they are dropped.
        assert [p.name for p in game.variants] == ["asterixaad.zip", "asterixj.zip"]
        assert variant_keys == {"FBNEO/asterixaad.zip", "FBNEO/asterixj.zip"}

    def test_metadata_associated_with_every_file(self, system_dir: Path) -> None:
        """Each variant ROM resolves to the same title, not its bare file name."""
        entries = PegasusSource().load(system_dir)
        for name in ("asterix.zip", "asterixaad.zip", "asterixj.zip"):
            assert entries[name].fields.get("game") == "Asterix"
