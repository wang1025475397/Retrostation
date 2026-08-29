#!/usr/bin/env python3
"""Deploy Retrostation to a Linux handheld over SSH.

Usage::

    scripts/deploy.sh root@192.168.31.205
    scripts/deploy.sh --dry-run root@192.168.31.205
    scripts/deploy.sh --reset root@192.168.31.205   # nuke /tmp/retrostation_*

It pushes:

* ``packaging/APPS/Retrostation.sh``        -> ``/mnt/mmc/Roms/APPS/``        (menu entry)
* ``packaging/APPS/Imgs/Retrostation.png``  -> ``/mnt/mmc/Roms/APPS/Imgs/``   (menu icon)
* ``src/``                                  -> ``/mnt/mmc/Roms/APPS/Retrostation/src/``
* ``retrostation.sh``                       -> ``/mnt/mmc/Roms/APPS/Retrostation/retrostation.sh``
* ``scripts/screenshot.py``, ``scripts/device_selftest.py``,
  ``scripts/probe_input.py``
                                            -> ``/mnt/mmc/Roms/APPS/Retrostation/scripts/``

It does **not** touch ``/mnt/mmc/Roms`` (the ROM library) nor the cached
``index.json`` (the on-device index).  Use the menu's *Rescan* to pick up
newly added ROMs.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = "/mnt/mmc/Roms/APPS"
INSTALL_DIR = f"{APPS_DIR}/Retrostation"

# (local path, remote path) -- order matters: the menu entry is copied last
# so a half-deployed frontend never appears in the menu.
ARTIFACTS: tuple[tuple[Path, str], ...] = (
    (ROOT / "src", f"{INSTALL_DIR}/src"),
    (ROOT / "retrostation.sh", f"{INSTALL_DIR}/retrostation.sh"),
    (ROOT / "scripts" / "screenshot.py", f"{INSTALL_DIR}/scripts/screenshot.py"),
    (ROOT / "scripts" / "device_selftest.py", f"{INSTALL_DIR}/scripts/device_selftest.py"),
    (ROOT / "scripts" / "probe_input.py", f"{INSTALL_DIR}/scripts/probe_input.py"),
    (ROOT / "packaging" / "APPS" / "Imgs" / "Retrostation.png",
     f"{APPS_DIR}/Imgs/Retrostation.png"),
    (ROOT / "packaging" / "APPS" / "Retrostation.sh", f"{APPS_DIR}/Retrostation.sh"),
)


def run(args: list[str], *, dry_run: bool, check: bool = True) -> None:
    line = " ".join(shlex.quote(a) for a in args)
    if dry_run:
        print(f"DRY-RUN  {line}")
        return
    print(f"EXEC     {line}")
    result = subprocess.run(args, check=False)
    if check and result.returncode != 0:
        print(f"FAILED  (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode or 1)


def ssh(host: str, remote_args: list[str], *, dry_run: bool) -> None:
    run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         host, "--", *remote_args], dry_run=dry_run)


def scp(host: str, sources: list[Path], target: str, *, dry_run: bool,
         recursive: bool = False) -> None:
    args = ["scp", "-O", "-q", "-o", "StrictHostKeyChecking=accept-new"]
    if recursive:
        args.append("-r")
    args.extend([str(s) for s in sources] + [f"{host}:{target}"])
    run(args, dry_run=dry_run)


def ensure_clean_install_dir(host: str, dry_run: bool) -> None:
    """Delete the old src/ and the old launcher so we are not mixing versions."""
    ssh(host, ["rm", "-rf", f"{INSTALL_DIR}/src", f"{INSTALL_DIR}/retrostation.sh"],
        dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Retrostation over SSH.")
    parser.add_argument("host", help="SSH target, e.g. root@192.168.31.205")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen, don't touch the device")
    parser.add_argument("--reset", action="store_true",
                        help="also nuke /tmp/retrostation_* on the device (debug cruft)")
    args = parser.parse_args()

    if shutil.which("scp") is None or shutil.which("ssh") is None:
        print("ssh/scp not found in PATH", file=sys.stderr)
        return 1

    print(f"deploying to {args.host} -> {INSTALL_DIR}")
    ssh(args.host, ["mkdir", "-p", f"{INSTALL_DIR}/scripts", f"{APPS_DIR}/Imgs"],
        dry_run=args.dry_run)
    ensure_clean_install_dir(args.host, args.dry_run)

    for local, remote in ARTIFACTS:
        if not local.exists():
            print(f"missing local artifact: {local}", file=sys.stderr)
            return 1
        scp(args.host, [local], remote, dry_run=args.dry_run, recursive=local.is_dir())

    # Ensure executability: rsync preserves mode, scp does not.
    ssh(args.host, ["chmod", "+x",
                    f"{INSTALL_DIR}/retrostation.sh",
                    f"{APPS_DIR}/Retrostation.sh"],
        dry_run=args.dry_run)

    if args.reset:
        ssh(args.host, ["sh", "-c", "rm -rf /tmp/retrostation_* /tmp/shots 2>/dev/null || true"],
            dry_run=args.dry_run)

    if args.dry_run:
        print("OK (dry-run)")
        return 0

    # Confirm what actually landed.
    ssh(args.host, ["ls", "-la", f"{APPS_DIR}/Retrostation.sh", f"{APPS_DIR}/Imgs/Retrostation.png"],
        dry_run=False)
    print()
    print("Now open the device's APPS menu and tap Retrostation.sh.")
    print("If nothing happens, read:  /mnt/mmc/Roms/APPS/Retrostation/log.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
