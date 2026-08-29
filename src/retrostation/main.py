"""Entry point.

Boot order (DESIGN §3): platform -> config -> translator -> library -> UI.

The library scan runs in a background thread, so the home page appears
immediately and fills in as systems are indexed.

Exit codes (contract with ``retrostation.sh``):

* ``0``  -- the user quit the frontend; the bootstrap stops.
* ``42`` -- a game finished running; the bootstrap restarts us and the session
  resumes where it left off.
* other  -- crash; the bootstrap still restarts us, which is the safest thing
  a console can do.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from .core.config import Config
from .core.i18n import Translator
from .data.library import Library
from .data.systems import display_name
from .platform.base import Platform
from .platform.linux.platform import LinuxPlatform
from .ui.app import App

log = logging.getLogger("retrostation")


def build_platform(args: argparse.Namespace) -> Platform:
    return LinuxPlatform(rom_root=args.rom_root, headless=args.headless)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="retrostation")
    parser.add_argument("--config", help="path to config.json", default=None)
    parser.add_argument("--rom-root", help="override the ROM root directory", default=None)
    parser.add_argument("--headless", action="store_true", help="run without SDL (development)")
    parser.add_argument("--scan-only", action="store_true",
                        help="scan the library, print a summary and exit")
    parser.add_argument("--check", metavar="SYSTEM",
                        help="report metadata/media coverage for one system and exit")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def scan_only(platform: Platform, config: Config) -> int:
    """Library summary without a display."""
    result = Library(platform, config).scan()
    print(f"root:     {platform.rom_root}")
    print(f"systems:  {len(result.systems)}")
    print(f"roms:     {result.total_roms}")
    print(f"duration: {result.duration:.2f}s (cached {result.cached} / rescanned {result.rescanned})")
    for key in sorted(result.systems, key=lambda k: -len(result.systems[k])):
        print(f"  {key:<12} {len(result.systems[key]):>5}  {display_name(key)}")
    return 0


def check_system(platform: Platform, config: Config, system_key: str) -> int:
    """Metadata/media coverage report for one system."""
    library = Library(platform, config)
    library.scan()
    games = library.resolve_all(system_key)
    total = len(games)
    if total == 0:
        print(f"{system_key}: no ROMs")
        return 1

    print(f"{system_key}: {total} games")
    print(f"  named:     {sum(1 for g in games if g.name and g.name != g.path.stem)}")
    print(f"  described: {sum(1 for g in games if g.blurb)}")
    print(f"  rated:     {sum(1 for g in games if g.rating is not None)}")
    print(f"  dated:     {sum(1 for g in games if g.release is not None)}")
    print(f"  favorite:  {sum(1 for g in games if g.favorite)}")
    print(f"  played:    {sum(1 for g in games if g.play_count)}")
    for kind in ("cover", "logo", "video"):
        print(f"  {kind:<9}  {sum(1 for g in games if g.has_asset(kind))}")
    return 0


def run_ui(platform: Platform, config: Config, translator: Translator) -> int:
    """Background scan, then hand over to the UI loop."""
    library = Library(platform, config)
    threading.Thread(target=_scan_in_background, args=(library,),
                     name="retrostation-scan", daemon=True).start()

    app = App(platform, config, translator, library)
    code = app.run()
    log.info("frontend exit code %s", code)
    return code


def _scan_in_background(library: Library) -> None:
    try:
        library.scan()
    except Exception:  # noqa: BLE001 - the UI must still come up
        log.exception("library scan failed")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    platform = build_platform(args)
    config = Config.load(args.config or (platform.config_dir / "config.json"))
    translator = Translator(config.language)

    if args.scan_only:
        return scan_only(platform, config)
    if args.check:
        return check_system(platform, config, args.check.upper())
    return run_ui(platform, config, translator)


if __name__ == "__main__":
    sys.exit(main())

