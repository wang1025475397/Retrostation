"""Visual design tokens.

Two rules keep this module honest:

1. **No pixel literals outside this file.**  Layout code asks for
   ``metrics.row_h`` instead of writing ``34``, which is what makes the planned
   Android port (very different screen sizes) a configuration change instead of
   a rewrite.
2. **Everything scales from a 640x480 reference design**, which is the measured
   resolution of both RG DS panels (see ``docs/DESIGN.md`` section 2.1).
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Reference design
# --------------------------------------------------------------------------- #

#: Measured resolution of a single RG DS panel.
BASE_W = 640
BASE_H = 480

#: Minimum readable size -- never let a derived dimension collapse to 0.
_MIN_PX = 1


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Colors:
    """RGBA palette.

    Amber accent is inherited from tiny-scraper so both apps feel like the same
    product on the same device.
    """

    bg: tuple[int, int, int, int] = (20, 20, 20, 255)
    panel: tuple[int, int, int, int] = (28, 28, 30, 255)
    panel_2: tuple[int, int, int, int] = (36, 36, 38, 255)
    border: tuple[int, int, int, int] = (51, 51, 54, 255)

    accent: tuple[int, int, int, int] = (232, 163, 61, 255)
    accent_d1: tuple[int, int, int, int] = (184, 125, 34, 255)

    text: tuple[int, int, int, int] = (242, 242, 242, 255)
    text_dim: tuple[int, int, int, int] = (154, 154, 158, 255)

    ok: tuple[int, int, int, int] = (76, 175, 80, 255)
    warn: tuple[int, int, int, int] = (255, 200, 102, 255)
    danger: tuple[int, int, int, int] = (224, 82, 82, 255)

    #: Named lookup so widgets can do ``colors["accent"]``.
    def as_dict(self) -> dict[str, tuple[int, int, int, int]]:
        return {
            "bg": self.bg,
            "panel": self.panel,
            "panel_2": self.panel_2,
            "border": self.border,
            "accent": self.accent,
            "accent_d1": self.accent_d1,
            "text": self.text,
            "text_dim": self.text_dim,
            "ok": self.ok,
            "warn": self.warn,
            "danger": self.danger,
        }


#: Shared immutable instance -- the palette is not user-configurable yet.
COLORS = Colors()


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Metrics:
    """All layout numbers for one screen, derived from its pixel size."""

    width: int
    height: int

    # -- scaling ---------------------------------------------------------- #

    @property
    def scale(self) -> float:
        """Uniform scale factor against the 640x480 reference design."""
        return min(self.width / BASE_W, self.height / BASE_H)

    def u(self, base_px: float) -> int:
        """Scale a reference-design pixel value to this screen."""
        return max(_MIN_PX, round(base_px * self.scale))

    def font(self, base_px: float) -> int:
        """Scale a font size; kept separate because fonts clamp differently."""
        return max(8, round(base_px * self.scale))

    # -- vertical chrome -------------------------------------------------- #

    @property
    def status_h(self) -> int:
        return self.u(28)

    @property
    def head_h(self) -> int:
        return self.u(44)

    @property
    def bar_h(self) -> int:
        return self.u(30)

    @property
    def strip_h(self) -> int:
        """Single-screen bottom detail strip (see DESIGN section 11)."""
        return self.u(118)

    def content_h(self, *, single: bool = False) -> int:
        """Height left for the list / grid / carousel."""
        used = self.status_h + self.head_h + self.bar_h
        if single:
            used += self.strip_h
        return max(self.u(120), self.height - used)

    @property
    def content_top(self) -> int:
        """First pixel below the page header."""
        return self.status_h + self.head_h

    # -- game list view --------------------------------------------------- #

    @property
    def row_h(self) -> int:
        return self.u(34)

    @property
    def row_gap(self) -> int:
        return self.u(4)

    @property
    def row_step(self) -> int:
        return self.row_h + self.row_gap

    @property
    def thumb_w(self) -> int:
        """List row artwork.  Wide, because it shows the (4:1) logo first."""
        return self.u(84)

    @property
    def thumb_h(self) -> int:
        return self.u(30)

    def rows_per_page(self, *, single: bool = False) -> int:
        return max(1, self.content_h(single=single) // self.row_step)

    # -- game grid view --------------------------------------------------- #

    @property
    def grid_padding(self) -> int:
        return self.u(8)

    @property
    def grid_gap(self) -> int:
        return self.u(8)

    @property
    def grid_cols(self) -> int:
        """Column count adapts to width so tall phone screens look sane."""
        spare = self.width / (BASE_W * self.scale) if self.scale else 1.0
        return _clamp(round(4 * spare), 3, 6)

    def grid_rows(self, *, single: bool = False) -> int:
        return 2 if single else 3

    def grid_cell_h(self, *, single: bool = False) -> int:
        rows = self.grid_rows(single=single)
        usable = self.content_h(single=single) - 2 * self.grid_padding
        usable -= self.grid_gap * (rows - 1)
        return max(self.u(48), usable // rows)

    def items_per_grid_page(self, *, single: bool = False) -> int:
        return self.grid_cols * self.grid_rows(single=single)

    # -- game carousel view ----------------------------------------------- #

    @property
    def carousel_gap(self) -> int:
        return self.u(14)

    def carousel_card_h(self, *, single: bool = False) -> int:
        # 70 reference px are reserved for the logo/name banner below the card.
        return max(self.u(140), min(self.u(272), self.content_h(single=single) - self.u(70)))

    def carousel_card_w(self, *, single: bool = False) -> int:
        return round(self.carousel_card_h(single=single) * 0.72)

    # -- bottom screen ---------------------------------------------------- #

    @property
    def bottom_title_h(self) -> int:
        return self.u(32)

    @property
    def bottom_hint_h(self) -> int:
        return self.u(44)

    @property
    def body_padding(self) -> int:
        return self.u(12)

    @property
    def body_gap(self) -> int:
        return self.u(14)

    @property
    def media_w(self) -> int:
        """Width of the bottom-screen media column (video / cover)."""
        return max(self.u(200), round(self.width * 0.525))

    @property
    def media_h(self) -> int:
        return self.u(264)

    @property
    def logo_strip_h(self) -> int:
        return self.u(72)

    @property
    def meta_w(self) -> int:
        """Width of the bottom-screen metadata column."""
        available = self.width - 2 * self.body_padding - self.body_gap
        return max(self.u(180), available - self.media_w)

    def bottom_body_h(self) -> int:
        return max(
            self.u(180),
            self.height - self.bottom_title_h - self.bottom_hint_h - 2 * self.body_padding,
        )

    # -- misc ------------------------------------------------------------- #

    @property
    def radius(self) -> int:
        return self.u(8)

    @property
    def scrollbar_w(self) -> int:
        return self.u(4)


def metrics_for(width: int, height: int) -> Metrics:
    """Build :class:`Metrics` for a screen, rejecting nonsense sizes."""
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid screen size {width}x{height}")
    return Metrics(width=width, height=height)
