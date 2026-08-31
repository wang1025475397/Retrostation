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


class TestEllipsize:
    """Truncation runs once per drawn line, four of them in the detail strip."""

    @staticmethod
    def make_painter():
        from pathlib import Path

        from retrostation.core.i18n import Translator
        from retrostation.core.theme import metrics_for
        from retrostation.ui.painter import Painter
        from tests.conftest import FakePlatform

        canvas = PilCanvas(640, 480)
        return Painter(canvas, metrics_for(640, 480), FakePlatform(Path(".")), Translator("en"))

    def test_short_text_is_untouched(self) -> None:
        painter = self.make_painter()
        assert painter.ellipsize("Short", size=12, max_width=400) == "Short"

    def test_long_text_is_truncated(self) -> None:
        painter = self.make_painter()
        result = painter.ellipsize("A fairly long game description. " * 20, size=12, max_width=200)
        assert result.endswith("…")
        assert painter.text_width(result, size=12) <= 200

    def test_truncation_is_not_quadratic(self) -> None:
        """Removing one character at a time re-measures the whole string.

        A 426-character blurb cost seconds per call on the device -- and the
        strip asks four times a frame, which is what made one screen stutter.
        """
        import time

        painter = self.make_painter()
        text = "A fairly long game description. " * 40      # ~1280 characters
        start = time.perf_counter()
        painter.ellipsize(text, size=11, max_width=400)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.05, "ellipsize took %.1f ms" % (1000 * elapsed)


class TestTextAnchor:
    """The one place the canvas can quietly lose glyphs.

    ``_text_patch`` renders into a scratch bitmap and crops to the ink box, so
    whatever falls outside the scratch is gone for good -- not clipped on
    screen, never drawn at all.
    """

    def test_right_aligned_run_keeps_every_glyph(self) -> None:
        """PIL lays a run out *around* its anchor.

        With the anchor at the left margin, a right-aligned run started at a
        negative x and everything left of the anchor was cropped away: a
        two-character value in the settings dialog rendered as its last glyph.
        """
        canvas = PilCanvas(200, 40)
        font = make_font()
        left = canvas._text_patch("ABSOLUTE", font, (255, 255, 255, 255), "lm")
        right = canvas._text_patch("ABSOLUTE", font, (255, 255, 255, 255), "rm")
        assert right[0].width == left[0].width

    def test_centred_run_keeps_every_glyph(self) -> None:
        canvas = PilCanvas(200, 40)
        font = make_font()
        left = canvas._text_patch("ABSOLUTE", font, (255, 255, 255, 255), "lm")
        middle = canvas._text_patch("ABSOLUTE", font, (255, 255, 255, 255), "mm")
        assert middle[0].width == left[0].width

    def test_right_aligned_text_is_as_wide_as_it_measures(self) -> None:
        canvas = PilCanvas(200, 40)
        font = make_font()
        canvas.text((190, 20), "ABSOLUTE", font=font, fill=(255, 255, 255, 255), anchor="rm")
        ink = canvas.pil_image.getbbox()
        assert ink is not None
        assert ink[2] - ink[0] >= canvas.text_width("ABSOLUTE", font=font) - 2


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
