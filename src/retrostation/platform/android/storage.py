"""ROM root discovery for Android (DESIGN.ANDROID §7.2).

Pure path logic only -- no filesystem access here, so it is unit-tested on the
desktop.  The actual "does this exist / is it readable" check is a one-liner the
Kotlin ``StorageBridge`` performs against real device paths; this module owns the
*candidate order*, which is the part that must stay consistent between the handheld
and the phone.
"""

from __future__ import annotations

#: Tried in order.  ``config.rom_root`` (when the user has pinned one) is checked
#: first by the caller; these are the auto-discovery fallbacks.  Internal storage
#: first, then the SD card by its mount-point UUID pattern.
ROM_ROOT_CANDIDATES: tuple[str, ...] = (
    "/storage/emulated/0/Roms",
    "/storage/emulated/0/Games",
    "/storage/emulated/0/ROMs",
    "/storage/emulated/0/RetroArch/roms",
    # External SD card: Android exposes it as /storage/<UUID>/; the UUID is
    # device-specific, so the bridge supplies the concrete path at runtime and
    # prepends the same Roms/Games/ROMs names.
)


def candidate_roots(sd_uuid: str | None = None) -> list[str]:
    """Concrete candidate paths, with the SD card slot filled in when known.

    >>> candidate_roots()
    ['/storage/emulated/0/Roms', '/storage/emulated/0/Games', ...]
    >>> candidate_roots("1A2B-3C4D")[:5]
    ['/storage/emulated/0/Roms', ..., '/storage/1A2B-3C4D/Roms']
    """
    roots = list(ROM_ROOT_CANDIDATES)
    if sd_uuid:
        for name in ("Roms", "Games", "ROMs", "RetroArch/roms"):
            roots.append(f"/storage/{sd_uuid}/{name}")
    return roots
