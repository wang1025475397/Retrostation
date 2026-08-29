"""Settings and exit dialogs, drawn on the top screen."""

from __future__ import annotations

from ..painter import Painter
from ..session import Session
from ..widgets import dialog


def draw(painter: Painter, session: Session) -> None:
    rows = session.menu_rows()
    dialog(
        painter,
        title=painter.translator("menu.title"),
        rows=[(label, value) for _key, label, value in rows],
        selected=session.menu_index,
    )


def draw_exit(painter: Painter, session: Session) -> None:
    dialog(
        painter,
        title=painter.translator("dialog.exit_title"),
        body=painter.translator("dialog.exit_body"),
    )
