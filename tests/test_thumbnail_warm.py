"""Idle warm-up: one decode for many sizes.

A cover that is not on the card costs a full decode of the original, and a
carousel asks for four sizes of the same picture (the card plus each scaled
neighbour).  Filling them one request at a time pays for the same decode four
times over, which is what :meth:`ThumbnailCache.warm` exists to avoid.
"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from retrostation.data.media import ThumbnailCache, _entry_digest, cache_suffix
from retrostation.ui.app import _prefetch_order
from tests.conftest import FakePlatform

#: The four sizes a carousel asks for, in descending order.
SIZES = ((137, 190, False), (107, 148, False), (88, 122, False), (71, 99, False))


class CountingPlatform(FakePlatform):
    """Counts decodes: the whole point of the warm-up is to do fewer."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.decodes: list[Path] = []

    def load_image(self, path: Path) -> object:  # type: ignore[override]
        self.decodes.append(Path(path))
        return super().load_image(path)


def _cover(tmp_path: Path) -> Path:
    source = tmp_path / "boxFront.png"
    Image.new("RGB", (350, 478), (30, 60, 90)).save(source)
    return source


def _entries(directory: Path) -> set[str]:
    return {path.name for path in directory.glob(f"*{cache_suffix()}")}


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """The warm-up runs on its own thread; give it time to land."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestPrefetchOrder:
    def test_reaches_the_whole_list(self) -> None:
        """Not a window around the cursor: parking on one game should warm
        the system, not just the rows next to it."""
        assert set(_prefetch_order(250, 531)) == set(range(531))

    def test_nearest_first(self) -> None:
        assert list(_prefetch_order(5, 20))[:5] == [5, 6, 4, 7, 3]

    def test_edges(self) -> None:
        assert list(_prefetch_order(0, 3)) == [0, 1, 2]
        assert list(_prefetch_order(2, 3)) == [2, 1, 0]
        assert list(_prefetch_order(0, 1)) == [0]
        assert list(_prefetch_order(9, 0)) == []


class TestWarm:
    def test_decodes_once_for_every_size(self, tmp_path: Path) -> None:
        platform = CountingPlatform(tmp_path)
        source = _cover(tmp_path)
        cache = ThumbnailCache(platform, tmp_path / "cache")
        cache.warm_active(True)

        assert cache.warm(source, SIZES)
        directory = source.parent / ".cache"
        assert _wait_for(lambda: len(_entries(directory)) == len(SIZES))

        # One decode, four sizes -- the reason this exists.
        assert [path.name for path in platform.decodes] == [source.name]

    def test_the_frame_then_hits_the_card(self, tmp_path: Path) -> None:
        """What the warm-up buys: the next request reads instead of decoding."""
        platform = CountingPlatform(tmp_path)
        source = _cover(tmp_path)
        cache = ThumbnailCache(platform, tmp_path / "cache")
        cache.warm_active(True)
        cache.warm(source, SIZES)
        assert _wait_for(lambda: len(_entries(source.parent / ".cache")) == len(SIZES))

        platform.decodes.clear()
        for width, height, _fill in SIZES:
            assert cache.get("cover", source, width, height) is not None
        # Every size came off the card: the original was never opened again.
        # (A hit still *reads* -- the cached webp -- it just does not decode
        # a 350x478 original to do it.)
        assert source.resolve() not in [path.resolve() for path in platform.decodes]
        assert all(".cache" in path.parts for path in platform.decodes)

    def test_skips_sizes_already_on_the_card(self, tmp_path: Path) -> None:
        platform = CountingPlatform(tmp_path)
        source = _cover(tmp_path)
        cache = ThumbnailCache(platform, tmp_path / "cache")
        cache.get("cover", source, 137, 190)  # one size is already warm
        cache.flush()
        platform.decodes.clear()
        cache.warm_active(True)

        cache.warm(source, SIZES)
        assert _wait_for(lambda: len(_entries(source.parent / ".cache")) == len(SIZES))
        assert len(platform.decodes) == 1

    def test_writes_the_names_the_frame_will_ask_for(self, tmp_path: Path) -> None:
        platform = FakePlatform(tmp_path)
        source = _cover(tmp_path)
        cache = ThumbnailCache(platform, tmp_path / "cache")
        cache.warm_active(True)

        cache.warm(source, SIZES)
        assert _wait_for(lambda: len(_entries(source.parent / ".cache")) == len(SIZES))

        mtime = int(source.stat().st_mtime)
        expected = {
            f"{_entry_digest(source, w, h, mtime)}_{w}x{h}{cache_suffix()}"
            for w, h, _fill in SIZES
        }
        assert _entries(source.parent / ".cache") == expected

    def test_held_while_the_player_is_moving(self, tmp_path: Path) -> None:
        platform = CountingPlatform(tmp_path)
        source = _cover(tmp_path)
        cache = ThumbnailCache(platform, tmp_path / "cache")

        cache.warm(source, SIZES)  # not released yet
        time.sleep(0.2)
        assert _entries(source.parent / ".cache") == set()
        assert platform.decodes == []

        cache.warm_active(True)
        assert _wait_for(lambda: len(_entries(source.parent / ".cache")) == len(SIZES))

    def test_nothing_happens_when_the_cache_is_off(self, tmp_path: Path) -> None:
        platform = CountingPlatform(tmp_path)
        source = _cover(tmp_path)
        cache = ThumbnailCache(platform, tmp_path / "cache", enabled=False)
        cache.warm_active(True)

        assert cache.warm(source, SIZES) is False
        time.sleep(0.2)
        assert _entries(source.parent / ".cache") == set()

    def test_a_source_that_cannot_be_decoded_costs_nothing(
        self, tmp_path: Path,
    ) -> None:
        platform = CountingPlatform(tmp_path)
        missing = tmp_path / "nope.png"
        cache = ThumbnailCache(platform, tmp_path / "cache")
        cache.warm_active(True)

        cache.warm(missing, SIZES)
        time.sleep(0.2)
        assert platform.decodes == []
