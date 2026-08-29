"""Game launch command assembly.

One place knows how a ROM becomes a command line, so the UI stays free of
firmware paths.  Standalone emulators use their own launchers (verified by
reading the firmware scripts, DESIGN §2.4); everything else goes through the
RetroArch bootstrap, falling back to the stock binary when the mod script is
missing.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

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
