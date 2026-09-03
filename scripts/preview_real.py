#!/usr/bin/env python3
"""Preview real media/metadata with the desktop UI, headless (pure PIL).

Two data sources are supported:

* ES-DE tree (default): ``--esde-root <tree>`` holds
  ``gamelists/<sys>/gamelist.xml`` and ``downloaded_media/<sys>/<kind>``.
  Zero-byte stub ROMs are dropped next to the real artwork so we don't copy
  any ROMs.
* Pegasus / 天马 pack: ``--rom-root <base>`` holds the real ROMs in
  ``<base>/<sys>/`` plus a per-game media pack in
  ``<base>/<sys>/media/<game>/`` (boxFront.png, logo.png, background.jpg, ...).
  Media is resolved from that per-game layout automatically -- no gamelist.xml
  required (the app falls back to ROM file names for game titles).

    python scripts/preview_real.py                                  # ES-DE default
    python scripts/preview_real.py --rom-root E:/tianma/psx --system psx
"""
from __future__ import annotations

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (str(SCRIPTS), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

import screenshot as shot  # noqa: E402  (reuses FakePlatform / shoot / press)

from retrostation.core.config import Config  # noqa: E402
from retrostation.core.i18n import Translator  # noqa: E402
from retrostation.data.library import Library  # noqa: E402
from retrostation.ui.app import App  # noqa: E402
from retrostation.platform.base import InputAction  # noqa: E402

_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_ROM_SUFFIXES = (".chd", ".cue", ".iso", ".bin", ".img", ".pbp", ".zip", ".7z")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--esde-root", default=r"E:/baidu/ps1/ES-DE",
                   help="ES-DE tree with gamelist.xml + downloaded_media (default source)")
    p.add_argument("--rom-root", default=None,
                   help="Pegasus/天马 pack base: <base>/<system>/ holds ROMs "
                        "and a media/<game>/ per-game media pack")
    p.add_argument("--system", default="psx", help="system key (default: psx)")
    p.add_argument("--limit", type=int, default=8, help="max representative games")
    p.add_argument("--out", default=None, help="output dir (default: screenshots-preview)")
    return p.parse_args()


def _has(kind_dir: Path, stem: str) -> bool:
    return any((kind_dir / f"{stem}{suffix}").is_file() for suffix in _SUFFIXES)


def pick_from_esde(esde_root: Path, system: str, limit: int) -> list[tuple[str, str]]:
    """Representative (stem, ext) pairs chosen from an ES-DE gamelist.xml."""
    gamelist = esde_root / "gamelists" / system / "gamelist.xml"
    fanart = esde_root / "downloaded_media" / system / "fanart"
    screens = esde_root / "downloaded_media" / system / "screenshots"
    tree = ET.parse(gamelist)
    picked: list[tuple[str, str]] = []
    for game in tree.getroot().findall("game"):
        # ES-DE writes paths as `./Name.chd`; strip the leading `./` and only
        # skip genuine sub-directory paths (a `/` past the prefix).
        path_text = (game.findtext("path") or "").strip().lstrip("./")
        if not path_text or "/" in path_text:
            continue
        stem = Path(path_text).stem
        ext = Path(path_text).suffix or ".chd"
        if _has(fanart, stem) or _has(screens, stem):
            picked.append((stem, ext))
        if len(picked) >= limit:
            break
    return picked


def pick_from_rom_root(rom_root: Path, system: str, limit: int) -> list[tuple[str, str]]:
    """Representative (stem, ext) pairs from real ROM files in a pack."""
    sys_dir = rom_root / system
    picked: list[tuple[str, str]] = []
    if not sys_dir.is_dir():
        return picked
    # skip the media/ (and assets/) per-game folders -- they are not ROMs
    skip = {"media", "assets"}
    for child in sorted(sys_dir.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() not in _ROM_SUFFIXES:
            continue
        if child.stem.lower() in skip:
            continue
        picked.append((child.stem, child.suffix))
        if len(picked) >= limit:
            break
    return picked


def main() -> int:
    args = parse_args()
    system = args.system
    out_dir = Path(args.out) if args.out else ROOT / "screenshots-preview"

    if args.rom_root:
        # Pegasus / 天马 pack: real ROMs + per-game media/<game>/ layout.
        rom_root = Path(args.rom_root).resolve()
        if not (rom_root / system).is_dir():
            print(f"no system dir at {rom_root / system}")
            return 1
        reps = pick_from_rom_root(rom_root, system, args.limit)
        config = Config()
        config.rom_root = str(rom_root)
        config.metadata.esde_root = ""  # per-game media at <rom_root>/<sys>/media
        config.language = "zh_CN"
        config.bottom_video = False
        platform = shot.FakePlatform(rom_root)
        print(f"天马/Pegasus source: {rom_root}  system={system}")
    else:
        # ES-DE tree (default): stub ROMs next to real downloaded_media.
        esde_root = Path(args.esde_root)
        reps = pick_from_esde(esde_root, system, args.limit)
        tmp = ROOT / "build" / "preview-psx"
        ps = tmp / system
        shutil.rmtree(tmp, ignore_errors=True)
        ps.mkdir(parents=True, exist_ok=True)
        for stem, ext in reps:
            (ps / f"{stem}{ext}").write_bytes(b"")
        config = Config()
        config.rom_root = str(tmp)
        config.metadata.esde_root = str(esde_root)
        config.language = "zh_CN"
        config.bottom_video = False
        platform = shot.FakePlatform(tmp.resolve())
        print(f"ES-DE source: {esde_root}  system={system}")

    print("representative games:", [name for name, _ in reps])
    if not reps:
        print("no representative games found")
        return 1

    library = Library(platform, config)
    library.scan()
    app = App(platform, config, Translator("zh_CN"), library)

    # Drive the same way screenshot.py does: home -> ALL list/grid/carousel,
    # whose top screens paint the selected game's fanart as a dimmed backdrop.
    shot.shoot(app, platform, out_dir, "01-home")
    shot.press(app, InputAction.A)  # enter ALL -> game list (top backdrop)
    shot.shoot(app, platform, out_dir, "02-list")
    shot.press(app, InputAction.X)  # grid
    shot.shoot(app, platform, out_dir, "03-grid")
    shot.press(app, InputAction.X)  # carousel
    shot.shoot(app, platform, out_dir, "04-carousel")

    # Composite the top-screen captures onto black so the dimmed backdrop is
    # visible without an alpha-channel checkerboard (headless renders save raw
    # RGBA; real hardware composites onto the background colour).
    from PIL import Image  # noqa: E402

    for tag in ("02-list", "03-grid", "04-carousel"):
        src = out_dir / f"{tag}_top.png"
        if not src.exists():
            continue
        rgba = Image.open(src).convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        composite = Image.alpha_composite(bg, rgba).convert("RGB")
        target = out_dir / f"{tag}_top_on_black.jpg"
        composite.save(target, quality=95)
        print("saved", target)

    print("done ->", out_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
