#!/usr/bin/env python3
"""Package Retrostation for the TrimUI Smart Pro (CrossMix OS).

Usage::

    scripts/package_trimui.py
    scripts/package_trimui.py --out dist        # default
    scripts/package_trimui.py --list            # show what would go in

Produces ``dist/Retrostation-<version>-trimui.zip`` holding a CrossMix app:

    Apps/Retrostation/
      config.json          CrossMix menu metadata (NOT our settings)
      launch.sh            entry point CrossMix runs
      Retrostation.png     menu icon
      retrostation.sh      launcher: owns the exit-42 game hand-off loop
      src/retrostation/... the application
      vendor/              Pillow, for a Python that has no site-packages
      bin/  ffmpeg, ffprobe
      lib/  the two codec libraries those two need
      data/                settings, index and log are written here

Everything the device lacks is inside the archive, so installing it is one
unzip onto the card -- no pip, no network, nothing to read.  That is the point:
this Python has no working ssl module, so pip cannot reach PyPI at all, and the
firmware ships no ffmpeg.

``vendor/`` and ``vendor/ffmpeg`` are populated by::

    python -m pip download Pillow --no-deps \\
        --platform manylinux_2_28_aarch64 --python-version 311 \\
        --implementation cp --abi cp311 --only-binary=:all: -d vendor/wheels

plus the ffmpeg/ffprobe and libfdk-aac/libmp3lame pair taken from a device that
already has a build for this class of handheld.

vendor/ is git-ignored, so handing the build to somebody else goes through a
*vendor archive* — a zip of those two directories this script can produce and
consume::

    python scripts/package_trimui.py --make-vendor-archive          # producer
    python scripts/package_trimui.py --vendor-archive vendor.zip    # consumer
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_release import (  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
    DEVICE_SCRIPTS,
    ROOT,
    _skipped,  # pyright: ignore[reportPrivateUsage]
    version,
)

DEFAULT_OUT = ROOT / "dist"

#: Files inside the app tree that must come out of the archive executable.
EXECUTABLE_NAMES = frozenset({"ffmpeg", "ffprobe"})


def _executable(local: Path) -> bool:
    return local.suffix == ".sh" or local.name in EXECUTABLE_NAMES


def bundle_entries(root: Path) -> list[tuple[Path, str]]:
    """``(local path, name inside the zip)`` for every file in the bundle."""
    entries: list[tuple[Path, str]] = []

    def add(local: Path, zip_name: str) -> None:
        if local.exists():
            entries.append((local, zip_name))
        else:
            print(f"  ! missing, skipped: {local.name}", file=sys.stderr)

    # The application.
    src = root / "src"
    for path in sorted(src.rglob("*")):
        if path.is_dir() or _skipped(path):
            continue
        entries.append((path, f"Apps/Retrostation/src/{path.relative_to(src).as_posix()}"))

    # Pillow, unpacked from the wheel -- see the module docstring.
    vendor = root / "vendor" / "pillow"
    if not vendor.is_dir():
        print("error: vendor/pillow is missing (unpack the wheel first)", file=sys.stderr)
        raise SystemExit(1)
    for path in sorted(vendor.rglob("*")):
        if path.is_dir() or path.suffix in {".pyc", ".pyo"}:
            continue
        entries.append(
            (path, f"Apps/Retrostation/vendor/{path.relative_to(vendor).as_posix()}")
        )

    # ffmpeg: the binary, and the two libraries it cannot start without.
    ffmpeg = root / "vendor" / "ffmpeg"
    for name in ("ffmpeg", "ffprobe"):
        add(ffmpeg / "bin" / name, f"Apps/Retrostation/bin/{name}")
    for name in ("libfdk-aac.so.2", "libmp3lame.so.0"):
        add(ffmpeg / "lib" / name, f"Apps/Retrostation/lib/{name}")

    # CrossMix entry points and icon.
    trimui = root / "packaging" / "trimui"
    add(trimui / "config.json", "Apps/Retrostation/config.json")
    add(trimui / "launch.sh", "Apps/Retrostation/launch.sh")
    add(trimui / "retrostation.sh", "Apps/Retrostation/retrostation.sh")
    # Default settings, copied to data/config.json on first start only.
    add(trimui / "data-config.json", "Apps/Retrostation/data-config.json")
    add(root / "packaging" / "APPS" / "Imgs" / "Retrostation.png",
        "Apps/Retrostation/Retrostation.png")

    # Diagnostics: how a broken install gets diagnosed without a computer.
    for name in DEVICE_SCRIPTS:
        add(root / "scripts" / name, f"Apps/Retrostation/scripts/{name}")

    # Docs.
    for name in ("README.md", "README.en.md", "CHANGELOG.md", "CHANGELOG.en.md"):
        add(root / name, f"Apps/Retrostation/{name}")

    return entries


#: The directories a vendor archive carries.  ``wheels/`` is left out: it is a
#: pip download cache, regenerable from the command in the module docstring.
VENDOR_PARTS = ("pillow", "ffmpeg")


def make_vendor_archive(root: Path, out: Path) -> Path:
    """Zip the vendor directories into a shareable archive; return its path."""
    vendor = root / "vendor"
    missing = [name for name in VENDOR_PARTS if not (vendor / name).is_dir()]
    if missing:
        raise SystemExit(f"error: vendor/{missing[0]} is missing; build it first")
    target = out / f"Retrostation-trimui-vendor-{version(root)}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix(".zip.part")
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as bundle:
        for part in VENDOR_PARTS:
            base = vendor / part
            for path in sorted(base.rglob("*")):
                if path.is_dir() or path.suffix in {".pyc", ".pyo"}:
                    continue
                info = zipfile.ZipInfo(f"{part}/{path.relative_to(base).as_posix()}",
                                       date_time=_stamp(path))
                info.external_attr = (0o755 if _executable(path) else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, path.read_bytes())
    shutil.move(str(staged), str(target))
    return target


def unpack_vendor_archive(archive: Path, root: Path) -> None:
    """Lay a vendor archive's contents into ``vendor/``."""
    archive, root = Path(archive), Path(root)
    vendor = root / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        strangers = [n for n in names if n.split("/")[0] not in VENDOR_PARTS]
        if strangers:
            raise SystemExit(f"error: {archive} holds unexpected entries: {strangers[:3]}")
        bundle.extractall(vendor)


def _stamp(path: Path) -> tuple[int, int, int, int, int, int]:
    """Zip timestamps are local; keep them inside the 1980-2107 range."""
    import time

    moment = time.localtime(path.stat().st_mtime if path.exists() else time.time())
    return (max(1980, moment.tm_year), moment.tm_mon, moment.tm_mday,
            moment.tm_hour, moment.tm_min, min(58, moment.tm_sec))


def write_bundle(entries: list[tuple[Path, str]], target: Path, *, version_text: str) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    # Staged first: a partial zip left by a failed run looks exactly like a good
    # one until somebody installs it.
    staged = target.with_suffix(".zip.part")
    written = 0
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as bundle:
        for local, name in entries:
            info = zipfile.ZipInfo(name, date_time=_stamp(local))
            # unzip and scp both drop the executable bit; set it explicitly so
            # the launcher and ffmpeg work straight out of the archive.
            info.external_attr = (0o755 if _executable(local) else 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, local.read_bytes())
            written += 1
        bundle.writestr(
            zipfile.ZipInfo("Apps/Retrostation/VERSION", date_time=_stamp(target)),
            f"Retrostation {version_text}\n",
        )
    shutil.move(str(staged), str(target))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--name", help="override the archive's base name")
    parser.add_argument("--list", action="store_true", help="print contents and exit")
    parser.add_argument("--make-vendor-archive", action="store_true",
                        help="zip vendor/ into a shareable archive and exit")
    parser.add_argument("--vendor-archive", type=Path, metavar="ZIP",
                        help="unpack a vendor archive into vendor/ before packaging")
    args = parser.parse_args(argv)

    if args.make_vendor_archive:
        target = make_vendor_archive(ROOT, args.out)
        size = target.stat().st_size / 1024 / 1024
        print(f"vendor archive -> {target} ({size:.1f} MB)")
        print("Recipients build with: package_trimui.py --vendor-archive <this file>")
        return 0
    if args.vendor_archive:
        unpack_vendor_archive(args.vendor_archive, ROOT)
        print(f"vendor/ restored from {args.vendor_archive}")

    found = version(ROOT)
    entries = bundle_entries(ROOT)
    if args.list:
        for _local, name in entries:
            print(name)
        return 0

    base = args.name or f"Retrostation-{found}-trimui"
    target = args.out / f"{base}.zip"
    count = write_bundle(entries, target, version_text=found)
    size = target.stat().st_size / 1024 / 1024
    print(f"packaged {found} for TrimUI: {count} files -> {target} ({size:.1f} MB)")
    print("Install: unpack and copy Apps/ over the card's /mnt/SDCARD/Apps/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
