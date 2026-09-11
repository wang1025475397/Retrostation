"""The Python half of the Chaquopy boundary (DESIGN.ANDROID §6 / §6.2).

``AndroidPlatform`` talks to an ``AndroidBridge`` instead of touching Kotlin
directly.  The bridge wraps the Kotlin ``HostBridge`` object (handed in by
``PyRuntime``) and converts between Kotlin types and Python ones -- notably it
builds :class:`~retrostation.platform.base.InputEvent` objects here, so the
semantic mapping lives in one place and the Kotlin side only ever forwards raw
key codes / motion samples.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..base import FileEntry, InputAction, InputEvent, InputKind
from ..targets import IntentTarget


def _intent_dict(target: IntentTarget) -> dict[str, Any]:
    return {
        "package": target.package,
        "activity": target.activity,
        "action": target.action,
        "data_uri": target.data_uri,
        "mime": target.mime,
        "extras": list(target.extras),
        "fallbacks": [ _intent_dict(f) for f in target.fallbacks ],
    }


class AndroidBridge:
    """Thin, typed wrapper around the Kotlin ``HostBridge``."""

    def __init__(self, kt: Any) -> None:
        #: The live Kotlin object (a Chaquopy-exposed instance of ``HostBridge``).
        self._kt = kt

    # -- display ---------------------------------------------------------- #

    def probe_displays(self, mode: str) -> list[tuple[int, int]]:
        # Kotlin hands back a JSON string: Chaquopy does not auto-convert Java
        # containers into Python ones (iterating an ArrayList raises TypeError).
        raw = json.loads(self._kt.probeDisplays(mode))
        return [(int(w), int(h)) for (w, h) in raw]

    def push_frame(self, index: int, rgba: bytes) -> None:
        self._kt.pushFrame(index, rgba)

    # -- input ------------------------------------------------------------ #

    def drain_events(self, timeout: float) -> list[InputEvent]:
        raw = json.loads(self._kt.drainInput(timeout))
        return [self._to_event(item) for item in raw]

    @staticmethod
    def _to_event(item: dict[str, Any]) -> InputEvent:
        return InputEvent(
            action=InputAction(item["action"]),
            kind=InputKind(item.get("kind", "press")),
            x=item.get("x"),
            y=item.get("y"),
            dx=int(item.get("dx", 0)),
            dy=int(item.get("dy", 0)),
            screen=int(item.get("screen", 0)),
            text=item.get("text", ""),
        )

    # -- media ------------------------------------------------------------ #

    def decode_image(self, path: Path) -> bytes | None:
        """Android-decoded PNG bytes for ``path``, or ``None`` when unreadable.

        The bundled Pillow is built without the webp plugin, so the host decodes
        instead (BitmapFactory reads webp/gif/png/jpeg) and hands back PNG --
        something PIL can always open, at whatever size it needs.
        """
        data = self._kt.decodeImage(str(path))
        return bytes(data) if data is not None else None

    def open_video(self, path: Path, index: int, rect: tuple[int, int, int, int]) -> None:
        """Start the clip in the media box (canvas units) on canvas ``index``."""
        x, y, w, h = rect
        self._kt.openVideo(str(path), index, x, y, w, h)

    def play_sfx(self, kind: str) -> None:
        self._kt.playSfx(kind)

    def configure_sfx(self, *, enabled: bool, volume: float) -> None:
        self._kt.configureSfx(enabled, float(volume))

    def stop_video(self) -> None:
        self._kt.stopVideo()

    def move_video(self, index: int, rect: tuple[int, int, int, int]) -> None:
        """Reposition the running clip after the layout moved its media box."""
        x, y, w, h = rect
        self._kt.moveVideo(index, x, y, w, h)

    def set_video_volume(self, value: float) -> None:
        self._kt.setVideoVolume(float(value))

    # -- hardware --------------------------------------------------------- #

    def battery(self) -> int | None:
        return self._kt.battery()

    def temperature(self) -> float | None:
        return self._kt.temperature()

    def set_brightness(self, value: int, index: int) -> None:
        self._kt.setBrightness(value, index)

    # -- filesystem ------------------------------------------------------- #

    def rom_root(self) -> Path:
        return Path(str(self._kt.romRoot()))

    def config_dir(self) -> Path:
        return Path(str(self._kt.configDir()))

    def list_dir(self, path: Path) -> list[FileEntry]:
        raw = json.loads(self._kt.listDir(str(path)))
        return [
            FileEntry(
                name=str(entry["name"]),
                is_dir=bool(entry["is_dir"]),
                size=int(entry.get("size", 0)),
                mtime=float(entry.get("mtime", 0.0)),
            )
            for entry in raw
        ]

    # -- launching -------------------------------------------------------- #

    def start_activity(self, target: IntentTarget) -> bool:
        """Fire the intent; ``False`` when nothing on the device handles it."""
        return bool(self._kt.startActivity(json.dumps(_intent_dict(target))))

    def host_core(self, target: object) -> None:
        # Inline libretro host -- implemented at B0/B1.  Until then the platform
        # refuses InlineTarget loudly (see AndroidPlatform.launch_game).
        raise NotImplementedError("inline emulator arrives with the B series")

    def on_game_exited(self) -> None:
        self._kt.onGameExited()

    # -- lifecycle -------------------------------------------------------- #

    def shutdown(self) -> None:
        self._kt.shutdown()

    def suspend_display(self) -> None:
        self._kt.suspendDisplay()

    def resume_display(self) -> None:
        self._kt.resumeDisplay()
