"""KeyEvent -> semantic :class:`InputAction` mapping (DESIGN.ANDROID §10.2).

The constants are the integer values of ``android.view.KeyEvent`` so the Kotlin
``InputBridge`` can hand us raw key codes with no per-event object churn.  Touch
gestures (``TAP`` / ``DRAG`` / ``FLING``) are produced by the Kotlin side from
``MotionEvent`` and do not appear in this table -- they never have a key code.

This module is pure data + a lookup; it imports nothing Android-specific and is
exercised directly by the desktop test suite.
"""

from __future__ import annotations

from ..base import InputAction

# android.view.KeyEvent key code values (stable across API levels).
_DPAD_UP = 19
_DPAD_DOWN = 20
_DPAD_LEFT = 21
_DPAD_RIGHT = 22
_DPAD_CENTER = 23
_ENTER = 66
_BUTTON_A = 96
_BUTTON_B = 97
_BUTTON_X = 99
_BUTTON_Y = 100
_BUTTON_L1 = 102
_BUTTON_R1 = 103
_BUTTON_L2 = 104
_BUTTON_R2 = 105
_BUTTON_START = 108
_BUTTON_SELECT = 109
_BUTTON_MODE = 110
_BACK = 4
_MENU = 82
_VOLUME_UP = 24
_VOLUME_DOWN = 25


#: Key code -> semantic action.  Several physical keys collapse onto ``A`` /
#: ``START`` because that is what the handheld keymap already does.
KEYMAP: dict[int, InputAction] = {
    _DPAD_UP: InputAction.UP,
    _DPAD_DOWN: InputAction.DOWN,
    _DPAD_LEFT: InputAction.LEFT,
    _DPAD_RIGHT: InputAction.RIGHT,
    _BUTTON_A: InputAction.A,
    _DPAD_CENTER: InputAction.A,
    _ENTER: InputAction.A,
    _BUTTON_B: InputAction.B,
    _BACK: InputAction.B,  # system back doubles as "return"; see §10.2 pitfall
    _BUTTON_X: InputAction.X,
    _BUTTON_Y: InputAction.Y,
    _BUTTON_L1: InputAction.L1,
    _BUTTON_R1: InputAction.R1,
    _BUTTON_L2: InputAction.L2,
    _BUTTON_R2: InputAction.R2,
    _BUTTON_START: InputAction.START,
    _MENU: InputAction.START,
    _BUTTON_SELECT: InputAction.SEARCH,
    _VOLUME_UP: InputAction.VOLUME_UP,
    _VOLUME_DOWN: InputAction.VOLUME_DOWN,
    _BUTTON_MODE: InputAction.MENU,  # long press = quit
}


def keymap_action(keycode: int) -> InputAction | None:
    """Map an Android key code to a semantic action, or ``None`` if unknown."""
    return KEYMAP.get(keycode)


def keymap_action_name(keycode: int) -> str | None:
    """The mapped action's *value* as a plain string, for the Kotlin bridge.

    Kotlin reads the result with ``toString()``: on a ``str``-mixin enum that
    yields ``"InputAction.B"`` rather than ``"b"``, which the Python side then
    rejects.  Handing back ``action.value`` keeps the boundary plain strings.
    """
    action = KEYMAP.get(keycode)
    return action.value if action is not None else None
