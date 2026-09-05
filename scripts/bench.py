"""Benchmark the slow paths on a real handheld (no display needed).

Measures the stages a player feels as lag: module import, the ROM scan that
blocks first paint, per-cover thumbnail generation, and building the launch
plan for one game.  Run on the device from the install directory:

    cd /mnt/mmc/Roms/APPS/Retrostation
    PYTHONPATH=src python3 scripts/bench.py

Game *launch* itself (the emulator's cold start) is not measured here: it
needs a live display and an interactive tap, so time it by eye -- the splash
added for slow boxes covers the hand-off.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


def _now() -> float:
    return time.time()


def _bench_import() -> float:
    t = _now()
    import retrostation.ui.app  # noqa: F401  (pulls in session, painter, platform)
    return time.time() - t


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here / "src"))

    print(f"import            {_bench_import():.3f}s")

    from retrostation.platform.linux.platform import LinuxPlatform
    from retrostation.core.config import Config
    from retrostation.data.library import Library
    from retrostation.launcher.launch import build_plan, LaunchError

    cfg_dir = Path("/mnt/mmc/Roms/APPS/Retrostation")
    platform = LinuxPlatform(config_dir=str(cfg_dir), headless=True)
    config = Config.load(cfg_dir / "config.json")
    lib = Library(platform, config)

    t = _now()
    res = lib.scan()
    print(f"scan             {res.duration:.3f}s  "
          f"systems={len(res.systems)} roms={res.total_roms} "
          f"cached={res.cached} rescanned={res.rescanned}")

    picked_sys = None
    game = None
    for syskey in res.systems:
        t = _now()
        lib.load_games(syskey)
        g = lib.system(syskey).games
        cover_game = next(
            (gm for gm in g if (gm.asset("cover") or gm.asset("marquee") or gm.asset("screenshot"))),
            None,
        )
        if cover_game is not None:
            picked_sys = syskey
            game = cover_game
            print(f"load_games({syskey}) {time.time() - t:.3f}s  games={len(g)}")
            break
    if game is None:
        print("  no games with a cover found")
        return 0

    cover = game.asset("cover") or game.asset("marquee") or game.asset("screenshot")
    t = _now()
    bmp = lib._thumbnails.get("cover", cover, 200, 280, cover=True)
    print(f"thumbnail gen     {time.time() - t:.3f}s  {'ok' if bmp else 'FAIL'}")

    t = _now()
    try:
        plan = build_plan(game, config)
        print(f"build_plan        {time.time() - t:.3f}s  core={plan.core_label}")
        print(f"  launch argv      {' '.join(plan.argv)}")
    except LaunchError as exc:
        print(f"build_plan        ERROR: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
