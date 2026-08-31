#!/usr/bin/env python3
"""Password-based SSH helper for the RG DS dev box.

Windows ships OpenSSH but no ``sshpass``, and non-interactive automation cannot
type a password, so every remote step in this project goes through paramiko
instead of shelling out to ``ssh``/``scp`` (``scripts/deploy.py`` does the same).

    python scripts/remote.py root@192.168.0.55 "uname -a"
    python scripts/remote.py root@192.168.0.55 --push  local.txt /tmp/local.txt
    python scripts/remote.py root@192.168.0.55 --pushd build/src /mnt/.../src
    python scripts/remote.py root@192.168.0.55 --pull  /tmp/log.txt .

The password comes from ``--password`` / ``RETROSTATION_SSH_PASSWORD`` so it
never has to be committed; it defaults to the stock ``root``.
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import suppress
from pathlib import Path, PurePosixPath

DEFAULT_PASSWORD = "root"


def connect(target: str, password: str, timeout: float = 10.0):
    import paramiko

    user, _, host = target.partition("@")
    if not host:
        host, user = user, "root"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=user,
        password=password,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run(client, command: str, *, timeout: float = 120.0) -> int:
    """Stream a remote command's output; return its exit status."""
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    for line in iter(stdout.readline, ""):
        sys.stdout.write(line)
    for line in iter(stderr.readline, ""):
        sys.stderr.write(line)
    return stdout.channel.recv_exit_status()


# --------------------------------------------------------------------------- #
# File transfer
# --------------------------------------------------------------------------- #

def push(client, local: Path, remote: str) -> None:
    sftp = client.open_sftp()
    try:
        _sftp_put(sftp, local, remote)
    finally:
        sftp.close()


def _sftp_put(sftp, local: Path, remote: str) -> None:
    if local.is_dir():
        _sftp_mkdirs(sftp, remote)
        for child in sorted(local.iterdir()):
            if child.name in ("__pycache__", ".pytest_cache"):
                continue
            _sftp_put(sftp, child, f"{remote.rstrip('/')}/{child.name}")
        return
    _sftp_mkdirs(sftp, str(Path(remote).parent))
    sftp.put(str(local), remote)
    print(f"  <- {local} -> {remote}")


def _sftp_mkdirs(sftp, path: str) -> None:
    """``mkdir -p`` over SFTP; the device has no shell quoting we can trust.

    Remote paths are always POSIX, so they are parsed with ``PurePosixPath``:
    ``Path`` would use Windows rules and ``\\``.parent is ``\\``, which recurses
    forever.
    """
    if not path:
        return
    with suppress(OSError):
        sftp.stat(path)
        return
    parent = str(PurePosixPath(path).parent)
    if parent and parent != path:
        _sftp_mkdirs(sftp, parent)
    with suppress(OSError):
        # raced with a parallel create; the caller only needs it to exist
        sftp.mkdir(path)


def pull(client, remote: str, local: Path) -> None:
    sftp = client.open_sftp()
    try:
        if local.is_dir():
            local = local / Path(remote).name
        local.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(remote, str(local))
        print(f"  -> {remote} -> {local}")
    finally:
        sftp.close()


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ssh helper for the dev handheld")
    parser.add_argument("target", help="user@host, e.g. root@192.168.0.55")
    parser.add_argument("command", nargs="*", help="command to run remotely")
    parser.add_argument("--password", default=os.environ.get("RETROSTATION_SSH_PASSWORD"))
    parser.add_argument("--push", nargs=2, metavar=("SRC", "DST"))
    parser.add_argument("--pushd", nargs=2, metavar=("SRC", "DST"))
    parser.add_argument("--pull", nargs=2, metavar=("SRC", "DST"))
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    password = args.password or DEFAULT_PASSWORD
    client = connect(args.target, password)
    try:
        if args.push:
            push(client, Path(args.push[0]), args.push[1])
        if args.pushd:
            push(client, Path(args.pushd[0]), args.pushd[1])
        if args.pull:
            pull(client, args.pull[0], Path(args.pull[1]))
        if args.command:
            return run(client, " ".join(args.command), timeout=args.timeout)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
