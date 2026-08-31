"""PIL-backed :class:`~retrostation.platform.base.Canvas`.

Everything here is plain Pillow -- no SDL -- so it can be unit-tested on a
development machine without a handheld attached, and screenshotted for layout
review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

from ..base import Canvas

#: PIL needs ``(left, top, right, bottom)``; the public API takes ``(x, y, w, h)``.
def _box(box: Sequence[float]) -> tuple[int, int, int, int]:
    x, y, w, h = box
    return (round(x), round(y), round(x + w), round(y + h))


#: How many pre-rendered text patches / gradients one canvas keeps.  Measured
#: on the RG DS: a full frame draws ~70 strings, and CJK glyph rendering is the
#: single most expensive thing the UI does (~0.7 ms per call there).
_CACHE_LIMIT = 400
_GRADIENT_LIMIT = 32


def _fit_size(width: int, height: int, box_w: int, box_h: int) -> tuple[int, int]:
    if width <= 0 or height <= 0 or box_w <= 0 or box_h <= 0:
        return (1, 1)
    scale = min(box_w / width, box_h / height, 1.0)
    return (max(1, round(width * scale)), max(1, round(height * scale)))


class PilCanvas(Canvas):
    """Off-screen RGBA surface that the SDL display later uploads."""

    def __init__(self, width: int, height: int, background: Sequence[int] = (0, 0, 0, 255)) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid canvas size {width}x{height}")
        self._image = Image.new("RGBA", (width, height), tuple(background))
        self._draw = ImageDraw.Draw(self._image)
        self.size = (width, height)
        #: ``(content, font, fill, anchor) -> (patch, dx, dy)``
        self._text_cache: dict[tuple, tuple[Image.Image, int, int]] = {}
        #: ``(w, h, start, end) -> gradient bitmap``
        self._gradient_cache: dict[tuple, Image.Image] = {}

    # -- accessors -------------------------------------------------------- #

    @property
    def pil_image(self) -> Image.Image:
        """The underlying bitmap; used only by the SDL uploader."""
        return self._image

    # -- whole surface ---------------------------------------------------- #

    def clear(self, color: Sequence[int]) -> None:
        self._draw.rectangle([0, 0, self.size[0], self.size[1]], fill=tuple(color))

    # -- shapes ----------------------------------------------------------- #

    def rect(
        self,
        box: Sequence[float],
        *,
        fill: Sequence[int] | None = None,
        outline: Sequence[int] | None = None,
        width: int = 1,
    ) -> None:
        self._draw.rectangle(
            _box(box),
            fill=tuple(fill) if fill else None,
            outline=tuple(outline) if outline else None,
            width=width if outline else 0,
        )

    def rounded_rect(
        self,
        box: Sequence[float],
        *,
        radius: int,
        fill: Sequence[int] | None = None,
        outline: Sequence[int] | None = None,
        width: int = 1,
    ) -> None:
        self._draw.rounded_rectangle(
            _box(box),
            radius=max(0, radius),
            fill=tuple(fill) if fill else None,
            outline=tuple(outline) if outline else None,
            width=width if outline else 0,
        )

    def hgradient(
        self,
        box: Sequence[float],
        *,
        start: Sequence[int],
        end: Sequence[int],
        radius: int = 0,
    ) -> None:
        x, y, w, h = (round(v) for v in box)
        if w <= 0 or h <= 0:
            return

        gradient = self._gradient(w, h, start, end)
        if radius > 0:
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
            self._image.paste(gradient, (x, y), mask)
        else:
            self._image.paste(gradient, (x, y))

    # -- text ------------------------------------------------------------- #

    def ellipse(
        self,
        box: Sequence[float],
        *,
        fill: Sequence[int] | None = None,
        outline: Sequence[int] | None = None,
        width: int = 1,
    ) -> None:
        self._draw.ellipse(
            _box(box),
            fill=tuple(fill) if fill else None,
            outline=tuple(outline) if outline else None,
            width=width if outline else 0,
        )

    def text(
        self,
        xy: Sequence[float],
        content: str,
        *,
        font: object,
        fill: Sequence[int],
        anchor: str = "la",
    ) -> None:
        patch, dx, dy = self._text_patch(content, font, tuple(fill), anchor)
        # ``alpha_composite``, not ``paste``: paste would multiply the glyph
        # coverage into the alpha channel twice (once as colour, once as mask)
        # and every antialiased edge would come out too dark.
        self._image.alpha_composite(patch, (round(xy[0]) + dx, round(xy[1]) + dy))

    def _gradient(self, w: int, h: int, start, end) -> Image.Image:
        """A cached ``w x h`` horizontal gradient.

        Building one costs a ``putpixel`` per column plus a resize -- 42 ms per
        frame on the handheld for the selected-row highlight, which is why the
        result is kept.
        """
        key = (w, h, tuple(start), tuple(end))
        cached = self._gradient_cache.get(key)
        if cached is not None:
            return cached

        row = Image.new("RGBA", (max(1, w), 1))
        row.putdata([
            tuple(round(a + (b - a) * (column / max(1, w - 1))) for a, b in zip(start, end))
            for column in range(w)
        ])
        gradient = row.resize((w, h))
        if len(self._gradient_cache) >= _GRADIENT_LIMIT:
            self._gradient_cache.clear()
        self._gradient_cache[key] = gradient
        return gradient

    def _text_patch(self, content: str, font: object, fill: tuple, anchor: str):
        """Render ``content`` once, then paste it on every later frame.

        The trick for anchors: render into a scratch bitmap and crop to the ink
        box.  The crop says where the glyphs actually landed relative to the
        anchor point, so no font metrics have to be re-derived -- ``mm``, ``la``
        and friends all keep working.
        """
        key = (content, font, fill, anchor)
        cached = self._text_cache.get(key)
        if cached is not None:
            return cached

        margin = max(8, int(getattr(font, "size", 12)))
        length = int(font.getlength(content)) if content else 0  # type: ignore[attr-defined]
        # PIL lays the run out *around* the anchor: a right-aligned run starts
        # ``length`` px to its left and a centred one straddles it.  Shift the
        # anchor inside the scratch to match, because the crop below keeps only
        # what landed in it -- with the anchor at the left margin, everything
        # left of it fell outside and was clipped away for good (a right-aligned
        # two-character value rendered as just its last glyph).
        horizontal = anchor[0] if anchor else "l"
        if horizontal == "r":
            origin_x = margin + length
        elif horizontal == "m":
            origin_x = margin + length // 2
        else:
            origin_x = margin
        scratch = Image.new(
            "RGBA", (max(1, length + 2 * margin), max(1, 3 * margin)), (0, 0, 0, 0)
        )
        origin = (origin_x, margin)
        ImageDraw.Draw(scratch).text(origin, content, font=font, fill=fill, anchor=anchor)

        bbox = scratch.getbbox()
        if bbox is None:  # blank text: nothing to draw, ever
            entry = (Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0)
        else:
            entry = (scratch.crop(bbox), bbox[0] - origin[0], bbox[1] - origin[1])

        if len(self._text_cache) >= _CACHE_LIMIT:
            self._text_cache.clear()
        self._text_cache[key] = entry
        return entry

    def text_width(self, content: str, *, font: object) -> int:
        return int(round(font.getlength(content)))  # type: ignore[attr-defined]

    def text_height(self, content: str, *, font: object) -> int:
        left, top, _right, bottom = font.getbbox(content)  # type: ignore[attr-defined]
        return int(bottom - top)

    # -- bitmaps ---------------------------------------------------------- #

    def image(self, bitmap: object, box: Sequence[float]) -> None:
        x, y, w, h = (round(v) for v in box)
        if w <= 0 or h <= 0:
            return
        source: Image.Image = bitmap  # type: ignore[assignment]
        if source.size != (w, h):
            source = source.resize((w, h), Image.Resampling.LANCZOS)
        if source.mode != "RGBA":
            source = source.convert("RGBA")
        self._image.paste(source, (x, y), source)

    def image_fit(
        self,
        bitmap: object,
        box: Sequence[float],
        *,
        halign: str = "center",
        valign: str = "center",
    ) -> None:
        x, y, w, h = (round(v) for v in box)
        source: Image.Image = bitmap  # type: ignore[assignment]
        scaled_w, scaled_h = _fit_size(source.width, source.height, w, h)
        if halign == "left":
            dx = 0
        elif halign == "right":
            dx = w - scaled_w
        else:
            dx = (w - scaled_w) // 2
        if valign == "top":
            dy = 0
        elif valign == "bottom":
            dy = h - scaled_h
        else:
            dy = (h - scaled_h) // 2
        self.image(source, (x + dx, y + dy, scaled_w, scaled_h))

    def dim(self, bitmap: object, opacity: int) -> object:
        """Return a copy of ``bitmap`` scaled to ``opacity`` (0-255)."""
        source: Image.Image = bitmap  # type: ignore[assignment]
        if opacity >= 255:
            return source
        if source.mode != "RGBA":
            source = source.convert("RGBA")
        faded = source.copy()
        faded.putalpha(source.getchannel("A").point(lambda value: value * opacity // 255))
        return faded

    # -- text layout ------------------------------------------------------ #

    def wrap_text(
        self, text: str, *, font: object, max_width: int, max_lines: int
    ) -> list[str]:
        return wrap_text(self._draw, text, font, max_width, max_lines)


#: Wrapped lines already measured.  The bottom panel re-wraps its description
#: on every video frame, and a 350-character blurb costs ~47 ms of ``getlength``
#: -- at 13 fps that is most of the frame budget for text that never changes.
_WRAP_CACHE: dict[tuple[str | int, ...], list[str]] = {}
_WRAP_LIMIT = 512


def _font_tag(font: object) -> tuple[str, int]:
    """Something stable to key a cache on; falls back to 0/"" for bitmap fonts."""
    return (str(getattr(font, "path", "") or ""), int(getattr(font, "size", 0) or 0))


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: object,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """Greedy character wrapping for CJK text.

    Chinese has no spaces to break on, so we accumulate one character at a time.
    ``max_lines`` truncates with an ellipsis -- used by the bottom-screen
    description panel (see DESIGN section 7.2).
    """
    if not text:
        return []
    key = (text, max_width, max_lines, *_font_tag(font))
    cached = _WRAP_CACHE.get(key)
    if cached is not None:
        return cached

    lines = _measure(text, font, max_width, max_lines)
    if len(_WRAP_CACHE) >= _WRAP_LIMIT:
        _WRAP_CACHE.clear()
    _WRAP_CACHE[key] = lines
    return lines


def _measure(text: str, font: object, max_width: int, max_lines: int) -> list[str]:
    """The actual wrapping pass.

    Widths are accumulated per character instead of re-measuring the whole line
    for every character: ``getlength`` costs what the string is long, so the
    naive version is quadratic (~350 measures of up to 350 glyphs for one
    blurb).  Kerning differences are invisible in a 4-line, 12 px preview.
    """
    getlength = font.getlength  # type: ignore[attr-defined]

    lines: list[str] = []
    current: list[str] = []
    width = 0.0
    for char in text:
        if char == "\n":
            lines.append("".join(current))
            current.clear()
            width = 0.0
            if len(lines) == max_lines:
                return _ellipsis(lines)
            continue
        char_width = getlength(char)
        if current and width + char_width > max_width:
            lines.append("".join(current))
            current = [char]
            width = char_width
            if len(lines) == max_lines:
                return _ellipsis(lines)
        else:
            current.append(char)
            width += char_width
    if current:
        lines.append("".join(current))
    return lines[:max_lines]


def _ellipsis(lines: list[str]) -> list[str]:
    last = lines[-1]
    if last:
        lines[-1] = last[:-1] + "…"
    return lines


def save_bitmap(canvas: PilCanvas, path: Path) -> None:
    """Write a canvas to disk (diagnostics / layout review)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.pil_image.save(path)
