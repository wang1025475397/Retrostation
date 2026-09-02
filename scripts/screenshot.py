#!/usr/bin/env python3
"""Render UI screens headlessly to PNGs for layout review.

Builds a synthetic library (real PNG covers, a gamelist.xml), then drives the
real App and saves both panels for each state:

    python scripts/screenshot.py [out_dir]
    python scripts/screenshot.py --fake --lang en_US   # English UI -> screenshots/en_US
"""

from __future__ import annotations

import io
import os
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PIL import Image, ImageDraw  # noqa: E402

from retrostation.core.config import Config  # noqa: E402
from retrostation.core.i18n import Translator
from retrostation.data.library import Library
from retrostation.platform.base import Canvas, FileEntry, InputAction, InputEvent, Platform
from retrostation.platform.linux.canvas import PilCanvas
from retrostation.platform.linux.platform import LinuxPlatform
from retrostation.ui.app import App
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
LIB = Path("build/screenshot-lib")


def cover(name: str, hue: int) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (240, 320), (hue, 60 + hue % 120, 90))
    draw = ImageDraw.Draw(image)
    draw.ellipse([40, 40, 200, 200], fill=(250, 240, 220))
    draw.text((20, 270), name[:8], fill=(255, 255, 255))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def logo(name: str) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGBA", (360, 90), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 20, 360, 70], fill=(232, 163, 61, 220))
    draw.text((16, 32), name[:12], fill=(30, 20, 4))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# Per-language synthetic library so localized docs can show a localized UI.
GAME_DATA: dict[str, dict[str, tuple[list[str], int]]] = {
    "zh_CN": {
        "FC": (["超级马力欧兄弟", "魂斗罗", "坦克大战", "冒险岛", "赤色要塞", "沙罗曼蛇",
                "恶魔城", "忍者龙剑传", "双截龙", "热血高校", "炸弹人", "吃豆人"], 0),
        "SFC": (["超时空之钥", "最终幻想VI", "超级银河战士", "恶魔城X", "街头霸王II"], 120),
        "GBA": (["口袋妖怪 绿宝石", "塞尔达传说 缩小帽", "火焰纹章"], 210),
        "MD": (["索尼克", "怒之铁拳II", "梦幻之星IV"], 40),
    },
    "en_US": {
        "FC": (["Super Mario Bros.", "Contra", "Battle City", "Adventure Island", "Jackal",
                "Salamander", "Castlevania", "Ninja Gaiden", "Double Dragon",
                "River City Ransom", "Bomberman", "Pac-Man"], 0),
        "SFC": (["Chrono Trigger", "Final Fantasy VI", "Super Metroid", "Castlevania X",
                 "Street Fighter II"], 120),
        "GBA": (["Pokémon Emerald", "The Minish Cap", "Fire Emblem"], 210),
        "MD": (["Sonic the Hedgehog", "Streets of Rage 2", "Phantasy Star IV"], 40),
    },
}

DESC_BY_LANG = {
    "zh_CN": "经典名作，手感扎实，至今仍值得一玩。关卡设计精巧，音乐在当时属顶级水准。",
    "en_US": "A timeless classic with tight controls and level design that still holds up today.",
}


def build_library(lang: str = "zh_CN") -> None:
    shutil.rmtree(LIB, ignore_errors=True)
    systems = GAME_DATA.get(lang, GAME_DATA["zh_CN"])
    desc = DESC_BY_LANG.get(lang, DESC_BY_LANG["zh_CN"])
    en = lang != "zh_CN"
    dev = "Nintendo" if en else "任天堂"
    pub = "Nintendo" if en else "任天堂"
    genre = "Platformer" if en else "平台跳跃"
    entries = []
    for key, (names, hue) in systems.items():
        directory = LIB / key
        (directory / "Imgs").mkdir(parents=True, exist_ok=True)
        (directory / "logo").mkdir(parents=True, exist_ok=True)
        for index, name in enumerate(names):
            rom = f"{name}.nes" if key == "FC" else f"{name}.{'sfc' if key == 'SFC' else 'gba' if key == 'GBA' else 'md'}"
            (directory / rom).write_bytes(b"ROM")
            (directory / "Imgs" / f"{name}.png").write_bytes(cover(name, hue + index * 12))
            if index % 2 == 0:
                (directory / "logo" / f"{name}.png").write_bytes(logo(name))
            entries.append((key, rom, name, hue))

    lines = ["<?xml version=\"1.0\"?>", "<gameList>"]
    for key, rom, name, _hue in entries:
        lines += [
            "  <game>",
            f"    <path>./{rom}</path>",
            f"    <name>{name}</name>",
            f"    <desc>{desc}</desc>",
            "    <rating>0.860000</rating>",
            "    <releasedate>19850913T000000</releasedate>",
            f"    <developer>{dev}</developer>",
            f"    <publisher>{pub}</publisher>",
            f"    <genre>{genre}</genre>",
            "    <players>1-2</players>",
            "    <playcount>23</playcount>",
            "    <lastplayed>20260820T193000</lastplayed>",
            "    <favorite>true</favorite>",
            f"    <cover>./Imgs/{Path(rom).stem}.png</cover>",
            f"    <marquee>./logo/{Path(rom).stem}.png</marquee>",
            "  </game>",
        ]
    lines.append("</gameList>")
    (LIB / "FC" / "gamelist.xml").write_text("\n".join(lines), encoding="utf-8")


def press(app: App, action: InputAction) -> None:
    app.session.handle(InputEvent(action))


def shoot(app: App, platform, out: Path, tag: str) -> None:
    app.run(max_frames=1)
    for index, painter in enumerate(app._painters):  # noqa: SLF001 - dev tool
        target = out / f"{tag}_{'top' if index == 0 else 'bottom'}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas = cast(PilCanvas, painter.canvas)
        canvas.pil_image.save(target)
    print("saved", tag)


class FakePlatform(Platform):
    """Headless, SDL-free platform so screenshots build on any machine.

    The real ``App`` renders to in-memory ``PilCanvas`` objects, which need no
    display server -- this is the same stand-in the test suite uses.
    """

    name = "fake"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self.canvases: list[Canvas] = []
        self.launched: tuple[str, ...] | None = None
        # Reuse the real font discovery (CJK-capable, cross-platform) so the
        # rendered text is not tofu boxes where the device font is absent.
        from retrostation.platform.linux.fonts import FontBook

        self._fonts = FontBook()

    def init_display(self, mode: str) -> list[Canvas]:
        from retrostation.core.theme import BASE_H, BASE_W
        from retrostation.platform.linux.canvas import PilCanvas

        self.canvases = [
            PilCanvas(BASE_W, BASE_H) for _ in range(2 if mode in ("dual", "auto") else 1)
        ]
        return self.canvases

    def present(self, index: int) -> None:
        return None

    def poll_events(self, timeout: float = 0.0) -> list[InputEvent]:
        return []

    def battery(self) -> int | None:
        return 87

    def temperature(self) -> float | None:
        return 56.6

    def set_brightness(self, value: int, index: int = 0) -> None:
        return None

    @property
    def rom_root(self) -> Path:
        return self._root

    @property
    def config_dir(self) -> Path:
        path = self._root / ".retrostation"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_dir(self, path: Path) -> list[FileEntry]:
        try:
            with os.scandir(path) as iterator:
                return [
                    FileEntry(
                        name=entry.name,
                        is_dir=entry.is_dir(),
                        size=entry.stat().st_size,
                        mtime=entry.stat().st_mtime,
                    )
                    for entry in iterator
                ]
        except OSError:
            return []

    def launch_game(self, argv) -> None:
        self.launched = tuple(argv)

    def font(self, size: int) -> object:
        return self._fonts.get(size)

    def load_image(self, path: Path) -> object:
        with Image.open(path) as handle:
            return handle.convert("RGBA").copy()

    def shutdown(self) -> None:
        return None


def main() -> int:
    single = "--single" in sys.argv

    # --lang LANG renders a localized UI (e.g. en_US) for the localized docs;
    # default stays "zh_CN" so existing Chinese screenshots are unaffected.
    args = sys.argv[1:]
    lang = "zh_CN"
    rest: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--lang" and i + 1 < len(args):
            lang = args[i + 1]
            i += 2
            continue
        rest.append(args[i])
        i += 1
    positional = [a for a in rest if not a.startswith("--")]
    if positional:
        out = Path(positional[0])
    elif lang == "zh_CN":
        out = Path("screenshots")
    else:
        out = Path("screenshots") / lang

    build_library(lang)

    # A SDL-free, headless platform lets this run on any machine (CI, a
    # developer's laptop, Windows without the Linux input stack).  On the
    # handheld itself use the default Linux platform instead.
    if "--fake" in sys.argv:
        platform: Platform = FakePlatform(LIB.resolve())
    else:
        platform = LinuxPlatform(rom_root=str(LIB.resolve()),
                                 config_dir=str((LIB / ".config").resolve()),
                                 headless=True)
    config = Config()
    # Force the layout the device is in; without this, "auto" always builds two
    # panels and the single-screen strip never shows up.
    if single:
        config.screen_mode = "single"
    script = LIB / "RA_launch.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    config.launcher.ra_script = str(script)

    library = Library(platform, config)
    library.scan()
    app = App(platform, config, Translator(lang), library)

    shoot(app, platform, out, "01-home")
    press(app, InputAction.A)            # enter ALL
    shoot(app, platform, out, "02-list")
    press(app, InputAction.X)            # grid
    shoot(app, platform, out, "03-grid")
    press(app, InputAction.X)            # carousel
    shoot(app, platform, out, "04-carousel")
    press(app, InputAction.START)        # menu
    shoot(app, platform, out, "05-menu")
    press(app, InputAction.B)
    press(app, InputAction.B)            # back to home
    for _ in range(4):                   # walk to FC
        press(app, InputAction.RIGHT)
    shoot(app, platform, out, "06-home-fc")
    press(app, InputAction.A)            # FC games
    shoot(app, platform, out, "07-list-fc")
    app.session.modal = "exit"
    shoot(app, platform, out, "08-exit")

    # 09: multi-card menu -- the "存储卡" row only appears with > 1 ROM root,
    # which a single-card library never produces, so inject two for the shot.
    app.session.rom_roots = [
        (LIB.resolve(), "TF1"),
        (Path("/nonexistent/tf2"), "TF2"),
    ]
    app.session.modal = "menu"
    shoot(app, platform, out, "09-card-menu")

    print("done ->", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
