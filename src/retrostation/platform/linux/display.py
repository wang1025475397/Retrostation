"""SDL2 display for the Linux handheld.

Facts this module encodes, all verified on the RG DS (see DESIGN §4.4):

* create windows **once** -- destroying and re-creating them under Wayland
  crashes, which is why :meth:`SDLDisplay.close` only ever runs on the way out;
* the bottom window is created **after a delay** (500 ms), otherwise it fails
  or ends up hidden;
* renderers are ``SDL_RENDERER_SOFTWARE``: GPU composition fights Weston;
* one streaming texture per screen is created on the first paint and then only
  *updated*.  Building a texture per frame costs an extra full-screen copy plus
  an allocation, ~10 ms at 640x480 -- measured as the largest single line item
  in a video frame once the artwork was cached.
"""

from __future__ import annotations

import ctypes
from ctypes.util import find_library

from .canvas import PilCanvas

# --------------------------------------------------------------------------- #
# SDL constants
# --------------------------------------------------------------------------- #

SDL_INIT_VIDEO = 0x00000020
SDL_WINDOW_FULLSCREEN_DESKTOP = 0x00001001
SDL_RENDERER_SOFTWARE = 0x00000001
SDL_WINDOWPOS_CENTERED_MASK = 0x2FFF0000
SDL_TEXTUREACCESS_STREAMING = 1

#: Byte order Pillow produces for mode "RGBA", which is also SDL's RGBA8888.
_RMASK, _GMASK, _BMASK, _AMASK = 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000


def _centered(display_index: int) -> int:
    """``SDL_WINDOWPOS_CENTERED_DISPLAY(i)``."""
    return SDL_WINDOWPOS_CENTERED_MASK | display_index


def _load_sdl() -> ctypes.CDLL:
    """Load libSDL2, tolerating the different sonames in the wild."""
    candidates = ["libSDL2-2.0.so.0", "libSDL2.so", "SDL2.dll"]
    found = find_library("SDL2")
    if found:
        candidates.insert(0, found)
    for name in candidates:
        try:
            return ctypes.cdll.LoadLibrary(name)
        except OSError:
            continue
    raise RuntimeError("SDL2 not found; install libsdl2 or set LD_LIBRARY_PATH")


class _DisplayMode(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_uint32),
        ("w", ctypes.c_int),
        ("h", ctypes.c_int),
        ("refresh_rate", ctypes.c_int),
        ("driverdata", ctypes.c_void_p),
    ]


class SDLDisplay:
    """Owns the SDL windows/renderers and uploads PIL canvases to them."""

    def __init__(self, mode: str = "auto", title: bytes = b"Retrostation") -> None:
        self._lib = _load_sdl()
        self._bind_signatures()
        self._title = title

        if self._lib.SDL_Init(SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {self._error()}")

        # Stops SDL from minimising when focus is lost, which breaks the
        # bottom screen on some firmwares.
        self._lib.SDL_SetHint(b"SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", b"0")

        displays = self._lib.SDL_GetNumVideoDisplays()
        if displays <= 0:
            raise RuntimeError(f"SDL_GetNumVideoDisplays failed: {self._error()}")

        dual = (mode == "dual") or (mode == "auto" and displays >= 2)
        count = 2 if dual else 1

        self._windows: list[int] = []
        self._renderers: list[int] = []
        self._sizes: list[tuple[int, int]] = []
        self._canvases: list[PilCanvas] = []
        self._textures: list[int] = []
        self._format = self._lib.SDL_MasksToPixelFormatEnum(
            32, _RMASK, _GMASK, _BMASK, _AMASK
        )
        self._closed = False

        for index in range(count):
            if index > 0:
                # Verified on device: creating the second window immediately
                # after the first fails or hides it.
                self._lib.SDL_Delay(500)
            window, (w, h) = self._create_window(index)
            renderer = self._lib.SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE)
            if not renderer:
                raise RuntimeError(f"SDL_CreateRenderer failed: {self._error()}")
            self._windows.append(window)
            self._renderers.append(renderer)
            self._sizes.append((w, h))
            self._canvases.append(PilCanvas(w, h))
            self._textures.append(0)  # created on the first present()

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    def _bind_signatures(self) -> None:
        """Declare argtypes/restypes -- mandatory on 64-bit, pointers truncate."""
        lib = self._lib
        int_p = ctypes.POINTER(ctypes.c_int)

        lib.SDL_Init.argtypes = [ctypes.c_uint32]
        lib.SDL_Init.restype = ctypes.c_int
        lib.SDL_GetError.restype = ctypes.c_char_p
        lib.SDL_GetNumVideoDisplays.restype = ctypes.c_int
        lib.SDL_GetDesktopDisplayMode.argtypes = [ctypes.c_int, ctypes.POINTER(_DisplayMode)]
        lib.SDL_GetDesktopDisplayMode.restype = ctypes.c_int
        lib.SDL_CreateWindow.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
        ]
        lib.SDL_CreateWindow.restype = ctypes.c_void_p
        lib.SDL_CreateRenderer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        lib.SDL_CreateRenderer.restype = ctypes.c_void_p
        lib.SDL_DestroyTexture.argtypes = [ctypes.c_void_p]
        lib.SDL_DestroyRenderer.argtypes = [ctypes.c_void_p]
        lib.SDL_DestroyWindow.argtypes = [ctypes.c_void_p]
        lib.SDL_HideWindow.argtypes = [ctypes.c_void_p]
        lib.SDL_HideWindow.restype = None
        lib.SDL_ShowWindow.argtypes = [ctypes.c_void_p]
        lib.SDL_ShowWindow.restype = None
        lib.SDL_RenderCopy.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        lib.SDL_RenderPresent.argtypes = [ctypes.c_void_p]
        lib.SDL_Delay.argtypes = [ctypes.c_uint32]
        # Pixel format of a "RGBA" Pillow image, worked out by SDL itself --
        # hand-rolling the SDL_PIXELFORMAT_* bitfield is a classic off-by-one.
        lib.SDL_MasksToPixelFormatEnum.argtypes = [ctypes.c_int] + [ctypes.c_uint32] * 4
        lib.SDL_MasksToPixelFormatEnum.restype = ctypes.c_uint32
        lib.SDL_CreateTexture.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        lib.SDL_CreateTexture.restype = ctypes.c_void_p
        lib.SDL_UpdateTexture.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
        ]
        lib.SDL_UpdateTexture.restype = ctypes.c_int
        lib.SDL_Quit.restype = None
        lib.SDL_GetRendererOutputSize.argtypes = [ctypes.c_void_p, int_p, int_p]
        lib.SDL_GetRendererOutputSize.restype = ctypes.c_int

    def _error(self) -> str:
        message = self._lib.SDL_GetError()
        return message.decode("utf-8", "replace") if message else "unknown SDL error"

    def _create_window(self, index: int) -> tuple[int, tuple[int, int]]:
        mode = _DisplayMode()
        if self._lib.SDL_GetDesktopDisplayMode(index, ctypes.byref(mode)) == 0 and mode.w > 0:
            width, height = mode.w, mode.h
        else:
            # Never guess 0x0; a wrong size is much better than no window.
            width, height = 640, 480

        window = self._lib.SDL_CreateWindow(
            self._title,
            _centered(index),
            _centered(index),
            width,
            height,
            SDL_WINDOW_FULLSCREEN_DESKTOP,
        )
        if not window:
            raise RuntimeError(f"SDL_CreateWindow(display {index}) failed: {self._error()}")
        return window, (width, height)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def screens(self) -> int:
        return len(self._windows)

    def canvas(self, index: int) -> PilCanvas:
        return self._canvases[index]

    @property
    def canvases(self) -> list[PilCanvas]:
        return self._canvases

    def present(self, index: int) -> None:
        """Upload canvas ``index`` and flip.

        The texture is created once and then only updated: creating one per
        frame means an extra full-screen copy and an allocation, which measured
        ~10 ms per paint at 640x480 -- the frame budget is 33 ms.

        There is no ``SDL_RenderClear``: the canvas is opaque and covers the
        whole target, and a texture's default blend mode is ``NONE``, so the
        copy overwrites every pixel.
        """
        if self._closed or index >= len(self._renderers):
            return
        renderer = self._renderers[index]
        canvas = self._canvases[index]
        width, height = canvas.size
        lib = self._lib

        texture = self._textures[index]
        if not texture:
            texture = lib.SDL_CreateTexture(
                renderer, self._format, SDL_TEXTUREACCESS_STREAMING, width, height
            )
            if not texture:
                return
            self._textures[index] = texture

        # The canvas must be fully opaque on upload.  Weston composites the
        # RGBA framebuffer using its alpha channel, so any alpha<255 pixel would
        # show through to black instead of the (already alpha-composited) art.
        # Everything is drawn with its transparency pre-blended into the RGB, so
        # flattening the alpha to 255 keeps the look and makes it display right
        # (DESIGN §4.4).
        surface = canvas.pil_image
        if surface.mode == "RGBA":
            surface = surface.copy()
            surface.putalpha(255)
        pixels = surface.tobytes("raw", "RGBA")
        if lib.SDL_UpdateTexture(texture, None, pixels, width * 4) != 0:
            return
        lib.SDL_RenderCopy(renderer, texture, None, None)
        lib.SDL_RenderPresent(renderer)

    def hide(self) -> None:
        """Hide every window without destroying anything.

        The SDL context stays alive, so a game can take the screen over while
        this process waits, and :meth:`show` brings the UI back without
        re-initialising anything (DESIGN §8.2).
        """
        for window in self._windows:
            self._lib.SDL_HideWindow(window)

    def show(self) -> None:
        """Undo :meth:`hide`."""
        for window in self._windows:
            self._lib.SDL_ShowWindow(window)

    def close(self) -> None:
        """Destroy windows and quit SDL.  Called exactly once, on the way out.

        NOTE (measured on the RG DS): this costs ~2 s once a frame has been
        presented -- ``SDL_DestroyWindow`` waits ~1 s *per window* for the
        Wayland compositor to release buffers we already handed it, while
        destroying textures/renderers and ``SDL_Quit`` are free (<15 ms).

        Skipping that teardown does **not** make launching a game faster:
        hiding the windows and returning early moves the same ~2 s into
        process exit, where the kernel reclaims the surfaces, and
        ``retrostation.sh`` still has to wait for the process to disappear
        before it can start the game (total wall time 5.28 s vs 5.27 s).
        The cost is inherent to handing the display over, not to how we tear
        it down -- so don't spend time optimising here again.
        """
        if self._closed:
            return
        self._closed = True
        for texture in self._textures:
            if texture:
                self._lib.SDL_DestroyTexture(texture)
        self._textures.clear()
        for renderer in self._renderers:
            self._lib.SDL_DestroyRenderer(renderer)
        for window in self._windows:
            self._lib.SDL_DestroyWindow(window)
        self._windows.clear()
        self._renderers.clear()
        self._lib.SDL_Quit()
