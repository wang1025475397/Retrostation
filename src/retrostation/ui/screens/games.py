"""Game page: list, grid and cover-carousel views.

All three take the same ``games`` list and ``index`` -- the session owns the
cursor, so switching views with X never loses your place (DESIGN §7.1).
"""

from __future__ import annotations

from ..art import ArtProvider
from ..painter import Painter
from ..widgets import page_header, scrollbar
from ...core.model import Game
from ...core.theme import COLORS

_STAR = "★"


def header(painter: Painter, *, title: str, subtitle: str, right: str) -> None:
    page_header(painter, title=title, subtitle=subtitle, right=right)


def footer_hints(painter: Painter, layout: str, translator) -> list[tuple[str, str]]:
    next_view = {"list": "games.layout_grid", "grid": "games.layout_carousel",
                 "carousel": "games.layout_list"}[layout]
    return [
        ("A", translator("btn.start")),
        ("B", translator("btn.back")),
        ("Y", translator("btn.favorite")),
        ("X", translator(next_view)),
        ("SELECT", translator("btn.filter")),
        ("START", translator("btn.menu")),
    ]


# --------------------------------------------------------------------------- #
# List
# --------------------------------------------------------------------------- #


def draw_list(
    painter: Painter,
    art: ArtProvider,
    games: list[Game],
    index: int,
    *,
    rows_per_page: int,
) -> int:
    """Returns the first visible row, which the app keeps across frames."""
    m = painter.metrics
    content_h = m.content_h(single=_single(painter))
    row_step = m.row_step

    first = max(0, min(index, max(0, len(games) - rows_per_page)))
    for row, position in enumerate(range(first, min(len(games), first + rows_per_page))):
        game = games[position]
        y = m.content_top + m.u(8) + row * row_step
        selected = position == index
        _row(painter, art, game, (m.u(8), y, m.width - m.u(24), m.row_h), selected=selected,
             position=position, total=len(games))

    scrollbar(painter, index=index, total=len(games), visible=rows_per_page, content_h=content_h)
    return first


def _row(
    painter: Painter,
    art: ArtProvider,
    game: Game,
    box: tuple[int, int, int, int],
    *,
    selected: bool,
    position: int,
    total: int,
) -> None:
    m = painter.metrics
    x, y, w, h = box
    if selected:
        painter.hgradient(box, start=COLORS.accent, end=COLORS.accent_d1, radius=m.u(6))
        name_color = (26, 18, 6, 255)
        meta_color = (90, 66, 16, 255)
        index_color = (110, 82, 22, 255)
    else:
        painter.rounded_rect(box, radius=m.u(6), fill=COLORS.panel)
        name_color = COLORS.text
        meta_color = COLORS.text_dim
        index_color = (92, 92, 99, 255)

    thumb_w, thumb_h = m.thumb_w, m.thumb_h
    art_box = (x + m.u(4), y + (h - thumb_h) // 2, thumb_w, thumb_h)
    bitmap = art.thumbnail(game, thumb_w, thumb_h, prefer_logo=True)
    if bitmap is not None:
        painter.image_fit(bitmap, art_box)
    else:
        painter.rounded_rect(art_box, radius=m.u(3), fill=COLORS.panel_2, outline=(255, 255, 255, 20))
        placeholder = art.placeholder(game.key, thumb_w, thumb_h)
        painter.image_fit(placeholder, art_box)

    text_x = x + thumb_w + m.u(10)
    max_name_w = w - thumb_h - m.u(150)
    painter.text(
        (text_x, y + h // 2),
        painter.ellipsize(game.display_name, size=15, max_width=max_name_w),
        size=15, fill=name_color, anchor="lm",
    )

    meta_x = x + w - m.u(46)
    if game.favorite:
        painter.text((meta_x, y + h // 2), _STAR, size=14, fill=(122, 82, 0, 255) if selected else COLORS.accent,
                     anchor="rm")
    else:
        genre = game.genres[0] if game.genres else ""
        painter.text(
            (meta_x, y + h // 2),
            painter.ellipsize(genre, size=11, max_width=m.u(90)),
            size=11, fill=meta_color, anchor="rm",
        )

    painter.text(
        (x + w - m.u(8), y + h // 2),
        f"{position + 1}/{total}",
        size=11, fill=index_color, anchor="rm",
    )


# --------------------------------------------------------------------------- #
# Grid
# --------------------------------------------------------------------------- #


def draw_grid(
    painter: Painter,
    art: ArtProvider,
    games: list[Game],
    index: int,
    *,
    cols: int,
    rows: int,
) -> int:
    m = painter.metrics
    per_page = cols * rows
    page = index // per_page
    first = page * per_page
    cell_h = m.grid_cell_h(single=_single(painter))
    padding, gap = m.grid_padding, m.grid_gap
    cell_w = (m.width - 2 * padding - gap * (cols - 1)) // cols

    for slot in range(per_page):
        position = first + slot
        if position >= len(games):
            break
        col, row = slot % cols, slot // cols
        x = padding + col * (cell_w + gap)
        y = m.content_top + padding + row * (cell_h + gap)
        _card(painter, art, games[position], (x, y, cell_w, cell_h), selected=position == index)

    content_h = m.content_h(single=_single(painter))
    scrollbar(painter, index=index, total=len(games), visible=per_page, content_h=content_h)
    return first


def _card(
    painter: Painter,
    art: ArtProvider,
    game: Game,
    box: tuple[int, int, int, int],
    *,
    selected: bool,
) -> None:
    m = painter.metrics
    x, y, w, h = box
    name_h = m.u(24)

    if selected:
        box = (x - m.u(3), y - m.u(3), w + m.u(6), h + m.u(6))
        painter.rounded_rect(box, radius=m.u(7), fill=COLORS.panel, outline=COLORS.accent, width=2)
    else:
        painter.rounded_rect(box, radius=m.u(7), fill=COLORS.panel, outline=COLORS.border)

    art_h = h - name_h
    art_box = (x + 1, y + 1, w - 2, art_h)
    bitmap = art.thumbnail(game, w - 2, art_h)
    if bitmap is None:
        bitmap = art.placeholder(game.key, w - 2, art_h)
    painter.image_fit(bitmap, art_box)

    if game.favorite:
        painter.text((x + w - m.u(8), y + m.u(10)), _STAR, size=12, fill=COLORS.accent, anchor="rm")

    bar = (x + 1, y + art_h, w - 2, name_h)
    painter.rect(bar, fill=COLORS.accent_d1 if selected else COLORS.panel_2)
    painter.text(
        (x + m.u(6), y + art_h + name_h // 2),
        painter.ellipsize(game.display_name, size=11, max_width=w - m.u(12)),
        size=11, fill=(26, 18, 6, 255) if selected else COLORS.text_dim, anchor="lm",
    )


# --------------------------------------------------------------------------- #
# Carousel
# --------------------------------------------------------------------------- #

#: offset -> (scale, opacity).  Neighbours shrink, so their spacing must be
#: accumulated from the *scaled* widths or the gaps grow without bound.
_SCALE = (1.0, 0.75, 0.62, 0.62)
_OPACITY = (255, 108, 46, 18)


def draw_carousel(
    painter: Painter,
    art: ArtProvider,
    games: list[Game],
    index: int,
) -> int:
    m = painter.metrics
    single = _single(painter)
    card_h = m.carousel_card_h(single=single)
    card_w = m.carousel_card_w(single=single)
    gap = m.carousel_gap
    top = m.content_top + m.u(6)

    widths = [round(card_w * scale) for scale in _SCALE]
    offsets = [0]
    for k in range(1, len(_SCALE)):
        offsets.append(offsets[-1] + (widths[k - 1] + widths[k]) // 2 + gap)

    content_h = m.content_h(single=single)
    banner_h = content_h - (card_h + m.u(10)) - m.u(6)
    from_center = m.width // 2

    first = max(0, index - 3)
    last = min(len(games) - 1, index + 3)
    for position in range(first, last + 1):
        offset = abs(position - index)
        scale = _SCALE[offset]
        opacity = _OPACITY[offset]
        side = 1 if position > index else (-1 if position < index else 0)
        cx = from_center + side * offsets[offset]
        box = (cx - card_w // 2, top, card_w, card_h)
        _cover_card(painter, art, games[position], box, selected=offset == 0, opacity=opacity, logo_ratio=0.72)

    banner_top = top + card_h + m.u(10)
    game = games[index]
    if game.has_asset("logo"):
        bitmap = art.thumbnail(game, m.u(240), min(m.u(56), banner_h), prefer_logo=True)
        if bitmap is not None:
            painter.image_fit(bitmap, (from_center - m.u(120), banner_top, m.u(240), min(m.u(56), banner_h)))
    else:
        painter.text(
            (from_center, banner_top + min(m.u(28), banner_h // 2)),
            painter.ellipsize(game.display_name, size=17, max_width=m.width - m.u(40)),
            size=17, fill=COLORS.text, anchor="mm",
        )
        painter.text(
            (from_center, banner_top + min(m.u(50), banner_h)),
            " · ".join(filter(None, (game.publisher or "", str(game.release or ""),
                                     game.genres[0] if game.genres else ""))),
            size=12, fill=COLORS.text_dim, anchor="mm",
        )

    painter.text(
        (m.width - m.u(12), m.content_top + m.u(14)),
        f"{index + 1} / {len(games)}",
        size=11, fill=COLORS.text_dim, anchor="rm",
    )
    return first


def _cover_card(
    painter: Painter,
    art: ArtProvider,
    game: Game,
    box: tuple[int, int, int, int],
    *,
    selected: bool,
    opacity: int,
    logo_ratio: float,
) -> None:
    m = painter.metrics
    x, y, w, h = box
    painter.rounded_rect(
        box, radius=m.u(8),
        fill=COLORS.panel,
        outline=COLORS.accent if selected else COLORS.border,
        width=2 if selected else 1,
    )

    art_box = (x + 1, y + 1, w - 2, h - 2)
    bitmap = art.thumbnail(game, w - 2, h - 2)
    if bitmap is None:
        bitmap = art.placeholder(game.key, w - 2, h - 2)
    if opacity < 255:
        bitmap = painter.canvas.dim(bitmap, opacity)
    painter.image_fit(bitmap, art_box)

    if game.favorite:
        painter.text((x + w - m.u(8), y + m.u(10)), _STAR, size=13, fill=COLORS.accent, anchor="rm")

    if game.has_asset("logo"):
        logo = art.thumbnail(game, w - m.u(20), m.u(28), prefer_logo=True)
        if logo is not None:
            painter.image_fit(logo, (x + m.u(10), y + h - m.u(36), w - m.u(20), m.u(28)))


def _single(painter: Painter) -> bool:
    """Whether the top screen is in single-screen (split) mode."""
    return bool(getattr(painter, "single", False))
