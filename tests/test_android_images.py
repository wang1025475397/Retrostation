"""Core names on Android: one identity, two file names (DESIGN.ANDROID §8.1).

``SystemDef`` names a core once (``gambatte_libretro.so``); each platform turns
that into the file it actually has.  The Android launch path was once handed the
Linux name -- a bare name opens nothing there, and the content came up on a black
screen -- so the helpers that translate between the two are pinned here.
"""

from __future__ import annotations

import pytest

from retrostation.platform.android.images import core_basename, core_filename


@pytest.mark.parametrize(("core", "basename"), [
    ("fceumm_libretro.so", "fceumm"),              # Linux / handheld
    ("fceumm_libretro_android.so", "fceumm"),      # Android
    ("fceumm.so", "fceumm"),
    ("fceumm", "fceumm"),                          # already an identity
    ("mgba_libretro_android.so", "mgba"),
    # Only the suffix goes, even when the core's own name mentions it.
    ("mednafen_supergrafx_libretro.so", "mednafen_supergrafx"),
    ("pcsx_rearmed_libretro_android.so", "pcsx_rearmed"),
])
def test_the_identity_is_the_basename(core: str, basename: str) -> None:
    assert core_basename(core) == basename


def test_the_platform_name_is_built_from_the_identity() -> None:
    assert core_filename("fceumm") == "fceumm_libretro.so"
    assert core_filename("fceumm", suffix="_android") == "fceumm_libretro_android.so"


def test_the_two_sides_round_trip() -> None:
    # This is exactly what the Android launch path does: take the table's name
    # apart, then put this platform's name back together.
    for core in ("gambatte_libretro.so", "mgba_libretro.so", "pcsx_rearmed_libretro.so"):
        identity = core_basename(core)
        android = core_filename(identity, suffix="_android")
        assert android == f"{core[:-len('.so')]}_android.so"
        assert core_basename(android) == identity
