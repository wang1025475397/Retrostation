"""Home page: the platform carousel.

Pure drawing -- the app prepares every string and every artwork reference, so
this module contains no data access at all (DESIGN §7.1).

Each card is the platform's **background on top, logo underneath**, in two
stacked bands, taken from the shipped artwork (``assets/platforms/``).
Borrowing a system's first game cover instead looked arbitrary and changed with
scan order; this looks the same on every boot.

The background band is square because the sources are (1024x1024), so nothing
is cropped.  The logo band below it replaces the old name/count caption: the
artwork identifies the platform, and the info line under the carousel spells
out whichever one is selected.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..art import ArtProvider
from ..painter import Painter
from ..widgets import button_bar, page_header
from ...core.theme import COLORS, is_android_skin
from .games import cover_art

@dataclass(frozen=True)
class Tile:
    """One card on the carousel."""

    key: str
    title: str
    subtitle: str
    #: Text after the first space of a ``"<system> <variant>"`` directory
    #: (``Hack`` for ``GBA Hack``).  Empty for a plain system: those cards look
    #: exactly like their key, so there is nothing to disambiguate.
    variant: str = ""


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
    preview_index: int = -1,
    hints: list[tuple[str, str]] | None = None,
) -> None:
    m = painter.metrics
    hints = hints or []
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
    # 单屏模式下预览画在底部 detail strip（见 App._draw_detail_strip），
    # 这里跳过以免与 strip 重叠；双屏顶部屏仍照常显示预览。
    if not getattr(painter, "single", False):
        _preview(painter, art, previews, preview_index)
    button_bar(painter, hints)


def _carousel(painter: Painter, art: ArtProvider, tiles: list[Tile], index: int) -> None:
    m = painter.metrics
    art_side = m.platform_art
    card_w = art_side + m.u(8)
    card_h = m.platform_card_h
    gap = m.platform_gap

    visible = m.width // (card_w + gap) + 2
    first = max(0, index - visible // 2)
    last = min(len(tiles) - 1, first + visible)

    for position in range(first, last + 1):
        offset = position - index
        x = m.width // 2 - card_w // 2 + offset * (card_w + gap)
        selected = offset == 0
        tile = tiles[position]

        if selected:
            box = (x - m.u(6), m.platform_top - m.u(6), card_w + m.u(12), card_h + m.u(12))
            outline = COLORS.accent
        else:
            box = (x, m.platform_top, card_w, card_h)
            outline = COLORS.border
        painter.rounded_rect(box, radius=m.card_radius, fill=COLORS.panel,
                             outline=outline,
                             width=2 if is_android_skin() else 1)

        _card_art(painter, art, tile, (box[0] + 1, box[1] + 1, box[2] - 2, box[3] - 2))


def _card_art(painter: Painter, art: ArtProvider, tile: Tile,
              box: tuple[int, int, int, int]) -> None:
    """Background on top, logo underneath -- two stacked bands, no caption."""
    m = painter.metrics
    x, y, w, h = box
    art_side = m.platform_art
    logo_h = m.platform_logo_h

    # Square and centred horizontally: the selected card is wider than the
    # others, and stretching the background to fill it would distort it.
    art_box = (x + (w - art_side) // 2, y, art_side, art_side)
    background = art.platform_background(tile.key, art_side, art_side)
    logo = art.platform_logo(tile.key, w - m.u(10), logo_h)

    if background is None:
        # No cover: paint a neutral placeholder.  When the platform also ships
        # no logo there is nothing to identify it by, so write its name
        # straight onto the cover instead of leaving a blank invalid tile.
        painter.image(art.placeholder(tile.key, art_side, art_side), art_box)
        if logo is None:
            # Dim the gradient behind the name so it stays legible no matter
            # which hue the deterministic placeholder picked.
            painter.rounded_rect(art_box, radius=m.u(6), fill=(0, 0, 0, 120))
            painter.text(
                (art_box[0] + art_side // 2, art_box[1] + art_side // 2),
                painter.ellipsize(tile.title, size=14, max_width=art_side - m.u(16)),
                size=14, fill=COLORS.text, anchor="mm",
            )
    else:
        painter.image(background, art_box)

    # The cover is the base system's, so a variant directory would be pixel
    # identical to the platform it borrows from without this tag.
    if tile.variant:
        _variant_badge(painter, art_box, tile.variant)

    # The logo band sits in whatever is left below the artwork, vertically
    # centred so the selected card's extra padding is shared above and below.
    below_top = y + art_side
    below_h = max(logo_h, (y + h) - below_top)
    band = (x, below_top + (below_h - logo_h) // 2, w, logo_h)

    if logo is not None:
        painter.image_fit(logo, band)
    else:
        # No logo (RECENT, PORTS, or a platform with no artwork at all): name it
        # below the art.  When the cover is also missing the name already sits
        # on the cover placeholder above, but repeating it here keeps the strip
        # from looking broken.
        painter.text(
            (band[0] + w // 2, band[1] + logo_h // 2),
            painter.ellipsize(tile.title, size=11, max_width=w - m.u(10)),
            size=11, fill=COLORS.text_dim, anchor="mm",
        )


def _variant_badge(painter: Painter, art_box: tuple[int, int, int, int], text: str) -> None:
    """Tag a borrowed cover with the variant's own name, top-right corner.

    A translucent plate keeps the label readable on a light cover as well as a
    dark one; the text is upper-cased because these suffixes are short tags
    (``HACK``, ``ACT``) rather than words.
    """
    m = painter.metrics
    label = text.upper()
    size = 10
    pad_x, pad_y = m.u(4), m.u(2)
    width = painter.text_width(label, size=size) + pad_x * 2
    height = painter.text_height(label, size=size) + pad_y * 2
    x = art_box[0] + art_box[2] - m.u(4) - width
    y = art_box[1] + m.u(4)
    painter.rounded_rect((x, y, width, height), radius=m.u(3), fill=(0, 0, 0, 170))
    painter.text((x + width // 2, y + height // 2), label, size=size,
                 fill=COLORS.text, anchor="mm")


def _info(painter: Painter, title: str, subtitle: str, right: str) -> None:
    m = painter.metrics
    y = m.platform_info_y
    painter.text((m.u(16), y), title, size=15, fill=COLORS.text, anchor="lm")
    painter.text((m.u(16), y + m.u(20)), subtitle, size=12, fill=COLORS.text_dim, anchor="lm")
    if right:
        painter.text((m.width - m.u(16), y), right, size=12, fill=COLORS.text_dim, anchor="rm")


def _preview(painter: Painter, art: ArtProvider, previews: list[object],
             selected: int = -1) -> None:
    m = painter.metrics
    y = m.platform_preview_y
    painter.text((m.u(16), y + m.u(14)), painter.translator("home.preview"), size=11,
                 fill=(122, 122, 128, 255), anchor="lm")

    if not previews:
        painter.text(
            (m.u(66), y + m.u(14)),
            painter.translator("home.empty"),
            size=11, fill=(74, 74, 80, 255), anchor="lm",
        )
        return

    x = m.u(78)
    for position, game in enumerate(previews):
        box = (x, y, m.u(84), m.u(63))
        # 无封面直接画「无封面」空板，不再生成假占位图（与游戏列表一致）。
        cover_art(painter, art, game, box)
        if position == selected:
            painter.rounded_rect(
                (x - m.u(2), y - m.u(2), m.u(84) + m.u(4), m.u(63) + m.u(4)),
                radius=m.u(5), outline=COLORS.accent,
            )
        x += m.u(92)


def carousel_hit(m, count: int, index: int, x: int, y: int) -> int | None:
    """The carousel card a tap landed on, or ``None`` (DESIGN.ANDROID §10.3).

    Mirrors the geometry ``_carousel`` draws -- the selected card centred at
    ``m.width // 2``, neighbours stepping by ``card_w + gap`` -- so the two
    cannot drift apart without someone noticing.  A tap inside the gap between
    two cards hits nothing: that dead zone is the point.
    """
    card_w = m.platform_art + m.u(8)
    gap = m.platform_gap
    left0 = m.width // 2 - card_w // 2
    top = m.platform_top - m.u(6)          # the selected card's outline pad
    bottom = m.platform_top + m.platform_card_h + m.u(6)
    if not (top <= y <= bottom):
        return None
    rel = x - left0
    step_w = card_w + gap
    k = rel // step_w                      # floor; Python floors negatives too
    if rel - k * step_w > card_w:
        return None                        # landed in the gap between cards
    position = index + k
    if not (0 <= position < count):
        return None
    return position
