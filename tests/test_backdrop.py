"""Backdrop artwork: fanart wins over screenshot, and both fill the panel.

A game's own art is what sits behind the list -- fanart first, a screenshot as
the stand-in when there is no fanart.  Both must *fill* the screen (cover), not
letterbox, or the background colour shows along two edges and reads as broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.core.model import Game
from retrostation.core.theme import COLORS
from retrostation.data.library import Library
from retrostation.data.media import cover_bitmap
from retrostation.ui.art import ArtProvider
from retrostation.ui.screens.games import panel_fill
from tests.conftest import FakePlatform


def _image(root: Path, name: str, size=(120, 200), color=(30, 60, 90)) -> Path:
    path = root / name
    Image.new("RGB", size, color).save(path)
    return path


class _Painter:
    """Minimal stand-in carrying just the ``backdrop`` flag ``panel_fill`` reads."""

    def __init__(self, backdrop: bool) -> None:
        self.backdrop = backdrop


class TestCoverBitmap:
    def test_fills_a_wide_target(self, tmp_path: Path) -> None:
        bitmap = _image(tmp_path, "w.png", size=(200, 100))
        out = cover_bitmap(Image.open(bitmap).convert("RGBA"), 80, 80)
        assert out.size == (80, 80)
        assert out.mode == "RGBA"

    def test_crops_overflow(self, tmp_path: Path) -> None:
        """A tall image into a square must be centre-cropped, not letterboxed."""
        bitmap = _image(tmp_path, "tall.png", size=(100, 300))
        out = cover_bitmap(Image.open(bitmap).convert("RGBA"), 100, 100)
        assert out.size == (100, 100)

    def test_upscales_a_small_source(self, tmp_path: Path) -> None:
        """A backdrop is the one place a soft image is invisible."""
        bitmap = _image(tmp_path, "small.png", size=(20, 20))
        out = cover_bitmap(Image.open(bitmap).convert("RGBA"), 100, 100)
        assert out.size == (100, 100)


class TestBackdropPriority:
    def _library(self, root: Path) -> Library:
        return Library(FakePlatform(root), Config())

    def _game(self, root: Path, fanart, screenshot) -> Game:
        game = Game.from_rom("FC", root / "FC" / "game.nes")
        if fanart is not None:
            game.set_asset("fanart", fanart)
        if screenshot is not None:
            game.set_asset("screenshot", screenshot)
        return game

    def test_fanart_beats_screenshot(self, tmp_path: Path) -> None:
        fanart = _image(tmp_path, "fanart.png", color=(200, 10, 10))
        shot = _image(tmp_path, "shot.png", color=(10, 200, 10))
        art = ArtProvider(self._library(tmp_path), FakePlatform(tmp_path))
        result = art.backdrop(self._game(tmp_path, fanart, shot), 80, 80)
        assert result is not None
        assert result.size == (80, 80)

    def test_falls_back_to_screenshot(self, tmp_path: Path) -> None:
        shot = _image(tmp_path, "shot.png", color=(10, 200, 10))
        art = ArtProvider(self._library(tmp_path), FakePlatform(tmp_path))
        assert art.backdrop(self._game(tmp_path, None, shot), 80, 80) is not None

    def test_none_when_neither(self, tmp_path: Path) -> None:
        art = ArtProvider(self._library(tmp_path), FakePlatform(tmp_path))
        assert art.backdrop(self._game(tmp_path, None, None), 80, 80) is None

    def test_result_is_cached(self, tmp_path: Path) -> None:
        shot = _image(tmp_path, "shot.png")
        art = ArtProvider(self._library(tmp_path), FakePlatform(tmp_path))
        game = self._game(tmp_path, None, shot)
        assert art.backdrop(game, 80, 80) is art.backdrop(game, 80, 80)


class TestPanelFill:
    def test_opaque_without_a_backdrop(self) -> None:
        assert panel_fill(_Painter(False)) == COLORS.panel

    def test_translucent_while_a_backdrop_shows(self) -> None:
        fill = panel_fill(_Painter(True))
        assert len(fill) == 4
        assert fill[3] < 255
