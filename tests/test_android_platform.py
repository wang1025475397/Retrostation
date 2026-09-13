"""Integration test for AndroidPlatform + AndroidBridge using a fake Kotlin host.

The fake mimics the *Kotlin* object that [AndroidBridge] wraps (camelCase methods),
so this exercises both `bridge.py` (type conversion) and `platform.py` without a
device or the Android SDK.
"""

from __future__ import annotations

import json
from pathlib import Path

from retrostation.core.theme import Form
from retrostation.platform.android.bridge import AndroidBridge
from retrostation.platform.android.platform import AndroidPlatform
from retrostation.platform.base import FileEntry, InputAction, InputEvent, InputKind
from retrostation.platform.targets import ArgvTarget, IntentTarget, UnsupportedTarget


class FakeKt:
    """Stands in for the Kotlin HostBridge."""

    def __init__(self, displays) -> None:
        self._displays = displays
        self.pushed: list[tuple[int, bytes]] = []
        self.started: dict | None = None
        self.suspended = 0
        self.resumed = 0

    def probeDisplays(self, mode):
        # The real HostBridge crosses the Chaquopy boundary as JSON (see bridge.py).
        return json.dumps([[w, h] for (w, h) in self._displays])

    def pushFrame(self, index, rgba):
        self.pushed.append((index, rgba))

    def drainInput(self, timeout):
        return json.dumps([
            {"action": "down", "kind": "press"},
            {"action": "a", "kind": "press", "x": 10, "y": 20, "screen": 1},
        ])

    def battery(self):
        return 80

    def temperature(self):
        return None

    def setBrightness(self, value, index):
        pass

    def romRoot(self):
        return "/storage/emulated/0/Roms"

    def configDir(self):
        return "/data/data/ai.retrostation/files"

    def listDir(self, path):
        return json.dumps([{"name": "FC", "is_dir": True, "size": 0, "mtime": 0.0}])

    def startActivity(self, intent):
        self.started = intent
        return True

    def hostCore(self, target):
        raise NotImplementedError("inline at B0")

    def onGameExited(self):
        pass

    def shutdown(self):
        pass

    def suspendDisplay(self):
        self.suspended += 1

    def resumeDisplay(self):
        self.resumed += 1


def _platform(displays):
    return AndroidPlatform(AndroidBridge(FakeKt(displays)))


def test_init_display_builds_canvases() -> None:
    plat = _platform([(560, 1248)])
    canvases = plat.init_display("auto")
    assert len(canvases) == 1
    assert canvases[0].size == (560, 1248)


def test_init_display_dual() -> None:
    plat = _platform([(640, 480), (640, 480)])
    canvases = plat.init_display("dual")
    assert [c.size for c in canvases] == [(640, 480), (640, 480)]
    assert plat.screen_form() is Form.DUAL


def test_present_pushes_rgba_bytes() -> None:
    plat = _platform([(560, 1248)])
    canvases = plat.init_display("auto")
    plat.present(0)
    index, data = plat._bridge._kt.pushed[0]  # type: ignore[attr-defined]
    assert index == 0
    assert len(data) == 560 * 1248 * 4


def test_poll_events_converted_to_input_events() -> None:
    plat = _platform([(640, 480)])
    plat.init_display("auto")
    events = plat.poll_events(0.0)
    assert events[0] == InputEvent(InputAction.DOWN, InputKind.PRESS)
    tap = events[1]
    assert tap.action is InputAction.A and tap.screen == 1 and tap.x == 10


def test_launch_intent_routes_to_bridge() -> None:
    plat = _platform([(640, 480)])
    plat.init_display("auto")
    target = IntentTarget(package="com.retroarch.aarch64", activity=".RetroActivityFuture")
    plat.launch_game(target)
    started = json.loads(plat._bridge._kt.started)  # type: ignore[attr-defined]
    assert started["package"] == "com.retroarch.aarch64"


def test_launch_argv_refused() -> None:
    plat = _platform([(640, 480)])
    plat.init_display("auto")
    try:
        plat.launch_game(ArgvTarget(argv=("/bin/true",)))
    except UnsupportedTarget:
        pass
    else:
        raise AssertionError("ArgvTarget must be refused on Android")


def test_can_stay_resident() -> None:
    assert _platform([(640, 480)]).can_stay_resident() is True


def test_filesystem_delegates_to_bridge() -> None:
    plat = _platform([(640, 480)])
    assert plat.rom_root == Path("/storage/emulated/0/Roms")
    entries = plat.list_dir(Path("/storage/emulated/0/Roms"))
    assert entries == [FileEntry(name="FC", is_dir=True, size=0, mtime=0.0)]


def test_suspend_resume_counted() -> None:
    plat = _platform([(640, 480)])
    plat.suspend_display()
    plat.resume_display()
    kt = plat._bridge._kt  # type: ignore[attr-defined]
    assert kt.suspended == 1 and kt.resumed == 1
