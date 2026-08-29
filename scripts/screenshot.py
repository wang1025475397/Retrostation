#!/usr/bin/env python3
"""Render UI screens headlessly to PNGs for layout review.

Builds a synthetic library (real PNG covers, a gamelist.xml), then drives the
real App and saves both panels for each state:

    python scripts/screenshot.py [out_dir]
"""

from __future__ import annotations

import io
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
from retrostation.platform.base import InputAction, InputEvent
from retrostation.platform.linux.platform import LinuxPlatform
from retrostation.ui.app import App

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


def build_library() -> None:
    shutil.rmtree(LIB, ignore_errors=True)
    systems = {
        "FC": (["超级马力欧兄弟", "魂斗罗", "坦克大战", "冒险岛", "赤色要塞", "沙罗曼蛇",
                "恶魔城", "忍者龙剑传", "双截龙", "热血高校", "炸弹人", "吃豆人"], 0),
        "SFC": (["超时空之钥", "最终幻想VI", "超级银河战士", "恶魔城X", "街头霸王II"], 120),
        "GBA": (["口袋妖怪 绿宝石", "塞尔达传说 缩小帽", "火焰纹章"], 210),
        "MD": (["索尼克", "怒之铁拳II", "梦幻之星IV"], 40),
    }
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
            "    <desc>经典名作，手感扎实，至今仍值得一玩。关卡设计精巧，音乐在当时属顶级水准。</desc>",
            "    <rating>0.860000</rating>",
            "    <releasedate>19850913T000000</releasedate>",
            "    <developer>任天堂</developer>",
            "    <publisher>任天堂</publisher>",
            "    <genre>平台跳跃</genre>",
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
        painter.canvas.pil_image.save(target)
    print("saved", tag)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
    build_library()

    platform = LinuxPlatform(rom_root=str(LIB.resolve()), headless=True)
    config = Config()
    script = LIB / "RA_launch.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    config.launcher.ra_script = str(script)

    library = Library(platform, config)
    library.scan()
    app = App(platform, config, Translator("zh_CN"), library)

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
    print("done ->", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
