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

from ..core.theme import Form
from .targets import LaunchTarget, UnsupportedTarget


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
    MENU = "menu"    # long press = quit
    #: Open the search dialog.  The handheld's SELECT key and the desktop's
    #: Tab / "/" both map here.
    SEARCH = "search"

    #: The device's own volume rocker.  Not game buttons: while the frontend is
    #: up they move the preview volume, which is the only thing here that makes
    #: a sound of its own.
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"

    #: A typed character with no key mapping (desktop only; see
    #: :attr:`InputEvent.text`).  Handlers that do not care about typing
    #: ignore it, so it is inert everywhere except the search dialog.
    CHAR = "char"

    # -- touch ------------------------------------------------------------ #
    #
    # Produced only by platforms that have a touchscreen (Android; the RG DS
    # panel is wired but never exposed one).  Every feature must still be
    # reachable with buttons alone (DESIGN §5), so a screen that does not
    # implement hit-testing simply ignores these -- which is why adding them
    # changes nothing on the handhelds.

    #: A finger landed and lifted at :attr:`InputEvent.x` / ``y``, in the
    #: coordinate space of :attr:`InputEvent.screen`'s canvas.
    TAP = "tap"
    #: A finger is dragging: :attr:`InputEvent.dx` / ``dy`` carry the movement
    #: since the previous event, in canvas units.
    DRAG = "drag"
    #: The finger left the screen while still moving; ``dx`` / ``dy`` carry the
    #: velocity for inertial scrolling.
    FLING = "fling"


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
    #: Touch position for :attr:`InputAction.TAP`, in the coordinate space of
    #: :attr:`screen`'s canvas -- the platform maps physical pixels into it, so
    #: the UI never learns the device's real resolution.
    x: int | None = None
    y: int | None = None
    #: Movement (``DRAG``) or velocity (``FLING``) in the same canvas units.
    dx: int = 0
    dy: int = 0
    #: Which canvas the touch landed on: 0 = top, 1 = bottom.  Button events
    #: leave it 0 -- a key press does not belong to a screen, and on a
    #: dual-screen device both windows feed the same queue (DESIGN.ANDROID
    #: §6.4.3).
    screen: int = 0
    #: The typed character for keys that carry one (desktop keyboard).  An
    #: event can be both: ``s`` types an "s" *and* maps to START, and the
    #: handler in charge decides which side it consumes.
    text: str = ""

    @property
    def is_press(self) -> bool:
        return self.kind in (InputKind.PRESS, InputKind.REPEAT)

    @property
    def is_repeat(self) -> bool:
        return self.kind is InputKind.REPEAT

    @property
    def is_long(self) -> bool:
        return self.kind is InputKind.LONG_PRESS

    @property
    def is_touch(self) -> bool:
        """Whether this came from a finger rather than a button."""
        return self.action in (InputAction.TAP, InputAction.DRAG, InputAction.FLING)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pos = f" @({self.x},{self.y})" if self.x is not None else ""
        delta = f" d({self.dx},{self.dy})" if (self.dx or self.dy) else ""
        screen = f" s{self.screen}" if self.screen else ""
        return f"<{self.kind.value}:{self.action.value}{pos}{delta}{screen}>"


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

    # -- incremental repaint ---------------------------------------------- #
    #
    # Repainting a whole panel costs more than a frame budget on the handheld
    # (~32 ms measured, DESIGN §9.4), so the app paints one without its
    # selection highlight, keeps that, and restores it while only the cursor
    # moves.  The stored handle is **opaque**: PIL image here, a Bitmap on
    # Android.  Callers may only hand it back to the methods below -- that is
    # what keeps ``ui/`` free of any imaging library.

    @abc.abstractmethod
    def snapshot(self, box: Sequence[float] | None = None) -> object:
        """Copy of the whole surface, or of ``box`` ``(x, y, w, h)``."""

    @abc.abstractmethod
    def restore(self, snapshot: object, at: Sequence[float] | None = None) -> None:
        """Blit ``snapshot`` back, at ``at`` ``(x, y)`` or at the origin.

        Replaces the destination pixels rather than compositing: a snapshot is
        a previous state of this surface, not an overlay.
        """

    @abc.abstractmethod
    def update_snapshot(self, snapshot: object, box: Sequence[float]) -> None:
        """Copy this surface's ``box`` region into ``snapshot`` at the same place.

        The inverse of :meth:`restore`, for the part of a cached panel that
        keeps changing after the snapshot was taken (the single-screen detail
        strip repaints on every video frame; without re-capturing it, a restore
        would resurrect a stale frame -- that is the flicker described in
        ``App._cache_strip``).
        """

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
        clip: Sequence[float] | None = None,
    ) -> None:
        """Draw ``content`` with its anchor at ``xy`` (PIL anchor syntax).

        ``clip`` is an optional ``(x, y, w, h)`` window: only the part of the
        run that falls inside it is drawn.  That is what lets a line wider than
        its slot scroll through it (a marquee) without painting over whatever
        sits next door.
        """

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

    @abc.abstractmethod
    def bitmap_size(self, bitmap: object) -> tuple[int, int]:
        """``(width, height)`` of an opaque bitmap handle.

        Screens need it to key their own caches; reading ``.width`` off the
        handle would assume it is a PIL image.
        """

    @abc.abstractmethod
    def round_corners(self, bitmap: object, radius: int) -> object:
        """Return ``bitmap`` with its corners clipped to a rounded rectangle.

        Multiplied into the existing alpha, so a dimmed (semi-transparent)
        cover keeps its fade instead of snapping back to opaque.  Returns the
        same bitmap when ``radius`` is 0 or less.
        """

    @abc.abstractmethod
    def flatten(self, bitmap: object, background: Sequence[int]) -> object:
        """Composite ``bitmap`` onto opaque ``background`` and drop its alpha.

        The canvas has to stay fully opaque: on the handheld Weston composites
        the RGBA framebuffer using its alpha channel, so any pixel with
        alpha < 255 shows through to black rather than to what is behind it
        (DESIGN §4.4).  That is why a dimmed backdrop is flattened before it is
        drawn instead of being blended in.
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

    #: Platform composites the frames itself; read_frame() stays None.
    external: bool = False

    def set_volume(self, value: float) -> None:
        """Set the clip's soundtrack level, 0.0-1.0.

        Only meaningful for external pipes, where the platform owns both the
        pictures and the sound; the handheld mixes through its audio pipe.
        """
        return None

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

    def screen_form(self) -> Form:
        """How to arrange the detail view when there is only one screen.

        Only consulted in the single-screen case -- two canvases always mean
        :attr:`~retrostation.core.theme.Form.DUAL`.  The default is the
        handhelds' fixed strip; a platform whose screen is tall (a phone in
        portrait) returns ``PORTRAIT`` instead so the detail area is a
        proportion of the height (docs/DESIGN.ANDROID.md §11.1).
        """
        return Form.COMPACT

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

    def system_dir(self, system_key: str) -> Path:
        """Directory that holds ``system_key``'s ROMs.

        Most systems are ``rom_root/<system>``; platforms override this when
        the firmware keeps a system elsewhere (e.g. Ports next to Roms).
        """
        return self.rom_root / system_key

    def extra_system_keys(self) -> list[str]:
        """System keys living outside ``rom_root`` that must be scanned too.

        The library scan lists ``rom_root``'s sub-directories to discover
        systems, so anything kept elsewhere would silently never appear.
        """
        return []

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
    def launch_game(self, target: LaunchTarget) -> None:
        """Hand the device over to a game.

        Implementations **return** -- they must not replace the process.  The
        app still has to unwind so it can exit with the "a game ran" code,
        which is the only way the shell bootstrap can tell that apart from a
        plain quit (DESIGN §8.2).  On Linux the command is written to
        :attr:`launch_cmd_path` for the bootstrap to run; on Android it will
        start an activity or host a libretro core.

        A platform that does not understand this kind of target raises
        :class:`~retrostation.launcher.target.UnsupportedTarget`.
        """

    def run_foreground(self, target: LaunchTarget) -> int | None:
        """Run the game *now* and return once it exits (resident path).

        Only called when :meth:`can_stay_resident` is True: the app keeps its
        windows and memory, hides the display, runs this, then comes back
        (DESIGN §8.2 fast path).  The default refuses, so a platform that
        reports itself resident without implementing this fails loudly rather
        than appearing to launch nothing.

        Returns the game's exit status when there is one.
        """
        raise UnsupportedTarget(self.name, target)

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

    def set_video_rect(
        self, rect: tuple[int, int, int, int] | None, *, index: int = 0
    ) -> None:
        """Where the preview box sits, in canvas units (x, y, w, h).

        Only platforms that composite video themselves need it (Android's
        ExoPlayer surface): the UI leaves that box empty and the platform puts
        the moving picture behind it.  Default: nothing to do.
        """
        return None

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

    def play_sfx(self, kind: str) -> None:
        """Sound a button press.  Silent by default -- see :meth:`open_audio_pipe`."""

    def configure_sfx(self, *, enabled: bool, volume: float) -> None:
        """Apply the button-sound settings.  No-op where there is no sound."""

    def release_sfx(self) -> None:
        """Give up the sound card so something else can have it.

        Called before a game starts: a blip player still holding ALSA would
        leave the emulator silent.
        """

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

    # -- power-on ---------------------------------------------------------- #

    def set_autostart(self, enabled: bool, *, target: str = "", state_dir: str = "") -> None:
        """Register or unregister the frontend as the device's power-on app.

        ``enabled`` flips a flag file; the default does nothing, so platforms
        without a boot hook simply leave power-on behaviour to the firmware.
        Linux overrides this to patch the firmware's autostart script in place.
        """
        return None

    # -- power ------------------------------------------------------------ #

    def power_off(self) -> None:
        """Power the device down.  Default no-op, so headless/dev platforms
        simply leave the machine running when the player picks "关机"."""
        return None

    def reboot(self) -> None:
        """Restart the device.  Default no-op for the same reason as above."""
        return None
