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

    L1 = "l1"
    R1 = "r1"
    L2 = "l2"
    R2 = "r2"

    START = "start"
    SELECT = "select"
    MENU = "menu"    # long press = quit


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


# --------------------------------------------------------------------------- #
# Platform
# --------------------------------------------------------------------------- #


class Platform(abc.ABC):
    """Everything the app needs from the operating system."""

    #: Short identifier, used in logs and in the About screen.
    name: str = "base"

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

        On Linux this replaces the process (the shell bootstrap in
        ``launcher/`` restarts us afterwards); on Android it will start an
        activity.  Either way this call is not expected to return.
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

    def save_screenshot(self, canvas: Canvas, path: Path) -> None:
        """Write ``canvas`` to ``path`` (development / diagnostics only)."""
        raise NotImplementedError

    @abc.abstractmethod
    def shutdown(self) -> None:
        """Release display/input resources before handing over to a game."""
