"""Reusable drawing widgets.

Every function takes a :class:`~retrostation.ui.painter.Painter` and draws in
reference-design units -- no pixel literals, no PIL, no SDL.  Screens compose
these; they never hand-draw chrome a widget already provides.
"""

from __future__ import annotations

from datetime import datetime

from .. import __version__
from ..core.model import Game
from ..core.theme import COLORS, Metrics
from .art import ArtProvider
from .painter import Painter

# --------------------------------------------------------------------------- #
# Chrome
# --------------------------------------------------------------------------- #


def status_bar(painter: Painter, *, dual: bool) -> None:
    """Time / screen mode / temperature / battery strip."""
    m = painter.metrics
    painter.rect((0, 0, m.width, m.status_h), fill=(16, 16, 18, 255))
    center = m.status_h // 2

    painter.text((m.u(12), center), datetime.now().strftime("%H:%M"), size=12, fill=COLORS.text_dim, anchor="lm")

    parts: list[str] = []
    battery = painter.platform.battery()
    temperature = painter.platform.temperature()
    if battery is not None:
        parts.append(f"{battery}%")
    if temperature is not None:
        parts.append(f"{temperature:.0f}C")
    if parts:
        painter.text(
            (m.width - m.u(12), center), "  ".join(parts), size=12, fill=COLORS.text_dim, anchor="rm"
        )

    mode = painter.translator("status.dual" if dual else "status.single")
    painter.text((m.width // 2, center), mode, size=12, fill=COLORS.text_dim, anchor="mm")


def version_tag(painter: Painter) -> None:
    """App version + author, pinned to the bottom-right of the main screen."""
    m = painter.metrics
    text = f"v{__version__} · by 游戏师叔"
    y = m.height - m.bar_h - m.u(6)
    painter.text((m.width - m.u(10), y), text, size=12, fill=COLORS.text_dim, anchor="rm")


def page_header(painter: Painter, *, title: str, subtitle: str = "", right: str = "") -> None:
    """Accent bar + page title, directly below the status bar."""
    m = painter.metrics
    top = m.status_h
    painter.rect((0, top, m.width, m.head_h), fill=(25, 25, 25, 255))
    painter.rect((0, top, m.width, 1), fill=(34, 34, 37, 255))
    painter.rect((m.u(14), top + m.u(12), 3, m.head_h - m.u(24)), fill=COLORS.accent)

    center = top + m.head_h // 2
    x = m.u(26)
    painter.text((x, center), title, size=20, fill=COLORS.text, anchor="lm")
    if subtitle:
        x += painter.text_width(title, size=20) + m.u(10)
        painter.text((x, center), subtitle, size=12, fill=COLORS.text_dim, anchor="lm")
    if right:
        painter.text((m.width - m.u(14), center), right, size=12, fill=COLORS.text_dim, anchor="rm")


def button_bar(painter: Painter, hints: list[tuple[str, str]]) -> None:
    """``hints`` is a list of ``(key_label, action_label)`` pairs."""
    m = painter.metrics
    top = m.height - m.bar_h
    painter.rect((0, top, m.width, m.bar_h), fill=(16, 16, 18, 255))
    painter.rect((0, top, m.width, 1), fill=(34, 34, 37, 255))

    center = top + m.bar_h // 2
    radius = m.u(9)
    x = m.u(10)
    hits: list[tuple[tuple[int, int, int, int], str]] = []
    for key_label, action_label in hints:
        left = x
        painter.ellipse((x, center - radius, radius * 2, radius * 2), fill=COLORS.accent_d1)
        painter.text((x + radius, center), key_label, size=10, fill=(26, 18, 6, 255), anchor="mm")
        x += radius * 2 + m.u(5)
        painter.text((x, center), action_label, size=12, fill=COLORS.text_dim, anchor="lm")
        x += painter.text_width(action_label, size=12) + m.u(12)
        hits.append(((left, top, x - left, m.bar_h), key_label))
    # The bar is tappable: a phone has no physical buttons, so each span presses
    # the button it advertises (DESIGN.ANDROID §10.4).  The app hit-tests these;
    # it clears the list each frame before anything draws.
    if getattr(painter, "button_hits", None) is None:
        painter.button_hits = []
    painter.button_hits.extend(hits)


#: Arrow glyphs for the virtual d-pad arms.
_PAD_GLYPHS = {"UP": "\u25b2", "DOWN": "\u25bc", "LEFT": "\u25c0", "RIGHT": "\u25b6"}


def _scaled(alpha: int, opacity: int) -> int:
    """``alpha`` at ``opacity`` percent (0-100), clamped to a byte."""
    return max(0, min(255, round(alpha * max(0, min(100, opacity)) / 100)))


def _fit_text(painter: Painter, text: str, *, max_width: int, size: int) -> int:
    """Largest size <= ``size`` at which ``text`` still fits ``max_width``.

    The pad's labels go into fixed pills; on a wide canvas the size derived from
    the pill's height runs past the pill's width, so "SELECT" spilled over both
    edges.  Measure and step down instead.
    """
    while size > 8 and painter.text_width(text, size=size) > max_width:
        size -= 1
    return size


def dpad(painter: Painter, box: tuple[int, int, int, int], *,
         opacity: int = 100) -> None:
    """A translucent d-pad whose arms join ``painter.button_hits``.

    Directions only -- confirm / back / view live on the button bar and on the
    face buttons (see :func:`game_pad`).  Call it *after* :func:`button_bar`:
    that one resets the hit list each frame, this one appends to it.
    """
    x, y, w, h = box
    side = min(w, h)
    cx, cy = x + w // 2, y + h // 2
    half = side // 2
    painter.ellipse((cx - half, cy - half, side, side),
                    fill=(255, 255, 255, _scaled(90, opacity)))

    arm = int(side * 0.35)
    btn = int(side * 0.30)          # arm + btn/2 = 0.50: flush with the disc
    radius = max(2, int(side * 0.11))
    hits: list[tuple[tuple[int, int, int, int], str]] = []
    for dx, dy, label in ((-1, 0, "LEFT"), (1, 0, "RIGHT"), (0, -1, "UP"), (0, 1, "DOWN")):
        bx = cx + dx * arm - btn // 2
        by = cy + dy * arm - btn // 2
        area = (bx, by, btn, btn)
        painter.rounded_rect(area, radius=radius,
                             fill=(255, 255, 255, _scaled(150, opacity)))
        painter.text((bx + btn // 2, by + btn // 2), _PAD_GLYPHS[label],
                     size=max(9, side // 7), fill=(240, 240, 244), anchor="mm")
        hits.append((area, label))
    painter.button_hits.extend(hits)


#: Face-button colours, Switch-ish so each letter reads at a glance.
_FACE_COLOURS = {
    "A": (86, 196, 120),
    "B": (214, 92, 92),
    "X": (92, 148, 220),
    "Y": (214, 178, 84),
}


def game_pad(painter: Painter, box: tuple[int, int, int, int], *,
             opacity: int = 100,
             avoid: tuple[int, int, int, int] | None = None) -> None:
    """The full on-screen pad: d-pad, A/B/X/Y and select/start (§10.4).

    A phone has no keys at all, so every action the session understands has to
    be reachable by touch -- the button bar only carries context actions.  All
    arms, buttons and pills join ``painter.button_hits``, so the app's existing
    tap-to-button path presses them with no extra wiring.
    """
    x, y, w, h = box
    margin = painter.metrics.u(6)
    side = min(h, w // 3)
    cy = y + (h - side) // 2
    left = x + margin
    if avoid is not None:
        ax, ay, aw, ah = avoid
        # The clip is a native surface painted above this canvas, so it cannot
        # be covered: the d-pad steps aside instead -- it is the cluster that
        # shares a corner with the media box.  Clamped afterwards so a wide
        # media box (the platform page's bottom strip) cannot shove it clean off
        # the right edge, where it would simply vanish.
        if ax < left + side and left < ax + aw and ay < cy + side and cy < ay + ah:
            left = ax + aw + painter.metrics.u(8)
    left = max(x + margin, min(left, x + w - side - margin))
    dpad(painter, (left, cy, side, side), opacity=opacity)

    r = max(9, int(side * 0.205))
    # Clamp the spread to the box: the diamond reaches ``off + r`` out from the
    # centre, and letting that exceed half the box height clipped the bottom
    # button against the panel edge.
    off = min(int(side * 0.355), max(4, h // 2 - r - 2))
    # Right edge, not the centre of the right third: A ends at ``w - margin`` so
    # the cluster lands under the thumb resting on the corner.
    cx = x + w - margin - off - r
    mid = y + h // 2
    hits: list[tuple[tuple[int, int, int, int], str]] = []
    # Diamond, Switch layout: A right, B down, X up, Y left.
    for dx, dy, label in ((0, -off, "X"), (off, 0, "A"), (0, off, "B"), (-off, 0, "Y")):
        bx, by = cx + dx - r, mid + dy - r
        painter.ellipse((bx, by, r * 2, r * 2),
                        fill=(*_FACE_COLOURS[label], _scaled(255, opacity)))
        painter.text((bx + r, by + r), label,
                     size=_fit_text(painter, label, max_width=int(r * 1.15),
                                    size=max(10, int(r * 0.85))),
                     fill=(24, 24, 28), anchor="mm")
        hits.append(((bx, by, r * 2, r * 2), label))

    # SELECT / START: side by side along the bottom, centred between the sticks.
    pill_h = max(16, int(side * 0.20))
    pill_w = max(56, int(side * 0.52))
    gap = max(8, side // 10)         # the two pills read as a pair, not a blob
    px = x + (w - (pill_w * 2 + gap)) // 2
    py = y + h - pill_h
    for i, label in enumerate(("SELECT", "START")):
        bx = px + i * (pill_w + gap)
        painter.rounded_rect((bx, py, pill_w, pill_h), radius=pill_h // 2,
                             fill=(236, 238, 244, _scaled(230, opacity)))
        size = _fit_text(painter, label, max_width=pill_w - 2 * margin,
                         size=max(9, int(pill_h * 0.62)))
        painter.text((bx + pill_w // 2, py + pill_h // 2), label,
                     size=size, fill=(22, 22, 26), anchor="mm")
        hits.append(((bx, py, pill_w, pill_h), label))

    painter.button_hits.extend(hits)


def pad_box(m, *, single: bool, height: int | None = None) -> tuple[int, int, int, int]:
    """Where the floating pad sits, per form (DESIGN.ANDROID §10.4).

    The pad is an overlay, so its box comes from the surface it floats on, not
    from whatever page is showing: both forms anchor it to the bottom with one
    cluster in each lower corner.  ``height`` is that surface's height.
    """
    panel_h = m.height if height is None else height
    h = m.u(112) if single else m.u(150)
    if single:
        # Landscape: on the strip's band, clear of the button bar.
        y = m.height - m.bar_h - h - m.u(4)
    else:
        # Portrait: the same rule -- hard against the bottom of the surface the
        # pad is painted on.  ``height`` is that surface's height; ``m.height``
        # is the panel the layout was *planned* for and is taller than the
        # canvas handed to the painter, so anchoring to it pushed the pad past
        # the visible bottom and clipped it (which is why this used to sit at a
        # made-up 65% instead of at the bottom).
        y = panel_h - h - m.u(16)
    # Full width, edge to edge: the d-pad hugs the left and A/B/X/Y the right,
    # which is where the thumbs actually rest.  Insetting the box and then
    # centring each cluster inside its third left both well inboard of the
    # corners they are meant to reach.
    return (0, y, m.width, h)


def pad_bitmap(m, box, *, opacity: int, avoid=None, platform=None, translator=None):
    """The pad composited into its own surface: ``(image, hits)``.

    True alpha costs a layer per shape, and the pad is a dozen of them, so the
    whole cluster is drawn once into a scratch surface and pasted as a single
    picture.  Hit boxes come back in canvas coordinates, ready to append.
    """
    from ..platform.canvas import PilCanvas

    x, y, w, h = box
    # Transparent scratch: PilCanvas defaults to *opaque* black, and the pad is
    # only translucent shapes -- pasting that black plate back would leave the
    # whole cluster sitting on a solid black rectangle instead of the content.
    surface = PilCanvas(max(1, w), max(1, h), (0, 0, 0, 0))
    scratch = Painter(surface, m, platform, translator)
    scratch.button_hits = []
    local = None if avoid is None else (avoid[0] - x, avoid[1] - y, avoid[2], avoid[3])
    game_pad(scratch, (0, 0, w, h), opacity=opacity, avoid=local)
    hits = [((bx + x, by + y, bw, bh), label)
            for (bx, by, bw, bh), label in scratch.button_hits]
    return surface.snapshot(), hits


def scrollbar(
    painter: Painter, *, index: int, total: int, visible: int, content_h: int) -> None:
    """Thin indicator on the right edge; a no-op when everything fits."""
    m = painter.metrics
    if total <= visible:
        return
    thumb_h = max(m.u(24), content_h * visible / total)
    y = (content_h - thumb_h) * index / max(1, total - 1)
    x = m.content_w - m.scrollbar_w - m.u(3)

    painter.rect((x, m.u(4), m.scrollbar_w, content_h - m.u(8)), fill=(255, 255, 255, 15))
    painter.rect((x, m.u(4) + int(y), m.scrollbar_w, int(thumb_h)), fill=COLORS.accent)


def toast(painter: Painter, text: str) -> None:
    """Floating pill near the bottom edge."""
    m = painter.metrics
    width = painter.text_width(text, size=12) + m.u(28)
    height = m.u(24)
    x = (m.width - width) // 2
    y = m.height - m.bar_h - m.u(40)
    painter.rounded_rect((x, y, width, height), radius=height // 2, fill=(24, 24, 26, 242), outline=COLORS.border)
    painter.text((m.width // 2, y + height // 2), text, size=12, fill=COLORS.text, anchor="mm")


def dialog(
    painter: Painter,
    *,
    title: str,
    body: str = "",
    rows: list[tuple[str, str]] | None = None,
    selected: int = 0,
    top: int | None = None,
    buttons: tuple[str, ...] = (),
    steppers: frozenset[int] = frozenset(),
) -> tuple[list[tuple[tuple[int, int, int, int], int]],
           list[tuple[tuple[int, int, int, int], int]],
           list[tuple[tuple[int, int, int, int], int, int]]]:
    """Centred modal with a dimmed backdrop.

    ``rows`` are ``(label, value)`` pairs for a settings menu; a plain
    confirmation dialog passes an empty list and renders ``body``.  ``top`` pins
    the first visible row (a touch drag moves the *content*, not the cursor);
    without it the window follows ``selected``.  ``buttons`` adds a row of
    buttons along the bottom -- a touch device has no A/B to commit with.
    ``steppers`` names the rows whose value a finger steps: those are drawn with
    their own ``−`` and ``+`` buttons.  Returns the row boxes, the button boxes
    and the stepper boxes -- ``(box, index)`` and ``(box, index, ±1)`` -- so the
    caller can hit-test touches against exactly what is on screen.
    """
    m = painter.metrics
    painter.rect((0, 0, m.width, m.height), fill=(0, 0, 0, 158))

    rows = rows or []
    row_h = m.u(40)
    width = m.u(470)
    chrome = (m.u(42) + m.u(16) + (m.u(44) if body else 0) + m.u(14)
              + (m.u(46) if buttons else 0))
    # A settings list only ever grows, and a dialog taller than the screen is
    # unusable on a device with no scrolling: show a window around the cursor
    # instead, and hint that the list continues past its edges.
    room = max(1, (m.height - m.u(24) - chrome) // row_h)
    visible = min(len(rows), room)
    start = 0
    if len(rows) > visible:
        anchor = visible // 2 if top is None else top
        start = max(0, min(len(rows) - visible, anchor))
    shown = rows[start:start + visible]
    # The window, for a touch drag that has to know where it currently is.
    painter.dialog_window = (start, visible)

    height = chrome + row_h * visible
    x = (m.width - width) // 2
    y = (m.height - height) // 2

    painter.rounded_rect((x, y, width, height), radius=m.radius, fill=COLORS.panel, outline=COLORS.border)
    painter.rect((x, y, width, m.u(42)), fill=COLORS.panel_2)
    painter.text(
        (x + m.u(16), y + m.u(21)), title, size=15, fill=COLORS.text, anchor="lm"
    )

    content_y = y + m.u(42)
    if body:
        painter.text(
            (x + m.u(16), content_y + m.u(18)), body, size=13, fill=COLORS.text_dim, anchor="lm"
        )
        content_y += m.u(44)

    hits: list[tuple[tuple[int, int, int, int], int]] = []
    stepper_hits: list[tuple[tuple[int, int, int, int], int, int]] = []
    for offset, (label, value) in enumerate(shown):
        index = start + offset
        item_y = content_y + m.u(6) + offset * row_h
        is_selected = index == selected
        # The row's box, for touch hit-testing: the caller hands it back to the
        # session (the geometry lives here and nowhere else).  Full row height,
        # not the highlight's inset -- a tap landing in the gap between two rows
        # did nothing at all, which reads as "touch is broken".
        hits.append(((x + m.u(6), item_y, width - m.u(12), row_h), index))
        if is_selected:
            painter.hgradient(
                (x + m.u(6), item_y, width - m.u(12), row_h - m.u(8)),
                start=COLORS.accent,
                end=COLORS.accent_d1,
                radius=m.u(6),
            )
            label_color = value_color = (26, 18, 6, 255)
        else:
            label_color = COLORS.text
            value_color = COLORS.text_dim
        # Label and value share one line: left-aligned label, right-aligned
        # value.  Without truncating, a long pair runs the two into each other
        # (very visible with the longer translated labels).
        value_room = width // 2 - m.u(24)
        shown_value = painter.ellipsize(value, size=12, max_width=value_room)
        value_pos = (x + width - m.u(16), item_y + row_h // 2 - m.u(4))
        value_anchor = "rm"
        label_end = x + width - m.u(16)
        if index in steppers:
            # A stepped row is a little stepper -- [−] value [+] -- with the two
            # buttons a text line tall, so the marks read as part of the row
            # instead of as blocks parked on it.  The hit boxes recorded here are
            # exactly what the session tests, so a tap lands on the mark it looks
            # like it hit.  (Drawn all inside the value cell they put the "−"
            # itself in the right half of the row, and every tap added.)
            btn, gap, slot = m.u(22), m.u(6), m.u(56)
            top = item_y + (row_h - btn) // 2
            plus = (x + width - m.u(16) - btn, top, btn, btn)
            value_left = plus[0] - gap - slot
            minus = (value_left - gap - btn, top, btn, btn)
            stepper_hits.append((minus, index, -1))
            stepper_hits.append((plus, index, 1))
            if is_selected:
                # On the highlight the buttons belong to it: the label's own ink,
                # and a fill that darkens the accent rather than covering it with
                # a panel-coloured block.
                fill, outline, glyph_color = (0, 0, 0, 46), (0, 0, 0, 0), label_color
            else:
                fill, outline, glyph_color = COLORS.panel_2, COLORS.border, COLORS.text
            for box, glyph in ((minus, "−"), (plus, "+")):
                painter.rounded_rect(box, radius=m.u(4), fill=fill, outline=outline)
                painter.text(
                    (box[0] + box[2] // 2, box[1] + box[3] // 2),
                    glyph, size=12, fill=glyph_color, anchor="mm",
                )
            # The number sits between the two buttons.
            value_pos = (value_left + slot // 2, item_y + row_h // 2 - m.u(4))
            value_anchor = "mm"
            label_end = minus[0]
        label_room = label_end - x - m.u(28) - painter.text_width(shown_value, size=12)
        painter.text(
            (x + m.u(16), item_y + row_h // 2 - m.u(4)),
            painter.ellipsize(label, size=14, max_width=max(m.u(40), label_room)),
            size=14, fill=label_color, anchor="lm",
        )
        painter.text(value_pos, shown_value, size=12, fill=value_color, anchor=value_anchor)

    if start > 0 or start + visible < len(rows):
        painter.text(
            (x + width // 2, y + height - m.u(7) - (m.u(46) if buttons else 0)),
            "▲" if start > 0 else "▼",
            size=9, fill=COLORS.text_dim, anchor="mm",
        )

    button_hits: list[tuple[tuple[int, int, int, int], int]] = []
    if buttons:
        # Right-aligned along the bottom, cancel first -- the order every system
        # dialog uses, so a finger goes where it expects.
        bar_h, gap, pad = m.u(30), m.u(10), m.u(16)
        btn_w = (width - pad * 2 - gap * (len(buttons) - 1)) // max(1, len(buttons))
        bar_y = y + height - bar_h - m.u(8)
        for index, label in enumerate(buttons):
            bx = x + pad + index * (btn_w + gap)
            box = (bx, bar_y, btn_w, bar_h)
            primary = index == len(buttons) - 1
            painter.rounded_rect(
                box, radius=m.u(6),
                fill=COLORS.accent if primary else COLORS.panel_2,
                outline=COLORS.border,
            )
            painter.text(
                (bx + btn_w // 2, bar_y + bar_h // 2), label, size=13,
                fill=(26, 18, 6, 255) if primary else COLORS.text, anchor="mm",
            )
            button_hits.append((box, index))
    return hits, button_hits, stepper_hits


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #


def game_artwork(
    painter: Painter,
    art: ArtProvider,
    game: Game,
    box: tuple[int, int, int, int],
    *,
    prefer_logo: bool = False,
) -> None:
    """Best artwork for ``game`` inside ``box``.

    ``prefer_logo`` is used by the list view (wide slot); the grid, carousel
    and bottom screen prefer the cover.  When nothing exists a deterministic
    placeholder is drawn, never a blank box.

    Both bitmaps come from :class:`ArtProvider`: decoding a cover and scaling it
    to fit costs ~14 ms on the handheld, which is most of a frame if it happens
    per frame instead of once.
    """
    m = painter.metrics
    x, y, w, h = box
    bitmap = art.thumbnail(game, w, h, prefer_logo=prefer_logo)

    if bitmap is not None:
        painter.image_fit(bitmap, box)
        return

    painter.rounded_rect(box, radius=m.u(4), fill=COLORS.panel_2, outline=COLORS.border)
    painter.image_fit(art.placeholder(game.key, max(8, w), max(8, h)), box)
    painter.text(
        (x + w // 2, y + h // 2),
        game.display_name[:2] or "?",
        size=max(11, h // 3),
        fill=(255, 255, 255, 205),
        anchor="mm",
    )


def logo_banner(painter: Painter, art: ArtProvider, game: Game,
                box: tuple[int, int, int, int]) -> None:
    """Bottom-screen logo strip: the logo when present, otherwise the name.

    The logo is redrawn on every video frame, so it has to come from the
    thumbnail cache -- decoding the original PNG and scaling it each time
    measured 14 ms per frame, i.e. a fifth of the frame budget.
    """
    m = painter.metrics
    x, y, w, h = box
    painter.rounded_rect(box, radius=m.u(8), fill=COLORS.panel, outline=COLORS.border)

    inner = (w - m.u(24), h - m.u(20))
    logo = art.thumbnail(game, inner[0], inner[1], prefer_logo=True)
    if logo is not None:
        painter.image_fit(logo, (x + m.u(12), y + m.u(10), inner[0], inner[1]))
        return

    painter.text(
        (x + w // 2, y + h // 2), game.display_name, size=16, fill=COLORS.text, anchor="mm"
    )
