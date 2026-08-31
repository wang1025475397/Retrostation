"""Thumbnail cache format selection.

Motivated by a device where the cache silently did nothing: its Pillow is
linked against libjpeg 9 headers but resolves libjpeg 6b at runtime, so every
JPEG write raised and every JPEG read fell through -- invisible from the UI,
because a cache miss just means re-decoding the original.

The fix is to probe for a format the device can round-trip.  These tests pin
that behaviour down.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.data.library import Library
from retrostation.data.media import ThumbnailCache, cache_suffix
from tests.conftest import FakePlatform


class TestCacheSuffix:
    def test_reports_a_suffix_we_can_save_and_read(self, tmp_path: Path) -> None:
        suffix = cache_suffix()
        target = tmp_path / f"probe{suffix}"
        Image.new("RGB", (16, 16), (12, 34, 56)).save(target)

        with Image.open(target) as handle:
            handle.load()
            assert handle.size == (16, 16)
            assert handle.mode == "RGB"

    def test_result_is_cached_across_calls(self) -> None:
        assert cache_suffix() == cache_suffix()

    def test_never_returns_jpeg_when_jpeg_is_broken(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulate the RG DS: JPEG fails, the next format must win."""
        real_save = Image.Image.save

        def broken_save(self, fp, **kwargs):  # noqa: ANN001 - PIL signature
            if str(kwargs.get("format", "")).upper() == "JPEG":
                raise OSError("Wrong JPEG library version: library is 62, caller expects 90")
            return real_save(self, fp, **kwargs)

        monkeypatch.setattr(Image.Image, "save", broken_save)
        cache_suffix.cache_clear()
        try:
            assert cache_suffix() != ".jpg"
        finally:
            cache_suffix.cache_clear()


class TestCacheRoundTrip:
    @pytest.fixture
    def library(self, rom_root: Path) -> Library:
        platform = FakePlatform(rom_root)
        library = Library(platform, Config())
        library.scan()
        return library

    @staticmethod
    def _with_cover(library: Library) -> tuple[object, Path]:
        """The first FC game that actually has a cover."""
        # ``resolve_all`` is what attaches assets; ``load_games`` alone leaves
        # every cover un-resolved.
        game = next(g for g in library.resolve_all("FC") if g.asset("cover"))
        return game, Path(str(game.asset("cover")))

    def test_written_thumbnail_is_reused(self, library: Library, rom_root: Path) -> None:
        """A second cache must be read from disk, not re-decoded."""
        _game, cover = self._with_cover(library)

        cache = library._thumbnails  # noqa: SLF001 - testing the cache directly
        first = cache.get("cover", cover, 12, 10)
        assert first is not None
        cache.flush()

        on_disk = list((rom_root / "FC" / "Imgs" / ".cache").glob("*12x10*"))
        assert on_disk, "a thumbnail should have been written"
        assert on_disk[0].suffix == cache_suffix()

        # Drop the decoded bitmap from memory; the next get() must hit the card.
        cache.clear_memory()
        second = cache.get("cover", cover, 12, 10)
        assert second is not None
        assert second.size == first.size

    def test_a_corrupt_cache_file_is_discarded(
        self, library: Library, rom_root: Path,
    ) -> None:
        """A cache entry we cannot read must not shadow the real artwork."""
        _game, cover = self._with_cover(library)

        cache = library._thumbnails  # noqa: SLF001
        assert cache.get("cover", cover, 12, 10) is not None
        cache.flush()

        stale = sorted((rom_root / "FC" / "Imgs" / ".cache").glob("*12x10*"))[0]
        stale.write_bytes(b"not an image at all")

        cache.clear_memory()
        # Falls back to decoding the original rather than returning nothing.
        assert cache.get("cover", cover, 12, 10) is not None


class TestDisabledCache:
    def test_disabled_cache_writes_nothing(self, rom_root: Path, tmp_path: Path) -> None:
        platform = FakePlatform(rom_root)
        cache = ThumbnailCache(platform, tmp_path, enabled=False)
        library = Library(platform, Config())
        library.scan()
        game = library.load_games("FC").games[0]

        assert cache._disk_path(Path(str(game.asset("cover"))), 10, 10) is None  # noqa: SLF001
