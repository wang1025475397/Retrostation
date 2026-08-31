"""Shared fixtures: make ``src/`` importable and provide a fake platform."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from PIL import Image  # noqa: E402

from retrostation.platform.base import (  # noqa: E402
    Canvas,
    FileEntry,
    InputEvent,
    Platform,
)


class FakePlatform(Platform):
    """A filesystem-backed platform with no SDL and no input device.

    Good enough for every data-layer test; the Linux implementation is
    exercised separately by the canvas tests.
    """

    name = "fake"

    def __init__(self, root: Path) -> None:
        self._root = root
        self.canvases: list[Canvas] = []
        self.injected: list[InputEvent] = []
        self.launched: tuple[str, ...] | None = None

    # display ----------------------------------------------------------- #
    def init_display(self, mode: str) -> list[Canvas]:
        """Headless canvases: same size as the real panels, no SDL."""
        from retrostation.core.theme import BASE_H, BASE_W
        from retrostation.platform.linux.canvas import PilCanvas

        self.canvases = [PilCanvas(BASE_W, BASE_H) for _ in range(2 if mode in ("dual", "auto") else 1)]
        return self.canvases

    def present(self, index: int) -> None:
        return None

    # input ------------------------------------------------------------- #
    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        events, self.injected = self.injected, []
        return events

    def send(self, *events: InputEvent) -> None:
        """Queue events for the next poll (tests drive the app this way)."""
        self.injected.extend(events)

    # hardware ---------------------------------------------------------- #
    def battery(self) -> int | None:
        return 87

    def temperature(self) -> float | None:
        return 56.6

    def set_brightness(self, value: int, index: int = 0) -> None:
        return None

    # filesystem -------------------------------------------------------- #
    @property
    def rom_root(self) -> Path:
        return self._root

    @property
    def config_dir(self) -> Path:
        path = self._root / ".retrostation"
        path.mkdir(parents=True, exist_ok=True)
        return path

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

    # launching --------------------------------------------------------- #
    def launch_game(self, argv) -> None:
        """Record the command instead of exec'ing it (the test asserts on it)."""
        self.launched = tuple(argv)

    # fonts / media ----------------------------------------------------- #
    def font(self, size: int) -> object:
        from PIL import ImageFont

        return ImageFont.load_default()

    def load_image(self, path: Path) -> object:
        from PIL import Image

        with Image.open(path) as handle:
            return handle.convert("RGBA").copy()

    def save_screenshot(self, canvas: Canvas, path: Path) -> None:
        if isinstance(canvas, PilCanvas):
            canvas.pil_image.save(path)

    def shutdown(self) -> None:
        return None


def _remove_untrusted_link(path: str) -> bool:
    """Delete a Windows symlink Python refuses to traverse.

    Without Developer Mode, Windows marks a symlink that points outside its own
    directory as an *untrusted mount point*: ``Path.resolve``, ``os.unlink`` and
    even ``os.rmdir`` all fail with ``WinError 448``.  The raw Win32 calls act on
    the reparse point itself and do work, so try them first.
    """
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    for call in (kernel32.RemoveDirectoryW, kernel32.DeleteFileW):
        call.restype = ctypes.c_bool
        if call(path):
            return True
    return False


def _drop_current_links(directory: Path) -> None:
    """Remove every ``*current`` link in ``directory``."""
    try:
        entries = list(directory.glob("*current"))
    except OSError:  # noqa: BLE001 - cleanup must never break the session
        return
    for entry in entries:
        # No stat() first: on Windows even asking can raise WinError 448.
        if os.name == "nt" and _remove_untrusted_link(str(entry)):
            continue
        for action in (os.unlink, os.rmdir):
            try:
                action(entry)
                break
            except OSError:
                continue


def pytest_sessionfinish(session) -> None:
    """Drop pytest's ``*current`` symlinks before its own cleanup runs.

    Resolving them raises ``WinError 448`` on Windows, which aborts pytest's
    tmpdir hook and takes the whole failure report down with it -- the test run
    then looks green when it is not, and exits 1 even with zero failures.
    Removing them first is harmless on Linux.
    """
    # ``_tmp_path_factory`` is current pytest; older releases used ``_tmp_path_handler``.
    handler = getattr(session.config, "_tmp_path_factory", None)
    if handler is None:
        handler = getattr(session.config, "_tmp_path_handler", None)
    if handler is None:
        return
    try:
        basetemp = Path(handler.getbasetemp())
    except Exception:  # noqa: BLE001 - never break the session over cleanup
        return

    _drop_current_links(basetemp)
    if getattr(handler, "_given_basetemp", None) is None:
        # pytest keeps one more "pytest-current" link above the numbered
        # basetemp (in ``pytest-of-<user>``); it is ours only when the user did
        # not pass ``--basetemp``.
        _drop_current_links(basetemp.parent)


def png_bytes(color=(200, 120, 40, 255), size=(16, 16)) -> bytes:
    """A real PNG, because ``load_image()`` must be able to decode fixtures."""
    import io

    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def rom_root(tmp_path: Path) -> Path:
    """A library root with two systems and a few files."""
    fc = tmp_path / "FC"
    fc.mkdir()
    (fc / "超级马力欧兄弟.nes").write_bytes(b"nes")
    (fc / "魂斗罗.nes").write_bytes(b"nes")
    (fc / "冒險島 [T-Eng].nes").write_bytes(b"nes")
    (fc / "README.txt").write_text("not a rom", encoding="utf-8")
    (fc / "Imgs").mkdir()
    (fc / "Imgs" / "魂斗罗.png").write_bytes(png_bytes())
    (fc / ".cache").mkdir()
    (fc / ".cache" / "junk.nes").write_bytes(b"junk")

    gba = tmp_path / "GBA"
    gba.mkdir()
    (gba / "黄金太阳.gba").write_bytes(b"gba")
    return tmp_path


@pytest.fixture
def platform(rom_root: Path) -> FakePlatform:
    return FakePlatform(rom_root)
