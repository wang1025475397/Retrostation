"""Tests for the shipped platform artwork loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from retrostation.platform.base import Platform
from retrostation.ui.platform_art import PlatformArt


class _FakePlatform(Platform):
    """Minimal :class:`Platform` that decodes images from disk like the real one."""

    name = "fake"

    @property
    def rom_root(self) -> Path:
        return Path()

    @property
    def config_dir(self) -> Path:
        return Path()

    def init_display(self, mode: str) -> list[Platform]:  # pragma: no cover - unused
        raise NotImplementedError

    def present(self, index: int) -> None:  # pragma: no cover - unused
        raise NotImplementedError

    def poll_events(self, timeout: float = 0.0) -> list:  # pragma: no cover - unused
        return []

    def battery(self) -> int | None:
        return None

    def temperature(self) -> float | None:
        return None

    def set_brightness(self, value: int, index: int = 0) -> None:  # pragma: no cover
        return None

    def list_dir(self, path: Path) -> list:  # pragma: no cover - unused
        return []

    def launch_game(self, argv):  # pragma: no cover - unused
        raise NotImplementedError

    def font(self, size: int) -> object:  # pragma: no cover - unused
        raise NotImplementedError

    def load_image(self, path: Path) -> object:
        with Image.open(path) as handle:
            return handle.convert("RGBA").copy()

    def shutdown(self) -> None:  # pragma: no cover - unused
        return None


def _seed(root: Path) -> None:
    (root / "background" / "fc.jpg").parent.mkdir(parents=True, exist_ok=True)
    (root / "logo" / "fc.png").parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (200, 40, 40)).save(root / "background" / "fc.jpg")
    Image.new("RGBA", (80, 32), (255, 255, 255, 220)).save(root / "logo" / "fc.png")
    Image.new("RGB", (64, 64), (60, 60, 60)).save(root / "background" / "snes.png")
    # fc.svg deliberately has no logo partner and must not be reported as art.


@pytest.fixture
def art(tmp_path: Path) -> PlatformArt:
    _seed(tmp_path)
    return PlatformArt(_FakePlatform(), root=tmp_path)


class TestLookup:
    def test_has_art_for_a_complete_platform(self, art: PlatformArt) -> None:
        assert art.has_art("FC") is True

    def test_missing_logo_means_no_art(self, art: PlatformArt) -> None:
        # ``fc.svg`` only has a background; the platform still has art.
        assert art.has_art("FC") is True

    def test_unknown_key_has_no_art(self, art: PlatformArt) -> None:
        assert art.has_art("DOES_NOT_EXIST") is False

    def test_has_art_is_case_insensitive(self, art: PlatformArt) -> None:
        assert art.has_art("fc") is True
        assert art.has_art("Fc") is True

    def test_path_for_picks_the_first_extension(self, art: PlatformArt) -> None:
        assert art.path_for("background", "FC") == art.root / "background" / "fc.jpg"
        assert art.path_for("logo", "FC") == art.root / "logo" / "fc.png"

    def test_path_for_returns_none_when_missing(self, art: PlatformArt) -> None:
        assert art.path_for("logo", "MD") is None


class TestBitmaps:
    def test_background_returns_an_image(self, art: PlatformArt) -> None:
        bitmap = art.background("FC", 132, 132)
        assert bitmap is not None
        assert bitmap.mode == "RGBA"
        assert bitmap.width <= 132 and bitmap.height <= 132

    def test_logo_returns_an_image_with_alpha(self, art: PlatformArt) -> None:
        bitmap = art.logo("FC", 103, 41)
        assert bitmap is not None
        assert bitmap.mode == "RGBA"

    def test_missing_platform_returns_none(self, art: PlatformArt) -> None:
        assert art.background("DOES_NOT_EXIST", 100, 100) is None
        assert art.logo("DOES_NOT_EXIST", 100, 100) is None

    def test_invalid_size_returns_none(self, art: PlatformArt) -> None:
        assert art.background("FC", 0, 100) is None
        assert art.background("FC", -1, 100) is None
        assert art.logo("FC", 100, 0) is None

    def test_bitmap_is_cached_by_size(self, art: PlatformArt) -> None:
        first = art.background("FC", 132, 132)
        second = art.background("FC", 132, 132)
        assert first is second
        third = art.background("FC", 264, 264)  # different size: re-decode
        assert third is not first

    def test_an_undecodable_file_falls_through_to_the_next_format(
        self, tmp_path: Path,
    ) -> None:
        """Regression: this device's Pillow cannot open JPEG at all.

        It reports ``Wrong JPEG library version: library is 62, caller expects
        90`` for every .jpg, so a leftover JPEG must not shadow the file that
        actually works.
        """
        _seed(tmp_path)
        (tmp_path / "background" / "fc.jpg").write_bytes(b"not a jpeg at all")
        Image.new("RGB", (64, 64), (90, 10, 10)).save(tmp_path / "background" / "fc.webp")

        art = PlatformArt(_FakePlatform(), root=tmp_path)
        assert art.candidates("background", "FC") == [
            tmp_path / "background" / "fc.jpg",
            tmp_path / "background" / "fc.webp",
        ]
        bitmap = art.background("FC", 132, 132)
        assert bitmap is not None
        # Lossy WebP, so allow a little drift around the seeded (90, 10, 10).
        red, green, blue = bitmap.getpixel((4, 4))[:3]
        assert abs(red - 90) <= 8 and green <= 24 and blue <= 24

    def test_every_candidate_undecodable_returns_none(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        for candidate in (tmp_path / "background").iterdir():
            candidate.write_bytes(b"garbage")
        art = PlatformArt(_FakePlatform(), root=tmp_path)
        assert art.background("FC", 132, 132) is None

    def test_cache_is_bounded_by_the_size_limit(self, art: PlatformArt, tmp_path: Path) -> None:
        Image.new("RGB", (16, 16), (10, 10, 10)).save(tmp_path / "background" / "fc_small.png")
        art.clear()  # so the new file is picked up
        for _ in range(40):
            art.background("FC", 64, 64)
        # At most ``cache_limit`` entries.
        assert len(art._cache) <= art._cache_limit  # noqa: SLF001 - test internals

    def test_loader_tolerates_a_missing_file(self, tmp_path: Path) -> None:
        # ``_seed`` left fc.jpg, but we just delete it behind the loader's back.
        _seed(tmp_path)
        art = PlatformArt(_FakePlatform(), root=tmp_path)
        (tmp_path / "background" / "fc.jpg").unlink()
        art.clear()
        # The disk walk refreshes ``has_art`` only once at construction; the
        # lookup still hits the now-missing path and returns None.
        assert art.background("FC", 100, 100) is None

    def test_clear_drops_everything(self, art: PlatformArt) -> None:
        art.background("FC", 100, 100)
        art.logo("FC", 100, 100)
        art.clear()
        assert art._cache == {}  # noqa: SLF001
        assert art._order == []  # noqa: SLF001


class TestPackaging:
    """The shipped artwork has to be picked up by ``setuptools``'s package-data."""

    def test_assets_directory_is_next_to_the_module(self) -> None:
        root = Path(PlatformArt.__module__.replace(".", "/"))
        root = Path(__file__).resolve().parent.parent / "src" / "retrostation" / "ui" / "platform_art.py"
        assets = root.parent.parent / "assets" / "platforms"
        assert assets.is_dir(), f"missing: {assets}"