"""sysfs hardware probes (battery, temperature, backlight).

Every read is defensive: a missing node returns ``None`` instead of raising,
because these values are cosmetic (status bar) and must never take the
frontend down.
"""

from __future__ import annotations

from pathlib import Path

SYSFS_POWER = Path("/sys/class/power_supply")
SYSFS_THERMAL = Path("/sys/class/thermal")
SYSFS_BACKLIGHT = Path("/sys/class/backlight")

#: Values we accept as brightness ceilings -- filters out unrelated nodes.
_MIN_BACKLIGHT_MAX = 10


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def battery_level(power_dir: Path = SYSFS_POWER) -> int | None:
    """Battery percentage, or ``None``."""
    if not power_dir.is_dir():
        return None
    for supply in sorted(power_dir.iterdir()):
        capacity = _read_int(supply / "capacity")
        if capacity is None:
            continue
        status = (_read_text(supply / "status") or "").lower()
        if status in ("unknown", "") and capacity == 0:
            continue
        return max(0, min(100, capacity))
    return None


def cpu_temperature(thermal_dir: Path = SYSFS_THERMAL) -> float | None:
    """CPU temperature in Celsius, or ``None``."""
    if not thermal_dir.is_dir():
        return None
    for zone in sorted(thermal_dir.glob("thermal_zone*")):
        milli = _read_int(zone / "temp")
        if milli is None or milli <= 0:
            continue
        # Some kernels report tenths of a degree instead of milli-degrees.
        if milli > 1000:
            return round(milli / 1000, 1)
        return round(milli, 1)
    return None


def backlight_nodes(backlight_dir: Path = SYSFS_BACKLIGHT) -> list[Path]:
    """Usable backlight controls, sorted so the panels come first."""
    if not backlight_dir.is_dir():
        return []
    nodes: list[Path] = []
    for node in sorted(backlight_dir.iterdir()):
        maximum = _read_int(node / "max_brightness")
        if maximum is not None and maximum >= _MIN_BACKLIGHT_MAX:
            nodes.append(node)
    return nodes


def set_backlight(value: int, index: int = 0, backlight_dir: Path = SYSFS_BACKLIGHT) -> bool:
    """Write a brightness value.  Returns whether it stuck."""
    nodes = backlight_nodes(backlight_dir)
    if index >= len(nodes):
        return False
    node = nodes[index]
    maximum = _read_int(node / "max_brightness")
    if maximum is None:
        return False
    clamped = max(0, min(int(maximum), int(value)))
    try:
        (node / "brightness").write_text(f"{clamped}\n", encoding="utf-8")
    except OSError:
        return False
    return True


def backlight_value(index: int = 0, backlight_dir: Path = SYSFS_BACKLIGHT) -> int | None:
    nodes = backlight_nodes(backlight_dir)
    if index >= len(nodes):
        return None
    return _read_int(nodes[index] / "brightness")
