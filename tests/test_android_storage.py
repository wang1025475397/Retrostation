"""Where the phone looks for ROMs (DESIGN.ANDROID §7.2).

The *order* is the part that has to stay right: internal storage first, then the
card by its mount UUID, and the card searched under the same folder names.  The
existence check lives in the Kotlin bridge, so this is plain list logic.
"""

from __future__ import annotations

from retrostation.platform.android.storage import ROM_ROOT_CANDIDATES, candidate_roots


def test_internal_storage_is_tried_first() -> None:
    roots = candidate_roots()
    assert roots == list(ROM_ROOT_CANDIDATES)
    assert roots[0] == "/storage/emulated/0/Roms"


def test_the_card_is_only_offered_when_its_mount_is_known() -> None:
    # No UUID (nothing mounted, or the bridge could not read one) means no card
    # candidates at all -- guessing a mount point would just add stat() misses.
    assert candidate_roots(None) == list(ROM_ROOT_CANDIDATES)
    assert candidate_roots("") == list(ROM_ROOT_CANDIDATES)


def test_the_card_is_searched_under_the_same_folder_names() -> None:
    roots = candidate_roots("1A2B-3C4D")
    card = [root for root in roots if root.startswith("/storage/1A2B-3C4D/")]
    assert card == [
        "/storage/1A2B-3C4D/Roms",
        "/storage/1A2B-3C4D/Games",
        "/storage/1A2B-3C4D/ROMs",
        "/storage/1A2B-3C4D/RetroArch/roms",
    ]


def test_the_card_never_comes_before_internal_storage() -> None:
    roots = candidate_roots("1A2B-3C4D")
    assert roots.index("/storage/1A2B-3C4D/Roms") > roots.index("/storage/emulated/0/Roms")
