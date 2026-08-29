"""Drawing context.

Widgets receive a :class:`Painter` instead of a bare canvas.  It bundles the
four things every widget needs -- surface, metrics, fonts, translations -- so
widget signatures stay flat and, crucially, **no widget ever touches the
platform or PIL directly**.

Fonts are cached per ``(surface size, requested size)``: building a TrueType
font is measurable frame-rate loss if done per draw call.
"""

from __future__ import annotations

from typing import Sequence

from ..core.i18n import Translator
from ..core.theme import COLORS, Metrics
from ..platform.base import Canvas, Platform


class Painter:
    """Bundle of canvas + metrics + platform, with a font cache."""

    def __init__(self, canvas: Canvas, metrics: Metrics, platform: Platform, translator: Translator) -> None:
        self.canvas = canvas
        self.metrics = metrics
        self.platform = platform
        self.translator = translator
        self._fonts: dict[int, object] = {}

    # -- metrics shortcuts ------------------------------------------------- #

    @property
    def width(self) -> int:
        return self.metrics.width

    @property
    def height(self) -> int:
        return self.metrics.height

    def u(self, base_px: float) -> int:
        return self.metrics.u(base_px)

    # -- fonts ------------------------------------------------------------- #

    def font(self, base_px: int) -> object:
        """Font for a reference-design size, cached."""
        size = self.metrics.font(base_px)
        font = self._fonts.get(size)
        if font is None:
            font = self.platform.font(size)
            self._fonts[size] = font
        return font

    # -- drawing shortcuts -------------------------------------------------- #

    def clear(self, color: Sequence[int] = COLORS.bg) -> None:
        self.canvas.clear(color)

    def rect(self, box: Sequence[float], *, fill=None, outline=None, width: int = 1) -> None:
        self.canvas.rect(box, fill=fill, outline=outline, width=width)

    def rounded_rect(self, box: Sequence[float], *, radius: int, fill=None, outline=None, width: int = 1) -> None:
        self.canvas.rounded_rect(box, radius=radius, fill=fill, outline=outline, width=width)

    def hgradient(self, box: Sequence[float], *, start, end, radius: int = 0) -> None:
        self.canvas.hgradient(box, start=start, end=end, radius=radius)

    def ellipse(self, box: Sequence[float], *, fill=None, outline=None, width: int = 1) -> None:
        self.canvas.ellipse(box, fill=fill, outline=outline, width=width)

    def text(self, xy: Sequence[float], content: str, *, size: int, fill, anchor: str = "la") -> None:
        self.canvas.text(xy, content, font=self.font(size), fill=fill, anchor=anchor)

    def text_width(self, content: str, *, size: int) -> int:
        return self.canvas.text_width(content, font=self.font(size))

    def text_height(self, content: str, *, size: int) -> int:
        return self.canvas.text_height(content, font=self.font(size))

    def image(self, bitmap: object, box: Sequence[float]) -> None:
        self.canvas.image(bitmap, box)

    def image_fit(self, bitmap: object, box: Sequence[float]) -> None:
        self.canvas.image_fit(bitmap, box)

    def wrap_text(self, text: str, *, size: int, max_width: int, max_lines: int) -> list[str]:
        return self.canvas.wrap_text(text, font=self.font(size), max_width=max_width, max_lines=max_lines)

    # -- text helpers ------------------------------------------------------- #

    def ellipsize(self, text: str, *, size: int, max_width: int) -> str:
        """Truncate with an ellipsis when the text does not fit."""
        if self.canvas.text_width(text, font=self.font(size)) <= max_width:
            return text
        keep = text
        while keep and self.canvas.text_width(keep + "…", font=self.font(size)) > max_width:
            keep = keep[:-1]
        return keep + "…"
