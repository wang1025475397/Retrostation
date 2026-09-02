#!/usr/bin/env python3
"""Deploy Retrostation to a Linux handheld over SSH.

Usage::

    scripts/deploy.py root@<掌机IP>
    scripts/deploy.py --dry-run root@<掌机IP>
    scripts/deploy.py --reset root@<掌机IP>   # nuke /tmp/retrostation_*

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
#: ``Retrostation`` for development, ``Retrostation-Release`` for a built
#: bundle.  The menu entry takes its name from the install directory, so both
#: can sit side by side, each with its own config, index and log.
DEFAULT_VARIANT = "Retrostation"


def artifact_paths(variant: str) -> tuple[tuple[str, str], ...]:
    """(path relative to the source root, remote path) for one variant.

    Order matters: the menu entry goes last, so a half-finished deployment
    never shows up in the APPS menu.
    """
    install = f"{APPS_DIR}/{variant}"
    return (
        ("src", f"{install}/src"),
        ("retrostation.sh", f"{install}/retrostation.sh"),
        ("scripts/screenshot.py", f"{install}/scripts/screenshot.py"),
        ("scripts/device_selftest.py", f"{install}/scripts/device_selftest.py"),
        ("scripts/video_selftest.py", f"{install}/scripts/video_selftest.py"),
        ("scripts/probe_input.py", f"{install}/scripts/probe_input.py"),
        # A variant may bring its own icon; otherwise reuse the default one.
        (f"packaging/APPS/Imgs/{variant}.png", f"{APPS_DIR}/Imgs/{variant}.png"),
        ("packaging/APPS/Imgs/Retrostation.png", f"{APPS_DIR}/Imgs/{variant}.png"),
        ("packaging/APPS/Retrostation.sh", f"{APPS_DIR}/{variant}.sh"),
    )


def artifacts(source_root: Path, variant: str = DEFAULT_VARIANT) -> list[tuple[Path, str]]:
    """Existing artifacts under ``source_root``, in deployment order.

    ``source_root`` is the repository during development and a built bundle for
    a release (see ``scripts/build_release.py``), where every ``.py`` has been
    replaced by a ``.pyc`` and the diagnostics may have been left out.
    """
    # Keyed by remote path so the first match wins: a variant icon beats the
    # shared one, and a bundle's .pyc replaces the .py rather than adding to it.
    found: dict[str, Path] = {}
    for relative, remote in artifact_paths(variant):
        if remote in found:
            continue
        candidate = source_root / relative
        local = candidate if candidate.exists() else candidate.with_suffix(".pyc")
        if not local.exists():
            continue  # a bundle without diagnostics is perfectly deployable
        if local.suffix == ".pyc":
            remote = remote[: -len(".py")] + ".pyc"
        found[remote] = local
    return [(local, remote) for remote, local in found.items()]


def sweep_local_caches(source_root: Path) -> int:
    """Drop ``__pycache__`` under ``source_root/src`` before pushing.

    A Windows dev machine leaves behind ``.pyc`` files built by *its*
    interpreter (3.12 here, say) which the device's 3.11 will never load --
    dead weight that also makes the installed tree look like it ships
    bytecode.  They are a cache, so deleting them costs nothing; Python
    rebuilds them on the next run.
    """
    removed = 0
    src = source_root / "src"
    if not src.is_dir():
        return 0
    for cache in sorted(src.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    return removed


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
    parser.add_argument("--source", type=Path, default=ROOT,
                        help="directory to deploy from; defaults to the repository, "
                             "or point it at a bundle from scripts/build_release.py")
    parser.add_argument("--variant", default=DEFAULT_VARIANT,
                        help=f"install directory / menu entry name "
                             f"(default: {DEFAULT_VARIANT}; use Retrostation-Release "
                             f"for a built bundle, so both can coexist)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen, don't touch the device")
    parser.add_argument("--reset", action="store_true",
                        help="also nuke /tmp/retrostation_* on the device (debug cruft)")
    args = parser.parse_args()

    variant = args.variant
    install_dir = f"{APPS_DIR}/{variant}"
    pending = artifacts(args.source, variant)
    if not pending:
        print(f"nothing to deploy from {args.source}", file=sys.stderr)
        return 1

    transport = build_transport(args.host, args.password, args.dry_run)
    try:
        swept = sweep_local_caches(args.source)
        if swept:
            print(f"  swept {swept} stale __pycache__ directories")

        print(f"deploying to {args.host} -> {install_dir}")
        transport.run(["mkdir", "-p", f"{install_dir}/scripts", f"{APPS_DIR}/Imgs"])

        # Delete the old src/ and launcher so we never mix versions.
        transport.run(["rm", "-rf", f"{install_dir}/src", f"{install_dir}/retrostation.sh"])

        for local, remote in pending:
            transport.push(local, remote)

        # Ensure executability: scp/sftp does not preserve the mode.
        transport.run(["chmod", "+x",
                       f"{install_dir}/retrostation.sh",
                       f"{APPS_DIR}/{variant}.sh"])

        if args.reset:
            transport.run(["sh", "-c", "rm -rf /tmp/retrostation_* /tmp/shots 2>/dev/null || true"])

        if args.dry_run:
            print("OK (dry-run)")
            return 0

        # Confirm what actually landed.
        transport.run(["ls", "-la", f"{APPS_DIR}/{variant}.sh",
                       f"{APPS_DIR}/Imgs/{variant}.png"], check=False)
    finally:
        if hasattr(transport, "close"):
            transport.close()
    print()
    print(f"Now open the device's APPS menu and tap {variant}.sh.")
    print(f"If nothing happens, read:  {install_dir}/log.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
