#!/usr/bin/env python3
"""Probe the input devices of a Linux handheld.

Answers two questions the frontend cannot afford to guess:

* which ``/dev/input/event*`` node is actually the gamepad, and
* which raw codes that node emits for each button.

Run without arguments for a static report (device names + supported codes).
Run ``--watch`` to print raw events for a few seconds while you press buttons
-- that is how the default key map in ``platform/linux/input.py`` was derived,
and how to re-derive it on a device we have never seen.

    python3 scripts/probe_input.py                     # static report
    python3 scripts/probe_input.py --check-keymap      # does the key map fit?
    python3 scripts/probe_input.py --watch /dev/input/event4
    python3 scripts/probe_input.py --watch-any --seconds 10
"""

from __future__ import annotations

import argparse
import fcntl
import os
import select
import struct
import sys
import time
from pathlib import Path

# struct input_event on LP64: two timevals (long), type, code, value.
_EVENT = struct.Struct("llHHi")
_SIZE = _EVENT.size

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03

_IOC_READ = 2
_EVIOCGNAME = (_IOC_READ << 30) | (256 << 16) | (ord("E") << 8) | 0x06


def _eviocgbit(ev: int, size: int) -> int:
    """``EVIOCGBIT(ev, size)`` request number."""
    return (_IOC_READ << 30) | (size << 16) | (ord("E") << 8) | (0x20 + ev)


# Names we print instead of raw numbers, for the codes that matter to us.
_KEY_NAMES = {
    103: "KEY_UP", 105: "KEY_LEFT", 106: "KEY_RIGHT", 108: "KEY_DOWN",
    116: "KEY_POWER",
    304: "BTN_SOUTH", 305: "BTN_EAST", 306: "BTN_C", 307: "BTN_NORTH",
    308: "BTN_WEST", 309: "BTN_Z", 310: "BTN_TL", 311: "BTN_TR",
    312: "BTN_TL2", 313: "BTN_TR2", 314: "BTN_SELECT", 315: "BTN_START",
    316: "BTN_MODE", 317: "BTN_THUMBL", 318: "BTN_THUMBR",
}
_ABS_NAMES = {
    0x00: "ABS_X", 0x01: "ABS_Y", 0x02: "ABS_Z", 0x03: "ABS_RX",
    0x10: "ABS_HAT0X", 0x11: "ABS_HAT0Y",
    0x12: "ABS_HAT1X", 0x13: "ABS_HAT1Y",
}


def device_name(fd: int) -> str:
    buf = bytearray(256)
    try:
        fcntl.ioctl(fd, _EVIOCGNAME, buf)
    except OSError:
        return ""
    return bytes(buf).split(b"\x00", 1)[0].decode("utf-8", "replace")


def bitmask(fd: int, ev: int, limit: int) -> list[int]:
    """Codes the device reports support for in event type ``ev``."""
    size = (limit + 7) // 8
    buf = bytearray(size)
    try:
        fcntl.ioctl(fd, _eviocgbit(ev, size), buf)
    except OSError:
        return []
    codes = []
    for index, byte in enumerate(buf):
        for bit in range(8):
            if byte & (1 << bit):
                codes.append(index * 8 + bit)
    return codes


def name_of(table: dict[int, str], code: int) -> str:
    return table.get(code, str(code))


def report(root: Path) -> None:
    print(f"scanning {root}")
    for path in sorted(root.glob("event*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            print(f"  {path.name}: cannot open ({exc})")
            continue
        try:
            name = device_name(fd)
            keys = bitmask(fd, EV_KEY, 0x300)
            axes = bitmask(fd, EV_ABS, 0x40)
            print(f"\n  {path}  {name!r}")
            print(f"    keys ({len(keys)}): "
                  f"{', '.join(name_of(_KEY_NAMES, c) for c in keys) or '-'}")
            print(f"    axes ({len(axes)}): "
                  f"{', '.join(name_of(_ABS_NAMES, c) for c in axes) or '-'}")
        finally:
            os.close(fd)
    print()
    print("The gamepad is the node whose key list contains BTN_* codes.")
    print("Watch it with:  python3 scripts/probe_input.py --watch <node>")


def watch(path: str, seconds: float) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    print(f"listening on {path} ({device_name(fd)!r}) for {seconds:.0f}s -- press buttons now")
    print("format: type code value   (type 1=KEY, 3=ABS)")
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            raw = os.read(fd, _SIZE * 32)
            for offset in range(0, len(raw) - _SIZE + 1, _SIZE):
                _sec, _usec, etype, code, value = _EVENT.unpack(raw[offset:offset + _SIZE])
                if etype == EV_SYN:
                    continue
                table = _KEY_NAMES if etype == EV_KEY else (
                    _ABS_NAMES if etype == EV_ABS else {})
                print(f"  {etype}  {name_of(table, code):<12} {value}")
    finally:
        os.close(fd)
    print("done")


def check_keymap(root: Path) -> int:
    """Answer "will the buttons work?" without pressing anything."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from retrostation.platform.base import InputAction
    from retrostation.platform.linux.input import (
        DEFAULT_KEYMAP,
        HAT_AXES,
        _device_codes,
        find_key_device,
    )

    path = find_key_device(root)
    if path is None:
        print("FAIL  no key device found -- every button would be dead")
        return 1

    fd = os.open(path, os.O_RDONLY)
    try:
        name = device_name(fd)
    finally:
        os.close(fd)

    node = Path(path)
    keys = set(_device_codes(node, EV_KEY, 0x300))
    axes = set(_device_codes(node, EV_ABS, 0x40))

    print(f"device:   {path}  {name!r}")

    hats = sorted(code for code in HAT_AXES if code in axes)
    print(f"d-pad:    {', '.join(name_of(_ABS_NAMES, c) for c in hats) or 'MISSING'}")

    # Codes we can legitimately expect this device to advertise.
    expected = {c for c in DEFAULT_KEYMAP if c >= 0x120}
    covered: set[InputAction] = set()
    absent: list[str] = []
    for code, action in sorted(DEFAULT_KEYMAP.items()):
        if code not in expected and code not in (103, 105, 106, 108):
            continue  # hat axes, handled below
        if code in keys or code not in expected:
            covered.add(action)
        else:
            absent.append(f"{action.value} <- {name_of(_KEY_NAMES, code)} ({code})")
    for code in hats:
        covered.update(HAT_AXES[code])

    unmapped = sorted(
        name_of(_KEY_NAMES, c) for c in keys if c >= 0x120 and c not in DEFAULT_KEYMAP
    )
    gaps = sorted(a.value for a in InputAction if a not in covered)

    print(f"mapped:   {len(covered)}/{len(InputAction)} actions")
    if absent:
        print(f"  not advertised by this device: {', '.join(absent)}")
    if gaps:
        print(f"  UNREACHABLE actions: {', '.join(gaps)}")
    if unmapped:
        print(f"  device codes with no binding: {', '.join(unmapped)}")

    dpad_ok = bool(hats) or all(c in keys for c in (103, 105, 106, 108))
    if not gaps and dpad_ok:
        print("OK  every action is reachable from this device")
        return 0
    print("FAIL  some actions can never be triggered -- see above")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", metavar="NODE", help="print raw events from one node")
    parser.add_argument("--watch-any", action="store_true",
                        help="watch the first node that reports BTN_* codes")
    parser.add_argument("--check-keymap", action="store_true",
                        help="verify the built-in key map against this device")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="how long to listen (default 10)")
    parser.add_argument("--root", default="/dev/input")
    args = parser.parse_args()

    root = Path(args.root)
    if args.watch:
        watch(args.watch, args.seconds)
        return 0

    if args.check_keymap:
        return check_keymap(root)

    if args.watch_any:
        for path in sorted(root.glob("event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            keys = bitmask(fd, EV_KEY, 0x300)
            os.close(fd)
            if any(c >= 0x120 for c in keys):
                watch(str(path), args.seconds)
                return 0
        print("no node with gamepad buttons found", file=sys.stderr)
        return 1

    report(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
