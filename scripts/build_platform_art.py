#!/usr/bin/env python3
"""Build the platform artwork shipped with the app.

Source material (NeoStation theme assets, WebP):

* ``backgrounds/`` -- 1024x1024 RGB, one per platform
* ``logos/``       -- 820x330 RGBA (transparent), one per platform

They are converted into the two sizes the home carousel actually draws, and
into formats that decode fast on a 4-core Cortex-A55:

* ``assets/platforms/background/<key>.webp``  256x256, **square, never cropped**
* ``assets/platforms/logo/<key>.png``         256x103, alpha preserved

**Not JPEG.**  The RG DS ships a Pillow built against libjpeg 9 headers while
the runtime resolves libjpeg 6b, so *every* ``Image.open("*.jpg")`` there dies
with ``Wrong JPEG library version: library is 62, caller expects 90`` -- and it
does so silently, because a background that fails to load just falls back to
the generated placeholder.  WebP is supported there, and is what the sources
already are.

The source backgrounds are square, so the cards are square too: cropping them
to a 16:10 card cut ~21% off the top and bottom and left the artwork visibly
truncated.  The layout follows the artwork, not the other way round.

Logos keep their aspect ratio -- they are composited *on top of* the background
by the UI, never stretched to fill it.

    python scripts/build_platform_art.py
    python scripts/build_platform_art.py --logos DIR --backgrounds DIR
    python scripts/build_platform_art.py --systems FC,SFC,GBA   # subset
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "retrostation" / "assets" / "platforms"

DEFAULT_LOGOS = Path(r"D:\code\neostation-frontend\assets\images\logos")
DEFAULT_BACKGROUNDS = Path(r"D:\code\neostation-assets\themes\NeoStation\backgrounds")

#: Output geometry, ~2x the 640x480 reference card (132x132 art box) so the
#: art stays sharp if the layout ever grows.
#:
#: Square on purpose: the sources are 1024x1024 and the card's art box is
#: square too, so nothing has to be cropped away.
BACKGROUND_SIZE = (256, 256)
LOGO_WIDTH = 256               # height follows the source ratio (820:330)

#: JPEG is deliberately absent as a background format -- see the module
#: docstring: this device's Pillow cannot decode it at all.
BACKGROUND_SUFFIX = ".webp"
BACKGROUND_QUALITY = 82

#: Our system key -> asset stem.  Only the ones that *differ* are listed;
#: everything else resolves to ``key.casefold()``.
#:
#: The stems come from the theme, whose names follow EmulationStation-ish
#: conventions rather than this firmware's directory names -- hence "ds" for
#: NDS, "ps1" for PS, "32x" for SEGA32X and so on.
ALIASES: dict[str, str] = {
    # Nintendo
    "NDS": "ds",
    # Sony
    "PS": "ps1",
    # Sega
    "SEGA32X": "32x",
    "MDCD": "mcd",
    "SATURN": "sat",
    # NEC / SNK
    "PCECD": "pccd",
    "NEOCD": "ngcd",
    # Computers / misc
    "A2600": "2600",
    "A5200": "5200",
    "A7800": "7800",
    "A800": "a2",
    "ATARIST": "ast",
    "ATOMISWAVE": "aw",
    "EASYRPG": "rpgmaker",
    "PICO": "pico8",
    "VARCADE": "arc",
    # Aggregates -- RECENT has no counterpart, so it keeps the placeholder.
    "ALL": "all",
    "FAV": "favorites",
}

#: Firmware directory names, in the order the home page shows them.
SYSTEM_KEYS: tuple[str, ...] = (
    "ALL", "FAV", "RECENT",
    "FC", "FDS", "SFC", "GB", "GBC", "GBA", "NDS", "N64", "VB",
    "MD", "MDCD", "SEGA32X", "SMS", "GG", "SATURN",
    "PS", "PSP",
    "PCE", "PCECD", "WS", "NGP", "LYNX",
    "NEOGEO", "NEOCD", "CPS1", "CPS2", "CPS3", "FBNEO", "MAME", "NAOMI", "VARCADE",
    "MSX", "DOS", "C64", "AMIGA", "VIC20", "A800", "ATARIST",
    "A2600", "A5200", "A7800", "ATOMISWAVE",
    "SCUMMVM", "EASYRPG", "PICO", "GW", "HBMAME", "JAVA", "ONS",
    "OPENBOR", "PGM2", "POKE",
)


def stem_for(key: str) -> str:
    return ALIASES.get(key.upper(), key.casefold())


def dominant_colour(image: Image.Image) -> tuple[int, int, int]:
    """Average colour of the non-transparent pixels, alpha-weighted.

    Used to derive a background for the handful of platforms that ship a logo
    but no background, so the carousel does not mix photography with flat
    placeholders on adjacent cards.
    """
    rgba = image.convert("RGBA")
    small = rgba.resize((64, 64), Image.Resampling.BILINEAR)
    total_r = total_g = total_b = weight = 0
    for r, g, b, a in small.getdata():
        if a < 24:
            continue
        total_r += r * a
        total_g += g * a
        total_b += b * a
        weight += a
    if not weight:
        return (58, 58, 66)
    return (total_r // weight, total_g // weight, total_b // weight)


def gradient(size: tuple[int, int], colour: tuple[int, int, int]) -> Image.Image:
    """Vertical gradient: a touch lighter at the top, darker at the bottom."""
    from PIL import ImageDraw

    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    top = tuple(min(255, round(c * 1.18 + 16)) for c in colour)
    bottom = tuple(round(c * 0.42 + 8) for c in colour)
    for row in range(height):
        t = row / max(1, height - 1)
        draw.line([(0, row), (width, row)],
                  fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return image


@dataclass
class Result:
    key: str
    background: bool = False
    logo: bool = False
    #: The background was tinted from the logo, not taken from the theme.
    derived: bool = False

    @property
    def complete(self) -> bool:
        return self.background and self.logo


def convert(key: str, logos: Path, backgrounds: Path, out: Path,
            *, derive: bool = True) -> Result:
    stem = stem_for(key)
    result = Result(key=key)

    background_source = backgrounds / f"{stem}.webp"
    logo_source = logos / f"{stem}.webp"
    logo_image: Image.Image | None = None
    if logo_source.is_file():
        with Image.open(logo_source) as handle:
            logo_image = handle.convert("RGBA")

    background: Image.Image | None = None
    if background_source.is_file():
        with Image.open(background_source) as handle:
            background = handle.convert("RGB")
    elif derive and logo_image is not None:
        # No background shipped for this platform; tint one from the logo so
        # the card still reads as "a platform" rather than as a gap.
        background = gradient(BACKGROUND_SIZE, dominant_colour(logo_image))
        result.derived = True

    if background is not None:
        if background.size != BACKGROUND_SIZE:
            background = background.resize(BACKGROUND_SIZE, Image.Resampling.LANCZOS)
        target = out / "background" / f"{key.casefold()}{BACKGROUND_SUFFIX}"
        target.parent.mkdir(parents=True, exist_ok=True)
        background.save(target, format="WEBP", quality=BACKGROUND_QUALITY, method=6)
        result.background = True

    if logo_image is not None:
        logo_ratio = logo_image.height / logo_image.width
        size = (LOGO_WIDTH, max(1, round(LOGO_WIDTH * logo_ratio)))
        image = logo_image.resize(size, Image.Resampling.LANCZOS)
        target = out / "logo" / f"{key.casefold()}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG", optimize=True)
        result.logo = True

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logos", type=Path, default=DEFAULT_LOGOS)
    parser.add_argument("--backgrounds", type=Path, default=DEFAULT_BACKGROUNDS)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--systems", help="comma-separated subset, e.g. FC,SFC,GBA")
    args = parser.parse_args()

    for label, path in (("logos", args.logos), ("backgrounds", args.backgrounds)):
        if not path.is_dir():
            print(f"{label} directory not found: {path}", file=sys.stderr)
            return 1

    # Stale files from an earlier format would win the loader's suffix probe
    # and quietly shadow the good ones, so start from a clean tree.
    if args.systems is None:
        for kind_directory in (args.out / "background", args.out / "logo"):
            if kind_directory.is_dir():
                for stale in kind_directory.iterdir():
                    stale.unlink()

    keys = (tuple(k.strip().upper() for k in args.systems.split(","))
            if args.systems else SYSTEM_KEYS)

    results = [convert(key, args.logos, args.backgrounds, args.out) for key in keys]

    missing = [r.key for r in results if not r.complete]
    print(f"converted {sum(r.complete for r in results)}/{len(results)} platforms -> {args.out}")
    for result in results:
        flags = ("bg" if result.background else "--") + " " + ("logo" if result.logo else "----")
        if result.derived:
            flags += "  (background tinted from logo)"
        print(f"  {result.key:<12} {flags}")
    if missing:
        print(f"no complete artwork for: {', '.join(missing)}")
        print("those platforms fall back to the generated placeholder at runtime")

    total = sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file())
    print(f"total {total // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
