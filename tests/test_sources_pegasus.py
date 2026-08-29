"""Pegasus ``metadata.pegasus.txt`` source tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.data.sources.base import UnsupportedWrite
from retrostation.data.sources.pegasus import PegasusSource

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
        assert set(entries) == {"超级马力欧兄弟.nes", "魂斗罗.nes"}

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
