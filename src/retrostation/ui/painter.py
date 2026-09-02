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

#: Truncated strings kept per painter.  Measuring CJK text is the expensive
#: part of drawing, and the detail strip re-truncates the same four lines on
#: every clip frame.
_ELLIPSIS_LIMIT = 256


class Painter:
    """Bundle of canvas + metrics + platform, with a font cache."""

    def __init__(self, canvas: Canvas, metrics: Metrics, platform: Platform, translator: Translator) -> None:
        self.canvas = canvas
        self.metrics = metrics
        self.platform = platform
        self.translator = translator
        self._fonts: dict[int, object] = {}
        self._ellipsis: dict[tuple[str, int, int], str] = {}
        #: Set while a game's backdrop is in play, so panels know to go
        #: translucent instead of hiding it (see ``ui.screens.games``).
        self.backdrop = False

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
        key = (text, size, max_width)
        cached = self._ellipsis.get(key)
        if cached is not None:
            return cached
        result = self._truncate(text, size=size, max_width=max_width)
        if len(self._ellipsis) >= _ELLIPSIS_LIMIT:
            self._ellipsis.clear()
        self._ellipsis[key] = result
        return result

    def _truncate(self, text: str, *, size: int, max_width: int) -> str:
        font = self.font(size)
        if self.canvas.text_width(text, font=font) <= max_width:
            return text
        # Binary search for the longest prefix that still fits.  Removing one
        # character at a time re-measures the whole string every time, which is
        # quadratic in the length: on the device a 126-character description
        # cost 246 ms and the longest ones in a real library (~426 chars) cost
        # seconds -- per call, and the detail strip asks four times a frame.
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.canvas.text_width(text[:middle] + "…", font=font) <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low] + "…"
