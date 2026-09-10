"""What "start this game" means, independently of how the OS does it.

The Linux handhelds run a command line; Android starts an activity, or hosts a
libretro core inside this very app.  Those are three different things, so
:func:`~retrostation.launcher.launch.build_plan` produces a *target* and the
platform decides what to do with it -- rather than everything above assuming a
command line exists (see ``docs/DESIGN.ANDROID.md`` §5.3 / §8.1).

This lives next to :mod:`~retrostation.platform.base` rather than in
``launcher/`` because it is part of the platform's contract
(:meth:`~retrostation.platform.base.Platform.launch_game` takes one), the same
way :class:`~retrostation.platform.base.InputEvent` is: ``launcher/`` produces
them and the platform consumes them, so the vocabulary belongs to the side that
cannot depend upwards.

A platform only ever accepts the target kinds it understands and raises
:class:`UnsupportedTarget` for the rest, which fails loudly instead of silently
doing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


class UnsupportedTarget(RuntimeError):
    """Raised when a platform is handed a target kind it cannot run."""

    def __init__(self, platform_name: str, target: object) -> None:
        super().__init__(f"{platform_name} cannot run {type(target).__name__}")
        self.target = target


@dataclass(frozen=True)
class ArgvTarget:
    """A command line to execute -- the Linux handhelds' only kind.

    ``argv`` is a list, never a shell string: a ROM whose name contains a space
    or CJK characters must survive without quoting rules (DESIGN §14).
    """

    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("ArgvTarget needs at least a program name")


@dataclass(frozen=True)
class IntentTarget:
    """An Android activity to start (external emulator).

    No platform consumes it yet -- the Android adapter will
    (docs/DESIGN.ANDROID.md §8.3 / §8.4).  It is defined here because the
    decision "run this game in another app" has to be expressible before the
    platform that carries it out exists.
    """

    package: str
    activity: str = ""
    action: str = "android.intent.action.MAIN"
    #: ``ACTION_VIEW`` payload, when the emulator takes the ROM as intent data.
    data_uri: str = ""
    mime: str = ""
    #: Ordered so logs and tests read the same way every run.
    extras: tuple[tuple[str, str], ...] = ()
    #: Tried in order when this one does not come up.  RetroArch has shipped
    #: builds that need the core's full path rather than its file name, so the
    #: same launch has to be expressible twice (DESIGN.ANDROID §8.3).
    fallbacks: tuple[IntentTarget, ...] = ()


@dataclass(frozen=True)
class InlineTarget:
    """A libretro core to host inside this app (Android, B series).

    The core is named the same way the external paths name it -- ``SystemDef``
    stays the single definition of "which core plays this system", and only the
    execution position differs (DESIGN.ANDROID §8.1).
    """

    #: Core identity without suffixes: ``fceumm``.
    core: str
    rom: str
    system: str
    #: Ask the platform to split the core's framebuffer across two screens
    #: (NDS/3DS on a dual-screen handheld).  A request, not a demand: a platform
    #: with one screen, or a single-screen core, ignores it.
    dual_screen: bool = False
    #: libretro core variables to apply before the first frame.
    options: tuple[tuple[str, str], ...] = field(default_factory=tuple)


#: Anything :meth:`~retrostation.platform.base.Platform.launch_game` may be given.
LaunchTarget = Union[ArgvTarget, IntentTarget, InlineTarget]
