"""Core-name normalisation shared by every launch path (DESIGN.ANDROID §8.1).

The *identity* of a libretro core is its basename (``fceumm``).  Linux names the
shared object ``fceumm_libretro.so``; Android names it
``fceumm_libretro_android.so``.  Keeping that suffix in one place is what lets
:class:`~retrostation.data.systems.SystemDef` stay a single definition while the
two platforms point at different files on disk.

Pure string helpers -- exercised directly by the test suite.
"""

from __future__ import annotations


def core_basename(core: str) -> str:
    """The stable identity of a core, stripped of its platform-specific suffix.

    >>> core_basename("fceumm_libretro.so")
    'fceumm'
    >>> core_basename("fceumm_libretro_android.so")
    'fceumm'
    >>> core_basename("fceumm")
    'fceumm'
    """
    stem = core
    for suffix in ("_libretro_android.so", "_libretro.so", ".so"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def core_filename(basename: str, *, suffix: str = "") -> str:
    """A platform-specific ``.so`` name from a core basename.

    >>> core_filename("fceumm")
    'fceumm_libretro.so'
    >>> core_filename("fceumm", suffix="_android")
    'fceumm_libretro_android.so'
    """
    return f"{basename}_libretro{suffix}.so"
