"""Input layer tests.

Everything here exists because of a bug that shipped: on the RG DS *no* button
did what the player expected.  Two independent causes, both caught by these
tests:

* the key map assumed a Nintendo layout, but this kernel puts A on BTN_SOUTH,
  B on BTN_EAST, **Y on BTN_C** and starts the shoulders at BTN_WEST -- so
  "confirm" was bound to the back button;
* d-pad hat axes were handled as if any non-zero value meant one direction,
  which made RIGHT and DOWN unreachable while LEFT and UP fired either way.

The tests feed raw ``struct input_event`` bytes in, so they exercise the real
byte-parsing path rather than calling the private helpers directly.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from retrostation.platform.base import InputAction, InputEvent, InputKind
from retrostation.platform.linux import input as input_mod
from retrostation.platform.linux.input import (
    DEFAULT_KEYMAP,
    HAT_AXES,
    EvdevInput,
    _device_codes,
    find_key_device,
)

_EVENT = struct.Struct("llHHi")
EV_KEY = 0x01
EV_ABS = 0x03


def raw(etype: int, code: int, value: int) -> bytes:
    return _EVENT.pack(0, 0, etype, code, value)


def feed(reader: EvdevInput, *events: bytes) -> EvdevInput:
    for blob in events:
        reader._handle_raw(blob)
    return reader


def presses(reader: EvdevInput) -> list[InputAction]:
    return [e.action for e in reader.poll_events() if e.kind is InputKind.PRESS]


def releases(reader: EvdevInput) -> list[InputAction]:
    return [e.action for e in reader.poll_events() if e.kind is InputKind.RELEASE]


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> EvdevInput:
    """A reader with no device attached, so nothing is opened or threaded."""
    monkeypatch.setattr(input_mod, "find_key_device", lambda *a, **k: None)
    return EvdevInput()


class TestDpadHatAxes:
    """The bug: a hat axis carries a sign, and dropping it loses half the pad."""

    @pytest.mark.parametrize("axis", sorted(HAT_AXES))
    def test_every_axis_has_two_distinct_directions(self, axis: int) -> None:
        negative, positive = HAT_AXES[axis]
        assert negative is not positive

    def test_hat0x_left_then_right(self, reader: EvdevInput) -> None:
        feed(reader, raw(EV_ABS, 0x10, -1))
        assert presses(reader) == [InputAction.LEFT]

        feed(reader, raw(EV_ABS, 0x10, 0))
        feed(reader, raw(EV_ABS, 0x10, 1))
        assert presses(reader) == [InputAction.RIGHT]

    def test_hat0y_up_then_down(self, reader: EvdevInput) -> None:
        feed(reader, raw(EV_ABS, 0x11, -1))
        assert presses(reader) == [InputAction.UP]

        feed(reader, raw(EV_ABS, 0x11, 0))
        feed(reader, raw(EV_ABS, 0x11, 1))
        assert presses(reader) == [InputAction.DOWN]

    def test_hat1_axes_are_supported_too(self, reader: EvdevInput) -> None:
        feed(reader, raw(EV_ABS, 0x12, 1))
        assert presses(reader) == [InputAction.RIGHT]

        feed(reader, raw(EV_ABS, 0x12, 0))
        feed(reader, raw(EV_ABS, 0x13, -1))
        assert presses(reader) == [InputAction.UP]

    def test_centring_releases_both_directions(self, reader: EvdevInput) -> None:
        feed(reader, raw(EV_ABS, 0x10, -1))
        reader.poll_events()

        feed(reader, raw(EV_ABS, 0x10, 0))
        assert set(releases(reader)) == {InputAction.LEFT, InputAction.RIGHT}

    def test_a_held_axis_does_not_repeat(self, reader: EvdevInput) -> None:
        """The kernel re-reports the leaning value; that is not a new press."""
        feed(reader, raw(EV_ABS, 0x11, 1), raw(EV_ABS, 0x11, 1), raw(EV_ABS, 0x11, 1))
        assert presses(reader) == [InputAction.DOWN]

    def test_unknown_axis_is_ignored(self, reader: EvdevInput) -> None:
        # 0x00 = ABS_X (an analogue stick), never a d-pad.
        feed(reader, raw(EV_ABS, 0x00, -1))
        assert presses(reader) == []


class TestButtonMapping:
    """The other bug: the layout was guessed, and the guess was wrong."""

    #: (code, expected action) straight from the vendor's own definitions.
    EXPECTED = [
        (304, InputAction.A),       # BTN_SOUTH
        (305, InputAction.B),       # BTN_EAST
        (306, InputAction.Y),       # BTN_C
        (307, InputAction.X),       # BTN_NORTH
        (308, InputAction.L1),      # BTN_WEST
        (309, InputAction.R1),      # BTN_Z
        (310, InputAction.SEARCH),  # BTN_TL
        (311, InputAction.START),   # BTN_TR
        (314, InputAction.L2),      # BTN_SELECT
        (315, InputAction.R2),      # BTN_START
        (312, InputAction.MENU),    # BTN_TL2
        (313, InputAction.MENU),    # BTN_TR2
        (316, InputAction.MENU),    # BTN_MODE
        (103, InputAction.UP),      # d-pad as keys
        (108, InputAction.DOWN),
        (105, InputAction.LEFT),
        (106, InputAction.RIGHT),
    ]

    @pytest.mark.parametrize(("code", "action"), EXPECTED)
    def test_code_maps_to_action(self, code: int, action: InputAction) -> None:
        assert DEFAULT_KEYMAP[code] is action

    @pytest.mark.parametrize(("code", "action"), EXPECTED)
    def test_press_produces_the_action(self, reader: EvdevInput,
                                      code: int, action: InputAction) -> None:
        feed(reader, raw(EV_KEY, code, 1))
        assert presses(reader) == [action]

    def test_confirm_is_not_the_back_button(self, reader: EvdevInput) -> None:
        """Regression: A and B were swapped, so 'enter' left the game list."""
        feed(reader, raw(EV_KEY, 304, 1))
        assert presses(reader) == [InputAction.A]
        feed(reader, raw(EV_KEY, 305, 1))
        assert presses(reader) == [InputAction.B]

    def test_every_action_is_reachable(self) -> None:
        # HIDE and CHAR have no handheld button: HIDE is desktop-keymap only
        # (the handheld reaches hiding from the menu), and CHAR is a typed
        # character event.  Naming exceptions here is deliberate, so the test
        # still catches an action that was merely forgotten.
        desktop_only = {InputAction.HIDE, InputAction.CHAR}
        reachable = set(DEFAULT_KEYMAP.values()) | {
            side for pair in HAT_AXES.values() for side in pair
        }
        assert reachable | desktop_only == set(InputAction)

    def test_release_after_press(self, reader: EvdevInput) -> None:
        feed(reader, raw(EV_KEY, 304, 1))
        reader.poll_events()
        feed(reader, raw(EV_KEY, 304, 0))
        assert releases(reader) == [InputAction.A]

    def test_key_repeat_value_is_ignored(self, reader: EvdevInput) -> None:
        """The kernel's own auto-repeat must not double-fire; we synthesise ours."""
        feed(reader, raw(EV_KEY, 304, 2))
        assert reader.poll_events() == []


class TestDeviceDiscovery:
    """Picking the wrong node silently swallows every button press."""

    @staticmethod
    def _root(tmp_path: Path, *names: str) -> Path:
        for name in names:
            (tmp_path / name).write_bytes(b"")
        return tmp_path

    def test_prefers_a_node_with_gamepad_buttons(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = self._root(tmp_path, "event0", "event4", "event5")
        monkeypatch.setattr(input_mod, "_device_name", lambda p: {
            "event0": "rk805 pwrkey",
            "event4": "ANBERNIC-rk3568-keys",
            "event5": "dierct-keys-polled",
        }.get(p.name, ""))
        monkeypatch.setattr(input_mod, "_device_codes", lambda p, *_: {
            "event0": [116],
            # The gamepad: BTN_* codes.
            "event4": [304, 305, 306, 307, 308, 309, 310, 311],
            # A polled keyboard: key codes only, despite the name saying "keys".
            "event5": [103, 105, 106, 108],
        }.get(p.name, []))

        assert find_key_device(root) == str(root / "event4")

    def test_falls_back_to_hints_when_no_buttons(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = self._root(tmp_path, "event0", "event1")
        monkeypatch.setattr(input_mod, "_device_name",
                            lambda p: "gamepad" if p.name == "event1" else "touchscreen")
        monkeypatch.setattr(input_mod, "_device_codes", lambda p, *_: [])
        assert find_key_device(root) == str(root / "event1")

    def test_returns_none_when_nothing_looks_like_a_keypad(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = self._root(tmp_path, "event0")
        monkeypatch.setattr(input_mod, "_device_name", lambda p: "touchscreen")
        monkeypatch.setattr(input_mod, "_device_codes", lambda p, *_: [])
        assert find_key_device(root) is None

    def test_missing_root_is_not_an_error(self, tmp_path: Path) -> None:
        assert find_key_device(tmp_path / "nonexistent") is None

    def test_device_codes_survives_an_unopenable_node(self, tmp_path: Path) -> None:
        assert _device_codes(tmp_path / "does-not-exist", EV_KEY, 0x300) == []


class TestLifecycle:
    def test_no_device_means_no_thread_and_empty_polls(self, reader: EvdevInput) -> None:
        assert reader.device_path is None
        assert reader._thread is None
        assert reader.poll_events() == []

    def test_inject_round_trips(self, reader: EvdevInput) -> None:
        reader.inject(InputAction.START)
        assert reader.poll_events() == [InputEvent(InputAction.START)]

    def test_close_is_safe_without_a_device(self, reader: EvdevInput) -> None:
        reader.close()
