#!/usr/bin/env python3
"""Package Retrostation into a drop-in APPS bundle.

Usage::

    scripts/package_release.py
    scripts/package_release.py --out dist          # default
    scripts/package_release.py --list              # show what would go in

Produces ``dist/Retrostation-<version>.zip`` holding an ``APPS/`` tree:

    APPS/
      Retrostation.sh              menu entry (must sit in APPS itself)
      Imgs/Retrostation.png        menu icon
      Retrostation/
        retrostation.sh            launcher
        src/retrostation/...       the application
        scripts/...                on-device diagnostics
        README.md  CHANGELOG.md

Installing it is one copy: unpack the zip and drop ``APPS/`` onto the card's
``/mnt/mmc/Roms/APPS/``.  That is the whole point of shipping this shape
rather than a Python package -- the handheld has no pip, and the stock
frontend only lists scripts it finds directly under APPS.

Sources are shipped as ``.py``, not ``.pyc``: bytecode does not survive a
different Python minor version, and the three devices this runs on disagree
(3.10 and 3.11).  The launcher's environment is derived from what it finds at
run time, so nothing here is compiled against the build machine.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "dist"

#: Directories never shipped: development-only, or far too large for a card.
EXCLUDED_DIRS = frozenset({
    ".git", ".github", ".idea", ".venv", "venv", "__pycache__",
    "dist", "build", "tests", "docs", "screenshots", "prototype",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
#: Files never shipped.  ``.tmp-*`` are throwaway probes from a debug session.
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".orig", ".rej"})
EXCLUDED_PREFIXES = frozenset({".tmp-"})
EXCLUDED_NAMES = frozenset({
    ".gitignore", ".DS_Store", "Thumbs.db", "conftest.py", "pytest.ini",
    "systems.example.json",  # a template; the device writes systems.json
})

#: Diagnostics worth having on the device: they are how a broken install gets
#: diagnosed without a computer.
DEVICE_SCRIPTS = (
    "screenshot.py",
    "device_selftest.py",
    "video_selftest.py",
    "probe_input.py",
)


def version(root: Path) -> str:
    """The version string, read without importing the package."""
    namespace: dict[str, str] = {}
    source = root / "src" / "retrostation" / "__init__.py"
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    return namespace["__version__"]


def _skipped(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES or path.name.startswith(tuple(EXCLUDED_PREFIXES)):
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    return any(part in EXCLUDED_DIRS for part in path.parts)


def bundle_entries(root: Path) -> list[tuple[Path, str]]:
    """``(local path, name inside the zip)`` for every file in the bundle."""
    entries: list[tuple[Path, str]] = []

    def add(local: Path, zip_name: str) -> None:
        if local.exists():
            entries.append((local, zip_name))

    # The application: everything under src/, minus caches and test fixtures.
    src = root / "src"
    for path in sorted(src.rglob("*")):
        if path.is_dir() or _skipped(path):
            continue
        entries.append((path, f"APPS/Retrostation/src/{path.relative_to(src).as_posix()}"))

    add(root / "retrostation.sh", "APPS/Retrostation/retrostation.sh")
    for name in DEVICE_SCRIPTS:
        add(root / "scripts" / name, f"APPS/Retrostation/scripts/{name}")

    # Menu entry and icon live in APPS itself, not in the app directory.
    add(root / "packaging" / "APPS" / "Retrostation.sh", "APPS/Retrostation.sh")
    add(root / "packaging" / "APPS" / "Imgs" / "Retrostation.png",
        "APPS/Imgs/Retrostation.png")

    add(root / "README.md", "APPS/Retrostation/README.md")
    add(root / "README.en.md", "APPS/Retrostation/README.en.md")
    add(root / "CHANGELOG.md", "APPS/Retrostation/CHANGELOG.md")
    return entries


def write_bundle(entries: list[tuple[Path, str]], target: Path, *, version_text: str) -> int:
    """Zip ``entries`` to ``target``.  Returns the number of files written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    # A staging copy first: writing the zip directly into dist/ works, but a
    # partial zip left behind by a failed run looks exactly like a good one
    # until somebody tries to install it.
    staged = target.with_suffix(".zip.part")
    written = 0
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as bundle:
        for local, name in entries:
            info = zipfile.ZipInfo(name, date_time=_stamp(local))
            # scp and unzip both drop the executable bit; set it explicitly so
            # the launcher works straight out of the archive.
            info.external_attr = (0o755 if local.suffix == ".sh" else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, local.read_bytes())
            written += 1
        bundle.writestr(
            zipfile.ZipInfo("APPS/Retrostation/VERSION", date_time=_stamp(target)),
            f"Retrostation {version_text}\n",
        )
    shutil.move(str(staged), str(target))
    return written


def _stamp(path: Path) -> tuple[int, int, int, int, int, int]:
    """Zip timestamps are local; keep them inside the 1980-2107 range."""
    import time

    moment = time.localtime(path.stat().st_mtime if path.exists() else time.time())
    return (max(1980, moment.tm_year), moment.tm_mon, moment.tm_mday,
            moment.tm_hour, moment.tm_min, min(58, moment.tm_sec))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output directory (default: {DEFAULT_OUT})")
    parser.add_argument("--name", help="override the archive's base name")
    parser.add_argument("--list", action="store_true",
                        help="print the contents and exit")
    args = parser.parse_args(argv)

    found = version(ROOT)
    entries = bundle_entries(ROOT)
    if not entries:
        print(f"nothing to package under {ROOT}", file=sys.stderr)
        return 1

    if args.list:
        for _local, name in entries:
            print(name)
        return 0

    base = args.name or f"Retrostation-{found}"
    target = args.out / f"{base}.zip"
    count = write_bundle(entries, target, version_text=found)
    size = target.stat().st_size / 1024
    print(f"packaged {found}: {count} files -> {target} ({size:.0f} KB)")
    print("Install: unpack and copy APPS/ over the card's /mnt/mmc/Roms/APPS/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
