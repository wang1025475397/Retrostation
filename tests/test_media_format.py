"""Thumbnail cache format selection.

Motivated by a device where the cache silently did nothing: its Pillow is
linked against libjpeg 9 headers but resolves libjpeg 6b at runtime, so every
JPEG write raised and every JPEG read fell through -- invisible from the UI,
because a cache miss just means re-decoding the original.

The fix is to probe for a format the device can round-trip.  These tests pin
that behaviour down -- along with what goes *in* a cache file: thumbnails used
to be flattened onto a background colour, which turned every transparent logo
into a grey slab.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from retrostation.core.config import Config
from retrostation.data.library import Library
from retrostation.data.media import (
    ThumbnailCache,
    _copy_digest,
    _entry_digest,
    cache_suffix,
)
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


class TestCoverThumbnails:
    """The grid draws covers as filled crops; scaling twice (letterbox fit,
    then crop-and-grow) is what made them look soft."""

    def test_cover_fills_the_slot_in_one_pass(self, tmp_path: Path) -> None:
        platform = FakePlatform(tmp_path)
        source = tmp_path / "cover.png"
        Image.new("RGB", (300, 400), (10, 20, 30)).save(source)  # 3:4 portrait
        cache = ThumbnailCache(platform, tmp_path / "cache")

        fit = cache.get("cover", source, 160, 190)
        crop = cache.get("cover", source, 160, 190, cover=True)

        assert fit.size == (142, 190)      # letterboxed, never upscaled
        assert crop.size == (160, 190)     # filled in a single scaling pass

    def test_cover_and_fit_caches_do_not_evict_each_other(self, tmp_path: Path) -> None:
        platform = FakePlatform(tmp_path)
        source = tmp_path / "cover.png"
        Image.new("RGB", (300, 400), (10, 20, 30)).save(source)
        cache = ThumbnailCache(platform, tmp_path / "cache")

        first = cache.get("cover", source, 160, 190)
        crop = cache.get("cover", source, 160, 190, cover=True)
        again = cache.get("cover", source, 160, 190)

        assert first.size != crop.size     # distinct cache entries
        assert again.size == first.size    # the fit entry survived


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

    def test_an_entry_that_cannot_be_deleted_still_falls_back(
        self, library: Library, rom_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The cleanup after a failed read must never raise itself.

        Windows cannot delete a file that is open for writing, so a thumbnail
        the writer thread happens to be rewriting raises PermissionError -- and
        that used to escape from inside the ``except`` that was handling the
        failed read, taking the frame loop down with it.
        """
        _game, cover = self._with_cover(library)

        cache = library._thumbnails  # noqa: SLF001
        assert cache.get("cover", cover, 12, 10) is not None
        cache.flush()

        stale = sorted((rom_root / "FC" / "Imgs" / ".cache").glob("*12x10*"))[0]
        stale.write_bytes(b"not an image at all")
        monkeypatch.setattr(ThumbnailCache, "_remove", staticmethod(lambda path: False))

        cache.clear_memory()
        assert cache.get("cover", cover, 12, 10) is not None

    def test_a_failed_write_leaves_no_partial_entry(
        self, library: Library, rom_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Saving straight onto the entry left a truncated file behind.

        Killed mid-write, the card kept a file that no decoder can open, and
        every later frame paid for it -- which is how the corrupt entry above
        got there in the first place.
        """
        cache_suffix()  # warm the probe: the patch below must not poison it
        _game, cover = self._with_cover(library)
        real_save = Image.Image.save

        def killed_mid_write(self, fp, **kwargs):  # noqa: ANN001 - PIL signature
            real_save(self, fp, **kwargs)
            raise OSError("process died mid-write")

        monkeypatch.setattr(Image.Image, "save", killed_mid_write)
        cache = library._thumbnails  # noqa: SLF001
        assert cache.get("cover", cover, 12, 10) is not None
        cache.flush()

        directory = rom_root / "FC" / "Imgs" / ".cache"
        assert list(directory.glob("*12x10*")) == [], "no half-written entry"
        assert list(directory.glob("*.tmp")) == [], "no temporary left behind"


class TestTransparency:
    """Logos are transparent PNGs; a cache that flattens them is worse than
    no cache, because the damage only shows up on the second run -- when the
    entry is read back instead of decoded."""

    @staticmethod
    def _logo(tmp_path: Path) -> Path:
        logo = tmp_path / "logo.png"
        image = Image.new("RGBA", (320, 140), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((60, 20, 260, 120), fill=(255, 255, 255, 255))
        image.save(logo)
        return logo

    def test_alpha_survives_a_round_trip(self, rom_root: Path, tmp_path: Path) -> None:
        logo = self._logo(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)

        assert cache.get("logo", logo, 160, 70) is not None
        cache.flush()
        cache.clear_memory()

        # Read back off the card: this is the path that used to come back flat.
        cached = cache.get("logo", logo, 160, 70)
        assert cached is not None
        assert cached.mode == "RGBA"
        assert cached.getchannel("A").getextrema()[0] == 0, "alpha channel was flattened"

    def test_an_opaque_cover_is_stored_without_one(
        self, rom_root: Path, tmp_path: Path,
    ) -> None:
        """Nothing to keep, nothing to pay for: alpha costs bytes for nothing."""
        cover = tmp_path / "cover.png"
        Image.new("RGB", (300, 400), (10, 20, 30)).save(cover)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)

        cache.get("cover", cover, 150, 200)
        cache.flush()

        entry = next((tmp_path / ".cache").glob("*150x200*"))
        with Image.open(entry) as handle:
            assert handle.mode == "RGB"


class TestStaleEntries:
    """An entry is only worth keeping while a source still claims its name."""

    @staticmethod
    def _cover(tmp_path: Path) -> Path:
        media = tmp_path / "Imgs"
        media.mkdir(exist_ok=True)
        cover = media / "cover.png"
        Image.new("RGB", (400, 400), (10, 20, 30)).save(cover)
        return cover

    def test_replacing_a_cover_replaces_its_thumbnail(
        self, rom_root: Path, tmp_path: Path,
    ) -> None:
        """Same path, new picture: the old entry must not answer for it."""
        cover = self._cover(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)

        first = cache.get("cover", cover, 40, 40)
        cache.flush()

        Image.new("RGB", (400, 400), (220, 40, 40)).save(cover)
        cache._stats.clear()  # noqa: SLF001 - mtime is cached for a few seconds
        cache.clear_memory()

        second = cache.get("cover", cover, 40, 40)
        assert second is not None and first is not None
        assert tuple(second.convert("RGB").getpixel((20, 20))) != tuple(
            first.convert("RGB").getpixel((20, 20))
        ), "the previous cover's thumbnail was served for the new one"

    def test_a_name_no_source_hashes_to_is_dropped(
        self, rom_root: Path, tmp_path: Path,
    ) -> None:
        cover = self._cover(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)
        cache.get("cover", cover, 40, 40)
        cache.flush()

        directory = tmp_path / "Imgs" / ".cache"
        kept = next(directory.glob("*40x40*"))
        # An older build's entry: right shape, hash nobody can reproduce.
        stale = directory / f"{'0' * 16}_40x40{cache_suffix()}"
        stale.write_bytes(b"stale")

        assert cache.prune(directory) == 1
        assert not stale.exists()
        assert kept.exists()

    def test_the_current_entry_is_kept(self, rom_root: Path, tmp_path: Path) -> None:
        cover = self._cover(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)
        cache.get("cover", cover, 40, 40)
        cache.flush()

        directory = tmp_path / "Imgs" / ".cache"
        assert cache.prune(directory) == 0
        assert len(list(directory.glob("*40x40*"))) == 1

    def test_superseded_entries_go_on_the_next_pass(
        self, rom_root: Path, tmp_path: Path,
    ) -> None:
        """Re-scraping leaves two generations; only the live one stays."""
        cover = self._cover(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)
        cache.get("cover", cover, 40, 40)
        cache.flush()

        Image.new("RGB", (400, 400), (220, 40, 40)).save(cover)
        os.utime(cover, (cover.stat().st_atime, cover.stat().st_mtime + 120))
        cache._stats.clear()  # noqa: SLF001
        cache.clear_memory()
        cache.get("cover", cover, 40, 40)
        cache.flush()

        directory = tmp_path / "Imgs" / ".cache"
        assert cache.prune(directory) == 1
        assert len(list(directory.glob("*40x40*"))) == 1

    def test_a_cache_directory_whose_sources_are_gone_is_removed(
        self, rom_root: Path, tmp_path: Path,
    ) -> None:
        cover = self._cover(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)
        cache.get("cover", cover, 40, 40)
        cache.flush()

        directory = tmp_path / "Imgs" / ".cache"
        assert any(directory.iterdir())
        shutil.rmtree(tmp_path / "Imgs")

        cache.prune(directory)
        assert not directory.exists()

    def test_readable_copies_follow_the_same_rule(
        self, rom_root: Path, tmp_path: Path,
    ) -> None:
        cover = self._cover(tmp_path)
        cache = ThumbnailCache(FakePlatform(rom_root), tmp_path)
        copies = tmp_path / "Imgs" / ".cache" / "src"
        copies.mkdir(parents=True)
        live = copies / f"{_copy_digest(cover, int(cover.stat().st_mtime))}{cache_suffix()}"
        live.write_bytes(b"png")
        orphan = copies / f"{'0' * 16}{cache_suffix()}"
        orphan.write_bytes(b"png")

        cache.prune(tmp_path / "Imgs" / ".cache")
        assert not orphan.exists()
        assert live.exists()


class TestDisabledCache:
    def test_disabled_cache_writes_nothing(self, rom_root: Path, tmp_path: Path) -> None:
        platform = FakePlatform(rom_root)
        cache = ThumbnailCache(platform, tmp_path, enabled=False)
        library = Library(platform, Config())
        library.scan()
        game = library.load_games("FC").games[0]
        cover = Path(str(game.asset("cover")))
        # mtime is irrelevant here: a disabled cache answers before it looks.
        assert cache._disk_path(cover, 10, 10, 0) is None  # noqa: SLF001
