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

import enum
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


#: Accent families.  Amber came first because it is what tiny-scraper uses, so
#: both apps feel like one product on the same device.  Each pair is
#: ``(accent, accent_d1)``: the second is the gradient tail and button fill.
_ACCENTS: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "amber": {"accent": (232, 163, 61, 255), "accent_d1": (184, 125, 34, 255)},
    "ice": {"accent": (97, 175, 239, 255), "accent_d1": (47, 118, 186, 255)},
    "lime": {"accent": (163, 209, 78, 255), "accent_d1": (113, 154, 42, 255)},
}

#: Light and dark surfaces.  Only the neutrals live here -- the accent stays
#: whatever family was picked, so a theme is a (family, surface) pair.
_SURFACES: dict[str, dict[str, tuple[int, int, int, int]]] = {
    "dark": {
        "bg": (20, 20, 20, 255),
        "panel": (28, 28, 30, 255),
        "panel_2": (36, 36, 38, 255),
        "border": (51, 51, 54, 255),
        "text": (242, 242, 242, 255),
        "text_dim": (154, 154, 158, 255),
    },
    "light": {
        "bg": (233, 233, 236, 255),
        "panel": (248, 248, 250, 255),
        "panel_2": (222, 222, 227, 255),
        "border": (193, 193, 200, 255),
        "text": (30, 30, 33, 255),
        "text_dim": (104, 104, 112, 255),
    },
}

DEFAULT_THEME = "amber"
DEFAULT_VARIANT = "dark"

#: Values accepted by ``config.theme`` / ``config.theme_variant``; shared with
#: the settings dialog so there is one source of truth.
THEMES: tuple[str, ...] = tuple(_ACCENTS)
VARIANTS: tuple[str, ...] = tuple(_SURFACES)


@dataclass
class Colors:
    """RGBA palette.

    Mutable on purpose, and shared as a single instance: every screen does
    ``from ...core.theme import COLORS``, so switching a theme has to update
    *that* object rather than hand out a new one -- otherwise half the UI would
    keep painting with the palette it imported.
    """

    bg: tuple[int, int, int, int] = (20, 20, 20, 255)
    #: Vertical background gradient stops (android skin).  Kept equal to ``bg``
    #: on the handheld so its flat background is untouched.
    bg_top: tuple[int, int, int, int] = (20, 20, 20, 255)
    bg_bottom: tuple[int, int, int, int] = (20, 20, 20, 255)
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

    def apply(self, theme: str = DEFAULT_THEME, variant: str = DEFAULT_VARIANT) -> None:
        """Load a (family, surface) pair into this instance.

        Unknown names fall back to the defaults rather than raising: a config
        written by a newer build must not stop the app from painting.
        """
        accents = _ACCENTS.get(theme) or _ACCENTS[DEFAULT_THEME]
        surfaces = _SURFACES.get(variant) or _SURFACES[DEFAULT_VARIANT]
        for name, value in {**surfaces, **accents}.items():
            setattr(self, name, value)
        if _SKIN == "android":
            self.apply_android_modern()

    def apply_android_modern(self) -> None:
        """Overlay the modern-dark android look on whatever theme is active.

        Surfaces get a cooler, deeper ladder so panels float off the gradient
        background; accents, status colours and the player's theme choice are
        preserved.  Called from :meth:`apply` whenever the android skin is on,
        so a mid-session theme switch keeps the look.
        """
        self.bg = (13, 15, 19, 255)
        self.bg_top = (19, 22, 28, 255)
        self.bg_bottom = (10, 11, 15, 255)
        self.panel = (25, 28, 35, 255)
        self.panel_2 = (33, 37, 46, 255)
        self.border = (47, 52, 63, 255)
        self.text = (236, 239, 244, 255)
        self.text_dim = (137, 144, 156, 255)


#: The palette every screen paints with.  Call :meth:`Colors.apply` on it once
#: at startup (and whenever the settings change) -- never replace the object.
COLORS = Colors()


# --------------------------------------------------------------------------- #
# Skin
# --------------------------------------------------------------------------- #

#: Which surface family is painting.  ``handheld`` is the reference design the
#: metrics were measured against and stays the default everywhere; ``android``
#: turns on the modern-dark look -- deeper surfaces, gradient background,
#: larger corner radii (DESIGN.ANDROID §11 polish).  A module flag rather than
#: a ``Metrics`` field so every painter on the device switches together.
_SKIN = "handheld"


def set_skin(skin: str) -> None:
    global _SKIN
    _SKIN = "android" if skin == "android" else "handheld"


def current_skin() -> str:
    return _SKIN


def is_android_skin() -> bool:
    return _SKIN == "android"


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


class Form(str, enum.Enum):
    """How the detail panel is arranged on this screen.

    Not a synonym for "screen size": two panels of identical pixels are laid
    out differently depending on whether a second screen exists to put the
    detail view on.  Deriving the arrangement from a named form -- rather than
    from a bare ``single`` flag -- is what lets a phone reserve 40% of a tall
    screen where the handheld reserves a fixed 118px strip
    (docs/DESIGN.ANDROID.md §5.5 / §11.1).
    """

    #: Two panels: the top one interacts, the bottom one *is* the detail view.
    #: Both handhelds and Android dual-screen devices (AYN Thor, RG DS running
    #: Android) use this, and the two panels may differ in size.
    DUAL = "dual"
    #: One panel, roughly 4:3 to 16:9 -- the handhelds' single-screen mode and
    #: single-screen Android handhelds.  Detail folds into a fixed strip.
    COMPACT = "compact"
    #: One tall panel (phone portrait): the detail area is a proportion of the
    #: height rather than a fixed strip, because 118 reference px would be a
    #: sliver on a 20:9 screen.
    PORTRAIT = "portrait"


@dataclass(frozen=True)
class Metrics:
    """All layout numbers for one screen, derived from its pixel size."""

    width: int
    height: int
    #: Defaults to the handhelds' arrangement, so existing callers are unchanged.
    form: Form = Form.DUAL

    # -- form ------------------------------------------------------------- #

    @property
    def is_single(self) -> bool:
        """Whether the detail view shares this screen with the content."""
        return self.form is not Form.DUAL

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
        """Height of the folded detail area (see DESIGN §11).

        ``118`` is the measured handheld strip and stays exactly that on both
        handheld forms.  A tall phone gets a proportion instead: the same 118
        reference px would be about 5% of a 20:9 screen, too little to hold the
        cover and metadata the strip exists for.
        """
        if self.form is Form.PORTRAIT:
            return round(self.height * 0.40)
        return self.u(118)

    def content_h(self, *, single: bool | None = None) -> int:
        """Height left for the list / grid / carousel.

        ``single`` defaults to what :attr:`form` implies; passing it explicitly
        overrides that, which is what the callers written before forms existed
        still do.
        """
        single = self.is_single if single is None else single
        used = self.status_h + self.head_h + self.bar_h
        if single:
            used += self.strip_h
        return max(self.u(120), self.height - used)

    @property
    def content_top(self) -> int:
        """First pixel below the page header."""
        return self.status_h + self.head_h

    def detail_box(self) -> tuple[int, int, int, int]:
        """Where the detail panel lives: ``(x, y, w, h)``.

        One place computes this, because three used to: the strip painter, its
        artwork slot and the snapshot refresh each re-derived
        ``content_top + content_h(single=True)`` and would have drifted apart
        the moment a form needed a different arrangement.

        On :attr:`Form.DUAL` the whole panel is the detail view (that is what
        ``ui/screens/bottom.py`` paints), so the box is the entire screen.
        """
        if self.form is Form.DUAL:
            return (0, 0, self.width, self.height)
        pad = self.u(8)
        return (pad, self.content_top + self.content_h(single=True),
                self.width - 2 * pad, self.strip_h)

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

    def rows_per_page(self, *, single: bool | None = None) -> int:
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

    def grid_rows(self, *, single: bool | None = None) -> int:
        single = self.is_single if single is None else single
        return 2 if single else 3

    def grid_cell_h(self, *, single: bool | None = None) -> int:
        rows = self.grid_rows(single=single)
        usable = self.content_h(single=single) - 2 * self.grid_padding
        usable -= self.grid_gap * (rows - 1)
        return max(self.u(48), usable // rows)

    def items_per_grid_page(self, *, single: bool | None = None) -> int:
        return self.grid_cols * self.grid_rows(single=single)

    # -- platform carousel (home page) ------------------------------------ #

    @property
    def platform_art(self) -> int:
        """Side of the square art box on a platform card.

        Square because the shipped platform backgrounds are: the source art is
        1024x1024, and a 16:10 box cropped a fifth off the top and bottom of
        every image.  The layout follows the artwork, not the other way round.

        On the android skin's portrait phones the reference size fills barely a
        fifth of the width, so the card scales with the screen (capped) -- a
        carousel card is the hero of the home page there.
        """
        if is_android_skin() and self.form is Form.PORTRAIT:
            return min(round(self.width * 0.44), self.u(240))
        return self.u(132)

    @property
    def platform_logo_h(self) -> int:
        """Logo band underneath the background.

        Platform logos are 820x330 (~2.5:1), so a card-wide band works out
        about this tall.  It replaces the old name/count caption: the artwork
        identifies the platform, and the info line below the carousel spells
        out the selected one.
        """
        if is_android_skin() and self.form is Form.PORTRAIT:
            return max(self.u(48), round(self.platform_art / 2.5))
        return self.u(48)

    @property
    def platform_card_h(self) -> int:
        return self.platform_art + self.platform_logo_h

    @property
    def platform_gap(self) -> int:
        return self.u(14)

    @property
    def platform_top(self) -> int:
        return self.content_top + self.u(8)

    @property
    def platform_info_y(self) -> int:
        return self.platform_top + self.platform_card_h + self.u(14)

    @property
    def platform_preview_y(self) -> int:
        return self.platform_info_y + self.u(48)

    # -- game carousel view ----------------------------------------------- #

    @property
    def carousel_gap(self) -> int:
        return self.u(14)

    def carousel_card_h(self, *, single: bool | None = None) -> int:
        # 70 reference px are reserved for the logo/name banner below the card.
        return max(self.u(140), min(self.u(272), self.content_h(single=single) - self.u(70)))

    def carousel_card_w(self, *, single: bool | None = None) -> int:
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
        return self.u(14) if is_android_skin() else self.u(8)

    @property
    def card_radius(self) -> int:
        """Corner rounding for the big content cards (home carousel, grid)."""
        return self.u(18) if is_android_skin() else self.u(10)

    @property
    def scrollbar_w(self) -> int:
        return self.u(4)


def metrics_for(width: int, height: int, form: Form = Form.DUAL) -> Metrics:
    """Build :class:`Metrics` for a screen, rejecting nonsense sizes."""
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid screen size {width}x{height}")
    return Metrics(width=width, height=height, form=form)
