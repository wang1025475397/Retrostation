"""Bottom screen: media area + logo strip + metadata panel (DESIGN §7.2)."""

from __future__ import annotations

from dataclasses import dataclass

from ..art import ArtProvider
from ..painter import Painter
from ..widgets import logo_banner
from ...core.model import Game
from ...core.theme import COLORS
from .games import cover_art

_STAR = "★"

#: Gap between the media box frame and the artwork it contains.
MEDIA_INSET = 4


@dataclass(frozen=True)
class Meta:
    """Metadata-panel content, translated by the app before it gets here."""

    name: str
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
         key_label: str, hints: list[tuple[str, str]],
         video_frame=None, video_progress: float | None = None,
         clip_pending: bool = False, system_desc: str = "",
         game_count: int | None = None) -> None:
    m = painter.metrics
    painter.clear()
    _title_bar(painter, meta, key_label, game_count)

    if game is None or meta is None:
        # 平台总览（游戏库）：显示当前平台的介绍（多语言 desc），而非内部游戏。
        # games.empty 只在进入某个平台、且该平台确实没有任何游戏时才出现。
        desc = (system_desc or "").strip()
        if desc:
            y = m.bottom_title_h + m.body_padding
            for line in painter.wrap_text(desc, size=13,
                                          max_width=m.width - 2 * m.u(12), max_lines=10):
                painter.text((m.u(12), y), line, size=13, fill=COLORS.text_dim, anchor="la")
                y += m.u(22)
        else:
            painter.text((m.width // 2, (m.bottom_title_h + m.height) // 2),
                         painter.translator("home.empty"),
                         size=14, fill=(74, 74, 80, 255), anchor="mm")
        _hints(painter, hints)
        return

    top = m.bottom_title_h + m.body_padding
    media_box = (m.u(12), top, m.media_w, m.media_h)
    _media(painter, art, game, media_box, video_frame, video_progress, clip_pending)
    logo_banner(painter, art, game, (m.u(12), top + m.media_h + m.u(8), m.media_w, m.logo_strip_h))

    _meta(painter, meta, (m.u(12) + m.media_w + m.body_gap, top, m.meta_w, m.bottom_body_h()))
    _hints(painter, hints)


def _title_bar(painter: Painter, meta: Meta | None, key_label: str,
               game_count: int | None = None) -> None:
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
    # 平台总览（无游戏选中）时标题栏显示该平台的游戏数量。
    if meta is None and game_count is not None:
        label = f"{key_label} · {painter.translator('bottom.game_count', count=game_count)}"
    else:
        label = f"{key_label} · {painter.translator('bottom.detail')}"
    painter.text((x, m.bottom_title_h // 2), label,
                 size=14, fill=COLORS.text, anchor="lm")


def media_inner_size(m) -> tuple[int, int]:
    """Size a video frame must be decoded at to fill the media box as drawn.

    Decoding at exactly this size means :meth:`Canvas.image_fit` only pastes,
    with no per-frame resize -- worth it at 15 fps on a 1.5 GHz core.
    """
    inset = m.u(MEDIA_INSET)
    return (m.media_w - 2 * inset, m.media_h - 2 * inset)


def _media(painter: Painter, art: ArtProvider, game: Game,
           box: tuple[int, int, int, int], frame, progress: float | None,
           pending: bool = False) -> None:
    m = painter.metrics
    x, y, w, h = box
    playing = frame is not None
    painter.rounded_rect(box, radius=m.u(8), fill=(14, 14, 16, 255),
                         outline=(232, 163, 61, 90) if playing else COLORS.border)
    inset = m.u(MEDIA_INSET)
    inner = (x + inset, y + inset, w - 2 * inset, h - 2 * inset)
    # Video first, then the cover (DESIGN §6.5).  With neither, say so: a
    # generated tile read as artwork the game happened to have.  ``pending``
    # covers the moment before the first frame arrives -- showing the cover
    # then flashed it on every game that has a clip.
    if frame is not None:
        painter.image_fit(frame, inner)
    elif not pending:
        cover_art(painter, art, game, inner)
    if playing:
        progress_bar(painter, x, y + h - m.u(3), w, m.u(3), progress)


def progress_bar(painter: Painter, x: int, y: int, w: int, h: int,
                 progress: float | None) -> None:
    """3px bar under a playing clip; plain track when the length is unknown.

    Public because the single-screen detail strip draws its own clip and
    wants the same bar.
    """
    m = painter.metrics
    painter.rect((x, y, w, h), fill=(0, 0, 0, 140))
    if progress is None:
        return
    filled = max(m.u(2), round(w * min(1.0, max(0.0, progress))))
    painter.rect((x, y, filled, h), fill=COLORS.accent)


def _meta(painter: Painter, meta: Meta, box: tuple[int, int, int, int]) -> None:
    m = painter.metrics
    x, _, w, _ = box
    inner = w - m.u(4)
    y = box[1]

    # The panel named the system and the publisher but never the game itself --
    # it only ever appeared baked into the cover or logo artwork, which is
    # missing, or in a script you cannot read, often enough to matter.
    painter.text((x, y), painter.ellipsize(meta.name, size=16, max_width=inner),
                 size=16, fill=COLORS.text, anchor="la")
    y += m.u(23)
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
