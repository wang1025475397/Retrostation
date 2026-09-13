"""Touch in the settings dialog: the controls a finger actually uses.

A phone has no arrow keys, so every row carries its own control -- a switch, an
option list, a pair of stepper buttons -- and the dialog carries cancel/confirm.
Three bugs lived here, and each one is pinned below:

* the stepper marks were drawn *inside the row's value cell*, so the "−" itself
  sat in the right half of the row: every tap added;
* the numeric rows (背光/预览音量/按键音量) were missing from the set the touch
  path consults, so they read as read-only and a tap on them did nothing;
* cancel/confirm were drawn and their boxes recorded, but nothing read them --
  and the on-screen pad, drawn across the same spot, answered instead.

The boxes come from the dialog that drew them (``dialog_hits``,
``dialog_buttons``, ``dialog_steppers``): the test taps what is on screen, so a
control drawn somewhere other than where it is hit-tested fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.data.library import Library
from retrostation.platform.base import InputAction, InputEvent, InputKind
from retrostation.ui.app import App
from retrostation.ui.session import MODAL_MENU, MODAL_NONE
from tests.conftest import FakePlatform

Box = tuple[int, int, int, int]


@pytest.fixture
def app(rom_root: Path) -> App:
    platform = FakePlatform(rom_root)
    config = Config()
    library = Library(platform, config)
    library.scan()
    return App(platform, config, Translator(config.language), library)


@pytest.fixture
def menu(app: App) -> App:
    """The settings dialog, open and drawn once, so its boxes are known."""
    app._handle(InputEvent(InputAction.START))
    app.run(max_frames=1)
    assert app.session.modal == MODAL_MENU
    return app


def tap(app: App, box: Box) -> None:
    """A finger on the middle of ``box``, then a frame to let it redraw."""
    x, y, w, h = box
    app._handle(InputEvent(action=InputAction.TAP, kind=InputKind.PRESS,
                           x=x + w // 2, y=y + h // 2))
    app.run(max_frames=1)


def drag_rows(app: App, rows: int) -> None:
    """One drag of ``rows`` row heights; positive walks down the list."""
    pitch = app.session._dialog_pitch()
    app._handle(InputEvent(action=InputAction.DRAG, kind=InputKind.PRESS,
                           x=1, y=1, dy=-pitch * rows))
    app.run(max_frames=1)


def row_index(app: App, key: str) -> int:
    rows = app.session.menu_rows()
    return next(i for i, (row_key, _label, _value) in enumerate(rows) if row_key == key)


def bring_into_view(app: App, key: str) -> None:
    """Drag the list until the row keyed ``key`` is on screen.

    The window does not follow the cursor -- a drag moves the *content* -- so a
    row further down the list is only reachable, and only tappable, after
    scrolling to it.
    """
    session = app.session
    for _ in range(len(session.menu_rows())):
        index = row_index(app, key)
        start, visible = session.dialog_window
        if start <= index < start + visible:
            return
        drag_rows(app, 1 if index >= start + visible else -1)
    raise AssertionError(f"{key} never came into view")


def row_box(app: App, key: str) -> Box:
    """The drawn row of the settings row keyed ``key``."""
    index = row_index(app, key)
    return next(box for box, row in app.session.dialog_hits if row == index)


def steppers(app: App, key: str) -> dict[int, Box]:
    """The two stepper boxes of a stepped row, keyed by the step they apply."""
    rows = app.session.menu_rows()
    return {direction: box
            for box, index, direction in app.session.dialog_steppers
            if rows[index][0] == key}


def buttons(app: App) -> tuple[Box, Box]:
    """The dialog's bottom row: cancel first, then confirm."""
    _cancel, confirm = sorted(app.session.dialog_buttons, key=lambda entry: entry[1])
    return _cancel[0], confirm[0]


def value(app: App, key: str) -> str:
    return app.session._menu_row_value(key)


class TestSteppers:
    @pytest.mark.parametrize("key", ["brightness", "video_volume", "sfx_volume"])
    def test_both_marks_step_their_own_way(self, menu: App, key: str) -> None:
        # These three used to be "info" rows to the touch path: a tap on them did
        # nothing, while the one numeric row that *was* listed answered every tap
        # with a "+".
        bring_into_view(menu, key)
        marks = steppers(menu, key)
        assert set(marks) == {-1, 1}
        start = int(value(menu, key))
        tap(menu, marks[1])
        assert int(value(menu, key)) > start
        tap(menu, marks[-1])
        assert int(value(menu, key)) == start

    def test_the_marks_sit_inside_the_row_they_belong_to(self, menu: App) -> None:
        # Drawn where they are hit-tested: a mark inside the value cell (which is
        # the row's right half) is what made every tap add.
        bring_into_view(menu, "video_volume")
        rows = {index: box for box, index in menu.session.dialog_hits}
        marked = [entry for entry in menu.session.dialog_steppers
                  if menu.session.menu_rows()[entry[1]][0] == "video_volume"]
        assert len(marked) == 2
        for box, index, _direction in marked:
            row = rows[index]
            assert row[0] <= box[0] and box[0] + box[2] <= row[0] + row[2]
            assert row[1] <= box[1] and box[1] + box[3] <= row[1] + row[3]

    def test_the_rest_of_the_row_changes_nothing(self, menu: App) -> None:
        # A tap on the label is not a tap on a mark: the row is not a slider.
        bring_into_view(menu, "brightness")
        row = row_box(menu, "brightness")
        before = value(menu, "brightness")
        tap(menu, (row[0] + 8, row[1], row[2] // 4, row[3]))
        assert value(menu, "brightness") == before


class TestDialogButtons:
    def test_cancel_puts_the_pass_back(self, menu: App) -> None:
        bring_into_view(menu, "brightness")
        start = int(value(menu, "brightness"))
        tap(menu, steppers(menu, "brightness")[1])
        assert int(value(menu, "brightness")) > start
        cancel, _confirm = buttons(menu)
        tap(menu, cancel)
        assert menu.session.modal == MODAL_NONE
        assert int(value(menu, "brightness")) == start

    def test_confirm_keeps_the_pass_and_closes(self, menu: App) -> None:
        bring_into_view(menu, "brightness")
        tap(menu, steppers(menu, "brightness")[1])
        stepped = int(value(menu, "brightness"))
        _cancel, confirm = buttons(menu)
        tap(menu, confirm)
        assert menu.session.modal == MODAL_NONE
        assert int(value(menu, "brightness")) == stepped
        # The pass is closed out: nothing is left to roll the value back to (the
        # app consumes ``settings_dirty`` as it saves, so that flag is already
        # False again by the time the frame is done).
        assert menu.session._menu_stash is None

    def test_confirm_is_not_a_press_of_a(self, menu: App) -> None:
        # "清除缓存" is an action row: A on it clears.  Confirm only commits the
        # pass the steppers staged -- a stray tap must not empty the card.
        for _ in range(len(menu.session.menu_rows())):
            if menu.session.menu_rows()[menu.session.menu_index][0] == "clear_cache":
                break
            menu._handle(InputEvent(InputAction.DOWN))
        assert menu.session.menu_rows()[menu.session.menu_index][0] == "clear_cache"
        _cancel, confirm = buttons(menu)
        tap(menu, confirm)
        assert menu.session.modal == MODAL_NONE
        # Nothing was cleared: no "clearing the cache" toast was raised.
        assert not menu.session.active_toast()


class TestPicker:
    def test_a_multi_value_row_opens_its_options(self, menu: App) -> None:
        bring_into_view(menu, "language")
        tap(menu, row_box(menu, "language"))
        assert menu.session.menu_choice_labels
        # The list is drawn over the dialog rather than replacing it: closing it
        # hands input straight back to the settings rows.
        assert menu.session.modal == MODAL_MENU

    def test_picking_an_option_steps_the_row_to_it(self, menu: App) -> None:
        bring_into_view(menu, "language")
        tap(menu, row_box(menu, "language"))
        session = menu.session
        labels = list(session.menu_choice_labels)
        wanted = (session.menu_choice_index + 1) % len(labels)
        option = next(box for box, index in session.dialog_hits if index == wanted)
        tap(menu, option)
        assert session.menu_choice_options is None
        assert session._menu_row_value("language") == labels[wanted]

    def test_its_cancel_button_closes_without_changing_anything(self, menu: App) -> None:
        bring_into_view(menu, "language")
        before = value(menu, "language")
        tap(menu, row_box(menu, "language"))
        cancel = next(box for box, _index in menu.session.dialog_buttons)
        tap(menu, cancel)
        assert menu.session.menu_choice_options is None
        assert value(menu, "language") == before


class TestDrag:
    def test_a_drag_scrolls_the_list_without_walking_the_cursor(self, menu: App) -> None:
        # The cursor belongs to the keyboard: dragging a list should move the
        # list, which is what made the menu feel wrong under a finger.
        session = menu.session
        before = session.menu_index
        rows = session.menu_rows()
        start, visible = session.dialog_window
        drag_rows(menu, 2)
        assert session.menu_index == before
        assert session.menu_top == max(0, min(max(0, len(rows) - visible), start + 2))

    def test_a_drag_of_a_row_scrolls_by_that_row(self, menu: App) -> None:
        session = menu.session
        start, visible = session.dialog_window
        drag_rows(menu, -1)
        assert session.menu_top == max(0, min(max(0, len(session.menu_rows()) - visible),
                                              start - 1))


class TestDialogOutranksThePad:
    def test_a_tap_on_the_dialog_is_left_to_the_dialog(self, menu: App) -> None:
        # The pad is an overlay drawn *after* the dialog, so its SELECT/START
        # boxes land on the dialog's own buttons.  The pad must not answer there
        # (it also arms a window that swallows the tap that follows).
        _cancel, confirm = buttons(menu)
        x, y, w, h = confirm
        on_button = InputEvent(action=InputAction.TAP, kind=InputKind.PRESS,
                               x=x + w // 2, y=y + h // 2)
        assert menu._dialog_owns(on_button) is True
        assert menu._dialog_owns(InputEvent(action=InputAction.TAP, kind=InputKind.PRESS,
                                            x=1, y=1)) is False
        # Nothing open, nothing owned -- the pad keeps its whole surface back.
        menu.session.modal = MODAL_NONE
        assert menu._dialog_owns(on_button) is False
