"""Typed application configuration.

Rules we follow here:

* every setting has a default, so a missing key is never an error;
* a *wrong* value is an error — we fail loudly at load time instead of
  silently rendering a broken layout;
* saving is atomic (temp file + ``os.replace``) so a crash or a power cut
  cannot truncate ``config.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar, Mapping

# --------------------------------------------------------------------------- #
# Valid values (shared with the UI so there is a single source of truth)
# --------------------------------------------------------------------------- #

#: Game page views, cycled with the X button.
LAYOUTS: tuple[str, ...] = ("list", "grid", "carousel")
SCREEN_MODES: tuple[str, ...] = ("auto", "dual", "single")
SINGLE_LAYOUTS: tuple[str, ...] = ("split_v", "split_h")
SORT_ORDERS: tuple[str, ...] = ("name", "play", "recent")
FILTERS: tuple[str, ...] = ("all", "covered", "missing")


class ConfigError(ValueError):
    """Raised when ``config.json`` contains a value we cannot accept."""


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


@dataclass
class MetadataConfig:
    """Metadata source settings — see ``docs/DESIGN.md`` §6.8."""

    #: Source names in priority order (first one wins on conflicting fields).
    sources: list[str] = field(default_factory=lambda: ["esde", "pegasus"])
    #: The only source we ever write ``favorite``/``playcount``/... back to.
    primary_write_source: str = "esde"
    #: Never write anything (useful when the card is shared with other tools).
    read_only: bool = False
    #: Keep one ``gamelist.xml.bak`` next to the file we rewrite.
    backup: bool = True
    #: Fall back to a sidecar file when no writable source is available.
    sidecar_fallback: bool = True


@dataclass
class LauncherConfig:
    """How games are started — see ``docs/DESIGN.md`` §2.4."""

    ra_script: str = "/mnt/mod/ctrl/RA_launch.sh"
    cores_dir: str = "/mnt/vendor/deep/retro/cores"
    fallback_ra: str = "/oem/retro/retroarch"
    fallback_cores_dir: str = "/oem/retro/cores"


@dataclass
class Config:
    """Root configuration object."""

    # display ------------------------------------------------------------- #
    screen_mode: str = "auto"
    single_layout: str = "split_v"

    # library ------------------------------------------------------------- #
    rom_root: str = "auto"
    layout: str = "list"
    sort: str = "name"
    filter: str = "all"

    # media --------------------------------------------------------------- #
    media_dirs: dict[str, str] = field(
        default_factory=lambda: {"cover": "Imgs", "video": "video", "logo": "logo"}
    )
    bottom_video: bool = True
    video_fps: int = 15
    video_size: list[int] = field(default_factory=lambda: [288, 216])

    # metadata ------------------------------------------------------------ #
    metadata: MetadataConfig = field(default_factory=MetadataConfig)

    # look & feel --------------------------------------------------------- #
    language: str = "auto"
    theme: str = "amber"
    theme_variant: str = "dark"
    show_status_bar: bool = True
    bottom_refresh_ms: int = 90
    thumbnail_cache: bool = True

    # integration --------------------------------------------------------- #
    launcher: LauncherConfig = field(default_factory=LauncherConfig)
    core_overrides: dict[str, str] = field(default_factory=dict)
    brightness: dict[str, int] = field(default_factory=lambda: {"top": 140, "bottom": 140})

    #: Keys that must be one of a fixed set, and the set they belong to.
    _CHOICES: ClassVar[dict[str, tuple[str, ...]]] = {
        "screen_mode": SCREEN_MODES,
        "single_layout": SINGLE_LAYOUTS,
        "layout": LAYOUTS,
        "sort": SORT_ORDERS,
        "filter": FILTERS,
    }

    # ------------------------------------------------------------------ #
    # Loading / saving
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: Path | str) -> Config:
        """Load from ``path``; missing file or missing keys fall back to defaults."""
        path = Path(path)
        raw: Mapping[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"cannot read config {path}: {exc}") from exc
            if not isinstance(raw, dict):
                raise ConfigError(f"config {path} must contain a JSON object")
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Config:
        """Build from a plain dict, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}

        for key, value in raw.items():
            if key not in known:
                continue  # forward compatible: old configs must keep working
            if key == "metadata":
                kwargs[key] = _build_section(MetadataConfig, value, "metadata")
            elif key == "launcher":
                kwargs[key] = _build_section(LauncherConfig, value, "launcher")
            else:
                kwargs[key] = value

        config = cls(**kwargs)
        config.validate()
        return config

    def save(self, path: Path | str) -> None:
        """Write atomically; never leaves a half-written file behind."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".cfg-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ------------------------------------------------------------------ #

    def validate(self) -> None:
        """Raise :class:`ConfigError` if any value is out of range."""
        for key, choices in type(self)._CHOICES.items():
            value = getattr(self, key)
            if value not in choices:
                raise ConfigError(
                    f"config.{key}={value!r} is invalid; expected one of {list(choices)}"
                )

        if not isinstance(self.media_dirs, dict) or not self.media_dirs:
            raise ConfigError("config.media_dirs must be a non-empty object")
        if self.video_fps <= 0:
            raise ConfigError("config.video_fps must be positive")
        if len(self.video_size) != 2 or any(s <= 0 for s in self.video_size):
            raise ConfigError("config.video_size must be [width, height] with positive values")
        if self.bottom_refresh_ms < 0:
            raise ConfigError("config.bottom_refresh_ms must not be negative")

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    def next_layout(self) -> str:
        """The view the X button should switch to."""
        return LAYOUTS[(LAYOUTS.index(self.layout) + 1) % len(LAYOUTS)]

    def media_dir(self, kind: str) -> str | None:
        return self.media_dirs.get(kind)


def _build_section(section_cls: type, value: Any, name: str) -> Any:
    """Build a nested config section, tolerating partial objects."""
    if value is None:
        return section_cls()
    if isinstance(value, section_cls):
        return value
    if not isinstance(value, dict):
        raise ConfigError(f"config.{name} must be an object")
    allowed = {f.name for f in fields(section_cls)}
    return section_cls(**{k: v for k, v in value.items() if k in allowed})
