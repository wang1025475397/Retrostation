"""Android platform adapter for Retrostation (DESIGN.ANDROID.md).

The Python half runs on the Chaquopy runtime inside the Kotlin host.  Importing
this package on the desktop is harmless: it pulls in only pure-Python modules and
``PilCanvas`` (PIL is available everywhere), never anything Android-specific, so
the test suite can exercise ``display`` / ``input`` / ``storage`` / ``images``
without a device.
"""

from __future__ import annotations

from .display import form_for, is_dual, logical_size
from .images import core_basename, core_filename
from .input import KEYMAP, keymap_action
from .platform import AndroidPlatform

__all__ = [
    "AndroidPlatform",
    "KEYMAP",
    "core_basename",
    "core_filename",
    "form_for",
    "is_dual",
    "keymap_action",
    "logical_size",
]
