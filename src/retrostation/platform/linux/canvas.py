"""Re-export ``PilCanvas`` from ``retrostation.platform.canvas``.

The shared canvas now lives at the platform top level (it is plain Pillow, no
SDL) so the Android and desktop adapters can reuse it without importing the
Linux package -- ``platform.linux.__init__`` pulls in SDL2 + evdev, which do not
exist off the handheld.  This module keeps the Linux package's existing
``from .canvas import PilCanvas, wrap_text, save_bitmap`` imports working.
"""

from __future__ import annotations

from ..canvas import (
    PilCanvas,
    save_bitmap,
    wrap_text,
    _box,
    _fit_size,
    _font_tag,
    _measure,
    _ellipsis,
)

__all__ = [
    "PilCanvas",
    "save_bitmap",
    "wrap_text",
    "_box",
    "_fit_size",
    "_font_tag",
    "_measure",
    "_ellipsis",
]
