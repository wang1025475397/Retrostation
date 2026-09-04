"""Search overlay: query line, live results, on-screen letter grid.

SELECT+START opens it and the same combo closes it.  The grid is ABC-ordered
because a d-pad user scans alphabetically, and every keypress refilters the
results -- there is no confirm step.  Result rows reuse the game list's row
painter, so covers and the selection style stay consistent with browsing.
"""

from __future__ import annotations

from ...core.theme import COLORS
from ..painter import Painter
from ..session import SEARCH_CODES, SEARCH_COLS, Session
from . import games

#: Overlay that lets the game list show through, like the dialogs do.
_SCRIM = (0, 0, 0, 158)

#: i18n keys for the three action keys; single characters label themselves.
_KEY_LABELS = {
    "BS": "search.key_bs",
    "CLR": "search.key_clear",
    "OFF": "search.key_off",
}

_KEY_ROWS = (len(SEARCH_CODES) + SEARCH_COLS - 1) // SEARCH_COLS


def draw(painter: Painter, art, session: Session) -> None:
    m = painter.metrics
    translator = painter.translator
    painter.rect((0, 0, m.width, m.height), fill=_SCRIM)

    key_h = m.u(30)
    kb_h = _KEY_ROWS * key_h + (_KEY_ROWS - 1) * m.u(4)
    kb_top = m.height - m.u(10) - kb_h

    _draw_query(painter, session)

    results = session.search_results()
    rpp = max(1, (kb_top - m.u(8) - m.content_top) // m.row_step)
    if results:
        games.draw_list(
            painter, art, results, session.search_result_index,
            rows_per_page=rpp,
            highlight=session.search_focus == "results",
        )
        games.draw_scrollbar(
            painter, session.search_result_index, len(results), rpp,
            kb_top - m.u(8) - m.content_top,
        )
    else:
        painter.text(
            (m.width // 2, (m.content_top + kb_top) // 2),
            translator("search.empty"),
            size=14, fill=COLORS.text_dim, anchor="mm",
        )

    _draw_keyboard(painter, session, kb_top, key_h)


def _draw_query(painter: Painter, session: Session) -> None:
    m = painter.metrics
    # Below the status strip (time / battery), which is painted after this
    # overlay and would otherwise cover the query line.
    top = m.status_h + m.u(6)
    height = max(m.u(20), min(m.u(36), m.content_top - top - m.u(4)))
    box = (m.u(8), top, m.width - m.u(16), height)
    # Accent border while the keyboard owns the input; plain otherwise.
    outline = COLORS.accent if session.search_focus == "kb" else COLORS.border
    painter.rounded_rect(box, radius=m.u(6), fill=COLORS.panel, outline=outline)
    middle = top + height // 2
    text = painter.ellipsize(session.search_text, size=15, max_width=m.width - m.u(140))
    painter.text((m.u(16), middle), text + "|", size=15, fill=COLORS.text, anchor="lm")
    hits = len(session.search_results())
    if hits:
        painter.text(
            (m.width - m.u(16), middle), str(hits),
            size=11, fill=COLORS.text_dim, anchor="rm",
        )


def _draw_keyboard(painter: Painter, session: Session, top: int, key_h: int) -> None:
    m = painter.metrics
    translator = painter.translator
    key_w = (m.width - m.u(16) - m.u(4) * (SEARCH_COLS - 1)) // SEARCH_COLS
    for index, code in enumerate(SEARCH_CODES):
        row, col = divmod(index, SEARCH_COLS)
        box = (
            m.u(8) + col * (key_w + m.u(4)),
            top + row * (key_h + m.u(4)),
            key_w,
            key_h,
        )
        selected = session.search_focus == "kb" and index == session.search_kb
        if selected:
            painter.rounded_rect(box, radius=m.u(5), fill=COLORS.accent)
            fill = (26, 18, 6, 255)
        else:
            painter.rounded_rect(box, radius=m.u(5), fill=COLORS.panel_2,
                                 outline=COLORS.border)
            fill = COLORS.text
        label = translator(_KEY_LABELS[code]) if code in _KEY_LABELS else code
        size = 13 if len(label) == 1 else 9
        painter.text(
            (box[0] + key_w // 2, box[1] + key_h // 2),
            label, size=size, fill=fill, anchor="mm",
        )
