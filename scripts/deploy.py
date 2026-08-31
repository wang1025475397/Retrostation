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
    (ROOT / "scripts" / "video_selftest.py", f"{INSTALL_DIR}/scripts/video_selftest.py"),
    (ROOT / "scripts" / "probe_input.py", f"{INSTALL_DIR}/scripts/probe_input.py"),
    (ROOT / "packaging" / "APPS" / "Imgs" / "Retrostation.png",
     f"{APPS_DIR}/Imgs/Retrostation.png"),
    (ROOT / "packaging" / "APPS" / "Retrostation.sh", f"{APPS_DIR}/Retrostation.sh"),
)


def _quote(args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


class ShellTransport:
    """Plain ``ssh``/``scp`` -- needs a key, because typing is not automatable."""

    def __init__(self, host: str, dry_run: bool) -> None:
        self._host = host
        self._dry_run = dry_run

    def run(self, remote_args: list[str], *, check: bool = True) -> None:
        self._exec(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                    self._host, "--", *remote_args], check=check)

    def push(self, local: Path, remote: str) -> None:
        args = ["scp", "-O", "-q", "-o", "StrictHostKeyChecking=accept-new"]
        if local.is_dir():
            args.append("-r")
        args.extend([str(local), f"{self._host}:{remote}"])
        self._exec(args)

    def _exec(self, args: list[str], check: bool = True) -> None:
        line = _quote(args)
        if self._dry_run:
            print(f"DRY-RUN  {line}")
            return
        print(f"EXEC     {line}")
        result = subprocess.run(args, check=False)
        if check and result.returncode != 0:
            print(f"FAILED  (exit {result.returncode})", file=sys.stderr)
            sys.exit(result.returncode or 1)


class ParamikoTransport:
    """Password login (Windows ssh cannot be given a password non-interactively)."""

    def __init__(self, host: str, password: str, dry_run: bool, remote_module) -> None:
        self._host = host
        self._dry_run = dry_run
        self._remote = remote_module
        self._client = None if dry_run else remote_module.connect(host, password)

    def run(self, remote_args: list[str], *, check: bool = True) -> None:
        command = " ".join(shlex.quote(a) for a in remote_args)
        if self._dry_run:
            print(f"DRY-RUN  ssh {self._host} -- {command}")
            return
        print(f"EXEC     ssh {self._host} -- {command}")
        code = self._remote.run(self._client, command)
        if check and code != 0:
            print(f"FAILED  (exit {code})", file=sys.stderr)
            sys.exit(code or 1)

    def push(self, local: Path, remote: str) -> None:
        if self._dry_run:
            print(f"DRY-RUN  {local} -> {self._host}:{remote}")
            return
        print(f"PUSH     {local} -> {remote}")
        self._remote.push(self._client, local, remote)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def build_transport(host: str, password: str | None, dry_run: bool):
    """Key-based ssh when possible, paramiko when a password is all we have."""
    if not password and shutil.which("scp") and shutil.which("ssh"):
        return ShellTransport(host, dry_run)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import remote  # paramiko-backed helper; a development-only dependency
    except ImportError:  # pragma: no cover
        print("paramiko is required for password login: pip install paramiko",
              file=sys.stderr)
        raise SystemExit(1) from None
    return ParamikoTransport(host, password or "root", dry_run, remote)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Retrostation over SSH.")
    parser.add_argument("host", help="SSH target, e.g. root@192.168.0.55")
    parser.add_argument("--password", default=os.environ.get("RETROSTATION_SSH_PASSWORD"),
                        help="SSH password; enables paramiko (no key needed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen, don't touch the device")
    parser.add_argument("--reset", action="store_true",
                        help="also nuke /tmp/retrostation_* on the device (debug cruft)")
    args = parser.parse_args()

    transport = build_transport(args.host, args.password, args.dry_run)
    try:
        print(f"deploying to {args.host} -> {INSTALL_DIR}")
        transport.run(["mkdir", "-p", f"{INSTALL_DIR}/scripts", f"{APPS_DIR}/Imgs"])

        # Delete the old src/ and launcher so we never mix versions.
        transport.run(["rm", "-rf", f"{INSTALL_DIR}/src", f"{INSTALL_DIR}/retrostation.sh"])

        for local, remote in ARTIFACTS:
            if not local.exists():
                print(f"missing local artifact: {local}", file=sys.stderr)
                return 1
            transport.push(local, remote)

        # Ensure executability: scp/sftp does not preserve the mode.
        transport.run(["chmod", "+x",
                       f"{INSTALL_DIR}/retrostation.sh",
                       f"{APPS_DIR}/Retrostation.sh"])

        if args.reset:
            transport.run(["sh", "-c", "rm -rf /tmp/retrostation_* /tmp/shots 2>/dev/null || true"])

        if args.dry_run:
            print("OK (dry-run)")
            return 0

        # Confirm what actually landed.
        transport.run(["ls", "-la", f"{APPS_DIR}/Retrostation.sh",
                       f"{APPS_DIR}/Imgs/Retrostation.png"], check=False)
    finally:
        if hasattr(transport, "close"):
            transport.close()
    print()
    print("Now open the device's APPS menu and tap Retrostation.sh.")
    print("If nothing happens, read:  /mnt/mmc/Roms/APPS/Retrostation/log.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
