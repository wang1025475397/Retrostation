"""evdev input for the Linux handheld.

Reads the key device directly and turns raw codes into :class:`InputEvent`
with three behaviours the UI depends on:

* **repeat** -- holding UP/DOWN scrolls the list (400 ms delay, 80 ms rate);
* **long press** -- ``MENU`` held 800 ms quits;
* **non-blocking poll** -- the main loop must also drive animations, so
  :meth:`EvdevInput.poll_events` never blocks indefinitely.

The key map is derived from the vendor's own button definitions, not from the
"standard gamepad" convention -- on the RG DS they disagree, and the result of
guessing was that no button did what the player expected.  It stays a plain
dict so a different handheld only needs a new mapping, never a code change.

Verify any suspected mapping problem with ``scripts/probe_input.py``, which
prints both the capability bitmap and the raw events a device emits.
"""

from __future__ import annotations

import os
import select
import struct
import threading
import time
from collections import deque
from pathlib import Path

from ..base import InputAction, InputEvent, InputKind

# --------------------------------------------------------------------------- #
# evdev plumbing
# --------------------------------------------------------------------------- #

#: ``struct input_event`` on LP64: two timevals (long), type, code, value.
_EVENT_STRUCT = struct.Struct("llHHi")
_EVENT_SIZE = _EVENT_STRUCT.size

EV_KEY = 0x01
EV_ABS = 0x03
VALUE_UP = 0
VALUE_DOWN = 1
VALUE_REPEAT = 2

#: ``EVIOCGNAME(len)`` ioctl request number.
_IOC_READ = 2
_EVIOCGNAME = (_IOC_READ << 30) | (256 << 16) | (ord("E") << 8) | 0x06


def _eviocgbit(ev: int, size: int) -> int:
    """``EVIOCGBIT(ev, size)`` request number."""
    return (_IOC_READ << 30) | (size << 16) | (ord("E") << 8) | (0x20 + ev)


#: First ``BTN_*`` code.  A device advertising anything at or above this is a
#: gamepad; keyboards and touchscreens never do.
_BTN_CODE_FLOOR = 0x120

# --------------------------------------------------------------------------- #
# Key map
# --------------------------------------------------------------------------- #

#: Raw code -> semantic action, following the **vendor's own** mapping from
#: ``/mnt/mod/ctrl/configs/functions`` on the RG DS, cross-checked against the
#: capability bitmap of ``ANBERNIC-rk3568-keys`` (see scripts/probe_input.py).
#:
#: The ordering looks surprising if you assume a Nintendo layout: this kernel
#: puts A on BTN_SOUTH, B on BTN_EAST, X on BTN_NORTH and **Y on BTN_C**, while
#: the shoulders start at BTN_WEST.  Guessing "the standard gamepad layout"
#: here is what made every button do the wrong thing on the first boot.
DEFAULT_KEYMAP: dict[int, InputAction] = {
    # Face buttons
    304: InputAction.A,      # BTN_SOUTH
    305: InputAction.B,      # BTN_EAST
    306: InputAction.Y,      # BTN_C
    307: InputAction.X,      # BTN_NORTH
    # Shoulders
    308: InputAction.L1,     # BTN_WEST
    309: InputAction.R1,     # BTN_Z
    314: InputAction.L2,     # BTN_SELECT
    315: InputAction.R2,     # BTN_START
    # System keys
    310: InputAction.SELECT,  # BTN_TL
    311: InputAction.START,   # BTN_TR
    312: InputAction.MENU,    # BTN_TL2  (the FUNC button)
    313: InputAction.MENU,    # BTN_TR2  (L3 -- also quits, so you are never stuck)
    316: InputAction.MENU,    # BTN_MODE (R3)
    # D-pad reported as *key* codes instead of hat axes.  Devices differ:
    # the RG DS uses hat axes, but its ``dierct-keys-polled`` node uses these.
    103: InputAction.UP,
    108: InputAction.DOWN,
    105: InputAction.LEFT,
    106: InputAction.RIGHT,
    # Volume rocker (KEY_VOLUMEDOWN / KEY_VOLUMEUP).  These reach the frontend
    # as ordinary key events, so without this the rocker does nothing at all.
    114: InputAction.VOLUME_DOWN,
    115: InputAction.VOLUME_UP,
}

#: Hat axis code -> ``(action when value < 0, action when value > 0)``.
#:
#: HAT0X/HAT0Y is what the RG DS reports (codes 16/17); HAT1X/HAT1Y is common
#: on other handhelds.  Both are listed because the axis an Anbernic kernel
#: picks varies by board -- the value sign is what actually matters.
HAT_AXES: dict[int, tuple[InputAction, InputAction]] = {
    0x10: (InputAction.LEFT, InputAction.RIGHT),   # ABS_HAT0X
    0x11: (InputAction.UP, InputAction.DOWN),      # ABS_HAT0Y
    0x12: (InputAction.LEFT, InputAction.RIGHT),   # ABS_HAT1X
    0x13: (InputAction.UP, InputAction.DOWN),      # ABS_HAT1Y
}

#: Actions that synthesise a LONG_PRESS.  Others keep repeating instead.
LONG_PRESS_ACTIONS: frozenset[InputAction] = frozenset({InputAction.MENU})

#: Substrings used as a tie-breaker when picking the right /dev/input node.
_DEVICE_HINTS = ("keys", "gamepad", "anbernic", "rk3566", "rk3568")


# --------------------------------------------------------------------------- #
# Device discovery
# --------------------------------------------------------------------------- #


def _device_name(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return ""
    try:
        buf = bytearray(256)
        try:
            import fcntl

            fcntl.ioctl(fd, _EVIOCGNAME, buf)
        except (OSError, ImportError):
            return ""
        raw = bytes(buf).split(b"\x00", 1)[0]
        return raw.decode("utf-8", "replace")
    finally:
        os.close(fd)


def _device_codes(path: Path, ev_type: int, limit: int) -> list[int]:
    """Codes the device says it supports for ``ev_type``."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return []
    try:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-Linux
            return []
        buf = bytearray((limit + 7) // 8)
        try:
            fcntl.ioctl(fd, _eviocgbit(ev_type, len(buf)), buf)
        except OSError:
            return []
        return [
            index * 8 + bit
            for index, byte in enumerate(buf)
            for bit in range(8)
            if byte & (1 << bit)
        ]
    finally:
        os.close(fd)


def find_key_device(root: Path = Path("/dev/input")) -> str | None:
    """Pick the node that really is the gamepad.

    The capability bitmap decides, not the name: a gamepad advertises ``BTN_*``
    codes (>= 0x120) and nothing else does.  Name hints are only a tie-breaker,
    because on the RG DS *both* ``ANBERNIC-rk3568-keys`` (the gamepad) and
    ``dierct-keys-polled`` (a keyboard) contain "keys" in their name, so hints
    alone can pick the wrong node and silently drop every button press.
    """
    if not root.is_dir():
        return None

    hinted: list[str] = []
    with_buttons: list[str] = []
    for path in sorted(root.glob("event*")):
        name = _device_name(path).lower()
        if any(hint in name for hint in _DEVICE_HINTS):
            hinted.append(str(path))
        if any(code >= _BTN_CODE_FLOOR for code in _device_codes(path, EV_KEY, 0x300)):
            with_buttons.append(str(path))

    for path in with_buttons:
        if path in hinted:
            return path
    return with_buttons[0] if with_buttons else (hinted[0] if hinted else None)


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #


class EvdevInput:
    """Background reader with repeat and long-press synthesis."""

    def __init__(
        self,
        device: str | None = None,
        *,
        keymap: dict[int, InputAction] | None = None,
        repeat_delay: float = 0.40,
        repeat_rate: float = 0.08,
        long_press: float = 0.80,
    ) -> None:
        self._keymap = dict(keymap or DEFAULT_KEYMAP)
        self._repeat_delay = repeat_delay
        self._repeat_rate = repeat_rate
        self._long_press = long_press

        self._events: deque[InputEvent] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        #: Set while a game owns the screen: events are read but dropped.
        #: See :meth:`pause`.
        self._paused = False

        #: action -> (held_since, last_repeat, long_fired)
        self._held: dict[InputAction, tuple[float, float, bool]] = {}

        self._path = device or find_key_device()
        self._fd: int | None = None
        self._thread: threading.Thread | None = None

        if self._path:
            self._fd = self._open(self._path)
        if self._fd is not None:
            self._thread = threading.Thread(target=self._run, name="retrostation-input", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _open(path: str) -> int | None:
        try:
            # Non-blocking + exclusive-ish; other readers (the stock frontend)
            # may still be attached, so we do not grab the device.
            return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            return None

    def _run(self) -> None:
        assert self._fd is not None
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                # Drain everything pending: a d-pad tap plus its SYN report is
                # several events, and reading one at a time lets them pile up.
                raw = os.read(self._fd, _EVENT_SIZE * 32)
            except (OSError, ValueError):
                break
            for offset in range(0, len(raw) - _EVENT_SIZE + 1, _EVENT_SIZE):
                self._handle_raw(raw[offset:offset + _EVENT_SIZE])

    def _handle_raw(self, raw: bytes) -> None:
        _tv_sec, _tv_usec, etype, code, value = _EVENT_STRUCT.unpack(raw)
        if etype == EV_KEY:
            self._on_key(code, value)
        elif etype == EV_ABS and code in HAT_AXES:
            self._on_hat(code, value)

    def _on_key(self, code: int, value: int) -> None:
        action = self._keymap.get(code)
        if action is None:
            return
        if value == VALUE_DOWN:
            self._press(action)
        elif value == VALUE_UP:
            self._release(action)
        # value == 2 is a kernel auto-repeat; we synthesise our own so the
        # timing is consistent across devices.

    def _on_hat(self, code: int, value: int) -> None:
        """A d-pad axis moved.  ``-1``/``0``/``+1`` are the only values.

        The sign is the whole point: treating every non-zero value as one
        direction is what made RIGHT and DOWN unreachable while LEFT and UP
        fired for both directions of each axis.
        """
        negative, positive = HAT_AXES.get(code, (None, None))
        if negative is None:
            return
        if value == 0:
            self._release(negative)
            self._release(positive)
        elif value < 0:
            self._release(positive)
            self._press(negative, dedupe=True)
        else:
            self._release(negative)
            self._press(positive, dedupe=True)

    # ------------------------------------------------------------------ #

    def _press(self, action: InputAction, *, dedupe: bool = False) -> None:
        if self._paused:
            return
        now = time.monotonic()
        with self._lock:
            # An axis leaning the same way keeps reporting its value; without
            # this the d-pad would scroll as fast as the kernel reports.
            if dedupe and action in self._held:
                return
            self._held[action] = (now, now, False)
            self._events.append(InputEvent(action, InputKind.PRESS))

    def _release(self, action: InputAction) -> None:
        if self._paused:
            return
        with self._lock:
            self._held.pop(action, None)
            self._events.append(InputEvent(action, InputKind.RELEASE))

    # ------------------------------------------------------------------ #

    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        """Drain queued events and synthesise repeats for held buttons."""
        if timeout > 0 and not self._events:
            time.sleep(min(timeout, 0.05))
        self._synthesize_repeats()

        with self._lock:
            pending = list(self._events)
            self._events.clear()
        return pending

    def _synthesize_repeats(self) -> None:
        now = time.monotonic()
        generated: list[InputEvent] = []
        with self._lock:
            for action, (since, last_repeat, long_fired) in list(self._held.items()):
                if not long_fired and action in LONG_PRESS_ACTIONS:
                    if now - since >= self._long_press:
                        self._held[action] = (since, last_repeat, True)
                        generated.append(InputEvent(action, InputKind.LONG_PRESS))
                        continue
                if now - since < self._repeat_delay:
                    continue
                fired = 0
                while now - last_repeat >= self._repeat_rate and fired < 3:
                    last_repeat += self._repeat_rate
                    fired += 1
                if fired:
                    self._held[action] = (since, last_repeat, long_fired)
                    generated.extend(InputEvent(action, InputKind.REPEAT) for _ in range(fired))
            self._events.extend(generated)

    # ------------------------------------------------------------------ #
    # Testing / development helpers
    # ------------------------------------------------------------------ #

    def inject(self, action: InputAction, kind: InputKind = InputKind.PRESS) -> None:
        """Queue an event by hand (unit tests, and a future on-screen keymap UI)."""
        with self._lock:
            self._events.append(InputEvent(action, kind))

    @property
    def device_path(self) -> str | None:
        return self._path

    def pause(self) -> None:
        """Stop delivering input: a game owns the screen (DESIGN §8.2).

        The frontend stays alive while the game runs and the input device is
        shared -- we deliberately do not grab it -- so without this every
        button the player presses in the game is queued and then replayed into
        the menu the instant they quit, which launches another game and walks
        straight back out of the frontend.  The reader thread keeps draining
        the device so it cannot back up; the events are simply dropped.
        """
        self._paused = True
        with self._lock:
            self._events.clear()
            self._held.clear()

    def resume(self) -> None:
        """Undo :meth:`pause`, dropping anything seen in the meantime."""
        with self._lock:
            self._events.clear()
            self._held.clear()
        self._paused = False

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
