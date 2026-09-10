"""Logical resolution + form inference for Android (DESIGN.ANDROID §4.1 / §5.4).

These are pure functions: no Android imports, so they run on the desktop test
suite and the headless screenshot harness exactly as on the device.  The Kotlin
side only supplies the *physical* sizes (one per display); everything the Python
UI needs (a logical canvas size and a :class:`~retrostation.core.theme.Form`) is
derived here.
"""

from __future__ import annotations

from math import sqrt

from ...core.theme import Form

#: Rendering budget in megapixels.  Python paints to a *logical* canvas and the
#: GPU upscales for free (DESIGN.ANDROID §4.1).  0.7 MP leaves ~30 fps of headroom
#: even on a mid-range phone SoC.
_TARGET_MP = 0.7

#: Logical dimensions are aligned to a multiple of this (Bitmap stride friendly).
_ALIGN = 4

#: Clamp so a tiny or absurd display never produces a degenerate canvas.
_BOUNDS = (480, 1280)


def logical_size(
    physical_w: int,
    physical_h: int,
    *,
    target_mp: float = _TARGET_MP,
    align: int = _ALIGN,
    bounds: tuple[int, int] = _BOUNDS,
) -> tuple[int, int]:
    """Map a physical display size to a logical canvas size.

    >>> logical_size(1080, 2400)          # phone portrait
    (560, 1248)
    >>> logical_size(1920, 1080)          # Thor top, 16:9
    (1116, 628)
    """
    if physical_w <= 0 or physical_h <= 0:
        raise ValueError(f"invalid physical size {physical_w}x{physical_h}")

    target_px = target_mp * 1_000_000.0
    ratio = physical_w / physical_h
    logical_h = sqrt(target_px / ratio)
    logical_w = logical_h * ratio

    def snap(value: float) -> int:
        snapped = max(bounds[0], min(bounds[1], int(round(value / align) * align)))
        return max(align, snapped)

    return snap(logical_w), snap(logical_h)


def form_for(width: int, height: int, *, is_tablet: bool = False) -> Form:
    """Single-screen form for a canvas of ``width x height``.

    ``DUAL`` is never returned here: it is decided by the number of canvases the
    platform hands back (two = dual), not by aspect ratio.  Callers only consult
    this for the single-screen case (DESIGN.ANDROID §5.4).

    ``WIDE`` (tablet / foldable side-by-side detail column) is deferred to A6 along
    with the matching :class:`~retrostation.core.theme.Metrics` work, so a wide
    single screen currently resolves to ``COMPACT``; revisit when that lands.
    """
    if height >= width * 1.1:
        return Form.PORTRAIT
    return Form.COMPACT


def is_dual(form: Form) -> bool:
    """Whether ``form`` uses two physical canvases."""
    return form is Form.DUAL
