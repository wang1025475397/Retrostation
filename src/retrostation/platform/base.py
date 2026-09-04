"""Platform abstraction.

This is the seam that keeps the rest of the codebase portable:

* ``ui/`` may only call :class:`Canvas` methods -- never PIL, never SDL.
* ``data/`` may only ask the :class:`Platform` for paths and directory
  listings -- never hard-code ``/mnt/mmc``.
* Input arrives as **semantic events** (:class:`InputAction`); nothing above
  this module knows what an evdev code is.

On the Linux handheld everything below is SDL2 + PIL + evdev.  On Android the
same interfaces will be backed by KeyEvent/MotionEvent and MediaPlayer, which
is the whole point of keeping them this small.
"""

from __future__ import annotations

import abc
import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #


class InputAction(str, enum.Enum):
    """Semantic buttons -- the only vocabulary the UI is allowed to know."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"

    A = "a"          # confirm / launch
    B = "b"          # back
    X = "x"          # cycle view (list / grid / carousel)
    Y = "y"          # favourite
    #: Hide / unhide the game under the cursor.  Deliberately not a face button:
    #: the handheld has no spare one (and only MENU synthesises a long press),
    #: so this is bound on the desktop keymap and reached from the menu
    #: everywhere else.
    HIDE = "hide"

    L1 = "l1"
    R1 = "r1"
    L2 = "l2"
    R2 = "r2"

    START = "start"
    SELECT = "select"
    MENU = "menu"    # long press = quit

    #: The device's own volume rocker.  Not game buttons: while the frontend is
    #: up they move the preview volume, which is the only thing here that makes
    #: a sound of its own.
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"


class InputKind(str, enum.Enum):
    PRESS = "press"
    RELEASE = "release"
    #: Auto-repeat while held (list scrolling).
    REPEAT = "repeat"
    #: Held past the long-press threshold without moving.
    LONG_PRESS = "long_press"


@dataclass(frozen=True)
class InputEvent:
    """One semantic input event."""

    action: InputAction
    kind: InputKind = InputKind.PRESS
    #: Touch coordinates, only set for ``InputAction.TAP``.
    x: int | None = None
    y: int | None = None

    @property
    def is_press(self) -> bool:
        return self.kind in (InputKind.PRESS, InputKind.REPEAT)

    @property
    def is_repeat(self) -> bool:
        return self.kind is InputKind.REPEAT

    @property
    def is_long(self) -> bool:
        return self.kind is InputKind.LONG_PRESS

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pos = f" @({self.x},{self.y})" if self.x is not None else ""
        return f"<{self.kind.value}:{self.action.value}{pos}>"


# --------------------------------------------------------------------------- #
# Drawing surface
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rect:
    """Axis-aligned box in screen pixels."""

    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_box(cls, box: Sequence[float]) -> Rect:
        x, y, w, h = box
        return cls(int(x), int(y), int(w), int(h))

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def box(self) -> tuple[int, int, int, int]:
        """PIL-style ``(left, top, right, bottom)`` box."""
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def inflate(self, dx: int, dy: int) -> Rect:
        return Rect(self.x - dx, self.y - dy, self.w + 2 * dx, self.h + 2 * dy)


#: Opaque decoded bitmap.  Implemented by the platform (PIL today).
Bitmap = "Any"


class Canvas(abc.ABC):
    """A drawing surface.

    Implementations must be cheap to create and must not require an explicit
    ``flush`` -- the platform calls :meth:`Platform.present` when a frame is
    ready.
    """

    #: (width, height) in pixels.
    size: tuple[int, int]

    # -- whole surface ---------------------------------------------------- #

    @abc.abstractmethod
    def clear(self, color: Sequence[int]) -> None:
        """Fill the whole surface with ``color`` (RGBA)."""

    # -- shapes ----------------------------------------------------------- #

    @abc.abstractmethod
    def rect(
        self,
        box: Sequence[float],
        *,
        fill: Sequence[int] | None = None,
        outline: Sequence[int] | None = None,
        width: int = 1,
    ) -> None:
        """Draw a rectangle.  ``box`` is ``(x, y, w, h)``."""

    @abc.abstractmethod
    def rounded_rect(
        self,
        box: Sequence[float],
        *,
        radius: int,
        fill: Sequence[int] | None = None,
        outline: Sequence[int] | None = None,
        width: int = 1,
    ) -> None:
        """Draw a rounded rectangle, ``(x, y, w, h)``."""

    @abc.abstractmethod
    def hgradient(
        self,
        box: Sequence[float],
        *,
        start: Sequence[int],
        end: Sequence[int],
        radius: int = 0,
    ) -> None:
        """Horizontal linear gradient, used for the selected row highlight."""

    @abc.abstractmethod
    def ellipse(
        self,
        box: Sequence[float],
        *,
        fill: Sequence[int] | None = None,
        outline: Sequence[int] | None = None,
        width: int = 1,
    ) -> None:
        """Draw an ellipse inscribed in ``box`` ``(x, y, w, h)``."""

    # -- text ------------------------------------------------------------- #

    @abc.abstractmethod
    def text(
        self,
        xy: Sequence[float],
        content: str,
        *,
        font: object,
        fill: Sequence[int],
        anchor: str = "la",
    ) -> None:
        """Draw ``content`` with its anchor at ``xy`` (PIL anchor syntax)."""

    @abc.abstractmethod
    def text_width(self, content: str, *, font: object) -> int:
        """Measured advance width, used by wrapping and ellipsis."""

    @abc.abstractmethod
    def text_height(self, content: str, *, font: object) -> int:
        """Rendered height of ``content``, for vertical centring."""

    # -- bitmaps ---------------------------------------------------------- #

    @abc.abstractmethod
    def image(self, bitmap: object, box: Sequence[float]) -> None:
        """Draw ``bitmap`` scaled into ``box`` ``(x, y, w, h)``."""

    @abc.abstractmethod
    def image_fit(
        self,
        bitmap: object,
        box: Sequence[float],
        *,
        halign: str = "center",
        valign: str = "center",
    ) -> None:
        """Draw ``bitmap`` contained inside ``box``, never stretched.

        Logos are wide and thin, covers are portrait; both must keep their
        aspect ratio inside their slot.
        """

    @abc.abstractmethod
    def dim(self, bitmap: object, opacity: int) -> object:
        """Return a copy of ``bitmap`` scaled to ``opacity`` (0-255).

        Used for the dimmed neighbour cards in the carousel view.  Returns the
        same bitmap when ``opacity`` is 255.
        """

    # -- text layout ------------------------------------------------------ #

    @abc.abstractmethod
    def wrap_text(
        self, text: str, *, font: object, max_width: int, max_lines: int
    ) -> list[str]:
        """Greedy wrap for CJK text (no spaces to break on), with ellipsis."""


# --------------------------------------------------------------------------- #
# Video
# --------------------------------------------------------------------------- #


class VideoPipe(abc.ABC):
    """A decoder that hands out one decoded frame at a time (DESIGN §6.5).

    On the handheld this is an ``ffmpeg`` process writing rawvideo into a pipe;
    on Android it will be a MediaCodec surface.  Either way the frame is an
    opaque bitmap from the platform -- ``data/`` never imports PIL.
    """

    #: Decoded frame size in pixels.
    size: tuple[int, int]

    @abc.abstractmethod
    def read_frame(self) -> object | None:
        """Block until the next frame is decoded.

        Returns ``None`` at end of stream, and also once :meth:`close` has been
        called from another thread -- terminating the decoder is what unblocks
        this call, so implementations must make ``close()`` safe to call
        concurrently.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Stop decoding and release the process.  Must be idempotent."""

    @property
    def duration(self) -> float:
        """Clip length in seconds, or ``0`` when the decoder cannot tell.

        Read once by the pumping thread before the first frame is published;
        probing here keeps an ``ffprobe`` call off the UI thread.
        """
        return 0.0


# --------------------------------------------------------------------------- #
# Filesystem
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FileEntry:
    """One directory item.

    Deliberately path-free: the platform may be backed by something that is not
    a POSIX filesystem (Android SAF returns opaque document URIs), so callers
    hand the entry back to the platform instead of building paths themselves.
    """

    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


class AudioPipe(abc.ABC):
    """A player that is sounding one clip's track (DESIGN §6.5).

    Much smaller than :class:`VideoPipe`: there is no per-frame work to do, only
    "stop".  A platform that cannot play sound never hands one out, and the
    video then plays silently exactly as it always has.
    """

    @abc.abstractmethod
    def close(self) -> None:
        """Stop sounding.  Must be safe to call twice and must not raise."""

    def set_volume(self, volume: float) -> None:
        """Change the loudness of what is playing, 0.0-1.0.

        Optional.  The default does nothing, which is right for a platform that
        would have to rebuild its pipe to change volume: rebuilding means
        waiting for the audio device again, and that stalls the frame loop -- so
        the old volume simply stays until the next clip starts.
        """


# --------------------------------------------------------------------------- #
# Platform
# --------------------------------------------------------------------------- #


class Platform(abc.ABC):
    """Everything the app needs from the operating system."""

    #: Short identifier, used in logs and in the About screen.
    name: str = "base"

    #: Where the shell bootstrap looks for a pending launch command
    #: (DESIGN §8.2).  Only platforms that hand off through a file use it.
    launch_cmd_path: str = "/tmp/retrostation_launch.cmd"

    # -- display ---------------------------------------------------------- #

    @abc.abstractmethod
    def init_display(self, mode: str) -> list[Canvas]:
        """Create the canvases.

        ``mode`` is one of ``"dual"`` / ``"single"`` / ``"auto"``.  Returns one
        canvas for single-screen devices and two for dual-screen ones (top
        first, bottom second).  Must be idempotent per process: creating,
        destroying and re-creating windows under Wayland crashes, so callers
        are expected to do it exactly once (see DESIGN §8.2).
        """

    @abc.abstractmethod
    def present(self, index: int) -> None:
        """Push the current contents of canvas ``index`` to the screen."""

    # -- input ------------------------------------------------------------ #

    @abc.abstractmethod
    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        """Drain pending input, waiting at most ``timeout`` seconds.

        Must never block indefinitely: the main loop also drives animations
        and the video decoder.
        """

    # -- hardware --------------------------------------------------------- #

    @abc.abstractmethod
    def battery(self) -> int | None:
        """Battery percentage 0-100, or ``None`` when unknown."""

    @abc.abstractmethod
    def temperature(self) -> float | None:
        """CPU temperature in degrees Celsius, or ``None``."""

    @abc.abstractmethod
    def set_brightness(self, value: int, index: int = 0) -> None:
        """Set backlight for screen ``index``; ignore failures silently."""

    # -- filesystem ------------------------------------------------------- #

    @property
    @abc.abstractmethod
    def rom_root(self) -> Path:
        """Root directory that holds one sub-directory per system."""

    def available_rom_roots(self) -> list[tuple[Path, str]]:
        """ROM roots present on this device, with a short label each.

        One entry means there is nothing to switch between; two means the
        cards are browsed separately rather than merged.
        """
        return [(self.rom_root, self.rom_root.name)]

    def rom_root_label(self) -> str:
        """Short label for the root in use ("TF1"), else its folder name."""
        for path, label in self.available_rom_roots():
            if path == self.rom_root:
                return label
        return self.rom_root.name

    @property
    @abc.abstractmethod
    def config_dir(self) -> Path:
        """Directory for ``config.json`` / ``state.json``."""

    @abc.abstractmethod
    def list_dir(self, path: Path) -> list[FileEntry]:
        """List ``path``; returns ``[]`` when it cannot be read."""

    # -- launching -------------------------------------------------------- #

    @abc.abstractmethod
    def launch_game(self, argv: Sequence[str]) -> None:
        """Hand the device over to a game.

        Implementations **return** -- they must not replace the process.  The
        app still has to unwind so it can exit with the "a game ran" code,
        which is the only way the shell bootstrap can tell that apart from a
        plain quit (DESIGN §8.2).  On Linux the command is written to
        :attr:`launch_cmd_path` for the bootstrap to run; on Android it will
        start an activity.
        """

    def on_resume(self) -> None:
        """Called after a game exits.  Default: nothing to do."""

    # -- fonts ------------------------------------------------------------ #

    @abc.abstractmethod
    def font(self, size: int) -> object:
        """A cached font object usable by :class:`Canvas`.

        Must be cached by the platform: constructing a TTF font per frame is a
        measurable frame-rate loss.
        """

    # -- media ------------------------------------------------------------ #

    @abc.abstractmethod
    def load_image(self, path: Path) -> object:
        """Decode an image, or raise :class:`OSError`."""

    def transcode_image(self, source: Path, target: Path, width: int, height: int) -> bool:
        """Decode ``source`` into ``target`` using an external decoder.

        A fallback for files :meth:`load_image` cannot open even though the
        file itself is fine: the RG DS links Pillow against a libjpeg it does
        not ship, so every JPEG cover there raises on open.  ffmpeg is present
        and decodes them, so the Linux platform overrides this.

        ``target`` is written at most ``width x height``.  Returns ``True`` on
        success.  The default says no, which keeps platforms without such a
        decoder honest instead of pretending to have tried.
        """
        return False

    def save_screenshot(self, canvas: Canvas, path: Path) -> None:
        """Write ``canvas`` to ``path`` (development / diagnostics only)."""
        raise NotImplementedError

    def open_video_pipe(
        self,
        path: Path,
        *,
        width: int,
        height: int,
        fps: int,
    ) -> VideoPipe | None:
        """Decode ``path`` into frames of ``width x height`` at ``fps``.

        ``None`` means "this platform cannot decode video" (or the file cannot
        be opened): the caller then silently falls back to cover art, which is
        the behaviour DESIGN §6.5 asks for.  Implementations must return
        quickly -- this runs on the UI thread.
        """
        return None

    def open_audio_pipe(self, path: Path, *, volume: float = 1.0) -> AudioPipe | None:
        """Sound the track of ``path`` while its clip is being previewed.

        ``None`` means this platform has no way to play sound, which is the
        default and keeps every other platform (and the test double) exactly as
        silent as before.  Implementations must return quickly -- this runs on
        the UI thread, next to :meth:`open_video_pipe`.
        """
        return None

    @abc.abstractmethod
    def shutdown(self) -> None:
        """Release display/input resources before handing over to a game."""

    def suspend_display(self) -> None:
        """Hide the UI so another program can use the screen (optional).

        The process stays alive and keeps whatever context it needs to come
        back.  Implementations that cannot do this leave the no-op and
        :meth:`can_stay_resident` False, which keeps the hand-off-by-exit path
        described in DESIGN §8.2.
        """

    def resume_display(self) -> None:
        """Undo :meth:`suspend_display`."""

    def can_stay_resident(self) -> bool:
        """Whether there is enough memory to stay alive while a game runs.

        Conservative default: a platform that does not check keeps handing the
        device over by exiting, which costs time but no extra memory.
        """
        return False
