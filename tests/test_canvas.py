"""PIL canvas tests -- the drawing primitives the UI is built from."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageFont

from retrostation.platform.base import Rect
from retrostation.platform.linux.canvas import PilCanvas, wrap_text


def make_font(size: int = 14):
    return ImageFont.load_default(size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()


class TestPrimitives:
    def test_size_and_background(self) -> None:
        canvas = PilCanvas(64, 32, (10, 20, 30, 255))
        assert canvas.size == (64, 32)
        assert canvas.pil_image.getpixel((0, 0)) == (10, 20, 30, 255)

    def test_invalid_size_rejected(self) -> None:
        with pytest.raises(ValueError):
            PilCanvas(0, 10)
        with pytest.raises(ValueError):
            PilCanvas(10, -5)

    def test_clear(self) -> None:
        canvas = PilCanvas(8, 8)
        canvas.clear((1, 2, 3, 4))
        assert canvas.pil_image.getpixel((4, 4)) == (1, 2, 3, 4)

    def test_rect_uses_xywh(self) -> None:
        canvas = PilCanvas(20, 20)
        canvas.rect((5, 5, 10, 10), fill=(255, 0, 0, 255))
        assert canvas.pil_image.getpixel((6, 6)) == (255, 0, 0, 255)
        # PIL rectangles are inclusive of both end pixels.
        assert canvas.pil_image.getpixel((15, 15)) == (255, 0, 0, 255)
        assert canvas.pil_image.getpixel((16, 16)) == (0, 0, 0, 255)

    def test_rounded_rect_corners_keep_background(self) -> None:
        canvas = PilCanvas(20, 20, (9, 9, 9, 255))
        canvas.rounded_rect((0, 0, 20, 20), radius=6, fill=(0, 255, 0, 255))
        # The corner falls outside the arc, so the background shows through.
        assert canvas.pil_image.getpixel((0, 0)) == (9, 9, 9, 255)
        assert canvas.pil_image.getpixel((10, 10)) == (0, 255, 0, 255)

    def test_hgradient_ends(self) -> None:
        canvas = PilCanvas(16, 8)
        canvas.hgradient((0, 0, 16, 8), start=(0, 0, 0, 255), end=(255, 255, 255, 255))
        assert canvas.pil_image.getpixel((0, 0))[:3] == (0, 0, 0)
        assert canvas.pil_image.getpixel((15, 0))[:3] == (255, 255, 255)

    def test_text_is_drawn(self) -> None:
        canvas = PilCanvas(64, 32)
        canvas.text((2, 2), "ABC", font=make_font(), fill=(255, 255, 255, 255))
        pixels = list(canvas.pil_image.getdata())
        assert any(p[0] > 0 for p in pixels)

    def test_text_width_positive(self) -> None:
        canvas = PilCanvas(64, 32)
        assert canvas.text_width("Hello", font=make_font()) > 0

    def test_image_scaled_into_box(self) -> None:
        canvas = PilCanvas(32, 32)
        source = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        canvas.image(source, (8, 8, 16, 16))
        assert canvas.pil_image.getpixel((16, 16)) == (255, 0, 0, 255)
        assert canvas.pil_image.getpixel((4, 4)) == (0, 0, 0, 255)


class TestWrapText:
    def test_short_text_single_line(self) -> None:
        canvas = PilCanvas(64, 32)
        assert wrap_text(canvas._draw, "短文本", make_font(), 100, 4) == ["短文本"]

    def test_respects_max_lines(self) -> None:
        canvas = PilCanvas(64, 32)
        text = "这是很长的一段中文文本，没有空格可以断行，只能逐字累加。"
        lines = wrap_text(canvas._draw, text, make_font(), 40, 3)
        assert len(lines) <= 3

    def test_truncation_adds_ellipsis(self) -> None:
        canvas = PilCanvas(64, 32)
        text = "一二三四五六七八九十" * 5
        lines = wrap_text(canvas._draw, text, make_font(), 30, 2)
        assert lines[-1].endswith("…")

    def test_empty_text(self) -> None:
        canvas = PilCanvas(64, 32)
        assert wrap_text(canvas._draw, "", make_font(), 30, 2) == []

    def test_explicit_newlines_are_honoured(self) -> None:
        canvas = PilCanvas(64, 32)
        lines = wrap_text(canvas._draw, "第一行\n第二行", make_font(), 200, 4)
        assert lines[0] == "第一行"
        assert lines[1] == "第二行"


class TestRect:
    def test_box_conversion(self) -> None:
        assert Rect(3, 4, 10, 20).box == (3, 4, 13, 24)
        assert Rect(3, 4, 10, 20).right == 13
        assert Rect(3, 4, 10, 20).bottom == 24

    def test_inflate(self) -> None:
        assert Rect(5, 5, 10, 10).inflate(2, 1) == Rect(3, 4, 14, 12)
