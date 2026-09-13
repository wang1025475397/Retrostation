"""Draw and export the Android launcher icon set.

The mark is a dual-screen handheld in the product's own palette: the theme's
amber (#E8A33D, `retrostation.core.theme`) on the near-black the artwork and the
app's panels use (#121215).  Everything is drawn as flat shapes at 4x and scaled
down, so the 48px icon is as crisp as the 1024px master -- a generated raster
turned to mush at launcher size, which is why this is code rather than artwork.

Run from the repository root: ``python scripts/build_app_icon.py``.  It writes the
1024 master (`packaging/android/icon/`), every legacy density, the round variant
and the adaptive-icon layers, plus the two XML resources and the background
colour -- all committed, so this only runs when the mark itself changes.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

MASTER_DIR = "packaging/android/icon"
RES = os.path.join("android", "app", "src", "main", "res")

BG = (18, 18, 21)          # #121215 -- the panels' own black
AMBER = (232, 163, 61)     # #E8A33D -- the theme's amber accent
GREY = (48, 48, 56)        # #303038 -- the secondary panel's outline
SUPERSAMPLE = 4

#: density suffix -> legacy icon side (48dp at mdpi).
DENSITIES = (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192))

ADAPTIVE_XML = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
"""

COLORS_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <!-- The adaptive icon needs a colour behind its foreground layer: the same
         near-black the mark is drawn on (#121215). -->
    <color name="ic_launcher_background">#121215</color>
</resources>
"""


def draw_mark(size: int, *, share: float, background: tuple[int, int, int] | None) -> Image.Image:
    """The mark centred on a ``size`` square.

    ``share`` is how much of the canvas the mark occupies: 0.78 for a legacy icon
    (room for the launcher's own shadow), 0.62 for an adaptive foreground (inside
    the safe zone).  ``background=None`` leaves the corners transparent.
    """
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big),
                      (background + (255,)) if background else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    side = big * share
    x0 = (big - side) / 2
    y0 = (big - side) / 2

    def box(left: float, top: float, right: float, bottom: float) -> tuple[float, ...]:
        return (x0 + side * left, y0 + side * top, x0 + side * right, y0 + side * bottom)

    # Top screen: solid amber, two dark bars -- the menu the frontend draws.
    draw.rounded_rectangle(box(0.06, 0.02, 0.94, 0.50), radius=side * 0.10, fill=AMBER)
    draw.rounded_rectangle(box(0.18, 0.15, 0.82, 0.24), radius=side * 0.045, fill=BG)
    draw.rounded_rectangle(box(0.18, 0.30, 0.58, 0.39), radius=side * 0.045, fill=BG)

    # Bottom screen: the detail panel -- an amber tile and three lines.
    draw.rounded_rectangle(box(0.22, 0.58, 0.78, 0.98), radius=side * 0.07,
                           fill=BG, outline=GREY, width=max(1, round(side * 0.045)))
    draw.rounded_rectangle(box(0.30, 0.68, 0.42, 0.80), radius=side * 0.02, fill=AMBER)
    for top in (0.68, 0.755, 0.83):
        draw.rounded_rectangle(box(0.48, top, 0.70, top + 0.045),
                               radius=side * 0.02, fill=AMBER)

    return image.resize((size, size), Image.LANCZOS)


def write(name: str, text: str) -> None:
    path = os.path.join(RES, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main() -> None:
    os.makedirs(MASTER_DIR, exist_ok=True)
    draw_mark(1024, share=0.78, background=BG).save(os.path.join(MASTER_DIR, "ic_launcher-1024.png"))

    circle = Image.new("L", (1024, 1024), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, 1023, 1023), fill=255)

    for density, size in DENSITIES:
        folder = os.path.join(RES, "mipmap-" + density)
        os.makedirs(folder, exist_ok=True)
        square = draw_mark(size, share=0.78, background=BG)
        square.save(os.path.join(folder, "ic_launcher.png"))
        # Legacy round icon: the same art, masked to a circle.
        rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rounded.paste(square, (0, 0), circle.resize((size, size), Image.LANCZOS))
        rounded.save(os.path.join(folder, "ic_launcher_round.png"))
        # Adaptive foreground: 108dp canvas, mark inside the safe zone, no
        # background of its own (the background layer supplies the colour).
        layer_size = round(size * 108 / 48)
        draw_mark(layer_size, share=0.62, background=None).save(
            os.path.join(folder, "ic_launcher_foreground.png"))
        print(f"mipmap-{density}: {size}px icon, {layer_size}px adaptive layer")

    for name in ("ic_launcher", "ic_launcher_round"):
        write(os.path.join("mipmap-anydpi-v26", name + ".xml"), ADAPTIVE_XML)
    write(os.path.join("values", "colors.xml"), COLORS_XML)
    print("wrote adaptive icon xml + colors.xml")


if __name__ == "__main__":
    main()
