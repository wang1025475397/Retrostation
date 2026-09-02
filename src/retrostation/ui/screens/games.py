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


def _dimmed(painter: Painter, bitmap: object, opacity: int) -> object:
    """A cached, faded copy of ``bitmap`` (see ``_DIM_CACHE`` below).

    The cover itself is dimmed -- a cross-platform alpha scale -- rather than
    overlaid with a rect: ``rect()`` fills solid colour and would crush the card
    to black instead of fading it.  The cache matters because the carousel
    re-dims the same neighbour covers on every frame.
    """
    key = (id(bitmap), bitmap.width, bitmap.height, opacity)
    faded = _DIM_CACHE.get(key)
    if faded is None:
        faded = painter.canvas.dim(bitmap, opacity)
        if len(_DIM_CACHE) > 64:
            _DIM_CACHE.clear()
        _DIM_CACHE[key] = faded
    return faded


def cover_art(
    painter: Painter,
    art: ArtProvider,
    game: Game,
    box: tuple[int, int, int, int],
    *,
    prefer_logo: bool = False,
    opacity: int = 255,
) -> None:
    """The game's artwork, or an empty plate that says there is none.

    This used to fall back to a generated gradient tile.  It read as
    decoration, so a missing cover was easy to mistake for an unusual one -- and
    each view drew a different one.  An empty plate labelled "no cover" is
    unambiguous, and the three views now agree.
    """
    x, y, w, h = box
    bitmap = art.thumbnail(game, w, h, prefer_logo=prefer_logo)
    if bitmap is None:
        painter.rounded_rect(box, radius=painter.metrics.u(3),
                             fill=COLORS.panel_2, outline=(255, 255, 255, 20))
        painter.text(
            (x + w // 2, y + h // 2),
            painter.translator("games.no_cover"),
            size=11 if h >= 60 else 9, fill=COLORS.text_dim, anchor="mm",
        )
        return
    painter.image_fit(_dimmed(painter, bitmap, opacity) if opacity < 255 else bitmap, box)


#: ``(game key, w, h) -> dimmed backdrop``.  Kept apart from ``_DIM_CACHE``
#: below: these are panel-sized, so a handful of them would crowd out the small
#: cover copies the carousel re-dims on every frame.
_BACKDROP_DIM: dict[tuple, object] = {}
_BACKDROP_LIMIT = 4

#: How far the backdrop is faded.  It sits under text, so it has to stay quiet:
#: bright enough to read as a picture, dim enough to read the list on top.
_BACKDROP_OPACITY = 96

#: Alpha of a row / card while a backdrop is showing through it.  Opaque would
#: simply hide the backdrop; much lower and the text loses its ground.
_PANEL_OVER_BACKDROP = 214


def draw_backdrop(painter: Painter, art: ArtProvider, game: Game) -> None:
    """Fill the panel with the game's own art, dimmed, behind everything else.

    Called straight after the panel is cleared: the header, the rows and the
    cards are then composited on top of it.  ``painter.backdrop`` records
    whether there is one, which is how the panels below know to let it through.
    """
    width, height = painter.width, painter.height
    bitmap = art.backdrop(game, width, height)
    painter.backdrop = bitmap is not None
    if bitmap is None:
        return

    key = (game.key, width, height)
    faded = _BACKDROP_DIM.get(key)
    if faded is None:
        faded = painter.canvas.dim(bitmap, _BACKDROP_OPACITY)
        if len(_BACKDROP_DIM) >= _BACKDROP_LIMIT:
            _BACKDROP_DIM.clear()
        _BACKDROP_DIM[key] = faded
    painter.image(faded, (0, 0, width, height))


def panel_fill(painter: Painter):
    """What a row or card fills itself with.

    Translucent only while a backdrop is in play: over the plain background it
    would just be a slightly different shade of the same dark, and the text
    would sit on nothing.
    """
    if not getattr(painter, "backdrop", False):
        return COLORS.panel
    red, green, blue, _alpha = COLORS.panel
    return (red, green, blue, _PANEL_OVER_BACKDROP)


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
    highlight: bool = True,
    only: int | None = None,
) -> int:
    """Returns the first visible row, which the app keeps across frames.

    ``highlight=False`` paints every row unselected (so the panel can be cached
    and the selection repainted on top later); ``only`` repaints just one row.
    """
    m = painter.metrics
    row_step = m.row_step

    first = (index // rows_per_page) * rows_per_page
    for row, position in enumerate(range(first, min(len(games), first + rows_per_page))):
        if only is not None and position != only:
            continue
        game = games[position]
        y = m.content_top + m.u(8) + row * row_step
        selected = highlight and position == index
        _row(painter, art, game, (m.u(8), y, m.width - m.u(24), m.row_h), selected=selected,
             position=position, total=len(games))

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
        painter.rounded_rect(box, radius=m.u(6), fill=panel_fill(painter))
        name_color = COLORS.text
        meta_color = COLORS.text_dim
        index_color = (92, 92, 99, 255)

    thumb_w, thumb_h = m.thumb_w, m.thumb_h
    cover_art(painter, art, game, (x + m.u(4), y + (h - thumb_h) // 2, thumb_w, thumb_h),
           prefer_logo=True)

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
    highlight: bool = True,
    only: int | None = None,
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
        if only is not None and position != only:
            continue
        col, row = slot % cols, slot // cols
        x = padding + col * (cell_w + gap)
        y = m.content_top + padding + row * (cell_h + gap)
        _card(painter, art, games[position], (x, y, cell_w, cell_h),
              selected=highlight and position == index)

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
        painter.rounded_rect(box, radius=m.u(7), fill=panel_fill(painter), outline=COLORS.accent, width=2)
    else:
        painter.rounded_rect(box, radius=m.u(7), fill=panel_fill(painter), outline=COLORS.border)

    art_h = h - name_h
    cover_art(painter, art, game, (x + 1, y + 1, w - 2, art_h))

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
#: accumulated from the *scaled* widths or the gaps grow without bound, and each
#: card is painted at its scaled size (not the full card_w) or they overlap.
_SCALE = (1.0, 0.78, 0.64, 0.52)
_OPACITY = (255, 175, 125, 90)

#: ``(id(bitmap), w, h, opacity) -> dimmed bitmap`` -- the carousel re-dims the
#: same neighbour covers on every frame, so cache the faded copies.
_DIM_CACHE: dict[tuple[int, int, int, int], object] = {}


def draw_carousel(
    painter: Painter,
    art: ArtProvider,
    games: list[Game],
    index: int,
    *,
    highlight: bool = True,
    only: int | None = None,
) -> int:
    m = painter.metrics
    single = _single(painter)
    card_h = m.carousel_card_h(single=single)
    card_w = m.carousel_card_w(single=single)
    gap = m.carousel_gap
    top = m.content_top + m.u(6)

    widths = [round(card_w * scale) for scale in _SCALE]
    heights = [round(card_h * scale) for scale in _SCALE]
    offsets = [0]
    for k in range(1, len(_SCALE)):
        offsets.append(offsets[-1] + (widths[k - 1] + widths[k]) // 2 + gap)

    content_h = m.content_h(single=single)
    banner_h = content_h - (card_h + m.u(10)) - m.u(6)
    from_center = m.width // 2

    first = max(0, index - 3)
    last = min(len(games) - 1, index + 3)
    for position in range(first, last + 1):
        if only is not None and position != only:
            continue
        offset = abs(position - index)
        opacity = _OPACITY[offset]
        side = 1 if position > index else (-1 if position < index else 0)
        cw, ch = widths[offset], heights[offset]
        cx = from_center + side * offsets[offset]
        box = (cx - cw // 2, top + (card_h - ch) // 2, cw, ch)
        _cover_card(painter, art, games[position], box,
                    selected=highlight and offset == 0, opacity=opacity, logo_ratio=0.72)

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
        fill=panel_fill(painter),
        outline=COLORS.accent if selected else COLORS.border,
        width=2 if selected else 1,
    )

    # No logo overlay here: the banner below the carousel already shows one for
    # the selected game, and repeating it on every card just covered the art.
    cover_art(painter, art, game, (x + 1, y + 1, w - 2, h - 2), opacity=opacity)

    if game.favorite:
        painter.text((x + w - m.u(8), y + m.u(10)), _STAR, size=13, fill=COLORS.accent, anchor="rm")


def _single(painter: Painter) -> bool:
    """Whether the top screen is in single-screen (split) mode."""
    return bool(getattr(painter, "single", False))


def draw_scrollbar(
    painter: Painter,
    index: int,
    total: int,
    visible: int,
    content_h: int,
) -> None:
    """Repaint only the scrollbar -- the rows/cells around it are unchanged."""
    scrollbar(painter, index=index, total=total, visible=visible, content_h=content_h)
