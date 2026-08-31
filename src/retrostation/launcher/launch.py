"""Game launch command assembly.

One place knows how a ROM becomes a command line, so the UI stays free of
firmware paths.  Standalone emulators use their own launchers (verified by
reading the firmware scripts, DESIGN §2.4); everything else goes through the
RetroArch bootstrap, falling back to the stock binary when the mod script is
missing.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..core.config import Config
from ..core.model import Game
from ..data.systems import SystemDef, lookup


class LaunchError(RuntimeError):
    """Raised when no launcher can be assembled for a game."""


@dataclass(frozen=True)
class LaunchPlan:
    """Everything needed to start a game, already resolved."""

    argv: tuple[str, ...]
    core_label: str


def build_plan(game: Game, config: Config) -> LaunchPlan:
    """Assemble the command for ``game``; raises :class:`LaunchError` if none."""
    definition = lookup(_system_of(game))
    rom = str(game.path)

    if definition.standalone:
        return LaunchPlan(argv=_expand(definition.standalone, rom), core_label=definition.core_label)
    if definition.key.upper() == "PORTS":
        return LaunchPlan(argv=("bash", rom), core_label="PortMaster")
    return _retroarch_plan(definition, rom, config)


def _system_of(game: Game) -> str:
    return game.key.split("/", 1)[0]


def _expand(template: str, rom: str) -> tuple[str, ...]:
    """Turn ``/path/launch.sh HLE {rom}`` into an argv tuple (quote-safe)."""
    expanded = template.replace("{rom}", shlex.quote(rom))
    return tuple(shlex.split(expanded))


def _retroarch_plan(definition: SystemDef, rom: str, config: Config) -> LaunchPlan:
    core = config.core_overrides.get(definition.key) or definition.core
    if not core:
        raise LaunchError(f"no core configured for system {definition.key!r}")

    script = Path(config.launcher.ra_script)
    if script.is_file():
        return LaunchPlan(argv=(str(script), core, rom), core_label=core)

    binary = Path(config.launcher.fallback_ra)
    cores_dir = Path(config.launcher.fallback_cores_dir)
    if not binary.is_file():
        raise LaunchError(f"neither {script} nor {binary} exists")
    return LaunchPlan(
        argv=(str(binary), "-c", _ra_config(), "-L", str(cores_dir / core), rom),
        core_label=core,
    )


def _ra_config() -> str:
    """The stock frontend keeps its config next to the binary."""
    for candidate in ("/.config/retroarch/retroarch.cfg", "/oem/retro/retroarch.cfg"):
        if Path(candidate).is_file():
            return candidate
    return "/.config/retroarch/retroarch.cfg"


# --------------------------------------------------------------------------- #
# Handing the command to the shell bootstrap (DESIGN §8.2)
# --------------------------------------------------------------------------- #

#: Where the frontend drops the pending launch command.  ``retrostation.sh``
#: sources this file and runs it once we have exited, which is what keeps the
#: frontend's exit-code contract meaningful.
LAUNCH_CMD_PATH = Path("/tmp/retrostation_launch.cmd")


def write_launch_cmd(argv: Sequence[str], path: Path | str = LAUNCH_CMD_PATH) -> Path:
    """Record ``argv`` for the bootstrap to run after we exit.

    The file is *sourced* by a POSIX shell, so it has to be valid shell.
    ``set --`` makes the arguments the script's positional parameters, which it
    then runs as ``"$@"`` -- every argument stays quoted, so a ROM whose name
    contains a space or CJK characters survives the round trip (DESIGN §14).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "set -- " + " ".join(shlex.quote(str(arg)) for arg in argv) + "\n"

    # Atomic: the bootstrap reads this the moment we exit, so it must never see
    # a half-written command.
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".launch-", suffix=".cmd")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return target


def clear_launch_cmd(path: Path | str = LAUNCH_CMD_PATH) -> None:
    """Drop a consumed (or stale) command file."""
    Path(path).unlink(missing_ok=True)
