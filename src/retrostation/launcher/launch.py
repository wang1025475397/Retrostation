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
from ..platform.targets import ArgvTarget, LaunchTarget


class LaunchError(RuntimeError):
    """Raised when no launcher can be assembled for a game."""


@dataclass(frozen=True)
class LaunchPlan:
    """Everything needed to start a game, already resolved.

    The *how* lives in :attr:`target` (see :mod:`.target`): a command line here,
    an activity or a hosted libretro core on Android.
    """

    target: LaunchTarget
    core_label: str

    @property
    def argv(self) -> tuple[str, ...]:
        """The command line, for callers that run one.

        Raises :class:`TypeError` for the other target kinds rather than
        returning something empty: a caller that assumes a command line is
        making an assumption that has to fail where it is wrong.
        """
        if not isinstance(self.target, ArgvTarget):
            raise TypeError(f"{type(self.target).__name__} has no command line")
        return self.target.argv


def build_plan(game: Game, config: Config) -> LaunchPlan:
    """Assemble the command for ``game``; raises :class:`LaunchError` if none."""
    definition = lookup(_system_of(game))
    rom = str(game.path)

    if definition.standalone:
        return LaunchPlan(target=ArgvTarget(_expand(definition.standalone, rom)),
                          core_label=definition.core_label)
    if definition.key.upper() == "PORTS":
        return LaunchPlan(target=ArgvTarget(("bash", rom)), core_label="PortMaster")
    return _retroarch_plan(definition, rom, config)


#: RetroArch for Android: the ROM and core travel as intent extras (§8.3).
RETROARCH_PACKAGE = "com.retroarch"
RETROARCH_ACTIVITY = "com.retroarch.browser.retroactivity.RetroActivityFuture"


def build_android_plan(game: Game, config: Config) -> LaunchPlan:
    """The Android equivalent of :func:`build_plan`: an activity to start.

    Only the external-emulator path is expressible here; hosting a core inside
    the app is the B series (``InlineTarget``).
    """
    from ..platform.android.images import core_basename, core_filename
    from ..platform.targets import IntentTarget

    definition = lookup(_system_of(game))
    if not definition.core:
        raise LaunchError(f"{definition.key} has no emulator on Android")
    rom = str(game.path)
    # Android cores carry an extra suffix (fceumm_libretro_android.so) -- §8.1.
    core = core_filename(core_basename(definition.core), suffix="_android")
    extras = (("ROM", rom), ("LIBRETRO", core))
    return LaunchPlan(
        target=IntentTarget(
            package=RETROARCH_PACKAGE,
            activity=RETROARCH_ACTIVITY,
            extras=extras,
            # Older RetroArch builds want the bare activity name; try both
            # before telling the player nothing can run the game (§8.3).
            fallbacks=(IntentTarget(package=RETROARCH_PACKAGE, extras=extras),),
        ),
        core_label=definition.core_label,
    )


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
        return LaunchPlan(target=ArgvTarget((str(script), core, rom)), core_label=core)

    binary = Path(config.launcher.fallback_ra)
    cores_dir = Path(config.launcher.fallback_cores_dir)
    if not binary.is_file():
        raise LaunchError(f"neither {script} nor {binary} exists")
    return LaunchPlan(
        target=ArgvTarget((str(binary), "-c", _ra_config(config.launcher.ra_config),
                           "-L", str(cores_dir / core), rom)),
        core_label=core,
    )


def _ra_config(explicit: str = "") -> str:
    """The stock frontend keeps its config next to the binary."""
    if explicit and Path(explicit).is_file():
        return explicit
    for candidate in ("/.config/retroarch/retroarch.cfg", "/oem/retro/retroarch.cfg"):
        if Path(candidate).is_file():
            return candidate
    return explicit or "/.config/retroarch/retroarch.cfg"


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
