"""Desktop platform: run Retrostation on a PC with a keyboard.

This is the "develop on a laptop" target.  It reuses the same ``PilCanvas``
the Linux handheld draws into, but shows it in a ``tkinter`` window (Python
standard library -- no extra dependencies, works on Windows/Linux/macOS) and
reads a keyboard instead of the handheld's evdev gamepad.

The app's frame loop already calls ``poll_events`` with a tiny timeout every
frame, so we drive Tk's event queue with ``root.update()`` and hand back
whatever keys were pressed.  That keeps animations and the video decoder running
while staying responsive to input.

Key map (matches the handheld's buttons; see README for the on-device layout):

    Arrow keys .... UP / DOWN / LEFT / RIGHT
    A (confirm) ... A or Enter
    B (back) ...... B or Esc
    X (switch view) X
    Y (favourite) . Y
    L1 / R1 ........ Q / E          (page up / down)
    L2 / R2 ........ Home / End      (jump to first / last)
    SELECT ......... Tab            (cycle filter)
    START (menu) ... S or M
    MENU (quit) .... ` (hold ~0.8s) or just close the window
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Sequence

import tkinter as tk
from tkinter import TclError

from PIL import Image, ImageTk

from ...core.theme import BASE_H, BASE_W
from ..base import (
    AudioPipe,
    Canvas,
    FileEntry,
    InputAction,
    InputEvent,
    InputKind,
    Platform,
    VideoPipe,
)
from ..linux.canvas import PilCanvas
from ..linux.fonts import FontBook

log = logging.getLogger(__name__)

#: The UI is rendered at ``_RENDER_SCALE`` x the 640x480 reference so text and
#: art stay crisp, then each frame is downscaled to the on-screen size
#: (``_DISPLAY_W`` x ``_DISPLAY_H``) with LANCZOS -- supersampling, which looks
#: far sharper than the old 1x-render-then-upscale path that made text blurry.
_RENDER_SCALE = 2.0
#: On-screen size of a single panel.  Two panels are stacked vertically.
_DISPLAY_W = 640
_DISPLAY_H = int(_DISPLAY_W * BASE_H / BASE_W)  # 480

#: Lower-cased Tk keysym -> semantic action.
_KEYMAP: dict[str, InputAction] = {
    "up": InputAction.UP,
    "down": InputAction.DOWN,
    "left": InputAction.LEFT,
    "right": InputAction.RIGHT,
    "a": InputAction.A,
    "return": InputAction.A,
    "b": InputAction.B,
    "escape": InputAction.B,
    "x": InputAction.X,
    "y": InputAction.Y,
    "q": InputAction.L1,
    "e": InputAction.R1,
    "home": InputAction.L2,
    "end": InputAction.R2,
    "tab": InputAction.SELECT,
    "s": InputAction.START,
    "m": InputAction.START,
    "quoteleft": InputAction.MENU,  # ` (backtick): hold to quit
}

#: Actions that auto-repeat while held (list scrolling, page turns).
_REPEATABLE = frozenset(
    {
        InputAction.UP,
        InputAction.DOWN,
        InputAction.LEFT,
        InputAction.RIGHT,
        InputAction.L1,
        InputAction.R1,
        InputAction.L2,
        InputAction.R2,
    }
)

_REPEAT_DELAY = 0.40
_REPEAT_RATE = 0.08
_LONG_PRESS = 0.80


class DesktopPlatform(Platform):
    """Tkinter window + keyboard, backed by the shared ``PilCanvas``."""

    name = "desktop"

    def __init__(
        self,
        *,
        rom_root: str | None = None,
        config_dir: str | None = None,
        font_dirs: tuple[str, ...] | None = None,
    ) -> None:
        self._rom_root = self._resolve_rom_root(rom_root)
        self._config_dir = self._resolve_config_dir(config_dir)
        self._fonts = FontBook(font_dirs)
        self._canvases: list[Canvas] = []
        self._root: tk.Tk | None = None
        self._labels: list[tk.Label] = []
        self._photos: list[object] = []
        self._events: deque[InputEvent] = deque()
        self._lock = threading.Lock()
        #: action -> (held_since, last_repeat, long_fired)
        self._held: dict[InputAction, tuple[float, float, bool]] = {}
        self._closed = False

    # ------------------------------------------------------------------ #
    # Path resolution
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_rom_root(explicit: str | None) -> Path:
        if explicit:
            return Path(explicit)
        env = os.environ.get("RETROSTATION_ROM_ROOT")
        if env:
            return Path(env)
        for candidate in (Path.cwd() / "roms", Path.home() / ".retrostation" / "roms"):
            if candidate.is_dir():
                return candidate
        return Path.cwd() / "roms"

    @staticmethod
    def _resolve_config_dir(explicit: str | None) -> Path:
        if explicit:
            return Path(explicit)
        env = os.environ.get("RETROSTATION_CONFIG_DIR")
        if env:
            return Path(env)
        return Path.home() / ".retrostation"

    # ------------------------------------------------------------------ #
    # Display
    # ------------------------------------------------------------------ #

    def init_display(self, mode: str) -> list[Canvas]:
        if self._canvases:
            return self._canvases

        dual = mode in ("dual", "auto")
        count = 2 if dual else 1

        root = tk.Tk()
        root.title("Retrostation — 桌面预览 (按 ` 长按退出 / Alt+F4 关闭)")
        try:
            root.attributes("-topmost", True)
        except TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._root = root

        frame = tk.Frame(root)
        frame.pack(padx=4, pady=4)
        for _ in range(count):
            label = tk.Label(frame)
            # Stack panels top-to-bottom so the preview mirrors the handheld's
            # two vertical screens.
            label.pack(side="top", padx=2)
            self._labels.append(label)
            self._photos.append(None)
            self._canvases.append(
                PilCanvas(
                    int(BASE_W * _RENDER_SCALE), int(BASE_H * _RENDER_SCALE)
                )
            )

        root.bind_all("<KeyPress>", self._on_press)
        root.bind_all("<KeyRelease>", self._on_release)
        try:
            root.focus_force()
            # Process pending WM events (Map/Expose) so the window is actually
            # drawn before the app's self-driven event loop takes over -- without
            # a Tk mainloop, nothing else will flush the initial paint.
            root.update()
            root.lift()
        except TclError:
            pass
        return self._canvases

    def present(self, index: int) -> None:
        if self._closed or self._root is None or index >= len(self._canvases):
            return
        image = self._canvases[index].pil_image  # type: ignore[attr-defined]
        if image.mode != "RGB":
            image = image.convert("RGB")
        # Canvas is rendered at _RENDER_SCALE; downscale to the on-screen size
        # (supersampling keeps text sharp instead of upscaling a 640x480 frame).
        if (image.width, image.height) != (_DISPLAY_W, _DISPLAY_H):
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:  # Pillow < 9.1
                resample = Image.LANCZOS
            image = image.resize((_DISPLAY_W, _DISPLAY_H), resample)
        try:
            photo = ImageTk.PhotoImage(image)
            self._labels[index].configure(image=photo)
            self._photos[index] = photo  # keep a reference so it is not GC'd
            self._root.update_idletasks()
        except TclError:
            pass

    def _on_close(self) -> None:
        """Closing the window quits: open the exit dialog, then confirm it."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._events.append(InputEvent(InputAction.MENU, InputKind.LONG_PRESS))
            self._events.append(InputEvent(InputAction.A, InputKind.PRESS))

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    def _on_press(self, event) -> None:
        if self._closed:
            return
        action = _KEYMAP.get(event.keysym.lower())
        if action is None:
            return
        with self._lock:
            if action in self._held:
                return  # OS auto-repeat: we synthesise our own repeats
            now = time.monotonic()
            self._held[action] = (now, now, False)
            self._events.append(InputEvent(action, InputKind.PRESS))

    def _on_release(self, event) -> None:
        if self._closed:
            return
        action = _KEYMAP.get(event.keysym.lower())
        if action is None:
            return
        with self._lock:
            self._held.pop(action, None)
            self._events.append(InputEvent(action, InputKind.RELEASE))

    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        self._pump()
        self._synthesize()
        with self._lock:
            pending = list(self._events)
            self._events.clear()
        return pending

    def _pump(self) -> None:
        """Pump Tk's event queue so keys are delivered and the window repaints.

        The app runs its own frame loop (no Tk ``mainloop()``), so we drive Tk
        with ``update()`` -- which both dispatches pending WM/key events and
        flushes redraws in one call.  On Windows this is what keeps the window
        mapped and responsive; the bare ``dooneevent`` loop is not reliable
        there without a running mainloop.
        """
        if self._root is None:
            return
        try:
            self._root.update()
        except TclError:
            pass

    def _synthesize(self) -> None:
        """Synthesise long-press (quit) and auto-repeat (scrolling)."""
        now = time.monotonic()
        generated: list[InputEvent] = []
        with self._lock:
            for action, (since, last, long_fired) in list(self._held.items()):
                if action is InputAction.MENU and not long_fired:
                    if now - since >= _LONG_PRESS:
                        self._held[action] = (since, last, True)
                        generated.append(InputEvent(action, InputKind.LONG_PRESS))
                        continue
                if action not in _REPEATABLE:
                    continue
                if now - since < _REPEAT_DELAY:
                    continue
                fired = 0
                nlast = last
                while now - nlast >= _REPEAT_RATE and fired < 3:
                    nlast += _REPEAT_RATE
                    fired += 1
                if fired:
                    self._held[action] = (since, nlast, long_fired)
                    generated.extend(
                        InputEvent(action, InputKind.REPEAT) for _ in range(fired)
                    )
            self._events.extend(generated)

    # ------------------------------------------------------------------ #
    # Hardware
    # ------------------------------------------------------------------ #

    def battery(self) -> int | None:
        return None

    def temperature(self) -> float | None:
        return None

    def set_brightness(self, value: int, index: int = 0) -> None:
        return None

    # ------------------------------------------------------------------ #
    # Filesystem
    # ------------------------------------------------------------------ #

    @property
    def rom_root(self) -> Path:
        return self._rom_root

    @property
    def config_dir(self) -> Path:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        return self._config_dir

    def list_dir(self, path: Path) -> list[FileEntry]:
        try:
            with os.scandir(path) as iterator:
                return [
                    FileEntry(
                        name=entry.name,
                        is_dir=entry.is_dir(),
                        size=entry.stat(follow_symlinks=False).st_size,
                        mtime=entry.stat(follow_symlinks=False).st_mtime,
                    )
                    for entry in iterator
                ]
        except OSError:
            return []

    # ------------------------------------------------------------------ #
    # Launching
    # ------------------------------------------------------------------ #

    def launch_game(self, argv: Sequence[str]) -> None:
        # The real launcher is a Linux shell script; on the desktop the app
        # stays resident (can_stay_resident -> True) and the subprocess simply
        # fails, which surfaces as an error toast instead of exiting.
        log.info("desktop: would launch %s", " ".join(str(a) for a in argv))

    # ------------------------------------------------------------------ #
    # Fonts / media
    # ------------------------------------------------------------------ #

    def font(self, size: int) -> object:
        return self._fonts.get(size)

    def load_image(self, path: Path) -> object:
        with Image.open(path) as handle:
            return handle.convert("RGBA").copy()

    def save_screenshot(self, canvas: Canvas, path: Path) -> None:
        if isinstance(canvas, PilCanvas):
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            canvas.pil_image.save(target)

    def open_video_pipe(
        self, path: Path, *, width: int, height: int, fps: int
    ) -> VideoPipe | None:
        # No ffmpeg assumption on the desktop: the bottom panel falls back to
        # cover art, exactly as DESIGN §6.5 prescribes.
        return None

    def open_audio_pipe(self, path: Path, *, volume: float = 1.0) -> AudioPipe | None:
        return None

    def can_stay_resident(self) -> bool:
        return True

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def shutdown(self) -> None:
        self._closed = True
        if self._root is not None:
            try:
                self._root.destroy()
            except TclError:
                pass
            self._root = None
        self._canvases.clear()
        self._labels.clear()
        self._photos.clear()
