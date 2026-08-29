"""Platform adapters -- the only place allowed to know about SDL/PIL/evdev."""

from __future__ import annotations

from .base import Bitmap, Canvas, FileEntry, InputAction, InputEvent, InputKind, Platform, Rect

__all__ = [
    "Bitmap",
    "Canvas",
    "FileEntry",
    "InputAction",
    "InputEvent",
    "InputKind",
    "Platform",
    "Rect",
]
