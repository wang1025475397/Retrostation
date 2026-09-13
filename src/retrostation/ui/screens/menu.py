"""Settings and exit dialogs, drawn on the top screen."""

from __future__ import annotations

from ..painter import Painter
from ..session import Session
from ..widgets import dialog


def draw(painter: Painter, session: Session) -> None:
    if getattr(session, "menu_choice_options", None):
        _draw_choice(painter, session)
        return
    rows = session.menu_rows()
    # Keep the drawn rows and the bottom buttons: the session hit-tests a tap
    # against them, and scrolls the window on a drag (the dialog is the only
    # place that knows the geometry).  A touch device gets explicit buttons --
    # there is no A to commit and no B to cancel.
    # The rows a finger can step are handed to the dialog, which draws their two
    # buttons and gives back the boxes -- the value cell no longer carries the
    # marks, because a mark drawn away from its hit box is a mark that lies.
    steppers = frozenset(index for index, (key, _label, _value) in enumerate(rows)
                         if session._menu_row_kind(key) == "cycle")
    session.dialog_hits, session.dialog_buttons, session.dialog_steppers = dialog(
        painter,
        title=painter.translator("menu.title"),
        rows=[(label, value) for _key, label, value in rows],
        selected=session.menu_index,
        top=getattr(session, "menu_top", None),
        buttons=(painter.translator("menu.cancel"), painter.translator("menu.confirm")),
        steppers=steppers,
    )
    session.dialog_window = getattr(painter, "dialog_window", None)


def _draw_choice(painter: Painter, session: Session) -> None:
    """A row's options as a radio list: the touch stand-in for left/right.

    Tapping an option picks it; anything else dismisses the list, which is what
    the cancel button at the bottom does.
    """
    rows = [(label, "●" if index == session.menu_choice_index else "")
            for index, label in enumerate(session.menu_choice_labels)]
    session.dialog_hits, session.dialog_buttons, session.dialog_steppers = dialog(
        painter,
        title=session.menu_choice_title,
        rows=rows,
        selected=session.menu_choice_index,
        buttons=(painter.translator("menu.cancel"),),
    )
    session.dialog_window = getattr(painter, "dialog_window", None)


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
