"""Linux handheld platform implementation.

Covers the RG DS and, by being conservative, most other Linux-based handhelds.
Also usable on a desktop for development: pass ``headless=True`` and the app
runs against plain PIL canvases with no SDL and no input device.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Sequence

from PIL import Image

from ...core.theme import BASE_H, BASE_W, metrics_for
from ...launcher.launch import write_launch_cmd
from ..base import Canvas, FileEntry, InputEvent, Platform, VideoPipe
from .canvas import PilCanvas, save_bitmap
from .display import SDLDisplay
from .fonts import FontBook
from .input import EvdevInput
from . import hw as sysfs
from . import video as ffmpeg_pipe

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #

#: Every ROM root this device may have, with the label the settings menu shows.
#: Two cards are browsed as two separate libraries, never merged: the scan is
#: ``rom_root/<system>`` against a single root, and a game's key is
#: ``<system>/<file name>``, so merging would make the same title on both cards
#: collide -- and re-keying would orphan every existing favourite.
_ROM_ROOTS: tuple[tuple[str, str], ...] = (
    ("/mnt/mmc/Roms", "TF1"),     # SD1 / TF1 (measured, primary)
    ("/mnt/sdcard/Roms", "TF2"),  # SD2
)
_ROM_ROOT_CANDIDATES: tuple[str, ...] = tuple(path for path, _label in _ROM_ROOTS)


def available_rom_roots() -> list[tuple[Path, str]]:
    """The ROM roots that actually exist here, with their labels.

    Empty on a dev machine, one entry on a single-card device (nothing to
    switch between) and two when a second card is installed.
    """
    found: list[tuple[Path, str]] = []
    for candidate, label in _ROM_ROOTS:
        path = Path(candidate)
        if path.is_dir():
            found.append((path, label))
    return found

_CONFIG_DIR_CANDIDATES: tuple[str, ...] = (
    "/mnt/mmc/Roms/APPS/Retrostation",
    "/userdata/Retrostation",
)


def _first_existing_dir(candidates: Sequence[str]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_dir():
            return path
    return None


def resolve_rom_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("RETROSTATION_ROM_ROOT")
    if env:
        return Path(env)
    found = _first_existing_dir(_ROM_ROOT_CANDIDATES)
    if found:
        return found
    return Path.cwd() / "roms"  # development fallback


def resolve_config_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("RETROSTATION_CONFIG_DIR")
    if env:
        return Path(env)
    found = _first_existing_dir(_CONFIG_DIR_CANDIDATES)
    if found and os.access(found, os.W_OK):
        return found
    return Path.home() / ".retrostation"


# --------------------------------------------------------------------------- #
# Platform
# --------------------------------------------------------------------------- #


class LinuxPlatform(Platform):
    """SDL2 + PIL + evdev implementation of :class:`Platform`."""

    name = "linux"

    def __init__(
        self,
        *,
        rom_root: str | None = None,
        config_dir: str | None = None,
        headless: bool = False,
        input_device: str | None = None,
        keymap: dict[int, object] | None = None,
        font_dirs: tuple[str, ...] | None = None,
    ) -> None:
        self._headless = headless
        self._rom_root = resolve_rom_root(rom_root)
        self._config_dir = resolve_config_dir(config_dir)
        self._fonts = FontBook(font_dirs)
        self._display: SDLDisplay | None = None
        self._canvases: list[Canvas] = []
        self._input = EvdevInput(input_device, keymap=keymap)  # type: ignore[arg-type]

    # -- display ---------------------------------------------------------- #

    def init_display(self, mode: str) -> list[Canvas]:
        if self._display is not None:
            return self._canvases

        if self._headless:
            count = 2 if mode in ("dual", "auto") else 1
            self._canvases = [PilCanvas(BASE_W, BASE_H) for _ in range(count)]
            return self._canvases

        self._display = SDLDisplay(mode)
        self._canvases = list(self._display.canvases)
        return self._canvases

    def present(self, index: int) -> None:
        if self._display is not None:
            self._display.present(index)

    # -- input ------------------------------------------------------------ #

    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        return self._input.poll_events(timeout)

    # -- hardware --------------------------------------------------------- #

    def battery(self) -> int | None:
        return sysfs.battery_level()

    def temperature(self) -> float | None:
        return sysfs.cpu_temperature()

    def set_brightness(self, value: int, index: int = 0) -> None:
        sysfs.set_backlight(value, index)

    # -- filesystem ------------------------------------------------------- #

    @property
    def rom_root(self) -> Path:
        return self._rom_root

    def available_rom_roots(self) -> list[tuple[Path, str]]:
        return available_rom_roots()

    def rom_root_label(self) -> str:
        for path, label in available_rom_roots():
            if path == self._rom_root:
                return label
        return self._rom_root.name

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

    # -- launching -------------------------------------------------------- #

    def launch_game(self, argv: Sequence[str]) -> None:
        """Queue the command for the bootstrap instead of exec'ing it.

        This used to ``os.execv`` straight into the emulator, which handed the
        emulator's exit code to the bootstrap: it read a plain quit (0) and
        dropped the player on the APPS menu instead of resuming the session.
        Writing the command out and returning keeps the exit-code contract
        intact -- the app unwinds, exits 42, and only then does the game start
        (DESIGN §8.2).  Releasing the display is :meth:`App.run`'s job.
        """
        args = [str(a) for a in argv]
        if not args:
            raise ValueError("launch_game() needs a command")
        write_launch_cmd(args, self.launch_cmd_path)
        log.info("queued launch: %s", " ".join(args))

    # -- fonts / media ---------------------------------------------------- #

    def font(self, size: int) -> object:
        return self._fonts.get(size)

    def load_image(self, path: Path) -> object:
        with Image.open(path) as handle:
            return handle.convert("RGBA").copy()

    def save_screenshot(self, canvas: Canvas, path: Path) -> None:
        if isinstance(canvas, PilCanvas):
            save_bitmap(canvas, path)

    def open_video_pipe(self, path: Path, *, width: int, height: int, fps: int) -> VideoPipe | None:
        """Spawn ``ffmpeg``; ``None`` when it is missing or the file is junk.

        A missing binary is normal (stock firmwares ship without it), so this
        is a debug log rather than a warning: the UI just shows cover art.
        """
        if not ffmpeg_pipe.available():
            log.debug("ffmpeg not available; video disabled")
            return None
        try:
            return ffmpeg_pipe.FFmpegPipe(path, width=width, height=height, fps=fps)
        except (OSError, ValueError) as exc:
            log.warning("cannot decode %s: %s", path, exc)
            return None

    def load_metrics(self, index: int = 0) -> object:
        """Convenience helper for building :class:`Metrics` for a canvas."""
        if index < len(self._canvases):
            width, height = self._canvases[index].size
        else:
            width, height = BASE_W, BASE_H
        return metrics_for(width, height)

    # -- lifecycle -------------------------------------------------------- #

    def on_resume(self) -> None:
        """Nothing to restore: we come back as a fresh process by design."""

    def shutdown(self) -> None:
        if self._display is not None:
            self._display.close()
            self._display = None
        self._canvases.clear()
        self._input.close()
