"""SDL2 display for the Linux handheld.

Facts this module encodes, all verified on the RG DS (see DESIGN §4.4):

* create windows **once** -- destroying and re-creating them under Wayland
  crashes, which is why :meth:`SDLDisplay.close` only ever runs on the way out;
* the bottom window is created **after a delay** (500 ms), otherwise it fails
  or ends up hidden;
* renderers are ``SDL_RENDERER_SOFTWARE``: GPU composition fights Weston;
* the texture created for every frame **must** be destroyed, or we leak one
  640x480 texture per paint and die of OOM within minutes.
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
        lib.SDL_SetRenderDrawColor.argtypes = [
            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8,
        ]
        lib.SDL_RenderClear.argtypes = [ctypes.c_void_p]
        lib.SDL_CreateRGBSurfaceFrom.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ]
        lib.SDL_CreateRGBSurfaceFrom.restype = ctypes.c_void_p
        lib.SDL_CreateTextureFromSurface.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.SDL_CreateTextureFromSurface.restype = ctypes.c_void_p
        lib.SDL_FreeSurface.argtypes = [ctypes.c_void_p]
        lib.SDL_DestroyTexture.argtypes = [ctypes.c_void_p]
        lib.SDL_DestroyRenderer.argtypes = [ctypes.c_void_p]
        lib.SDL_DestroyWindow.argtypes = [ctypes.c_void_p]
        lib.SDL_RenderCopy.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        lib.SDL_RenderPresent.argtypes = [ctypes.c_void_p]
        lib.SDL_Delay.argtypes = [ctypes.c_uint32]
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

        The texture is destroyed on every call: leaking it means one full-screen
        RGBA surface per frame.
        """
        if self._closed or index >= len(self._renderers):
            return
        renderer = self._renderers[index]
        canvas = self._canvases[index]
        width, height = canvas.size

        # RGBA byte order, matching what Pillow produces for mode "RGBA".
        rmask = 0x000000FF
        gmask = 0x0000FF00
        bmask = 0x00FF0000
        amask = 0xFF000000

        pixels = canvas.pil_image.tobytes("raw", "RGBA")
        surface = self._lib.SDL_CreateRGBSurfaceFrom(
            pixels, width, height, 32, width * 4, rmask, gmask, bmask, amask
        )
        if not surface:
            return
        texture = self._lib.SDL_CreateTextureFromSurface(renderer, surface)
        self._lib.SDL_FreeSurface(surface)  # the pixel buffer may go now
        if not texture:
            return

        try:
            self._lib.SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255)
            self._lib.SDL_RenderClear(renderer)
            self._lib.SDL_RenderCopy(renderer, texture, None, None)
            self._lib.SDL_RenderPresent(renderer)
        finally:
            self._lib.SDL_DestroyTexture(texture)

    def close(self) -> None:
        """Destroy windows and quit SDL.  Called exactly once, on the way out."""
        if self._closed:
            return
        self._closed = True
        for renderer in self._renderers:
            self._lib.SDL_DestroyRenderer(renderer)
        for window in self._windows:
            self._lib.SDL_DestroyWindow(window)
        self._windows.clear()
        self._renderers.clear()
        self._lib.SDL_Quit()
