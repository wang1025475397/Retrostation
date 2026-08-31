"""Platform table.

This is *data*, not logic -- kept plain and complete so that adding a system is
a one-line edit.  Cores were cross-checked against the real core list on the
device (``/oem/retro/cores``) and the standalone launchers were verified by
reading the firmware scripts (see DESIGN §2.4).

Anything not in the table still works: :func:`lookup` returns a generic
definition, so an unknown ROM directory shows up in the UI instead of
disappearing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache


@dataclass(frozen=True)
class SystemDef:
    """One ROM directory.

    ``core`` is the RetroArch core file name (without path), or empty for
    standalone emulators / script-based ports.
    """

    key: str
    label: str
    label_zh: str
    extensions: tuple[str, ...] = ()
    core: str = ""
    alt_cores: tuple[str, ...] = ()
    #: Standalone launcher: ``{rom}`` is substituted with the quoted ROM path.
    standalone: str = ""
    #: Sorting on the home carousel.
    order: int = 100
    #: Hide from the home page (helper directories such as ``APPS``).
    hidden: bool = False

    @property
    def is_standalone(self) -> bool:
        return bool(self.standalone) or self.key == "PORTS"

    @property
    def core_label(self) -> str:
        if self.standalone:
            return self.standalone.split()[0].rsplit("/", 1)[-1]
        return self.core or "—"


# --------------------------------------------------------------------------- #
# Table
# --------------------------------------------------------------------------- #

_SYSTEMS: tuple[SystemDef, ...] = (
    # -- Nintendo -------------------------------------------------------- #
    SystemDef("fc", "NES / Famicom", "红白机", ("nes", "fds", "unf", "zip"),
              core="fceumm_libretro.so", alt_cores=("nestopia_libretro.so", "quicknes_libretro.so"), order=10),
    SystemDef("fds", "Famicom Disk System", "FC 磁碟机", ("fds", "zip"),
              core="nestopia_libretro.so", order=90),
    SystemDef("sfc", "Super Famicom", "超级任天堂", ("sfc", "smc", "fig", "swc", "zip"),
              core="snes9x2005_plus_libretro.so",
              alt_cores=("snes9x2010_libretro.so", "snes9x_libretro.so"), order=11),
    SystemDef("snes", "SNES", "超级任天堂", ("sfc", "smc", "zip"),
              core="snes9x2005_plus_libretro.so", hidden=True),
    SystemDef("gb", "Game Boy", "Game Boy", ("gb", "zip"),
              core="gambatte_libretro.so", alt_cores=("sameboy_libretro.so", "gearboy_libretro.so"), order=14),
    SystemDef("gbc", "Game Boy Color", "GB Color", ("gbc", "gb", "zip"),
              core="gambatte_libretro.so", alt_cores=("sameboy_libretro.so",), order=13),
    SystemDef("gba", "Game Boy Advance", "GBA", ("gba", "zip"),
              core="mgba_libretro.so", alt_cores=("vba_next_libretro.so",), order=12),
    SystemDef("nds", "Nintendo DS", "任天堂 DS", ("nds", "zip"),
              standalone="/mnt/vendor/ctrl/setNDS64.sh run {rom}", order=20),
    SystemDef("n64", "Nintendo 64", "任天堂 64", ("n64", "z64", "v64", "ndd", "bin", "zip"),
              core="mupen64plus_next_libretro.so", alt_cores=("parallel_n64_libretro.so",), order=30),
    SystemDef("vb", "Virtual Boy", "Virtual Boy", ("vb", "zip"),
              core="mednafen_vb_libretro.so", order=96),

    # -- Sega ------------------------------------------------------------ #
    SystemDef("md", "Mega Drive", "世嘉五代", ("md", "gen", "bin", "smd", "68k", "zip"),
              core="picodrive_libretro.so", alt_cores=("genesis_plus_gx_libretro.so",), order=15),
    SystemDef("segaMD", "Mega Drive", "世嘉五代", ("md", "gen", "bin", "smd", "zip"),
              core="picodrive_libretro.so", hidden=True),
    SystemDef("segaMS", "Master System", "世嘉 MS", ("sms", "zip"),
              core="smsplus_libretro.so", alt_cores=("gearsystem_libretro.so",), order=88),
    SystemDef("sms", "Master System", "世嘉 MS", ("sms", "zip"),
              core="smsplus_libretro.so", alt_cores=("gearsystem_libretro.so",), order=88),
    SystemDef("gg", "Game Gear", "Game Gear", ("gg", "zip"),
              core="gearsystem_libretro.so", alt_cores=("smsplus_libretro.so",), order=89),
    SystemDef("sg-1000", "SG-1000", "SG-1000", ("sg", "zip"),
              core="gearsystem_libretro.so", order=97),
    SystemDef("segaCD", "Sega CD", "世嘉 CD", ("cue", "chd", "iso"),
              core="genesis_plus_gx_libretro.so", order=85),
    SystemDef("sega32x", "32X", "世嘉 32X", ("32x", "bin", "zip"),
              core="picodrive_libretro.so", order=87),
    SystemDef("saturn", "Sega Saturn", "世嘉土星", ("cue", "ccd", "m3u", "chd", "iso"),
              standalone="/mnt/vendor/deep/saturn/launch.sh HLE {rom}", order=40),
    SystemDef("Saturn", "Sega Saturn", "世嘉土星", ("cue", "ccd", "m3u", "chd", "iso"),
              standalone="/mnt/vendor/deep/saturn/launch.sh HLE {rom}", hidden=True),
    SystemDef("dc", "Dreamcast", "世嘉 DC", ("cue", "cdi", "chd", "gdi", "m3u"),
              core="flycast_libretro.so", standalone="/mnt/vendor/deep/flycast/launch.sh {rom}", order=41),
    SystemDef("dreamcast", "Dreamcast", "世嘉 DC", ("cue", "cdi", "chd", "gdi"),
              core="flycast_libretro.so", hidden=True),
    SystemDef("atomiswave", "Atomiswave", "Atomiswave", ("chd", "bin", "zip"),
              core="flycast_libretro.so", order=84),

    # -- Sony ------------------------------------------------------------ #
    SystemDef("ps", "PlayStation", "PlayStation", ("bin", "cue", "pbp", "chd", "iso", "img", "m3u", "ccd"),
              core="pcsx_rearmed_libretro.so", order=21),
    SystemDef("ps1", "PlayStation", "PlayStation", ("bin", "cue", "pbp", "chd", "iso"),
              core="pcsx_rearmed_libretro.so", hidden=True),
    SystemDef("psp", "PSP", "PSP", ("iso", "cso", "pbp"),
              standalone="/mnt/vendor/deep/ppsspp/run_gles.sh {rom}", order=22),

    # -- NEC / Bandai / other handhelds ---------------------------------- #
    SystemDef("pce", "PC Engine", "PC Engine", ("pce", "zip"),
              core="mednafen_pce_fast_libretro.so", order=80),
    SystemDef("pcecd", "PC Engine CD", "PCE CD", ("cue", "ccd", "chd", "pce"),
              core="mednafen_pce_libretro.so", order=81),
    SystemDef("ws", "WonderSwan", "WonderSwan", ("ws", "wsc", "zip"),
              core="mednafen_wswan_libretro.so", order=92),
    SystemDef("wsc", "WonderSwan Color", "WonderSwan Color", ("wsc", "ws", "zip"),
              core="mednafen_wswan_libretro.so", hidden=True),
    SystemDef("wonderswan", "WonderSwan", "WonderSwan", ("ws", "wsc", "zip"),
              core="mednafen_wswan_libretro.so", hidden=True),
    SystemDef("ngp", "Neo Geo Pocket", "NGP", ("ngp", "ngc", "zip"),
              core="mednafen_ngp_libretro.so", order=93),
    SystemDef("lynx", "Atari Lynx", "Lynx", ("lnx", "zip"),
              core="handy_libretro.so", order=95),

    # -- Arcade ---------------------------------------------------------- #
    SystemDef("cps1", "Capcom CPS-1", "CPS1", ("zip", "7z"),
              core="fbalpha2012_cps1_libretro.so", alt_cores=("fbneo_libretro.so",), order=50),
    SystemDef("cps2", "Capcom CPS-2", "CPS2", ("zip", "7z"),
              core="fbalpha2012_cps2_libretro.so", alt_cores=("fbneo_libretro.so",), order=51),
    SystemDef("cps3", "Capcom CPS-3", "CPS3", ("zip", "7z"),
              core="fbalpha2012_cps3_libretro.so", alt_cores=("fbneo_libretro.so",), order=52),
    SystemDef("neogeo", "Neo Geo", "Neo Geo", ("zip", "7z"),
              core="fbalpha2012_neogeo_libretro.so", alt_cores=("fbneo_libretro.so",), order=53),
    SystemDef("fbneo", "FinalBurn Neo", "FB Neo", ("zip", "7z"),
              core="fbneo_libretro.so", order=54),
    SystemDef("arc", "Arcade", "街机", ("zip", "7z"),
              core="fbneo_libretro.so", hidden=True),
    SystemDef("mame", "MAME 2003-Plus", "MAME", ("zip", "7z"),
              core="mame2003_plus_libretro.so", order=55),
    SystemDef("hbmame", "HBMAME", "HBMAME", ("zip",),
              core="mame2003_plus_libretro.so", order=94),
    SystemDef("ssv", "Sammy SSV", "SSV", ("zip",),
              core="fbneo_libretro.so", order=98),

    # -- Computers & misc ------------------------------------------------ #
    SystemDef("msx", "MSX", "MSX", ("rom", "mx1", "mx2", "dsk", "cas", "zip"),
              core="bluemsx_libretro.so", order=86),
    SystemDef("scummvm", "ScummVM", "ScummVM", ("scummvm",),
              core="scummvm_libretro.so", order=60),
    SystemDef("easyrpg", "EasyRPG", "EasyRPG", ("easyrpg", "zip"),
              core="easyrpg_libretro.so", order=91),
    SystemDef("pico", "PICO-8", "PICO-8", ("p8", "png"),
              standalone="/mnt/vendor/deep/pico-8/launch.sh {rom}", order=61),
    SystemDef("a5200", "Atari 5200", "Atari 5200", ("a52", "bin", "zip"),
              core="a5200_libretro.so", order=97),
    SystemDef("atari2600", "Atari 2600", "雅达利 2600", ("a26", "bin", "zip", "rom"),
              core="stella2014_libretro.so", order=82),
    SystemDef("zx81", "ZX-81", "ZX-81", ("tzx", "p", "zip"),
              core="81_libretro.so", order=99),
    SystemDef("sufami", "Sufami Turbo", "Sufami", ("smc", "zip"),
              core="snes9x_libretro.so", order=99),
    SystemDef("sunplus", "Sunplus", "Sunplus", ("bin", "zip"),
              core="mame2003_plus_libretro.so", order=99),
    SystemDef("megaduck", "Mega Duck", "Mega Duck", ("bin", "gb", "zip"),
              core="sameduck_libretro.so", order=99),

    # -- Non-game directories ------------------------------------------- #
    SystemDef("ports", "Ports", "移植游戏", ("sh",), order=70),
    SystemDef("APPS", "Apps", "应用", ("sh", "py"), hidden=True, order=200),
)


#: Aggregated views shown on the home carousel before the real systems.
AGGREGATES: tuple[tuple[str, str, str], ...] = (
    ("ALL", "All Games", "全部游戏"),
    ("FAV", "Favorites", "收藏"),
    ("RECENT", "Recently Played", "最近游玩"),
)

#: Aggregate keys are not directories; never try to scan them for ROMs.
AGGREGATE_KEYS = frozenset(key for key, _, _ in AGGREGATES)


def _build() -> dict[str, SystemDef]:
    table: dict[str, SystemDef] = {}
    for definition in _SYSTEMS:
        table.setdefault(definition.key, definition)
    # Firmware directories are UPPER CASE (FC, SFC, NDS...) while the table is
    # lower case, so lookups must be case-insensitive.
    folded = {key.casefold(): definition for key, definition in table.items()}
    folded.update(table)
    return folded


SYSTEMS: dict[str, SystemDef] = _build()

#: Generic definition for directories we have never heard of.
_UNKNOWN = SystemDef(key="", label="Unknown", label_zh="未知", extensions=())


@lru_cache(maxsize=256)
def lookup(key: str) -> SystemDef:
    """Definition for ``key``; unknown keys still get a usable default.

    Cached because it is on the frame path: the home page asks for every
    system's label and order on every frame, and firmware directories are
    UPPER CASE while the table is lower case -- without the cache every one of
    those ~250 calls allocated a fresh ``SystemDef`` through ``replace``.
    """
    definition = SYSTEMS.get(key) or SYSTEMS.get(key.casefold())
    if definition is not None and definition.key:
        if key != definition.key:  # directory uses a different case than the table
            return replace(definition, key=key)
        return definition
    return SystemDef(
        key=key,
        label=key.upper(),
        label_zh=key.upper(),
        extensions=_UNKNOWN.extensions,
        core=_UNKNOWN.core,
    )


def known_keys() -> list[str]:
    """Directory names we recognise, hidden ones included."""
    return list(SYSTEMS)


def extensions_for(key: str) -> frozenset[str]:
    """Lower-cased extensions (without dot) a system accepts."""
    return frozenset(ext.lower().lstrip(".") for ext in lookup(key).extensions)


def display_name(key: str, lang: str = "zh") -> str:
    """Human label for a system key."""
    if key == "ALL":
        return "全部游戏" if lang.startswith("zh") else "All Games"
    if key == "FAV":
        return "收藏" if lang.startswith("zh") else "Favorites"
    if key == "RECENT":
        return "最近游玩" if lang.startswith("zh") else "Recently Played"
    definition = lookup(key)
    return definition.label_zh if lang.startswith("zh") else definition.label
