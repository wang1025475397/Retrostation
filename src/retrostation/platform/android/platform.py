"""The Android platform adapter (DESIGN.ANDROID §6).

``AndroidPlatform`` is the Python half of the port.  It implements the abstract
:class:`~retrostation.platform.base.Platform` contract and delegates every
device-touching operation to a *bridge* object injected at construction.  On the
device that bridge is the Kotlin ``PyRuntime`` (wired through Chaquopy's
``chaquopy_java``); on the desktop test suite it is a fake that records calls.

The split matters: this file contains **no** Android imports, so it is unit-
testable without a device, and the surface it exposes to ``ui/`` and ``data/`` is
byte-for-byte the same interface the Linux and desktop platforms implement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pathlib import Path

from ...core.theme import Form
from ..base import (
    Canvas,
    FileEntry,
    Form,
    InputEvent,
    Platform,
    UnsupportedTarget,
)
from ..fonts import FontBook
from ..targets import ArgvTarget, InlineTarget, IntentTarget, LaunchTarget
from .bridge import AndroidBridge
from .canvas import PilCanvas, rgba_bytes
from .display import form_for


class AndroidPlatform(Platform):
    """Front-end host on Android.  Resident by default (DESIGN.ANDROID §8.11)."""

    name = "android"

    def __init__(self, bridge: Any, *, font_dir: str | None = None) -> None:
        #: Injected by ``PyRuntime`` on device; a fake in tests.
        self._bridge = bridge
        self._font_dir = Path(font_dir) if font_dir else None
        self._fonts: FontBook | None = None
        self._canvases: list[Canvas] = []
        #: Media box the UI last drew (x, y, w, h, canvas index), canvas units.
        self._video_rect: tuple[int, int, int, int, int] | None = None

    # -- display ---------------------------------------------------------- #

    def init_display(self, mode: str) -> list[Canvas]:
        # Probe returns ``[(w, h), ...]`` -- one entry per physical display.
        # §4.1 computes the logical size per screen independently, so two
        # heterogeneous panels (Thor: 1920x1080 + 1240x1080) are both honoured.
        sizes = self._bridge.probe_displays(mode)
        self._canvases = [PilCanvas(w, h) for (w, h) in sizes]
        return self._canvases

    def present(self, index: int) -> None:
        canvas = self._canvases[index]
        self._bridge.push_frame(index, rgba_bytes(canvas))  # type: ignore[arg-type]

    def screen_form(self) -> Form:
        # Only consulted in the single-screen case; two canvases always mean DUAL
        # at the call site.
        if len(self._canvases) >= 2:
            return Form.DUAL
        w, h = self._canvases[0].size
        return form_for(w, h)

    # -- input ------------------------------------------------------------ #

    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        return self._bridge.drain_events(timeout)

    # -- hardware --------------------------------------------------------- #

    def battery(self) -> int | None:
        return self._bridge.battery()

    def temperature(self) -> float | None:
        return self._bridge.temperature()

    def set_brightness(self, value: int, index: int = 0) -> None:
        self._bridge.set_brightness(value, index)

    # -- filesystem ------------------------------------------------------- #

    @property
    def rom_root(self) -> Path:
        return self._bridge.rom_root()

    @property
    def config_dir(self) -> Path:
        return self._bridge.config_dir()

    def list_dir(self, path: Path) -> list:
        return self._bridge.list_dir(path)

    # -- launching -------------------------------------------------------- #

    def launch_game(self, target: LaunchTarget) -> None:
        # Android never runs an argv; the Linux-only kind is refused loudly.
        if isinstance(target, ArgvTarget):
            raise UnsupportedTarget(self.name, target)
        if isinstance(target, IntentTarget):
            # Try the intent and RetroArch's shakier variants (§8.3) before
            # giving up: "no app handled it" must not silently do nothing.
            if self._fires(target):
                return
            raise UnsupportedTarget(self.name, target)
        if isinstance(target, InlineTarget):
            self._bridge.host_core(target)  # B series; raises until implemented
            return
        raise UnsupportedTarget(self.name, target)

    # -- sound ------------------------------------------------------------ #

    def play_sfx(self, kind: str) -> None:
        """Button blips through the host's tone generator (§9.4)."""
        self._bridge.play_sfx(kind)

    def configure_sfx(self, *, enabled: bool, volume: float) -> None:
        self._bridge.configure_sfx(enabled=enabled, volume=volume)

    def release_sfx(self) -> None:
        # ExoPlayer owns the output while a clip plays; nothing else to give up.
        return None

    def run_foreground(self, target: LaunchTarget) -> int | None:
        """Start the game and return at once: this app stays resident (§8.11).

        The host tells us when the game comes back (``on_resume`` ->
        ``on_game_exited``), so there is nothing to wait for on this thread --
        the frontend keeps its windows and memory.
        """
        self.launch_game(target)
        return None

    def _fires(self, target: IntentTarget) -> bool:
        """Try the intent and its fallbacks; ``True`` once one of them lands."""
        if self._bridge.start_activity(target):
            return True
        for fallback in target.fallbacks:
            if self._bridge.start_activity(fallback):
                return True
        return False

    def can_stay_resident(self) -> bool:
        # The app keeps its windows and memory; the game runs in another activity
        # or a hosted core and returns via onActivityResult (§8.11).
        return True

    def on_resume(self) -> None:
        self._bridge.on_game_exited()

    # -- fonts / media ---------------------------------------------------- #

    def font(self, size: int) -> object:
        # R1: fonts are bundled as a CJK subset (DESIGN.ANDROID §12.2) and resolved
        # in Python via FontBook -- no Kotlin involvement.  The directory is supplied
        # by PyRuntime from the extracted assets path.
        if self._fonts is None:
            if self._font_dir:
                dirs: tuple[str, ...] = (str(self._font_dir),)
            else:
                # No bundled font dir: fall back to Android's built-ins.  Without
                # this the app renders with PIL's tiny bitmap font, which is the
                # single ugliest thing on the phone screen.
                dirs = ("/system/fonts", "/product/fonts")
            self._fonts = FontBook(dirs)
        return self._fonts.get(size)

    def load_image(self, path: Path) -> object:
        """Open ``path`` as a PIL image.

        The host decodes first: the Chaquopy Pillow wheel ships without the webp
        plugin, and the platform art (plus many covers) is webp.  Android returns
        PNG bytes, which PIL opens and scales as usual; if the host cannot read
        the file we fall back to a straight PIL open.
        """
        from io import BytesIO

        from PIL import Image

        data = self._bridge.decode_image(path)
        if data:
            return Image.open(BytesIO(data))
        return Image.open(path)

    def set_video_rect(self, rect, *, index: int = 0) -> None:
        """Remember where the preview box is, so the clip can be placed there."""
        if rect is None:
            self._video_rect = None
            return
        x, y, w, h = (int(v) for v in rect)
        moved = self._video_rect is not None and self._video_rect[:4] != (x, y, w, h)
        self._video_rect = (x, y, w, h, int(index))
        if moved:
            self._bridge.move_video(int(index), (x, y, w, h))

    def open_video_pipe(self, path, *, width: int, height: int, fps: int):
        """ExoPlayer renders the clip into the media box (DESIGN.ANDROID §9.2)."""
        if self._video_rect is None:
            return None
        from .video import SurfaceVideoPipe

        x, y, w, h, index = self._video_rect
        return SurfaceVideoPipe(self._bridge, path, (x, y, w, h), index)

    def shutdown(self) -> None:
        self._bridge.shutdown()

    def suspend_display(self) -> None:
        self._bridge.suspend_display()

    def resume_display(self) -> None:
        self._bridge.resume_display()
