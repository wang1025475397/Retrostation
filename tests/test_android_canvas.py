"""The Android canvas is the handheld's, plus one copy out (DESIGN.ANDROID §4.2).

``rgba_bytes`` is what crosses into Kotlin every frame
(``Bitmap.copyPixelsFromBuffer``), so its layout matters: RGBA per pixel, rows in
order, four bytes each.  A channel swap or a stride mismatch shows up on the
device as a colour-shifted, sheared frame -- which is exactly what this pins.
"""

from __future__ import annotations

from retrostation.platform.android.canvas import rgba_bytes
from retrostation.platform.linux.canvas import PilCanvas


def test_the_bytes_are_four_per_pixel_in_row_order() -> None:
    canvas = PilCanvas(3, 2)
    canvas.pil_image.putpixel((0, 0), (255, 0, 0, 255))
    canvas.pil_image.putpixel((2, 0), (0, 255, 0, 128))
    data = rgba_bytes(canvas)

    assert len(data) == 3 * 2 * 4
    assert tuple(data[0:4]) == (255, 0, 0, 255)      # first pixel, in RGBA order
    assert tuple(data[8:12]) == (0, 255, 0, 128)     # the first row's last pixel


def test_the_second_row_starts_after_a_full_row() -> None:
    canvas = PilCanvas(2, 3)
    canvas.pil_image.putpixel((0, 1), (1, 2, 3, 4))
    data = rgba_bytes(canvas)

    assert tuple(data[8:12]) == (1, 2, 3, 4)         # 2 pixels x 4 bytes ahead
