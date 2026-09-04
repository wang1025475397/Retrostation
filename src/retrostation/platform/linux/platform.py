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
from ..base import AudioPipe, Canvas, FileEntry, InputEvent, Platform, VideoPipe
from .canvas import PilCanvas, save_bitmap
from .display import SDLDisplay
from .fonts import FontBook
from . import ffmpeg as ffmpeg_codec
from .input import EvdevInput
from . import hw as sysfs
from . import video as ffmpeg_pipe

log = logging.getLogger(__name__)

#: Minimum RAM for the frontend to stay resident while a game runs (kB).
#: Measured cost is ~105 MB on the RG DS (3.9k ROM library + cover cache) --
#: ~3.5% of this box's 3 GB, but a tenth of a 1 GB device, where it would
#: compete with the emulator.  Small devices hand over by exiting instead.
_RESIDENT_MIN_TOTAL_KB = 1_800_000
#: ...and how much of it must still be free when we look.
_RESIDENT_MIN_AVAILABLE_KB = 700_000


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


def resolve_ports_dir(rom_root: Path) -> Path | None:
    """Where the ports system lives: ``Roms/PORTS`` first, then the sibling
    ``Ports`` folder next to Roms (this firmware's layout), else ``None``."""
    primary = rom_root / "PORTS"
    if primary.is_dir():
        return primary
    sibling = rom_root.parent / "Ports"
    if sibling.is_dir():
        return sibling
    return None


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

    def _ports_dir(self) -> Path | None:
        """Where the ports system lives on this firmware."""
        return resolve_ports_dir(self._rom_root)

    def system_dir(self, system_key: str) -> Path:
        if system_key.casefold() == "ports":
            ports = self._ports_dir()
            if ports is not None:
                return ports
        return self._rom_root / system_key

    def extra_system_keys(self) -> list[str]:
        # ``ports`` only when it is not a sub-directory of rom_root -- then it
        # would never show up in the library scan's directory listing.
        if (self._rom_root / "PORTS").is_dir():
            return []
        if (self._rom_root.parent / "Ports").is_dir():
            return ["ports"]
        return []

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

    def transcode_image(self, source: Path, target: Path, width: int, height: int) -> bool:
        """Decode a file Pillow cannot open itself -- see :mod:`.ffmpeg`.

        Only formats we know Pillow is broken for are worth the ~80 ms of
        shelling out; a PNG that fails to open is simply a corrupt file.
        """
        if not ffmpeg_codec.is_recoverable(source):
            return False
        return ffmpeg_codec.transcode(source, target, width, height)

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

    def open_audio_pipe(self, path: Path, *, volume: float = 1.0) -> AudioPipe | None:
        """Sound the clip's track through ALSA; ``None`` when that is impossible.

        Missing ``aplay``/``ffmpeg`` and clips with no audio track at all are
        both ordinary, so every failure here is a debug log: the preview simply
        stays silent, which is what it always used to be.
        """
        from .audio import AlsaAudioPipe, available

        if not available():
            log.debug("aplay/ffmpeg not available; preview stays silent")
            return None
        try:
            return AlsaAudioPipe(path, volume=volume)
        except (OSError, ValueError) as exc:
            log.warning("cannot play audio for %s: %s", path, exc)
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

    def suspend_display(self) -> None:
        # Stop input *before* hiding the windows: the device is shared (we do
        # not grab it), so everything the player presses in the game would
        # otherwise be queued and replayed into the menu on the way back.
        self._input.pause()
        if self._display is not None:
            self._display.hide()

    def resume_display(self) -> None:
        if self._display is not None:
            self._display.show()
        self._input.resume()

    def can_stay_resident(self) -> bool:
        """Enough RAM to stay alive while a game runs?

        Staying resident saves ~2 s per launch (no process exit and the ~2 s
        the kernel spends reclaiming our surfaces, no rescan, no window
        rebuild) but keeps ~105 MB of library and artwork in RAM alongside the
        emulator.
        """
        try:
            fields: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    fields[key] = int(rest.split()[0])
        except (OSError, ValueError, IndexError):
            return False        # cannot tell -- take the safe path
        return (fields.get("MemTotal", 0) >= _RESIDENT_MIN_TOTAL_KB
                and fields.get("MemAvailable", 0) >= _RESIDENT_MIN_AVAILABLE_KB)
