"""Unit tests for the device-independent Android logic (DESIGN.ANDROID §4.1 / §5.4)."""

from __future__ import annotations

from retrostation.core.theme import Form
from retrostation.platform.android.display import form_for, is_dual, logical_size


def test_logical_size_phone_portrait() -> None:
    # 1080x2400 -> a ~1.3x upscale: (820, 1824) at the 1.5 MP budget.
    assert logical_size(1080, 2400) == (820, 1824)


def test_logical_size_thor_top() -> None:
    # 1920x1080 16:9 -> (1632, 920).
    assert logical_size(1920, 1080) == (1632, 920)


def test_logical_size_clamps_and_aligns() -> None:
    w, h = logical_size(4000, 8000)
    assert w % 4 == 0 and h % 4 == 0
    # The ceiling is above the old 1280 so a tall panel keeps a ~1x upscale.
    assert 640 <= w <= 2400 and 640 <= h <= 2400
    # An extreme ratio clamps instead of asking for a degenerate canvas.
    assert logical_size(100, 10000) == (640, 2400)


def test_form_for() -> None:
    assert form_for(560, 1248) is Form.PORTRAIT   # tall phone
    # WIDE (tablet / foldable) is deferred to A6; wide single screens resolve to
    # COMPACT for now (matches the R-E scope).
    assert form_for(1116, 628) is Form.COMPACT    # wide landscape
    assert form_for(640, 480) is Form.COMPACT     # handheld 4:3


def test_is_dual() -> None:
    assert is_dual(Form.DUAL) is True
    assert is_dual(Form.COMPACT) is False
