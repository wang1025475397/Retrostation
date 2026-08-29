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

        row = Image.new("RGBA", (max(1, w), 1))
        for column in range(w):
            t = column / max(1, w - 1)
            row.putpixel(
                (column, 0),
                tuple(round(a + (b - a) * t) for a, b in zip(start, end)),
            )
        gradient = row.resize((w, h))

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
        self._draw.text(
            (round(xy[0]), round(xy[1])),
            content,
            font=font,  # type: ignore[arg-type]
            fill=tuple(fill),
            anchor=anchor,
        )

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
    getlength = font.getlength  # type: ignore[attr-defined]

    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            if len(lines) == max_lines:
                return _ellipsis(lines)
            continue
        if getlength(current + char) > max_width and current:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                return _ellipsis(lines)
        else:
            current += char
    if current:
        lines.append(current)
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
