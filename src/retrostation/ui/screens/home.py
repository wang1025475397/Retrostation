"""Home page: the platform carousel.

Pure drawing -- the app prepares every string and every artwork reference, so
this module contains no data access at all (DESIGN §7.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..art import ArtProvider
from ..painter import Painter
from ..widgets import button_bar, page_header
from ...core.theme import COLORS


@dataclass(frozen=True)
class Tile:
    """One card on the carousel."""

    key: str
    title: str
    subtitle: str
    #: A game whose cover can stand in for the platform, if any.
    artwork: object | None


def draw(
    painter: Painter,
    art: ArtProvider,
    *,
    tiles: list[Tile],
    index: int,
    info_title: str,
    info_subtitle: str,
    info_right: str,
    previews: list[object],
    hints: list[tuple[str, str]],
) -> None:
    m = painter.metrics
    page_header(
        painter,
        title=painter.translator("home.title"),
        subtitle=painter.translator("home.platform_count", count=len(tiles)),
        right=f"{index + 1} / {len(tiles)}" if tiles else "",
    )

    if not tiles:
        painter.text(
            (m.width // 2, m.height // 2),
            painter.translator("home.empty"),
            size=14, fill=(74, 74, 80, 255), anchor="mm",
        )
        button_bar(painter, hints)
        return

    _carousel(painter, art, tiles, index)
    _info(painter, info_title, info_subtitle, info_right)
    _preview(painter, art, previews)
    button_bar(painter, hints)


def _carousel(painter: Painter, art: ArtProvider, tiles: list[Tile], index: int) -> None:
    m = painter.metrics
    card_w = m.u(158)
    card_h = m.u(156)
    art_h = m.u(100)
    gap = m.u(12)
    top = m.status_h + m.head_h + m.u(16)

    visible = m.width // (card_w + gap) + 2
    first = max(0, index - visible // 2)
    last = min(len(tiles) - 1, first + visible)

    for position in range(first, last + 1):
        offset = position - index
        x = m.width // 2 - card_w // 2 + offset * (card_w + gap)
        selected = offset == 0
        tile = tiles[position]

        if selected:
            box = (x - m.u(6), top - m.u(6), card_w + m.u(12), card_h + m.u(12))
            outline = COLORS.accent
        else:
            box = (x, top, card_w, card_h)
            outline = COLORS.border
        painter.rounded_rect(box, radius=m.u(10), fill=COLORS.panel, outline=outline)

        art_box = (box[0] + 1, box[1] + 1, box[2] - 2, art_h)
        bitmap = art.thumbnail(tile.artwork, box[2] - 2, art_h) if tile.artwork else None
        if bitmap is None:
            bitmap = art.placeholder(tile.key, box[2] - 2, art_h)
        painter.image_fit(bitmap, art_box)

        name_y = box[1] + art_h
        painter.text(
            (box[0] + m.u(8), name_y + m.u(16)),
            painter.ellipsize(tile.title, size=13, max_width=box[2] - m.u(16)),
            size=13, fill=COLORS.text, anchor="lm",
        )
        painter.text(
            (box[0] + m.u(8), name_y + m.u(34)),
            tile.subtitle, size=10, fill=(122, 122, 128, 255), anchor="lm",
        )


def _info(painter: Painter, title: str, subtitle: str, right: str) -> None:
    m = painter.metrics
    y = m.status_h + m.u(206)
    painter.text((m.u(16), y), title, size=15, fill=COLORS.text, anchor="lm")
    painter.text((m.u(16), y + m.u(20)), subtitle, size=12, fill=COLORS.text_dim, anchor="lm")
    if right:
        painter.text((m.width - m.u(16), y), right, size=12, fill=COLORS.text_dim, anchor="rm")


def _preview(painter: Painter, art: ArtProvider, previews: list[object]) -> None:
    m = painter.metrics
    y = m.status_h + m.u(250)
    painter.text((m.u(16), y + m.u(14)), painter.translator("home.preview"), size=11,
                 fill=(122, 122, 128, 255), anchor="lm")

    if not previews:
        painter.text(
            (m.u(66), y + m.u(14)),
            painter.translator("home.empty"),
            size=11, fill=(74, 74, 80, 255), anchor="lm",
        )
        return

    x = m.u(66)
    for game in previews:
        bitmap = art.thumbnail(game, m.u(62), m.u(46))
        if bitmap is None:
            bitmap = art.placeholder(game.key, m.u(62), m.u(46))
        painter.image_fit(bitmap, (x, y, m.u(62), m.u(46)))
        x += m.u(70)
