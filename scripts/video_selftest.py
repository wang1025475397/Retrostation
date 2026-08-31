#!/usr/bin/env python3
"""Video pipeline self-test -- run this **on the handheld**.

Unit tests cover the player's logic with a fake decoder; this covers the part
they cannot: that ``ffmpeg`` on this device actually feeds us frames, how fast,
and what it costs.  Everything is measured, because the whole video feature
stands or falls on those numbers (DESIGN §6.5, §9.3).

    python3 -X utf8 scripts/video_selftest.py                 # auto-pick a video
    python3 -X utf8 scripts/video_selftest.py --system FC     # only look in FC
    python3 -X utf8 scripts/video_selftest.py --make-demo FC  # synthesise one
    python3 -X utf8 scripts/video_selftest.py --seconds 10    # longer soak

``--make-demo`` generates a 10s test clip named after a real ROM, which is the
quickest way to see the bottom screen actually play something.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from retrostation.core.config import Config  # noqa: E402
from retrostation.core.model import ASSET_VIDEO, Game  # noqa: E402
from retrostation.data.library import Library  # noqa: E402
from retrostation.data.video import VideoPlayer, VideoSettings  # noqa: E402
from retrostation.platform.linux.platform import LinuxPlatform  # noqa: E402
from retrostation.platform.linux.video import available, build_command  # noqa: E402


def cpu_seconds() -> float:
    """This process' CPU time so far (user + system), from /proc."""
    with open("/proc/self/stat", encoding="utf-8") as handle:
        fields = handle.read().rsplit(") ", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return (int(fields[11]) + int(fields[12])) / ticks


def find_video(library: Library, system: str | None) -> tuple[object, Path] | None:
    """First game that has a video asset, scanning the biggest systems first."""
    keys = [system] if system else sorted(
        library.system_keys(), key=lambda key: -library.rom_count(key)
    )
    for key in keys:
        for game in library.resolve_all(key):
            path = game.asset(ASSET_VIDEO)
            if path is not None and Path(path).is_file():
                return game, Path(path)
    return None


def make_demo(platform: LinuxPlatform, library: Library, system: str) -> Path | None:
    """Synthesise a clip named after a real ROM so the UI can show it."""
    if not available():
        print("ffmpeg is missing -- cannot synthesise a clip")
        return None
    games = library.resolve_all(system)
    if not games:
        print(f"{system}: no ROMs")
        return None

    game = games[0]
    target = platform.rom_root / system / "video" / f"{game.path.stem}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-vf", "drawtext=text=Retrostation:fontsize=28:x=20:y=196:fontcolor=white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(target),
    ]
    print("generating", target)
    subprocess.run(command, check=False)  # noqa: S603 - fixed argv
    return target if target.is_file() else None


def probe(path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,width,height,r_frame_rate", "-show_entries",
         "format=duration", "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, check=False,
    )
    print("  source:")
    for line in result.stdout.strip().splitlines():
        print("   ", line)


def play(platform: LinuxPlatform, path: Path, settings: VideoSettings, seconds: float) -> int:
    player = VideoPlayer(platform, settings)
    player.configure(size=(settings.width, settings.height))

    # The player only needs ``key`` and the video asset; a bare Game is enough.
    holder = Game(key=f"selftest/{path.name}", path=path)
    holder.set_asset(ASSET_VIDEO, path)

    before_cpu = cpu_seconds()
    started = time.monotonic()
    player.select(holder)

    last_seq = player.frame_seq  # 0: only *changes* count as frames
    frames = 0
    first_frame_at: float | None = None
    last_progress: float | None = None
    while time.monotonic() - started < seconds:
        player.update()
        seq = player.frame_seq
        if seq != last_seq:
            if first_frame_at is None:
                first_frame_at = time.monotonic()
                print(f"  first frame after {(first_frame_at - started) * 1000:.0f} ms "
                      f"(debounce + ffmpeg start)")
            frames += seq - last_seq
            last_seq = seq
            last_progress = player.progress()
        time.sleep(0.02)

    elapsed = time.monotonic() - started
    spent_cpu = cpu_seconds() - before_cpu
    player.stop()

    print(f"  frames:      {frames}")
    print(f"  wall:        {elapsed:.2f}s")
    print(f"  measured:    {frames / elapsed:.1f} fps (target {settings.fps})")
    print(f"  decode cost: {spent_cpu / elapsed * 100:.1f}% of one core")
    print(f"  progress:    {last_progress}")
    return 0 if frames else 1


def ui_smoke(platform: LinuxPlatform, library: Library, system: str, out: Path,
             seconds: float = 3.0) -> int:
    """Run the real UI headless and save the bottom panel while it plays.

    This is the only check that exercises the whole chain -- selection ->
    debounce -> ffmpeg -> frame queue -> MediaView -- on the device itself.
    """
    from retrostation.core.i18n import Translator
    from retrostation.ui.app import App

    config = Config()
    app = App(platform, config, Translator("zh_CN"), library)
    app.session.view = "games"
    app.session.platform_index = app.session.system_keys().index(system)

    index = next(
        (i for i, game in enumerate(app.session.games()) if game.asset(ASSET_VIDEO) is not None),
        None,
    )
    if index is None:
        print(f"{system}: no game with a video")
        return 1
    app.session.game_index = index

    frames = max(10, int(seconds * 30))
    started = time.monotonic()
    app.run(max_frames=frames)
    elapsed = time.monotonic() - started

    out.mkdir(parents=True, exist_ok=True)
    for position, painter in enumerate(app._painters):  # noqa: SLF001 - dev tool
        target = out / f"video_{'top' if position == 0 else 'bottom'}.png"
        painter.canvas.pil_image.save(target)
        print("  saved", target)

    decoded = app._video.frame_seq  # noqa: SLF001
    print(f"  ui frames:     {frames} in {elapsed:.2f}s ({frames / elapsed:.1f} fps)")
    print(f"  video frames:  {decoded} ({decoded / elapsed:.1f} fps, target {config.video_fps})")
    return 0 if decoded else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="video pipeline self-test")
    parser.add_argument("--system", help="only look inside this system")
    parser.add_argument("--video", help="decode this file instead of searching")
    parser.add_argument("--ui", metavar="OUT_DIR",
                        help="render the real UI headless and save the panels")
    parser.add_argument("--make-demo", metavar="SYSTEM", help="synthesise a clip first")
    parser.add_argument("--seconds", type=float, default=5.0, help="how long to play")
    parser.add_argument("--size", default="288x216", help="decode target, WxH")
    parser.add_argument("--fps", type=int, default=15, help="decode rate")
    args = parser.parse_args(argv)

    # Headless: no SDL, so this also works over SSH while the stock frontend
    # holds the panels (DESIGN §8.1).
    platform = LinuxPlatform(rom_root=os.environ.get("RETROSTATION_ROM_ROOT"), headless=True)
    config = Config()
    library = Library(platform, config)
    library.scan()

    print(f"platform:     {platform.name}")
    print(f"rom root:     {platform.rom_root}")
    print(f"ffmpeg:       {'yes' if available() else 'NO -- video stays off'}")
    if not available():
        return 1

    if args.make_demo:
        made = make_demo(platform, library, args.make_demo)
        if made is None:
            return 1

    if args.video:
        path = Path(args.video)
        if not path.is_file():
            print("no such file:", path)
            return 1
    else:
        found = find_video(library, args.system)
        if found is None:
            print("no game with a video found; try --make-demo FC or --video <file>")
            return 1
        _game, path = found

    if args.ui:
        system = args.system or str(path.parent.parent.name)
        return ui_smoke(platform, library, system, Path(args.ui), args.seconds)

    width, height = (int(part) for part in args.size.split("x"))
    settings = VideoSettings(width=width, height=height, fps=args.fps)
    print(f"video:        {path}")
    print("command:      " + " ".join(build_command(path, width=width, height=height, fps=args.fps)))
    probe(path)
    return play(platform, path, settings, args.seconds)


if __name__ == "__main__":
    sys.exit(main())
