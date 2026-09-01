"""Fallback decoding for files Pillow cannot open.

The RG DS links Pillow against libjpeg 9 but only ships libjpeg 6b, so every
JPEG cover raises on open -- and NDS libraries are almost entirely JPEG.  The
fix is to hand those files to an external decoder (ffmpeg) and cache the
result, so the cost is paid once.

These tests simulate that device without needing one: ``load_image`` rejects
JPEG, ``transcode_image`` rescues it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.data.library import Library
from retrostation.data.media import ThumbnailCache, cache_suffix
from retrostation.platform.base import Platform
from retrostation.platform.linux import ffmpeg as ffmpeg_codec
from tests.conftest import FakePlatform, png_bytes


class _JpegBlindPlatform(FakePlatform):
    """A platform whose Pillow cannot read JPEG, like the RG DS."""

    def __init__(self, root: Path, *, transcode: bool = True) -> None:
        super().__init__(root)
        self.transcode = transcode
        self.transcoded: list[Path] = []

    def load_image(self, path: Path) -> object:
        if path.suffix.lower() in ffmpeg_codec.RECOVERABLE_SUFFIXES:
            raise OSError("Wrong JPEG library version: library is 62, caller expects 90")
        return super().load_image(path)

    def transcode_image(self, source: Path, target: Path, width: int, height: int) -> bool:
        if not self.transcode:
            return False
        # Same guard as the real Linux platform: only formats Pillow is known
        # to be broken for are worth shelling out for.
        if not ffmpeg_codec.is_recoverable(source):
            return False
        self.transcoded.append(source)
        # Stand in for ffmpeg: decode with PIL (allowed here) and scale.
        with Image.open(source) as handle:
            image = handle.convert("RGBA")
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        image.save(target, format="PNG")
        return True


@pytest.fixture
def jpeg_cover(tmp_path: Path) -> Path:
    """A real JPEG cover, written by this (working) Pillow."""
    cover = tmp_path / "FC" / "Imgs" / "塞尔达.jpg"
    cover.parent.mkdir(parents=True)
    Image.new("RGB", (400, 300), (30, 90, 160)).save(cover, format="JPEG")
    return cover


@pytest.fixture
def rom_root(tmp_path: Path, jpeg_cover: Path) -> Path:
    """A library whose only cover is a JPEG.

    The ROM must share the cover's stem and use an extension the FC system
    accepts, or media resolution never pairs the two up.
    """
    (tmp_path / "FC" / "塞尔达.nes").write_bytes(b"nes")
    return tmp_path


class TestPlatformContract:
    def test_base_platform_reports_no_fallback(self, tmp_path: Path) -> None:
        class Bare(Platform):
            name = "bare"

            @property
            def rom_root(self) -> Path:
                return tmp_path

            @property
            def config_dir(self) -> Path:
                return tmp_path

            def init_display(self, mode: str):  # pragma: no cover - unused
                raise NotImplementedError

            def present(self, index: int) -> None:  # pragma: no cover - unused
                return None

            def poll_events(self, timeout: float = 0.0) -> list:
                return []

            def battery(self) -> int | None:
                return None

            def temperature(self) -> float | None:
                return None

            def set_brightness(self, value: int, index: int = 0) -> None:
                return None

            def list_dir(self, path: Path) -> list:
                return []

            def launch_game(self, argv):  # pragma: no cover - unused
                raise NotImplementedError

            def font(self, size: int) -> object:  # pragma: no cover - unused
                raise NotImplementedError

            def load_image(self, path: Path) -> object:  # pragma: no cover - unused
                raise OSError

            def shutdown(self) -> None:  # pragma: no cover - unused
                return None

        bare = Bare()
        assert bare.transcode_image(tmp_path / "a.jpg", tmp_path / "a.png", 10, 10) is False

    def test_only_jpeg_is_worth_retrying(self, tmp_path: Path) -> None:
        assert ffmpeg_codec.is_recoverable(tmp_path / "cover.jpg") is True
        assert ffmpeg_codec.is_recoverable(tmp_path / "cover.JPEG") is True
        assert ffmpeg_codec.is_recoverable(tmp_path / "cover.png") is False

    def test_transcode_refuses_a_non_png_target(self, jpeg_cover: Path, tmp_path: Path) -> None:
        # The webp encoder is compiled out on the device, so PNG is all we ask for.
        assert ffmpeg_codec.transcode(jpeg_cover, tmp_path / "x.webp", 10, 10) is False


class TestFallbackDecoding:
    @staticmethod
    def _cache(platform: _JpegBlindPlatform) -> ThumbnailCache:
        return ThumbnailCache(platform, platform.config_dir / "thumbnails")

    def test_jpeg_cover_is_rescued(self, rom_root: Path, jpeg_cover: Path) -> None:
        platform = _JpegBlindPlatform(rom_root)
        cache = self._cache(platform)

        bitmap = cache.get("cover", jpeg_cover, 132, 108)
        assert bitmap is not None, "a JPEG cover must not fall back to the placeholder"
        assert bitmap.width <= 132 and bitmap.height <= 108
        assert platform.transcoded == [jpeg_cover]

    def test_result_is_cached_for_next_time(self, rom_root: Path, jpeg_cover: Path) -> None:
        platform = _JpegBlindPlatform(rom_root)
        cache = self._cache(platform)

        cache.get("cover", jpeg_cover, 12, 10)
        cache.flush()
        written = list((jpeg_cover.parent / ".cache").glob("*12x10*"))
        assert [p.suffix for p in written] == [cache_suffix()]

        # Second time round it must come off the card, not from ffmpeg again.
        cache.clear_memory()
        assert cache.get("cover", jpeg_cover, 12, 10) is not None
        assert platform.transcoded == [jpeg_cover]

    def test_one_copy_serves_every_layout(self, rom_root: Path, jpeg_cover: Path) -> None:
        """Switching layout must not re-run the external decoder."""
        platform = _JpegBlindPlatform(rom_root)
        cache = self._cache(platform)

        for size in ((84, 30), (132, 108), (196, 272), (336, 264)):
            assert cache.get("cover", jpeg_cover, *size) is not None, size
        assert platform.transcoded == [jpeg_cover], "decoded more than once"

    def test_without_a_fallback_the_cover_is_skipped(
        self, rom_root: Path, jpeg_cover: Path,
    ) -> None:
        platform = _JpegBlindPlatform(rom_root, transcode=False)
        cache = self._cache(platform)
        assert cache.get("cover", jpeg_cover, 132, 108) is None

    def test_a_broken_png_is_not_sent_to_ffmpeg(self, tmp_path: Path) -> None:
        """A PNG that fails to open is genuinely broken; don't shell out for it."""
        platform = _JpegBlindPlatform(tmp_path)
        cache = self._cache(platform)
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"not a png")

        assert cache.get("cover", broken, 12, 10) is None
        assert platform.transcoded == []


class TestLibraryIntegration:
    def test_jpeg_only_library_still_shows_artwork(
        self, rom_root: Path, jpeg_cover: Path,
    ) -> None:
        """End to end: scan, then ask for the cover the way a screen does."""
        platform = _JpegBlindPlatform(rom_root)
        library = Library(platform, Config())
        library.scan()

        games = library.resolve_all("FC")
        assert len(games) == 1
        assert games[0].asset("cover") is not None

        bitmap = library.thumbnail("cover", games[0], 132, 108)
        assert bitmap is not None
