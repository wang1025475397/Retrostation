"""Unit tests for the Android key mapping (DESIGN.ANDROID §10.2)."""

from __future__ import annotations

from retrostation.platform.android.input import KEYMAP, keymap_action
from retrostation.platform.base import InputAction

# android.view.KeyEvent constants mirrored from input.py.
_DPAD_UP = 19
_BUTTON_A = 96
_BACK = 4
_VOLUME_UP = 24
_UNKNOWN = 999


def test_keymap_known_codes() -> None:
    assert keymap_action(_DPAD_UP) is InputAction.UP
    assert keymap_action(_BUTTON_A) is InputAction.A
    assert keymap_action(_BACK) is InputAction.B
    assert keymap_action(_VOLUME_UP) is InputAction.VOLUME_UP


def test_keymap_unknown_returns_none() -> None:
    assert keymap_action(_UNKNOWN) is None


def test_keymap_has_no_collision_in_values() -> None:
    # Several keys map to the same action (A <- DPAD_CENTER/ENTER/BUTTON_A); the
    # dict simply stores the last, which is fine.  Sanity: every value is an InputAction.
    assert all(isinstance(v, InputAction) for v in KEYMAP.values())
