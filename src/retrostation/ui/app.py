"""Application: owns the canvases, drives the session, renders every frame.

The frame loop is deliberately boring:

    events -> session.handle() -> build view models -> draw -> present

All data preparation (strings, artwork lookups) happens here, so the screen
modules stay free of data access and can be reviewed against the prototype
one-to-one.
"""

from __future__ import annotations

import logging
import math
import subprocess
import time
from pathlib import Path

from ..core.config import Config
from ..core.i18n import Translator
from ..core.model import Game
from ..core.state import read_state, update_state, update_state
from typing import Callable

from ..core.theme import COLORS, metrics_for
from ..data.library import Library
from ..data.systems import display_name, lookup
from ..data.video import VideoPlayer, VideoSettings
from ..launcher.launch import LaunchError, LaunchPlan, build_plan
from ..platform.base import InputAction, InputEvent, InputKind, Platform
from .art import ArtProvider
from .painter import Painter
from .session import (
    MODAL_EXIT,
    MODAL_MENU,
    MODAL_ROM_SELECT,
    MODAL_SEARCH,
    Session,
    VIEW_GAMES,
    VIEW_PLATFORMS,
)
from .screens import bottom, games, home, menu, search
from .widgets import button_bar, dialog, status_bar, toast, version_tag

log = logging.getLogger(__name__)

#: Exit code the shell bootstrap treats as "a game just ran, restart me".
EXIT_RESTART = 42
#: Exit code for a plain user quit -- the bootstrap stops.
EXIT_OK = 0
#: Exit code for "start me again, but run nothing in between".  Switching
#: screen mode needs a different set of windows, and building those inside a
#: live process is precisely what crashes under Wayland (DESIGN §4.4), so the
#: bootstrap restarts us instead of us rebuilding them.
EXIT_RESTART_UI = 43

#: Frame budget.  A millisecond is shaved off to absorb scheduler overshoot:
#: the loop sleeps and polls in slices so a video frame is picked up promptly,
#: and every one of those waits lands a little late -- a straight 1/30 came out
#: the other end at 29.3 fps, not 30.
_TARGET_FPS = 1 / 30 - 0.001
#: Fallback bottom-screen refresh when nothing moves (DESIGN §9.2: <= 12 fps).
_BOTTOM_REFRESH = 0.09
#: Idle heartbeat for the top screen.  It only has to keep the clock, battery
#: and temperature current, and a full repaint costs ~39 ms on the handheld --
#: most of a video frame interval, which is why it is a second and not a
#: fraction of one.  Anything the user does repaints immediately (``_draw``
#: compares a state key, so interaction never waits for this).
_TOP_REFRESH = 1.0
#: How long a top-screen repaint holds the loop.  Measured ~39 ms on the RG DS
#: (a screenful of rows, each with artwork and text) -- most of a 66 ms video
#: frame interval, so a repaint that starts just before a frame is due shows up
#: as a stutter.  See :meth:`App._draw`.
_TOP_DRAW_COST = 0.05
#: A postponed repaint is forced after this much extra delay, so a fast video
#: cannot starve the top screen forever.
_TOP_GRACE = 0.25
#: How long a single input wait may last.  Sleeping the whole ``_TARGET_FPS``
#: here delays a decoded video frame by up to 33 ms -- the pump publishes on its
#: own clock, so the loop has to come back and look for it.  8 ms costs about
#: four wake-ups per frame and removes that delay.
_POLL_SLICE = 0.008
#: How long the hand-off splash keeps spinning before we exit and the bootstrap
#: launches the emulator (DESIGN §8): long enough to read "正在启动", not so long
#: it delays the game.  The spinner animates for this whole window.
_LAUNCH_SPIN_SECONDS = 0.9
_SPIN_FRAME = 0.05
#: Cursor moves repaint only the selection highlight (~3 ms).  Refreshing the
#: game's backdrop (a synchronous fanart decode) on every move is what made fast
#: scrolling stutter, so the backdrop is deferred until the selection rests for
#: this long -- long enough to skip it while scrolling, short enough that it
#: appears the instant the player stops (DESIGN §9.2).
_BACKDROP_DEBOUNCE = 0.12
#: Same idea for the bottom detail strip's *static* content (cover + metadata).
#: The video frame stays real-time, but decoding a cover on every cursor move is
#: what made fast scrolling stutter, so the static part catches up only after the
#: selection rests (DESIGN §9.2).
_STRIP_DEBOUNCE = 0.12
#: Width of the single-screen detail strip's artwork slot, in reference px.
#: 16:9 in a 118 px strip, so a clip fills the slot instead of being
#: letterboxed into a cover-shaped box.
_STRIP_ART_W = 160
#: Characters of description the strip will even try to fit.  Real blurbs run
#: to 400+; measuring them costs more than the whole frame budget.
_STRIP_DESC_CHARS = 60


class App:
    """Wires platform, library and session into a running frontend."""

    def __init__(
        self,
        platform: Platform,
        config: Config,
        translator: Translator,
        library: Library,
        *,
        video: VideoPlayer | None = None,
    ) -> None:
        self.platform = platform
        self.config = config
        self.translator = translator
        self.library = library
        self.art = ArtProvider(library, platform)
        self.session = Session(library, config, translator)
        # The library is already scoped to one root, so the session only needs
        # these to offer a switch -- and only when there is more than one card.
        self.session.rom_roots = platform.available_rom_roots()
        self.session.current_rom_root = platform.rom_root
        # Injected by tests and by the screenshot tool (which wants no ffmpeg).
        self._video = video or VideoPlayer(platform, VideoSettings.from_config(config))
        self._canvases: list = []
        self._painters: list[Painter] = []
        self._running = True
        #: Called once, just after the first frame is on screen.  Startup work
        #: that is not needed for that frame (re-listing the ROM tree) goes
        #: here: running it alongside the first paint doubled its cost.
        self.on_ready: Callable[[], None] | None = None
        self._launch_plan = None
        self._launching = False
        self._launch_resident_mode = False
        self._launch_game_name = ""
        self._launch_at = 0.0
        self._restart_ui = False
        self._power_request: str | None = None
        self._top_at = 0.0
        self._bottom_at = 0.0
        self._bottom_seq = -1
        self._dual = False
        #: What each screen last painted; a change means "repaint me".
        self._top_key: tuple | None = None
        self._bottom_key: tuple | None = None
        #: Last painted top panel *without* the selection highlight, so moving
        #: the cursor only repaints one row/cell (see :meth:`_paint_top`).
        self._top_cache: object | None = None
        #: Structural signature (everything but the selection cursor) of the
        #: cached panel.
        self._top_struct: tuple = ()
        #: Backdrop (fanart/screenshot) of the last fully-painted game.  Cursor
        #: moves repaint only the highlight; the backdrop refreshes after a pause
        #: so fast scrolling stays inside the 33 ms frame budget (see :meth:`_draw`).
        self._top_backdrop = None
        self._backdrop_pending = False
        self._backdrop_at = 0.0
        #: Static (cover + metadata) part of the bottom detail strip, debounced
        #: like the backdrop so fast scrolling does not decode a cover per move.
        self._strip_state_pending = False
        self._strip_state_at = 0.0
        #: Same as ``_strip_state_*`` but for the dual-screen bottom panel, which
        #: is only used when there are two painters (see :meth:`_bottom_due`).
        self._bottom_state_pending = False
        self._bottom_state_at = 0.0
        #: Selection cursor at the time the cached panel was painted.
        self._top_sel: int = -1
        #: First visible row/cell of the cached games panel.
        self._top_first: int = 0
        #: First visible row/cell produced by the most recent full paint.
        self._last_first: int = 0
        #: Set by :meth:`_handle` when an event changed something the state key
        #: does not cover, e.g. the selected game's favourite flag.
        self._top_dirty = False
        #: Where the player was standing.  A game runs as a *different* process
        #: and the bootstrap starts us fresh afterwards, so the only way back
        #: is through this file (DESIGN §8.2).
        self._state_path = Path(platform.config_dir) / "state.json"
        self._pending_resume = read_state(self._state_path).get("resume")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def run(self, *, max_frames: int | None = None) -> int:
        """Run until quit.  ``max_frames`` bounds it (tests, screenshots)."""
        canvases = self.platform.init_display(self.config.screen_mode)
        dual = len(canvases) > 1
        metrics = metrics_for(*canvases[0].size)
        self.session.attach_metrics(metrics, single=not dual)

        self._canvases = canvases
        self._dual = dual
        self._painters = [
            Painter(canvas, metrics_for(*canvas.size), self.platform, self.translator)
            for canvas in canvases
        ]
        self._painters[0].single = not dual
        # Backlight is saved per panel; only the platform knows how to set it.
        self._apply_brightness()

        # Decode at the size the media box actually draws (no per-frame resize).
        # One screen has no bottom panel, so the clip plays in the detail
        # strip's artwork slot instead (DESIGN §11).
        size = (bottom.media_inner_size(metrics_for(*canvases[1].size)) if dual
                else self._strip_art_size(metrics))
        self._video.configure(size=size, enabled=self.config.bottom_video)

        last_frame = 0.0
        frames = 0
        try:
            while self._running:
                now = time.monotonic()
                if self._launching:
                    # Hand-off splash: keep spinning it so the player sees motion.
                    # Resident devices hand off by hiding the windows (no exit), so
                    # the spin ends by running the game rather than stopping the loop.
                    self._draw(now)
                    if now - self._launch_at >= _LAUNCH_SPIN_SECONDS:
                        if self._launch_resident_mode:
                            self._launch_resident(self._launch_plan)
                        else:
                            self._running = False
                        self._launching = False
                        self._launch_resident_mode = False
                    time.sleep(_SPIN_FRAME)
                    continue
                self._resume_once()
                # Sleep only until the next frame is due, and never longer than
                # ``_POLL_SLICE``: a video frame can land at any moment and has
                # to be picked up promptly, but the frame itself must still land
                # on a 33 ms boundary rather than on a multiple of the slice.
                remaining = _TARGET_FPS - (now - last_frame)
                timeout = min(_POLL_SLICE, remaining) if remaining > 0 else 0.0
                for event in self.platform.poll_events(timeout=timeout):
                    self._handle(event)

                # After input, before drawing: the frame the player sees has to
                # already use the palette/translation they just picked.  Doing
                # this at the top of the loop instead meant a change made on the
                # last frame of a bounded run was never applied at all.
                self._tick_settings()

                if now - last_frame < _TARGET_FPS:
                    # Sleep the sliver that is actually left, never a flat 2 ms:
                    # overshooting the boundary every frame is what held the
                    # loop just under 30 fps (29.2 measured).
                    time.sleep(min(0.002, _TARGET_FPS - (now - last_frame)))
                    continue
                last_frame = now
                self._tick_video()
                self._draw(now)
                frames += 1
                if frames == 1:
                    self._fire_ready()
                if max_frames is not None and frames >= max_frames:
                    break
        finally:
            # Stop the decoder but keep the feature alive: the screenshot tool
            # calls run() once per screen.  Launching a game closes it for good
            # (DESIGN §8.1 step ③, see ``_launch``).
            self._video.stop()
            self.platform.shutdown()

        if self._launch_plan is not None:
            return EXIT_RESTART
        if self._restart_ui:
            return EXIT_RESTART_UI
        # Honour a power request *before* any state write.  The card can be
        # mounted read-only (vfat ``errors=remount-ro``), in which case saving
        # would raise and -- before this reorder -- abort the call below, so the
        # device would only quit the app instead of actually rebooting/powering
        # off.  A power action must never depend on a writable filesystem.
        if self._power_request == "reboot":
            self.platform.reboot()
        elif self._power_request == "poweroff":
            self.platform.power_off()
        # A plain quit remembers the place too: opening the app again should
        # feel like picking up where you left off, not like a cold boot.  Best
        # effort only -- a read-only card must not crash the shutdown path.
        try:
            self._save_resume()
        except OSError as exc:
            log.warning("could not persist resume state (%s); continuing", exc)
        return EXIT_OK

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    def _handle(self, event: InputEvent) -> None:
        outcome = self.session.handle(event)
        if outcome.quit:
            self._running = False
            return
        if outcome.power is not None:
            # Release the display in the loop's ``finally`` first, then actually
            # power off / reboot -- so the OS command never runs while SDL still
            # owns the windows (EGL/Wayland state would be left dirty).
            self._power_request = outcome.power
            self._running = False
            return
        if outcome.redraw:
            # The state key does not cover everything the list shows -- toggling
            # a favourite changes no key -- so an explicit redraw also has to
            # drop the cached top panel.
            self._top_dirty = True
        if outcome.launch is not None:
            self._launch(outcome.launch)

    def _notify(self, message: str) -> None:
        self.session.notify(message)

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #

    def _tick_video(self) -> None:
        """Keep the decoder in sync with the selection (once per frame).

        Single screen included: there is no bottom panel, but the clip plays in
        the detail strip, so there is still something to decode.
        """
        # The settings dialog can switch video off while we are running.
        self._video.configure(enabled=self.config.bottom_video)
        game = None
        if self.session.modal == MODAL_SEARCH:
            # 搜索（筛选）时停止解码：切换结果不需要视频，关闭后由 select 恢复播放。
            game = None
        elif self.session.view == VIEW_GAMES:
            game = self.session.current_game()
        elif self.session.preview_mode and self.session.view == VIEW_PLATFORMS:
            # 双屏预览选中：下屏的游戏详情面板同样播放该游戏的片段。
            previews = self.session.preview_games()
            if previews:
                game = previews[min(self.session.preview_index, len(previews) - 1)]
        self._video.select(game)

    def _fire_ready(self) -> None:
        """Run :attr:`on_ready` exactly once, after the first painted frame."""
        callback, self.on_ready = self.on_ready, None
        if callback is not None:
            callback()

    def library_changed(self) -> None:
        """The ROM tree changed underneath us -- a background scan finished.

        Called from the scanning thread, so it only drops caches and raises a
        flag for the next frame; :meth:`_draw` does the repainting.
        """
        self.session.invalidate()
        self._top_cache = None      # the cached panel holds the old listing
        self._top_dirty = True

    def _draw(self, now: float) -> None:
        """Repaint only what changed; a full frame costs ~70 ms on the device.

        The top panel is the expensive one.  Its content is cached *without* a
        selection highlight, so moving the cursor within a page repaints a
        single row/cell (~3 ms) instead of the whole list (~39 ms).  See
        :meth:`_paint_full` / :meth:`_paint_incremental` / :meth:`_reuse`.
        """
        if self._launching:
            # Hand-off splash: the emulator takes a moment to appear, so show
            # "正在启动" instead of letting the last frame / a black screen sit
            # there with no sign the tap registered (DESIGN §8).
            self._draw_launch_overlay()
            return
        key = self._state_key()
        # Track the backdrop separately from the structural key: a different
        # game usually means a different fanart, and re-decoding it on every
        # cursor move is what made fast scrolling stutter.  Refresh it after a
        # pause (see the ``full`` test below) instead of on every move.
        bk = self._backdrop_key()
        if bk != self._top_backdrop:
            self._backdrop_pending = True
            self._backdrop_at = now
        due = key != self._top_key or self._top_dirty
        # Postpone a repaint that would land on top of a video frame: the frame
        # is published on the decoder's clock, so it waits for us.  ``overdue``
        # is the backstop that stops a never-ending video from starving the top.
        overdue = now - self._top_at >= _TOP_REFRESH + _TOP_GRACE
        next_frame = self._video.next_frame_in()
        # Only a *full* repaint is expensive enough to be worth waiting for.
        # Moving the cursor inside a page repaints one row (~3 ms); holding
        # that back for a frame that is about to land made the list feel slow
        # whenever a clip was playing, which is most of the time.
        full = (self._top_cache is None or self._top_dirty
                or self._struct_changed() or not self._same_page()
                or (self._backdrop_pending
                    and now - self._backdrop_at >= _BACKDROP_DEBOUNCE))
        blocked = (due and full and not overdue
                   and next_frame is not None and next_frame < _TOP_DRAW_COST)

        painter = self._painters[0]
        top_painted = False
        if not blocked:
            if due or self._top_cache is None:
                if full:
                    self._paint_full(painter)
                else:
                    self._paint_incremental(painter)
            else:
                self._reuse(painter)
            top_painted = True
            self._top_at = now
            self._top_key = key
            self._top_dirty = False
            if full:
                # A full repaint re-decodes the backdrop, so it is now current.
                self._top_backdrop = bk
                self._backdrop_pending = False

        if len(self._painters) < 2:
            # One panel.  The strip is baked into the top cache (see
            # _paint_full), so a cache restore brings it back for free --
            # repainting it every frame measured 38 ms on the device, more than
            # a whole frame budget.  A new clip frame is real-time (the video
            # plays), but the static part -- cover + metadata -- is debounced
            # exactly like the backdrop: decoding a cover on every cursor move
            # is what made fast scrolling stutter, so it catches up only after
            # the selection rests (DESIGN §9.2).
            state_due = key != self._bottom_key
            if state_due:
                if not self._strip_state_pending:
                    self._strip_state_at = now
                self._strip_state_pending = True
            video_due = self._video.frame_seq != self._bottom_seq
            state_ready = (self._strip_state_pending
                           and now - self._strip_state_at >= _STRIP_DEBOUNCE)
            strip_due = video_due or state_ready
            if top_painted and not strip_due:
                self._draw_overlays(painter)
                status_bar(painter, dual=False)
                self.platform.present(0)
            else:
                if not top_painted:
                    painter.canvas.pil_image.paste(self._top_cache)
                    self._draw_selection(painter, only=self._top_sel)
                self._draw_detail_strip(painter)
                self._cache_strip(painter)
                self._draw_overlays(painter)
                status_bar(painter, dual=False)
                self.platform.present(0)
                if state_ready:
                    # The debounced static content is now current.
                    self._strip_state_pending = False
            self._bottom_at = now
            self._bottom_key = key
            self._bottom_seq = self._video.frame_seq
            return

        if top_painted:
            self._draw_overlays(painter)
            status_bar(painter, dual=True)
            self.platform.present(0)
        if self._bottom_due(now, key):
            self._draw_bottom(self._painters[1])
            self.platform.present(1)
            self._bottom_at = now
            self._bottom_key = key
            self._bottom_seq = self._video.frame_seq

    def _draw_launch_overlay(self) -> None:
        """Full-screen splash shown while we hand the device over to a game.

        The emulator can take a few seconds to paint its first frame on a slow
        box; without this the player stares at the frozen last UI frame (or a
        black screen after the display is released) with no clue the tap worked.
        Painted on every screen so dual-panel devices show it on both -- the
        bottom panel would otherwise stay frozen on the old list while the top
        spins.
        """
        title = self.translator.t("launch.starting", name=self._launch_game_name)
        angle = (time.monotonic() * 7.0) % (2 * math.pi)
        for i, painter in enumerate(self._painters):
            w, h = painter.canvas.size
            painter.clear((16, 16, 20))
            size = 22
            tw = painter.text_width(title, size=size)
            painter.text(((w - tw) / 2, h * 0.40), title, size=size,
                         fill=(236, 236, 236), anchor="la")
            cx, cy, r = w / 2, h * 0.55, 24
            # NOTE: Painter.ellipse takes (x, y, w, h), not (x0, y0, x1, y1).
            painter.ellipse((cx - r, cy - r, 2 * r, 2 * r),
                            outline=(122, 122, 122), width=4)
            px = cx + (r - 2) * math.cos(angle)
            py = cy + (r - 2) * math.sin(angle)
            pr = 5
            painter.ellipse((px - pr, py - pr, 2 * pr, 2 * pr),
                            fill=(236, 236, 236))
            self.platform.present(i)

    def _state_key(self) -> tuple:
        session = self.session
        return (
            session.view, session.layout, session.platform_index, session.game_index,
            session.sort, session.modal, session.menu_index,
            session.exit_selected, session.active_toast(),
            len(session.system_keys()),
        )

    def _bottom_due(self, now: float, key: tuple) -> bool:
        """Video drives the bottom panel in real time; the static part (cover +
        metadata) is debounced so fast scrolling does not decode a cover on every
        move -- it catches up after the selection rests (DESIGN §9.2).

        A new clip frame repaints immediately (the video plays); a selection
        change only repaints once it has been still for ``_STRIP_DEBOUNCE``.
        """
        video_due = self._video.frame_seq != self._bottom_seq
        state_due = key != self._bottom_key
        if state_due:
            if not self._bottom_state_pending:
                self._bottom_state_at = now
            self._bottom_state_pending = True
        state_ready = (self._bottom_state_pending
                       and now - self._bottom_state_at >= _STRIP_DEBOUNCE)
        if video_due:
            # The bottom panel is being repainted anyway (and now shows the
            # current game), so the debounced static content is current too.
            self._bottom_state_pending = False
            return True
        if state_ready:
            self._bottom_state_pending = False
            return True
        return False

    def _draw_top(self, painter: Painter, highlight: bool = True) -> None:
        """The top panel's content, painted *without* the selection highlight.

        The highlight is added afterwards by :meth:`_draw_selection`, so the
        painted result can be cached and the cursor moved cheaply.  Overlays
        (menu, toast) are drawn by :meth:`_draw_overlays` on top.
        """
        painter.clear()
        if self.session.view == VIEW_GAMES:
            self._last_first = self._draw_games(painter, highlight=highlight)
        else:
            self._draw_home(painter)
        version_tag(painter)

    # -- cached-panel repaint strategies ----------------------------------- #

    def _top_index(self) -> int:
        return self.session.game_index if self.session.view == VIEW_GAMES else -1

    def _struct_key(self) -> tuple:
        """Structural signature: everything but the selection cursor *and* the
        backdrop.

        The cursor is dropped so moving within a page repaints only the
        highlight (~3 ms).  The backdrop is dropped too: it changes with the
        game, but re-decoding the fanart on every move stutters fast scrolling,
        so :meth:`_draw` tracks it separately and refreshes it after a pause
        (``_BACKDROP_DEBOUNCE``) instead of forcing a full repaint per move.
        """
        key = self._state_key()
        return key[:3] + key[4:]

    def _backdrop_key(self) -> object:
        """The backdrop behind the current game, or None when there is none.

        Cheap to recompute: it reads two asset paths already resolved on the
        game, never decodes.  Returning the asset path (not just a flag) means
        two games that both have a fanart invalidate the cache only when the
        actual picture changes.
        """
        if self.session.view != VIEW_GAMES:
            return None
        games = self.session.games()
        if not games:
            return None
        index = self.session.game_index
        if not (0 <= index < len(games)):
            return None
        game = games[index]
        for kind in ("fanart", "screenshot"):
            if game.asset(kind) is not None:
                return (kind, game.asset(kind))
        return ("none", game.key)

    def _struct_changed(self) -> bool:
        return self._top_struct != self._struct_key()

    def _first_for(self, index: int) -> int | None:
        """First visible slot of ``index`` in the games view, or None for views
        that always repaint fully (home, carousel)."""
        session = self.session
        if session.view != VIEW_GAMES:
            return None
        games = session.games()
        if not games:
            return 0
        single = self._painters[0].single
        m = self._painters[0].metrics
        if session.layout == "grid":
            per_page = m.grid_cols * m.grid_rows(single=single)
            return (index // per_page) * per_page
        if session.layout == "carousel":
            return None  # the centred card moves, shifting every neighbour
        rpp = m.rows_per_page(single=single)
        return (index // rpp) * rpp

    def _same_page(self) -> bool:
        first = self._first_for(self._top_index())
        return first is not None and first == self._top_first

    def _paint_full(self, painter: Painter) -> None:
        self._draw_top(painter, highlight=False)
        # One panel: bake the strip into the cache.  It lives on this canvas,
        # so a restore has to bring it back -- otherwise every frame would have
        # to repaint it, and a strip costs more than a whole frame budget.
        if painter.single:
            self._draw_detail_strip(painter)
        self._top_cache = painter.canvas.pil_image.copy()
        self._draw_selection(painter, only=self._top_index())
        self._top_struct = self._struct_key()
        self._top_sel = self._top_index()
        self._top_first = self._last_first

    def _paint_incremental(self, painter: Painter) -> None:
        painter.canvas.pil_image.paste(self._top_cache)
        self._draw_selection(painter, only=self._top_index())
        self._top_sel = self._top_index()

    def _reuse(self, painter: Painter) -> None:
        painter.canvas.pil_image.paste(self._top_cache)
        self._draw_selection(painter, only=self._top_sel)

    def _draw_selection(self, painter: Painter, *, only: int) -> None:
        """Paint the selection highlight (and scrollbar) over the cached panel."""
        session = self.session
        if session.view != VIEW_GAMES or only < 0:
            return
        all_games = session.games()
        if not all_games:
            return
        m = painter.metrics
        index = session.game_index
        if session.layout == "grid":
            cols, rows = m.grid_cols, m.grid_rows(single=painter.single)
            games.draw_grid(painter, self.art, all_games, index,
                            cols=cols, rows=rows, highlight=True, only=only)
            games.draw_scrollbar(painter, index, len(all_games), cols * rows,
                                 m.content_h(single=painter.single))
        elif session.layout == "carousel":
            games.draw_carousel(painter, self.art, all_games, index,
                                highlight=True, only=only)
        else:
            rpp = m.rows_per_page(single=painter.single)
            games.draw_list(painter, self.art, all_games, index,
                            rows_per_page=rpp, highlight=True, only=only)
            games.draw_scrollbar(painter, index, len(all_games), rpp,
                                 m.content_h(single=painter.single))

    def _draw_overlays(self, painter: Painter) -> None:
        session = self.session
        if session.modal == MODAL_MENU:
            menu.draw(painter, session)
        elif session.modal == MODAL_EXIT:
            menu.draw_exit(painter, session)
        elif session.modal == MODAL_ROM_SELECT:
            menu.draw_rom_select(painter, session)
        elif session.modal == MODAL_SEARCH:
            search.draw(painter, self.art, session)
        message = session.active_toast()
        if message:
            toast(painter, message)

    def _draw_home(self, painter: Painter) -> None:
        session = self.session
        key = session.current_system_key()
        if key not in ("ALL", "FAV", "RECENT"):
            # Warm the selected system so the preview strip and the ROM count
            # are ready before the player scrolls onto them.
            self.library.load_games(key)

        tiles = self._home_tiles()
        index = session.platform_index % max(1, len(tiles))

        home.draw(
            painter,
            self.art,
            tiles=tiles,
            index=index,
            info_title=display_name(key, self.translator.language),
            info_subtitle=self._info_subtitle(key),
            info_right=self._info_right(key),
            previews=self._previews(key),
            preview_index=session.preview_index if session.preview_mode else -1,
            hints=self._platform_hints(),
        )

    def _platform_hints(self) -> list[tuple[str, str]]:
        """平台总览的按键提示；预览选中模式换成预览操作的提示。"""
        if self.session.preview_mode:
            return [("A", self.translator("btn.enter")),
                    ("UP", self.translator("btn.back")),
                    ("START", self.translator("btn.menu"))]
        return [("A", self.translator("btn.enter")),
                ("DOWN", self.translator("home.preview")),
                ("START", self.translator("btn.menu"))]

    def _draw_games(self, painter: Painter, *, highlight: bool = True) -> int:
        session = self.session
        all_games = session.games()
        title = self._system_title()
        hints = games.footer_hints(painter, session.layout, self.translator)
        painter.backdrop = False

        if not all_games:
            games.header(painter, title=title, subtitle="0", right="")
            painter.text(
                (painter.width // 2, painter.height // 2),
                painter.translator("games.empty"),
                size=14, fill=(74, 74, 80, 255), anchor="mm",
            )
            button_bar(painter, hints)
            return 0

        subtitle = str(len(all_games))
        right = self.translator('games.layout_' + session.layout)

        index = session.game_index
        if 0 <= index < len(all_games):
            games.draw_backdrop(painter, self.art, all_games[index])
        games.header(painter, title=title, subtitle=subtitle, right=right)

        m = painter.metrics
        index = session.game_index
        if session.layout == "grid":
            first = games.draw_grid(painter, self.art, all_games, index,
                                    cols=m.grid_cols, rows=m.grid_rows(single=painter.single),
                                    highlight=highlight)
        elif session.layout == "carousel":
            first = games.draw_carousel(painter, self.art, all_games, index,
                                        highlight=highlight)
        else:
            first = games.draw_list(painter, self.art, all_games, index,
                                    rows_per_page=m.rows_per_page(single=painter.single),
                                    highlight=highlight)
        button_bar(painter, hints)
        return first

    def _strip_art_box(self, m) -> tuple[int, int, int, int]:
        """The detail strip's artwork slot, in absolute pixels.

        Cover and clip share it, and the decoder is sized from it, so all three
        have to agree on one number.
        """
        return (m.u(8) + m.u(10),
                m.content_top + m.content_h(single=True) + m.u(10),
                m.u(_STRIP_ART_W),
                m.strip_h - 2 * m.u(10))

    def _strip_art_size(self, m) -> tuple[int, int]:
        """Decode size for the strip's slot (no per-frame resize)."""
        _x, _y, w, h = self._strip_art_box(m)
        return (w, h)

    def _draw_detail_strip(self, painter: Painter) -> None:
        """The bottom screen's essentials, folded under the list (DESIGN §11).

        ``content_h(single=True)`` already reserves the room; this fills it.  It
        is a summary rather than a copy of ``bottom.py`` -- the full panel layout
        (media box, metadata, hints) does not fit in 118px.
        """
        m = painter.metrics
        session = self.session
        x = m.u(8)
        w = m.width - 2 * m.u(8)
        y = m.content_top + m.content_h(single=True)
        h = m.strip_h

        painter.rounded_rect((x, y, w, h), radius=m.u(8),
                             fill=COLORS.panel, outline=COLORS.border)

        if session.view != VIEW_GAMES:
            # 平台总览（游戏库）：显示当前选中平台的预览，而非「这里还没有游戏」。
            # games.empty 只在进入某个平台、且该平台确实没有任何游戏时才应出现。
            key = session.current_system_key()
            painter.text((x + m.u(14), y + m.u(22)),
                         display_name(key, self.translator.language),
                         size=14, fill=COLORS.text, anchor="lm")
            count = self._tile_subtitle(key)
            if count:
                painter.text((x + m.u(14), y + m.u(44)), str(count),
                             size=12, fill=COLORS.text_dim, anchor="lm")
            previews = self._previews(key)
            selected = session.preview_index if session.preview_mode else -1
            px = x + m.u(14)
            py = y + m.u(60)
            pw, ph = m.u(88), m.u(50)
            gap = m.u(8)
            for position, preview_game in enumerate(previews[:6]):
                games.cover_art(painter, self.art, preview_game, (px, py, pw, ph))
                if position == selected:
                    painter.rounded_rect(
                        (px - m.u(2), py - m.u(2), pw + m.u(4), ph + m.u(4)),
                        radius=m.u(5), outline=COLORS.accent,
                    )
                px += pw + gap
                if px + pw > x + w:
                    break
            return

        game = session.current_game()
        meta = self._meta(game) if game is not None else None
        if game is None or meta is None:
            return

        # The clip plays where the cover would be (DESIGN §6.5: video first,
        # cover only as the fallback).  A decoder needs the debounce interval
        # plus a frame before there is anything to draw, though -- showing the
        # cover for that moment flashed one on every game that has a clip.
        art = self._strip_art_box(m)
        frame = self._video.frame()
        painter.rounded_rect(art, radius=m.u(8), fill=(14, 14, 16, 255),
                             outline=(232, 163, 61, 90) if frame is not None else COLORS.border)
        if frame is not None:
            painter.image_fit(frame, art)
            bottom.progress_bar(painter, art[0], art[1] + art[3] - m.u(3),
                                art[2], m.u(3), self._video.progress())
        elif not self._video.is_pending(game.key):
            games.cover_art(painter, self.art, game, art)

        text_x = art[0] + art[2] + m.u(10)
        text_w = max(m.u(20), x + w - m.u(10) - text_x - (m.u(18) if game.favorite else 0))
        lines = (
            (game.display_name, 14, COLORS.text),
            (f"{meta.system_label} · {meta.publisher}", 11, COLORS.text_dim),
            (f"{meta.genre} · {meta.players} · {meta.release}", 11, COLORS.text_dim),
            # The strip is ~426px wide at 11px, so roughly 38 characters fit.
            # Hand ellipsize a string of that order rather than a whole blurb:
            # even a binary search has to measure what it is given.
            ((meta.description or "")[:_STRIP_DESC_CHARS], 11, COLORS.text_dim),
        )
        for index, (text, size, colour) in enumerate(lines):
            painter.text(
                (text_x, y + m.u(24) + index * m.u(25)),
                painter.ellipsize(text, size=size, max_width=text_w),
                size=size, fill=colour, anchor="lm",
            )
        if game.favorite:
            painter.text((x + w - m.u(12), y + m.u(24)), "★", size=13,
                         fill=COLORS.accent, anchor="rm")

    def _cache_strip(self, painter: Painter) -> None:
        """Copy the strip just drawn into the cached panel.

        The strip is baked into the top cache (see :meth:`_paint_full`) so a
        restore brings it back for free.  But the strip changes on every clip
        frame while the cache is only rebuilt on a full repaint, so the copy
        goes stale: a restore then put the *old* frame back -- or the cover,
        from before the clip started.  At 30 fps against a 15 fps clip that
        alternates every other frame, which is the flicker.
        """
        cache = self._top_cache
        if cache is None:
            return
        m = painter.metrics
        top = m.content_top + m.content_h(single=True)
        cache.paste(
            painter.canvas.pil_image.crop((0, top, m.width, top + m.strip_h)),
            (0, top),
        )

    def _draw_bottom(self, painter: Painter) -> None:
        session = self.session
        key = session.current_system_key()
        # 双屏预览选中：详情面板展示预览选中的游戏（渲染路径与游戏详情相同）。
        previewing = session.preview_mode and session.view == VIEW_PLATFORMS
        if session.modal == MODAL_SEARCH:
            # 双屏搜索选中：详情面板跟随搜索结果的光标（与预览选中同一路径）。
            results = session.search_results()
            game = (
                results[min(session.search_result_index, len(results) - 1)]
                if results else None
            )
        elif previewing:
            previews = session.preview_games()
            game = previews[min(session.preview_index, len(previews) - 1)] if previews else None
        else:
            game = session.current_game() if session.view == VIEW_GAMES else None

        searching = session.modal == MODAL_SEARCH
        meta = self._meta(game) if game is not None else None
        # 搜索切换选中不驱动视频（上一个游戏的片段会与结果错位），详情走封面兜底。
        frame = None if searching else (self._video.frame() if game is not None else None)
        # 平台总览的工具栏标题带上该平台的游戏数量；聚合视图没有单一数量。
        game_count = (
            self.library.rom_count(key)
            if game is None and key not in ("ALL", "FAV", "RECENT") else None
        )

        bottom.draw(
            painter,
            self.art,
            game,
            meta,
            key_label=display_name(key, self.translator.language),
            hints=self._platform_hints() if previewing else self._bottom_hints(),
            video_frame=frame,
            video_progress=self._video.progress() if frame is not None else None,
            clip_pending=(game is not None and not searching and self._video.is_pending(game.key)),
            system_desc=self._system_desc(key),
            game_count=game_count,
        )

    # ------------------------------------------------------------------ #
    # View models
    # ------------------------------------------------------------------ #

    def _home_tiles(self) -> list[home.Tile]:
        """One card per platform, using the artwork shipped with the app.

        These used to show the cover of each system's *first* game, which looked
        arbitrary and followed scan order.  The cards now come from
        ``assets/platforms/`` (see ``scripts/build_platform_art.py``).
        """
        return [
            home.Tile(
                key=key,
                title=display_name(key, self.translator.language),
                subtitle=self._tile_subtitle(key),
            )
            for key in self.session.system_keys()
        ]

    def _tile_subtitle(self, key: str) -> str:
        if key == "ALL":
            return str(self.library.last_scan.total_roms) if self.library.last_scan else ""
        if key in ("FAV", "RECENT"):
            return self.translator("bottom.games_total")
        return str(self.library.rom_count(key))

    def _info_subtitle(self, key: str) -> str:
        """信息行副标题：标题已是平台名，这里改显该平台游戏数量，不再重复。"""
        if key in ("ALL", "FAV", "RECENT"):
            return ""
        return self.translator("bottom.game_count", count=self.library.rom_count(key))

    def _info_right(self, key: str) -> str:
        if key in ("ALL", "FAV", "RECENT"):
            return ""
        definition = lookup(key)
        return "standalone" if definition.is_standalone else "RetroArch"

    def _previews(self, key: str) -> list[Game]:
        """预览条：与游戏列表同源（尊重筛选），按 最近游玩 > 收藏 > 名称 排序。"""
        return self.session.preview_games()

    def _system_desc(self, key: str) -> str:
        """平台总览文本：简介（多语言 ``system.desc.*``）+ 核心 + 支持格式。

        简介取自 ``assets/lang/*.json``：当前语言缺失时 Translator 自动回退到
        ``en_US``，新增语言只需补一份翻译。核心与支持格式始终附加在后。
        """
        if key in ("ALL", "FAV", "RECENT"):
            return ""  # 聚合视图没有单一平台介绍
        lines: list[str] = []
        # 固件目录名是大写（如 ``FC``），lang 里的 key 是表内小写（``fc``）。
        missing = f"system.desc.{key.casefold()}"
        desc = self.translator.t(missing)
        if desc != missing:
            lines.append(desc)
        definition = lookup(key)
        lines.append(f"{self.translator('bottom.core')}: {definition.core_label}")
        lines.append(
            f"{self.translator('bottom.formats')}: {', '.join(definition.extensions)}"
        )
        return "\n".join(lines)

    def _system_title(self) -> str:
        return display_name(self.session.current_system_key(), self.translator.language)

    def _source_note(self, game: Game) -> str:
        """Name the metadata files this game was actually built from.

        Both formats can sit on the same card, and the merge order decides what
        the player ends up seeing, so every contributing file is named.  A
        hard-coded "gamelist.xml" read as a promise we were not keeping on the
        many cards that only carry ``metadata.pegasus.txt``.
        """
        names: list[str] = []
        if "esde" in game.sources:
            names.append(self.translator("bottom.source_esde"))
        if "pegasus" in game.sources:
            names.append(self.translator("bottom.source_pegasus"))
        if not names:
            return self.translator("bottom.source_none")
        return self.translator("bottom.source", files=" + ".join(names))

    def _meta(self, game: Game) -> bottom.Meta | None:
        system_key = self.session.current_system_key()
        core = lookup(system_key).core_label
        stars = game.rating_stars
        last = game.last_played
        last_text = self.translator("bottom.today") if last and _is_today(last) else (
            self.translator("bottom.days_ago", days=_days_since(last)) if last else "-"
        )
        return bottom.Meta(
            name=game.display_name,
            system_label=display_name(system_key, self.translator.language),
            publisher=game.publisher or "-",
            rating_stars=stars,
            rating_value=f"{(game.rating or 0) * 5:.1f}",
            genre=game.genres[0] if game.genres else "-",
            players=game.players or "-",
            release=str(game.release) if game.release else "-",
            core=core,
            description=game.blurb or "-",
            play_count=f"{self.translator('bottom.play_count')}: {game.play_count}",
            last_played=f"{self.translator('bottom.last_played')}: {last_text}",
            source_note=self._source_note(game),
            favorite=game.favorite,
        )

    def _bottom_hints(self) -> list[tuple[str, str]]:
        return [
            ("A", self.translator("btn.start")),
            ("B", self.translator("btn.back")),
            ("Y", self.translator("btn.favorite")),
            ("X", self.translator("btn.view")),
        ]

    # ------------------------------------------------------------------ #
    # Launching
    # ------------------------------------------------------------------ #

    def _launch(self, game: Game) -> None:
        if self._launching:
            return  # already handing off; ignore further input
        system_key = self.session.current_system_key()
        try:
            plan = build_plan(game, self.config)
        except LaunchError as exc:
            log.error("launch failed: %s", exc)
            self.session.notify(str(exc))
            return

        game.play_count += 1
        game.last_played = _now()
        self.library.save_state(game, Session.system_of(game))
        self.config.save(Path(self.platform.config_dir) / "config.json")

        log.info("launching %s via %s", game.key, plan.core_label)
        # Save the place first (DESIGN §8.1 step ①): with enough RAM we stay
        # alive and come back to it; without, the bootstrap restores it.
        self._save_resume()

        if self.platform.can_stay_resident():
            # Resident path: still show the hand-off splash so the player sees
            # motion while the emulator spins up.  run()'s launch spin paints it
            # for _LAUNCH_SPIN_SECONDS, then calls _launch_resident() -- which
            # hides the windows and runs the game -- instead of exiting (DESIGN §8).
            self._launch_resident_mode = True
            self._launching = True
            self._launch_game_name = game.name
            self._launch_at = time.monotonic()
            self._launch_plan = plan
            return

        # Low-memory device, or a platform that cannot hide its windows: hand
        # over by exiting, the way retrostation.sh expects (DESIGN §8.2).
        # Kill the decoder *before* handing over: the hand-off is a file the
        # bootstrap runs once we exit, and an inherited ffmpeg would keep
        # decoding behind the game.
        self._video.close()
        # Show the hand-off splash and keep spinning it: the emulator's first
        # frame is seconds away on a slow box, and the frozen last UI frame (or
        # black screen) would otherwise look like a hang.  We do NOT set
        # ``_running = False`` here -- run() enters a spin loop that paints the
        # animation for _LAUNCH_SPIN_SECONDS, then exits so the bootstrap can
        # launch the emulator.  The launch command is already written, so the
        # brief delay only costs the player a beat of spinner, not game time.
        self._launching = True
        self._launch_game_name = game.name
        self._launch_at = time.monotonic()
        self._launch_plan = plan
        self.platform.launch_game(plan.argv)

    def _launch_resident(self, plan: LaunchPlan) -> None:
        """Run the game while this process stays alive (DESIGN §8.2 fast path).

        Hiding the windows hands the screen over in ~1 ms and destroys nothing,
        so coming back is free too: no process exit (whose kernel reclaim of
        the surfaces measured ~2 s here), no rescan, no window rebuild.  Only
        taken when the device has RAM to spare -- see
        :meth:`Platform.can_stay_resident`.
        """
        # Stop decoding for the duration of the game, but do not close() the
        # player: that would disable the bottom-screen clips for good, and we
        # are coming back.
        self._video.stop()
        log.info("staying resident: hiding windows for %s", plan.core_label)
        self.platform.suspend_display()
        try:
            result = subprocess.run(list(plan.argv))
            log.info("game exited with %s", result.returncode)
        except OSError as exc:
            log.error("could not start %s: %s", plan.argv, exc)
            self.session.notify(str(exc))
        finally:
            self.platform.resume_display()
            # The compositor may have dropped our buffers while we were hidden.
            self._top_dirty = True
            self._bottom_key = None


    # ------------------------------------------------------------------ #
    # Resume (DESIGN §8.1 step ① / §8.2)
    # ------------------------------------------------------------------ #

    def _save_resume(self) -> None:
        """Record where the player is, for the bootstrap to restore."""
        update_state(self._state_path, resume=self.session.capture_resume())

    def _resume_once(self) -> None:
        """Apply a pending resume snapshot once the scan has something to match.

        The snapshot is keyed by system and game, so it cannot be resolved
        until the background scan has filled the library -- before that
        ``Session.system_keys()`` is empty and every lookup would miss.
        """
        if self._pending_resume is None or self.library.last_scan is None:
            return
        snapshot, self._pending_resume = self._pending_resume, None
        if self.session.apply_resume(snapshot):
            log.info("resumed at %s", snapshot.get("game") or snapshot.get("system"))
            self._top_dirty = True

    def _apply_brightness(self) -> None:
        """Push the saved backlight to the panels (DESIGN §12).

        The second panel only exists in dual mode; there is nothing to set
        otherwise, and on this device writing to a missing node is a no-op we
        would rather not make a habit of.
        """
        top = self.config.brightness.get("top")
        bottom = self.config.brightness.get("bottom")
        if top is not None:
            self.platform.set_brightness(int(top), 0)
        if bottom is not None and self._dual:
            self.platform.set_brightness(int(bottom), 1)

    def _tick_settings(self) -> None:
        """Apply and persist whatever the settings dialog just changed.

        The Session only records that something happened: the palette is a
        shared instance, the backlight needs the platform, and the config path
        lives on the platform -- none of which the Session should reach for.
        Written here rather than per change, so dragging the backlight across
        its range is one save, not twenty.
        """
        if not self.session.settings_dirty:
            return
        self.session.settings_dirty = False
        COLORS.apply(self.config.theme, self.config.theme_variant)
        self._apply_brightness()
        # Preview sound: the rocker and the settings row both land here, and a
        # clip that is already sounding is swapped rather than left behind.
        self._video.configure(
            sound=self.config.video_sound,
            volume=max(0.0, min(1.0, self.config.video_volume / 100.0)),
        )
        # Boot autostart: only the flag file flips; the firmware hook was
        # patched idempotently the first time it was enabled, and is left alone
        # on disable -- so turning it off never rewrites the firmware script.
        self.platform.set_autostart(
            self.config.boot.enabled,
            target=self.config.boot.target,
            state_dir=self.config.boot.state_dir,
        )
        # Best effort: the card may be read-only, in which case persisting the
        # config must not crash the settings dialog.
        try:
            self.config.save(Path(self.platform.config_dir) / "config.json")
        except OSError as exc:
            log.warning("could not persist config (%s); continuing", exc)

        if self.session.restart_requested:
            if self.session.card_changed:
                # The snapshot names a game on the card we are leaving; keeping
                # it would restore us onto a ROM that is no longer mounted.
                try:
                    update_state(self._state_path, resume=None)
                except OSError as exc:
                    log.warning("could not clear resume state (%s)", exc)
                self.session.card_changed = False
            # Screen mode (or card) changed: we need new windows, and the only
            # safe way to get them is a fresh process.  Saved above, so the
            # bootstrap brings us back with the new setting already in place.
            self.session.restart_requested = False
            self._restart_ui = True
            self._running = False


def _now():
    from datetime import datetime

    return datetime.now()


def _is_today(value) -> bool:
    from datetime import datetime

    return value.date() == datetime.now().date()


def _days_since(value) -> int:
    from datetime import datetime

    return max(0, (datetime.now() - value).days)
