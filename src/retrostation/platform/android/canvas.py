"""Canvas for the R1 rendering path (DESIGN.ANDROID §4.2).

R1 reuses :class:`~retrostation.platform.linux.canvas.PilCanvas` verbatim: Python
draws into an off-screen RGBA ``PIL.Image``, and the Kotlin side uploads those
bytes to a ``Bitmap`` (see ``FrameBridge``).  R2 (Skia) would replace this with a
native ``AndroidCanvas`` -- which is exactly why the drawing code lives behind the
:class:`~retrostation.platform.base.Canvas` interface and never touches PIL.
"""

from __future__ import annotations

from ..canvas import PilCanvas


def rgba_bytes(canvas: PilCanvas) -> bytes:
    """The canvas' pixels as ``RGBA`` bytes, ready for ``Bitmap.copyPixelsFromBuffer``.

    One ``tobytes`` call per frame (~0.4 ms at 0.65 MP, DESIGN.ANDROID §4.4).  A
    future optimisation hands the Kotlin side a direct ``ByteBuffer`` and lets
    PilCanvas draw into it, removing this copy.
    """
    return canvas.pil_image.tobytes("raw", "RGBA")
