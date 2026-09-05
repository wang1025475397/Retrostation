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
    options = session.exit_options()
    dialog(
        painter,
        title=painter.translator("dialog.exit_title"),
        body=painter.translator("dialog.exit_body"),
        rows=[(label, "") for _key, label in options],
        selected=session.exit_selected,
    )


def draw_rom_select(painter: Painter, session: Session) -> None:
    """Picker for the files of a multi-file game (arcade hacks/clones, discs)."""
    game = session.rom_select_game
    paths = session.rom_select_paths
    rows = []
    for index, path in enumerate(paths):
        label = path.name
        if game is not None and path == game.path:
            label = f"{painter.translator('rom_select.primary')} · {label}"
        rows.append((label, ""))
    dialog(
        painter,
        title=painter.translator("rom_select.title"),
        rows=rows,
        selected=session.rom_select_index,
    )
