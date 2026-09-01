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
from .core.theme import COLORS
from .data.library import Library
from .data.systems import USER_SYSTEMS_FILE, apply_user_systems, display_name
from .platform.base import Platform
from .platform.linux.platform import LinuxPlatform, resolve_config_dir
from .ui.app import EXIT_OK, App

log = logging.getLogger("retrostation")


def build_platform(args: argparse.Namespace, config: Config) -> Platform:
    """Choose the ROM root: command line first, then the card in the config.

    ``config.rom_root`` stays ``"auto"`` until the player picks a card; anything
    else is a path they chose, and wins over probing.
    """
    explicit = args.rom_root or (None if config.rom_root == "auto" else config.rom_root)
    return LinuxPlatform(rom_root=explicit, headless=args.headless)


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
    """Come up on the index, then re-scan behind the UI."""
    library = Library(platform, config)
    # Listing the ROM tree is a stat() per file -- ~0.7 s for 3.9k ROMs here --
    # which is most of the cold start.  The index remembers the last listing,
    # so the first frame can come up with a full library, and the real scan
    # (which is what notices a newly copied game) runs behind it.
    library.scan(cached_only=True)

    app = App(platform, config, translator, library)
    # Started from the first-frame callback, not here: the listing is a stat()
    # per file and competing with the first paint for the card doubled that
    # frame's cost (0.57 s -> 1.34 s measured on the device).
    app.on_ready = lambda: threading.Thread(
        target=_scan_in_background, args=(app, library),
        name="retrostation-scan", daemon=True).start()
    try:
        code = app.run()
    except KeyboardInterrupt:
        # Ctrl+C over an SSH debug session.  ``App.run`` already shut the
        # display down in its ``finally``, so this is a plain user quit.
        code = EXIT_OK
    log.info("frontend exit code %s", code)
    return code


def _scan_in_background(app: App, library: Library) -> None:
    try:
        library.scan()
    except Exception:  # noqa: BLE001 - the UI must still come up
        log.exception("library scan failed")
        return
    # The listing is in; tell the UI so it stops showing the index's version.
    app.library_changed()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Config before platform: it names the card in use, and the platform needs
    # that to resolve the ROM root.  The config directory does not depend on
    # which card is active, so resolving it on its own is safe.
    config = Config.load(args.config or (resolve_config_dir(None) / "config.json"))
    # Before anything draws: the palette is a shared instance, so loading the
    # configured theme here is what every screen picks up.
    COLORS.apply(config.theme, config.theme_variant)
    translator = Translator(config.language)

    platform = build_platform(args, config)
    # Systems can be added or retuned from the config directory, so a player
    # dropping in a new core never has to edit the installed package.  This has
    # to land before anything resolves a key: the scan filters directories by
    # ``lookup(...).hidden`` and every screen asks for labels and cores.
    apply_user_systems(platform.config_dir / USER_SYSTEMS_FILE)

    if args.scan_only:
        return scan_only(platform, config)
    if args.check:
        return check_system(platform, config, args.check.upper())
    return run_ui(platform, config, translator)


if __name__ == "__main__":
    sys.exit(main())

