"""Power-on autostart for Linux handhelds (see docs/DESIGN §...).

Pure-stdlib so it can be unit-tested without SDL.  Mirrors the PegasusG-by-ROC
scheme: the firmware's own autostart script is patched in place with a marked
block, and an ``autostart.enabled`` flag file is the only thing the UI toggles
-- so switching autostart *off* never rewrites the firmware script, and an
interrupted write cannot strand the device on a black screen.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Bracket markers around the block injected into the firmware autostart script.
_AUTOSTART_BEGIN = "# BEGIN RETROSTATION AUTOSTART"
_AUTOSTART_END = "# END RETROSTATION AUTOSTART"

#: Firmware autostart hooks we know how to patch, most specific first.
_AUTOSTART_TARGETS: tuple[str, ...] = (
    "/mnt/vendor/ctrl/autostart",
    "/mnt/vendor/ctrl/autostart.sh",
    "/mnt/vendor/ctrl/launcher.sh",
    "/mnt/mod/ctrl/autostart",
    "/mnt/mod/ctrl/autostart.sh",
    "/storage/.config/autostart.sh",
)


def _apply_autostart(
    enabled: bool, *, target: str, state_dir: str, app_dir: Path,
) -> None:
    """Enable or disable boot autostart; see :meth:`LinuxPlatform.set_autostart`."""
    state_dir_path = Path(state_dir) if state_dir else Path("/mnt/data/retrostation")
    target_path = _resolve_autostart_target(target)
    if target_path is None:
        log.warning("no firmware autostart hook found; cannot manage boot autostart")
        return
    flag = state_dir_path / "autostart.enabled"
    launch = state_dir_path / "autostart_launch.sh"
    if enabled:
        state_dir_path.mkdir(parents=True, exist_ok=True)
        _write_autostart_launch_script(launch, app_dir)
        _patch_autostart(target_path, state_dir_path)
        flag.touch()
    else:
        try:
            flag.unlink()
        except FileNotFoundError:
            pass


def _resolve_autostart_target(override: str) -> Path | None:
    """Return the firmware autostart script to patch.

    An explicit ``override`` is honoured as-is (created if missing, because the
    firmware only runs it when present).  Without one we probe known hooks and
    return the first that already exists, so we never invent a path the firmware
    would ignore.
    """
    if override:
        return Path(override)
    for candidate in _AUTOSTART_TARGETS:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def _autostart_block(state_dir: Path) -> list[str]:
    return [
        _AUTOSTART_BEGIN,
        f'if [ -f "{state_dir}/autostart.enabled" ]; then',
        f'  exec "{state_dir}/autostart_launch.sh"',
        "fi",
        _AUTOSTART_END,
    ]


def _write_autostart_launch_script(launch: Path, app_dir: Path) -> None:
    """Write the boot helper: wait for the ROM card, then exec us."""
    text = (
        "#!/bin/sh\n"
        "set -u\n"
        'STATE_DIR="$(dirname -- "$0")"\n'
        '[ -f "$STATE_DIR/autostart.enabled" ] || exit 0\n'
        'APP_DIR="' + str(app_dir) + '"\n'
        "WAIT_STEPS=${RETROSTATION_AUTOSTART_WAIT_STEPS:-80}\n"
        "WAIT_INTERVAL=${RETROSTATION_AUTOSTART_WAIT_INTERVAL:-0.25}\n"
        'step=0\n'
        'while [ "$step" -lt "$WAIT_STEPS" ]; do\n'
        '  if [ -x "$APP_DIR/retrostation.sh" ]; then\n'
        '    exec "$APP_DIR/retrostation.sh"\n'
        "  fi\n"
        '  step=$((step + 1))\n'
        '  sleep "$WAIT_INTERVAL"\n'
        "done\n"
        "exit 0\n"
    )
    launch.write_text(text, encoding="utf-8")
    try:
        launch.chmod(0o755)
    except OSError:
        pass


def _patch_autostart(target: Path, state_dir: Path) -> None:
    """Inject (idempotently) the marked block into ``target``.

    Already patched -> no-op.  An existing script gets the block inserted just
    before its last ``exit 0`` (so the stock launcher still runs when autostart
    is disabled); a missing ``target`` is created as our autostart script.
    """
    block = _autostart_block(state_dir)
    if target.exists():
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if _AUTOSTART_BEGIN in lines:
            return
        last_exit: int | None = None
        for i, line in enumerate(lines):
            if line.strip() == "exit 0":
                last_exit = i
        out: list[str] = []
        inserted = False
        for i, line in enumerate(lines):
            if not inserted and last_exit is not None and i == last_exit:
                out.extend(block)
                inserted = True
            out.append(line)
        if not inserted:
            out.extend(block)
        target.write_text("\n".join(out) + "\n", encoding="utf-8")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(block) + "\n", encoding="utf-8")
        try:
            target.chmod(0o755)
        except OSError:
            pass
