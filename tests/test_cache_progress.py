"""The "N cached" figure in the game-list header.

Counted in *games*, not files: one game is four or five thumbnails, and the
player reading "531 · 缓存 85" wants to know how much of the list will still
stutter, not how many files are on the card.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image

from retrostation.core.config import Config
from retrostation.core.model import Game
from retrostation.data.library import Library
from retrostation.data.media import ThumbnailCache
from tests.conftest import FakePlatform

#: The carousel's four sizes -- what :func:`art_slots` asks for.
SLOTS = (("cover", 137, 190, False), ("cover", 107, 148, False),
         ("cover", 88, 122, False), ("cover", 71, 99, False))

ROOT = Path(__file__).resolve().parent.parent


def _library(tmp_path: Path) -> tuple[Library, FakePlatform]:
    platform = FakePlatform(tmp_path)
    return Library(platform, Config()), platform


def _warm(cache: ThumbnailCache, source: Path, sizes) -> None:
    """Queue a warm-up and wait for the thread to land it."""
    cache.warm(source, sizes)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(cache.cached(source, w, h, cover=c) for w, h, c in sizes):
            return
        time.sleep(0.01)


def _game(tmp_path: Path, name: str, *, cover: bool = True) -> Game:
    game = Game.from_rom("FC", tmp_path / f"{name}.nes")
    if cover:
        source = tmp_path / f"{name}.png"
        Image.new("RGB", (350, 478), (30, 60, 90)).save(source)
        game.set_asset("cover", source)
    return game


class TestCountCachedGames:
    def test_counts_games_not_files(self, tmp_path: Path) -> None:
        library, platform = _library(tmp_path)
        games = [_game(tmp_path, f"game{index}") for index in range(4)]
        cache = library._thumbnails  # noqa: SLF001
        cache.warm_active(True)

        assert library.count_cached_games(games, SLOTS) == 0

        for game in games[:2]:
            _warm(cache, game.asset("cover"), [(w, h, c) for _k, w, h, c in SLOTS])

        # Two games warmed, four sizes each: eight files, two games.
        assert library.count_cached_games(games, SLOTS) == 2

    def test_a_game_needs_every_slot(self, tmp_path: Path) -> None:
        """A half-warmed game still stutters on the size it is missing."""
        library, _platform = _library(tmp_path)
        game = _game(tmp_path, "only")
        cache = library._thumbnails  # noqa: SLF001
        cache.warm_active(True)

        _warm(cache, game.asset("cover"), [(w, h, c) for _k, w, h, c in SLOTS[:-1]])
        assert library.count_cached_games([game], SLOTS) == 0

        _warm(cache, game.asset("cover"), [SLOTS[-1][1:]])
        assert library.count_cached_games([game], SLOTS) == 1

    def test_a_game_without_artwork_is_not_held_against_it(
        self, tmp_path: Path,
    ) -> None:
        library, _platform = _library(tmp_path)
        games = [_game(tmp_path, "plain", cover=False), _game(tmp_path, "with")]
        cache = library._thumbnails  # noqa: SLF001
        cache.warm_active(True)

        _warm(cache, games[1].asset("cover"), [(w, h, c) for _k, w, h, c in SLOTS])
        # One has nothing to cache, one is complete: both are "done".
        assert library.count_cached_games(games, SLOTS) == 2

    def test_nothing_counts_when_the_cache_is_off(self, tmp_path: Path) -> None:
        platform = FakePlatform(tmp_path)
        library = Library(platform, Config())
        library._thumbnails.enabled = False  # noqa: SLF001
        game = _game(tmp_path, "game")
        ThumbnailCache(platform, tmp_path / "c").get("cover", game.asset("cover"), 88, 122)

        assert library.count_cached_games([game], SLOTS) == 0

    def test_asking_costs_no_decode(self, tmp_path: Path) -> None:
        """It walks a whole system, so it must never open an image."""
        library, platform = _library(tmp_path)
        game = _game(tmp_path, "game")
        cache = library._thumbnails  # noqa: SLF001
        cache.warm_active(True)
        _warm(cache, game.asset("cover"), [(w, h, c) for _k, w, h, c in SLOTS])

        opened: list[Path] = []
        real = platform.load_image

        def spy(path):  # noqa: ANN001 - platform signature
            opened.append(Path(path))
            return real(path)

        platform.load_image = spy  # type: ignore[method-assign]
        assert library.count_cached_games([game], SLOTS) == 1
        assert opened == []


class TestTheHeaderWording:
    @staticmethod
    def _table(name: str) -> dict:
        return json.loads((ROOT / "src" / "retrostation" / "assets" / "lang" / name)
                          .read_text(encoding="utf-8"))

    def test_both_languages_carry_the_key(self) -> None:
        zh, en = self._table("zh_CN.json"), self._table("en_US.json")
        assert zh["games.cached_of"] and en["games.cached_of"]
        for text in (zh["games.cached_of"], en["games.cached_of"]):
            assert "{total}" in text and "{cached}" in text
