"""Re-export ``FontBook`` from ``retrostation.platform.fonts``.

The shared font book now lives at the platform top level so the Android and
desktop adapters can reuse it without importing the Linux package.  This module
keeps the Linux package's existing ``from .fonts import FontBook`` imports working.
"""

from __future__ import annotations

from ..fonts import FontBook

__all__ = ["FontBook"]
