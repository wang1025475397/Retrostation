"""Bottom screen: media area + logo strip + metadata panel (DESIGN §7.2)."""

from __future__ import annotations

from dataclasses import dataclass

from ..art import ArtProvider
from ..painter import Painter
from ..widgets import logo_banner
from ...core.model import ASSET_VIDEO, Game
from ...core.theme import COLORS

_STAR = "★"


@dataclass(frozen=True)
class Meta:
    """Metadata-panel content, translated by the app before it gets here."""

    system_label: str
    publisher: str
    rating_stars: int
    rating_value: str
    genre: str
    players: str
    release: str
    core: str
    description: str
    play_count: str
    last_played: str
    source_note: str
    favorite: bool


def draw(painter: Painter, art: ArtProvider, game: Game | None, meta: Meta | None, *,
         key_label: str, hints: list[tuple[str, str]], playing_video: bool) -> None:
    m = painter.metrics
    painter.clear()
    _title_bar(painter, meta, key_label)

    if game is None or meta is None:
        painter.text((m.width // 2, m.height // 2), painter.translator("games.empty"),
                     size=14, fill=(74, 74, 80, 255), anchor="mm")
        _hints(painter, hints)
        return

    top = m.bottom_title_h + m.body_padding
    media_box = (m.u(12), top, m.media_w, m.media_h)
    _media(painter, art, game, media_box, playing_video)
    logo_banner(painter, game, (m.u(12), top + m.media_h + m.u(8), m.media_w, m.logo_strip_h))

    _meta(painter, meta, (m.u(12) + m.media_w + m.body_gap, top, m.meta_w, m.bottom_body_h()))
    _hints(painter, hints)


def _title_bar(painter: Painter, meta: Meta | None, key_label: str) -> None:
    m = painter.metrics
    painter.rect((0, 0, m.width, m.bottom_title_h), fill=COLORS.panel)
    painter.rect((0, m.bottom_title_h - 1, m.width, 1), fill=COLORS.border)
    x = m.u(14)
    if meta is not None and meta.favorite:
        painter.rounded_rect((x, m.u(7), m.u(64), m.u(18)), radius=m.u(9),
                             fill=(232, 163, 61, 36), outline=(232, 163, 61, 90))
        painter.text((x + m.u(32), m.u(16)), _STAR + " " + painter.translator("btn.favorite"),
                     size=10, fill=COLORS.accent, anchor="mm")
        x += m.u(74)
    painter.text((x, m.bottom_title_h // 2), f"{key_label} · {painter.translator('bottom.detail')}",
                 size=14, fill=COLORS.text, anchor="lm")


def _media(painter: Painter, art: ArtProvider, game: Game,
           box: tuple[int, int, int, int], playing: bool) -> None:
    m = painter.metrics
    x, y, w, h = box
    painter.rounded_rect(box, radius=m.u(8), fill=(14, 14, 16, 255),
                         outline=(232, 163, 61, 90) if playing else COLORS.border)
    inset = m.u(4)
    inner = (x + inset, y + inset, w - 2 * inset, h - 2 * inset)
    bitmap = art.thumbnail(game, inner[2], inner[3]) or art.placeholder(game.key, inner[2], inner[3])
    painter.image_fit(bitmap, inner)
    if playing and game.has_asset(ASSET_VIDEO):
        painter.rect((x, y + h - m.u(3), w, m.u(3)), fill=(0, 0, 0, 140))
        painter.rect((x, y + h - m.u(3), w // 3, m.u(3)), fill=COLORS.accent)


def _meta(painter: Painter, meta: Meta, box: tuple[int, int, int, int]) -> None:
    m = painter.metrics
    x, _, w, _ = box
    inner = w - m.u(4)
    y = box[1]

    painter.text((x, y), painter.ellipsize(f"{meta.system_label} · {meta.publisher}",
                                           size=13, max_width=w), size=13, fill=COLORS.text_dim, anchor="la")
    y += m.u(21)
    filled, empty = _STAR * meta.rating_stars, _STAR * (5 - meta.rating_stars)
    painter.text((x, y), filled, size=15, fill=COLORS.accent, anchor="la")
    if empty:
        painter.text((x + painter.text_width(filled, size=15), y), empty, size=15,
                     fill=(62, 62, 68, 255), anchor="la")
    painter.text((x + painter.text_width(filled + empty, size=15) + m.u(8), y + 1),
                 meta.rating_value, size=13, fill=COLORS.accent, anchor="la")
    y += m.u(26)

    y = _divider(painter, x, y, inner)
    rows = ((painter.translator("bottom.genre"), meta.genre),
            (painter.translator("bottom.players"), meta.players),
            (painter.translator("bottom.release"), meta.release),
            (painter.translator("bottom.core"), meta.core))
    for label, value in rows:
        painter.text((x, y + m.u(2)), label, size=12, fill=(106, 106, 114, 255), anchor="la")
        painter.text((x + m.u(58), y), painter.ellipsize(value, size=12, max_width=inner - m.u(58)),
                     size=12, fill=COLORS.text, anchor="la")
        y += m.u(17)

    y = _divider(painter, x, y + m.u(6), inner)
    painter.text((x, y), painter.translator("bottom.desc").upper(), size=10,
                 fill=(106, 106, 114, 255), anchor="la")
    y += m.u(15)
    for line in painter.wrap_text(meta.description, size=12, max_width=inner, max_lines=4):
        painter.text((x, y), line, size=12, fill=COLORS.text_dim, anchor="la")
        y += m.u(19)

    y = _divider(painter, x, y + m.u(4), inner)
    painter.text((x, y), meta.play_count, size=12, fill=COLORS.accent, anchor="la")
    painter.text((x + m.u(80), y), meta.last_played, size=12, fill=COLORS.text_dim, anchor="la")
    y += m.u(19)
    painter.text((x, y), meta.source_note, size=10, fill=(92, 92, 99, 255), anchor="la")


def _divider(painter: Painter, x: int, y: int, width: int) -> int:
    painter.rect((x, y, width, 1), fill=COLORS.border)
    return y + m_divider_gap(painter)


def m_divider_gap(painter: Painter) -> int:
    return painter.metrics.u(8)


def _hints(painter: Painter, hints: list[tuple[str, str]]) -> None:
    from ..widgets import button_bar
    button_bar(painter, hints)
