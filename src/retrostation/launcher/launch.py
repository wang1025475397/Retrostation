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
from ..data.systems import SystemDef, canonical_key, lookup
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
#:
#: RetroArch ships under more than one package id: the plain build, the 64-bit
#: "Plus" build (what the Play Store hands most phones) and a 32-bit Plus.  Which
#: one is installed is the device's business, so all of them are tried -- a
#: hardcoded ``com.retroarch`` missed the Plus build a real device had, and the
#: app told the player to install RetroArch they were already running.
RETROARCH_PACKAGES: tuple[str, ...] = (
    "com.retroarch.aarch64",
    "com.retroarch",
    "com.retroarch.ra32",
)
#: Same class in every build -- it is one codebase, only the id differs.
RETROARCH_ACTIVITY = "com.retroarch.browser.retroactivity.RetroActivityFuture"


@dataclass(frozen=True)
class AndroidApp:
    """A standalone emulator app on Android, and what to call it."""

    package: str
    #: Activity to start.  Empty means "let the package pick its launch activity".
    activity: str = ""
    #: Shown to the player when the app is not installed.
    name: str = ""


#: Systems the handheld runs through a standalone script (``SystemDef.standalone``)
#: map to the Android app that stands in for it.  Consulted *before* the RetroArch
#: path: these systems' default emulator is their own app, and pushing them through
#: a core just because RetroArch happens to be installed is not what the player
#: asked for -- nor what the firmware does.
_ANDROID_APPS: dict[str, AndroidApp] = {
    # DraStic wants the ROM as the *system storage provider's* ``content://`` URI
    # -- the shape a file manager passes on "open with".  Handed a raw ``file://``
    # path or another app's FileProvider URI it answers on its own main menu
    # ("Unable to open game from ..."), which is what a sibling frontend found too
    # (its ``keep_saf_uri`` opt-out, issue #50/#66).  See ``document_uri`` on
    # :class:`IntentTarget` for the Android half of this (§8.3).
    "nds": AndroidApp(
        package="com.dsemu.drastic",
        activity="com.dsemu.drastic.DraSticActivity",
        name="DraStic",
    ),
}




def build_android_plan(game: Game, config: Config) -> LaunchPlan:
    """The Android equivalent of :func:`build_plan`: an activity to start.

    Only the external-emulator path is expressible here; hosting a core inside
    the app is the B series (``InlineTarget``).
    """
    from ..platform.android.images import core_basename, core_filename
    from ..platform.targets import IntentTarget

    definition = lookup(_system_of(game))
    rom = str(game.path)

    # A system that runs under an emulator app of its own goes straight there:
    # that is the Android counterpart of the handheld's standalone script, and it
    # is the default the player expects (§8.3).
    # Keyed by the table's own spelling: ``definition.key`` carries the directory's
    # case ("NDS"), and a variant directory carries its whole name.
    app = _ANDROID_APPS.get(canonical_key(_system_of(game)))
    if app is not None:
        return LaunchPlan(
            target=IntentTarget(
                package=app.package,
                activity=app.activity,
                # The shape the emulator's own frontend integrations send, and
                # every part of it is load-bearing (§8.3):
                #  * the ROM as the intent's *data*;
                #  * the default action, not ``VIEW``;
                #  * no MIME type: its filter matches the scheme alone, and a type
                #    made the intent unresolvable on a real device;
                #  * a cleared task: it keeps a session of its own and otherwise
                #    answers on the ROM it already had.
                #
                # Measured on a device where DraStic plays these very ROMs from its
                # own browser: a ``file://`` path and a foreign FileProvider URI
                # both still leave it on its main menu ("Unable to open game from
                # ...").  What it takes is the *system storage provider's*
                # ``content://`` URI -- the shape a file manager passes on "open
                # with" -- and Android only lets an app grant a URI it holds itself
                # (``SecurityException``: "you could obtain access using
                # ACTION_OPEN_DOCUMENT"), so that needs the ROM folder to arrive
                # through SAF.  ``IntentTarget.document_uri`` and the host half of
                # it are in place for the day it does.
                data_uri=f"file://{rom}",
                document_uri=True,
                # ``new_task`` gives the emulator a task of its own.  Started
                # without it, its activity joins *our* task stack: the recents
                # view then shows one card for both apps, and swiping that card
                # away kills the frontend along with the game.
                flags=("new_task", "clear_task", "clear_top"),
            ),
            core_label=app.name,
        )

    if not definition.core:
        raise LaunchError(f"{definition.key} has no emulator on Android")

    # Android cores carry an extra suffix (fceumm_libretro_android.so) -- §8.1.
    # A user override wins here exactly as it does on Linux, so a system whose
    # stock core is not installed can be pointed at the one that is (the handheld
    # ships ``pcsx_rearmed``; a phone often has SwanStation instead).
    stock = config.core_overrides.get(canonical_key(_system_of(game))) or definition.core
    core = core_filename(core_basename(stock), suffix="_android")

    # ``LIBRETRO`` is the core's *path*: RetroArch opens exactly the file the extra
    # names, and a bare file name opens nothing -- content comes up loaded but
    # renders on a black screen, which is what the Linux shape (``retroarch -L
    # <name>``) produced here.  The path mirrors RetroArch's own Android default
    # ``libretro_directory``, ``/data/user/0/<pkg>/cores/``.
    targets = [
        IntentTarget(
            package=package,
            activity=RETROARCH_ACTIVITY,
            extras=(("ROM", rom), ("LIBRETRO", f"/data/user/0/{package}/cores/{core}")),
            # Same reason as the standalone path: RetroArch must own its task, or
            # it shares ours and one swipe in the recents view takes the frontend
            # down with the game.
            flags=("new_task",),
        )
        for package in RETROARCH_PACKAGES
    ]
    # Only ever the named activity: it is the one that takes ROM/LIBRETRO.  A
    # bare package still *starts* RetroArch -- on its menu, with the game ignored
    # -- so trailing it here would turn "nothing loaded the game" into a silent
    # success (§8.3).
    primary, *rest = targets
    return LaunchPlan(
        target=IntentTarget(
            package=primary.package,
            activity=primary.activity,
            extras=primary.extras,
            # Carried over explicitly: dropping them here left the first attempt
            # without the task flag while the fallbacks kept theirs.
            flags=primary.flags,
            fallbacks=tuple(rest),
        ),
        core_label=definition.core_label,
    )


def android_missing_app(plan: LaunchPlan) -> str:
    """What to install when an Android launch did not come up; ``""`` for RetroArch.

    The shared RetroArch path already has a message that names RetroArch; a system
    with an emulator app of its own names that app instead, which is the difference
    between "install RetroArch" on a phone that never needed it and "install
    DraStic" on one that does.
    """
    if getattr(plan.target, "package", "") in RETROARCH_PACKAGES:
        return ""
    return plan.core_label


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
