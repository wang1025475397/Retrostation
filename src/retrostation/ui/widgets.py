"""Reusable drawing widgets.

Every function takes a :class:`~retrostation.ui.painter.Painter` and draws in
reference-design units -- no pixel literals, no PIL, no SDL.  Screens compose
these; they never hand-draw chrome a widget already provides.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..core.model import ASSET_COVER, ASSET_LOGO, Game
from ..core.theme import COLORS, Metrics
from ..data.media import placeholder_bitmap
from .painter import Painter

# --------------------------------------------------------------------------- #
# Chrome
# --------------------------------------------------------------------------- #


def status_bar(painter: Painter, *, dual: bool) -> None:
    """Time / screen mode / temperature / battery strip."""
    m = painter.metrics
    painter.rect((0, 0, m.width, m.status_h), fill=(16, 16, 18, 255))
    center = m.status_h // 2

    painter.text((m.u(12), center), datetime.now().strftime("%H:%M"), size=12, fill=COLORS.text_dim, anchor="lm")

    parts: list[str] = []
    battery = painter.platform.battery()
    temperature = painter.platform.temperature()
    if battery is not None:
        parts.append(f"{battery}%")
    if temperature is not None:
        parts.append(f"{temperature:.0f}C")
    if parts:
        painter.text(
            (m.width - m.u(12), center), "  ".join(parts), size=12, fill=COLORS.text_dim, anchor="rm"
        )

    mode = painter.translator("status.dual" if dual else "status.single")
    painter.text((m.width // 2, center), mode, size=12, fill=COLORS.text_dim, anchor="mm")


def page_header(painter: Painter, *, title: str, subtitle: str = "", right: str = "") -> None:
    """Accent bar + page title, directly below the status bar."""
    m = painter.metrics
    top = m.status_h
    painter.rect((0, top, m.width, m.head_h), fill=(25, 25, 25, 255))
    painter.rect((0, top, m.width, 1), fill=(34, 34, 37, 255))
    painter.rect((m.u(14), top + m.u(12), 3, m.head_h - m.u(24)), fill=COLORS.accent)

    center = top + m.head_h // 2
    x = m.u(26)
    painter.text((x, center), title, size=20, fill=COLORS.text, anchor="lm")
    if subtitle:
        x += painter.text_width(title, size=20) + m.u(10)
        painter.text((x, center), subtitle, size=12, fill=COLORS.text_dim, anchor="lm")
    if right:
        painter.text((m.width - m.u(14), center), right, size=12, fill=COLORS.text_dim, anchor="rm")


def button_bar(painter: Painter, hints: list[tuple[str, str]]) -> None:
    """``hints`` is a list of ``(key_label, action_label)`` pairs."""
    m = painter.metrics
    top = m.height - m.bar_h
    painter.rect((0, top, m.width, m.bar_h), fill=(16, 16, 18, 255))
    painter.rect((0, top, m.width, 1), fill=(34, 34, 37, 255))

    center = top + m.bar_h // 2
    radius = m.u(9)
    x = m.u(10)
    for key_label, action_label in hints:
        painter.ellipse((x, center - radius, radius * 2, radius * 2), fill=COLORS.accent_d1)
        painter.text((x + radius, center), key_label, size=10, fill=(26, 18, 6, 255), anchor="mm")
        x += radius * 2 + m.u(5)
        painter.text((x, center), action_label, size=12, fill=COLORS.text_dim, anchor="lm")
        x += painter.text_width(action_label, size=12) + m.u(12)


def scrollbar(painter: Painter, *, index: int, total: int, visible: int, content_h: int) -> None:
    """Thin indicator on the right edge; a no-op when everything fits."""
    m = painter.metrics
    if total <= visible:
        return
    thumb_h = max(m.u(24), content_h * visible / total)
    y = (content_h - thumb_h) * index / max(1, total - 1)
    x = m.width - m.scrollbar_w - m.u(3)

    painter.rect((x, m.u(4), m.scrollbar_w, content_h - m.u(8)), fill=(255, 255, 255, 15))
    painter.rect((x, m.u(4) + int(y), m.scrollbar_w, int(thumb_h)), fill=COLORS.accent)


def toast(painter: Painter, text: str) -> None:
    """Floating pill near the bottom edge."""
    m = painter.metrics
    width = painter.text_width(text, size=12) + m.u(28)
    height = m.u(24)
    x = (m.width - width) // 2
    y = m.height - m.bar_h - m.u(40)
    painter.rounded_rect((x, y, width, height), radius=height // 2, fill=(24, 24, 26, 242), outline=COLORS.border)
    painter.text((m.width // 2, y + height // 2), text, size=12, fill=COLORS.text, anchor="mm")


def dialog(
    painter: Painter,
    *,
    title: str,
    body: str = "",
    rows: list[tuple[str, str]] | None = None,
    selected: int = 0,
) -> None:
    """Centred modal with a dimmed backdrop.

    ``rows`` are ``(label, value)`` pairs for a settings menu; a plain
    confirmation dialog passes an empty list and renders ``body``.
    """
    m = painter.metrics
    painter.rect((0, 0, m.width, m.height), fill=(0, 0, 0, 158))

    rows = rows or []
    row_h = m.u(40)
    width = m.u(470)
    height = m.u(42) + m.u(16) + (m.u(44) if body else 0) + row_h * len(rows) + m.u(14)
    x = (m.width - width) // 2
    y = (m.height - height) // 2

    painter.rounded_rect((x, y, width, height), radius=m.radius, fill=COLORS.panel, outline=COLORS.border)
    painter.rect((x, y, width, m.u(42)), fill=COLORS.panel_2)
    painter.text(
        (x + m.u(16), y + m.u(21)), title, size=15, fill=COLORS.text, anchor="lm"
    )

    content_y = y + m.u(42)
    if body:
        painter.text(
            (x + m.u(16), content_y + m.u(18)), body, size=13, fill=COLORS.text_dim, anchor="lm"
        )
        content_y += m.u(44)

    for index, (label, value) in enumerate(rows):
        item_y = content_y + m.u(6) + index * row_h
        is_selected = index == selected
        if is_selected:
            painter.hgradient(
                (x + m.u(6), item_y, width - m.u(12), row_h - m.u(8)),
                start=COLORS.accent,
                end=COLORS.accent_d1,
                radius=m.u(6),
            )
            label_color = value_color = (26, 18, 6, 255)
        else:
            label_color = COLORS.text
            value_color = COLORS.text_dim
        painter.text((x + m.u(16), item_y + row_h // 2 - m.u(4)), label, size=14, fill=label_color, anchor="lm")
        painter.text(
            (x + width - m.u(16), item_y + row_h // 2 - m.u(4)), value, size=12, fill=value_color, anchor="rm"
        )


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #


def game_artwork(
    painter: Painter,
    game: Game,
    box: tuple[int, int, int, int],
    *,
    prefer_logo: bool = False,
) -> None:
    """Best artwork for ``game`` inside ``box``.

    ``prefer_logo`` is used by the list view (wide slot); the grid, carousel
    and bottom screen prefer the cover.  When nothing exists a deterministic
    placeholder is drawn, never a blank box.
    """
    m = painter.metrics
    x, y, w, h = box
    kind = ASSET_LOGO if prefer_logo else ASSET_COVER
    bitmap = _decode(painter, game.asset(kind))

    if bitmap is not None:
        painter.image_fit(bitmap, box)
        return

    painter.rounded_rect(box, radius=m.u(4), fill=COLORS.panel_2, outline=COLORS.border)
    placeholder = placeholder_bitmap(painter.platform, game.key, max(8, w), max(8, h))
    painter.image_fit(placeholder, box)
    painter.text(
        (x + w // 2, y + h // 2),
        game.display_name[:2] or "?",
        size=max(11, h // 3),
        fill=(255, 255, 255, 205),
        anchor="mm",
    )


def logo_banner(painter: Painter, game: Game, box: tuple[int, int, int, int]) -> None:
    """Bottom-screen logo strip: the logo when present, otherwise the name."""
    m = painter.metrics
    x, y, w, h = box
    painter.rounded_rect(box, radius=m.u(8), fill=COLORS.panel, outline=COLORS.border)

    logo = _decode(painter, game.asset(ASSET_LOGO))
    if logo is not None:
        painter.image_fit(logo, (x + m.u(12), y + m.u(10), w - m.u(24), h - m.u(20)))
        return

    painter.text(
        (x + w // 2, y + h // 2), game.display_name, size=16, fill=COLORS.text, anchor="mm"
    )


def _decode(painter: Painter, path) -> object | None:
    """Decode an asset path, returning ``None`` instead of raising."""
    if path is None:
        return None
    try:
        if not Path(path).is_file():
            return None
        return painter.platform.load_image(Path(path))
    except OSError:
        return None
