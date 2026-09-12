"""Platform adapters -- the only place allowed to know about SDL/PIL/evdev."""

from __future__ import annotations

from .base import Bitmap, Canvas, FileEntry, InputAction, InputEvent, InputKind, Platform, Rect
from .targets import (
    ArgvTarget,
    InlineTarget,
    IntentTarget,
    LaunchTarget,
    UnsupportedTarget,
)

__all__ = [
    "ArgvTarget",
    "Bitmap",
    "Canvas",
    "FileEntry",
    "InlineTarget",
    "InputAction",
    "InputEvent",
    "InputKind",
    "IntentTarget",
    "LaunchTarget",
    "Platform",
    "Rect",
    "UnsupportedTarget",
]
