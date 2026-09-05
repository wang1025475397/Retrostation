"""UI state machine.

Pure logic: no drawing, no platform calls beyond the :class:`~...data.library.Library`
facade.  :meth:`Session.handle` turns one :class:`InputEvent` into an
:class:`Outcome`, which makes the whole interaction model unit-testable --
``tests/test_session.py`` drives it exactly the way a player would.

Navigation rules come from DESIGN §5.2; the notable ones:

* the game index is shared between the three views, so switching layout with X
  never loses your place;
* LEFT/RIGHT mean "previous/next system" on the home page but "page" in the
  list view and "step" in the carousel;
* MENU long-press opens the exit dialog anywhere.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass, field, fields
from pathlib import Path
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping

from ..core.config import LAYOUTS, SEARCH_BY, Config
from ..core.i18n import Translator, available_builtin
from .. import __version__
from ..core.model import Game
from ..core.pinyin import initials
from ..core.theme import THEMES, VARIANTS
from ..data.library import Library
from ..data.systems import AGGREGATE_KEYS, AGGREGATES, lookup
from ..platform.base import InputAction, InputEvent, InputKind

log = logging.getLogger(__name__)

VIEW_PLATFORMS = "platforms"
VIEW_GAMES = "games"

MODAL_NONE = ""
MODAL_MENU = "menu"
MODAL_EXIT = "exit"
MODAL_ROM_SELECT = "rom_select"
MODAL_SEARCH = "search"

#: The on-screen search keyboard, row-major, ``SEARCH_COLS`` per row: letters
#: and digits, then backspace / clear / close.  ABC order -- a d-pad user
#: scans alphabetically -- and the actions trail where the eye lands last.
SEARCH_COLS = 7
SEARCH_CODES: tuple[str, ...] = (
    *"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "BS", "CLR", "OFF",
)

#: Search match ranks.  A hit's rank is ``quality + field offset``, so the
#: display title always outranks the sortname, which outranks the bare ROM
#: file name -- within one field, a prefix beats a containment hit and a name
#: match beats an initials match.  The ROM name exists for English queries
#: (the pinyin initial of 拳皇97 will never spell KOF), but it must not push a
#: title hit off the first page.
_RANK_NAME_PREFIX = 0
_RANK_NAME_CONTAINS = 1
_RANK_INITIALS_PREFIX = 2
_RANK_INITIALS_CONTAINS = 3
_OFFSET_TITLE = 0
_OFFSET_STEM = 4
_RANK_MISS = 99


@lru_cache(maxsize=8192)
def _folded_name(text: str) -> str:
    """Upper-cased and full-width-folded: what containment matches against."""
    return unicodedata.normalize("NFKC", text).upper()


@lru_cache(maxsize=8192)
def _name_initials(text: str) -> str:
    """Pinyin / word initials of the folded name, cached per title."""
    return initials(_folded_name(text))


def _search_rank(game: Game, query: str, mode: str) -> int:
    """Best rank across the names ``config.search_by`` selects; ``_RANK_MISS``
    when nothing matches.  ``mode`` is one of :data:`SEARCH_BY`."""
    if mode == "rom":
        fields: tuple[tuple[int, str], ...] = ((_OFFSET_STEM, game.path.stem),)
    elif mode == "both":
        fields = (
            (_OFFSET_TITLE, game.display_name),
            (_OFFSET_STEM, game.path.stem),
        )
    else:  # "title", the default
        fields = ((_OFFSET_TITLE, game.display_name),)
    best = _RANK_MISS
    for offset, text in fields:
        if not text:
            continue
        name = _folded_name(text)
        name_ini = _name_initials(text)
        if name.startswith(query):
            best = min(best, _RANK_NAME_PREFIX + offset)
        elif query in name:
            best = min(best, _RANK_NAME_CONTAINS + offset)
        if name_ini.startswith(query):
            best = min(best, _RANK_INITIALS_PREFIX + offset)
        elif query in name_ini:
            best = min(best, _RANK_INITIALS_CONTAINS + offset)
    return best

SORTS = ("name", "play", "recent")

#: Settings-menu rows whose value cycles with LEFT/RIGHT (see
#: :meth:`Session._adjust_menu`).  Since the menu became a transaction, the
#: restart-grade rows (screen mode, card) cycle safely too: the arrows only
#: stage, and the restart happens when A commits.  "hide_game" is an action
#: and stays on A only.
_CYCLING_ROWS = frozenset(
    {"screen", "card", "layout", "bvideo", "video_sound", "sort",
     "show_hidden", "search_by", "theme", "variant", "language", "status_bar",
     "tcache", "autostart"}
)


def _cycle(options: tuple, current, direction: int):
    """One step through ``options``, forwards or backwards, wrapping."""
    return options[(options.index(current) + direction) % len(options)]

TOAST_SECONDS = 2.0

#: Page sizes for the list view (rows) and the carousel.
LIST_PAGE = 10
CAROUSEL_PAGE = 10

#: Backlight range in the device's own units (0-255 per panel).  The floor
#: matters: a screen driven to 0 looks like a crash and the player cannot find
#: the setting again to undo it.
BRIGHTNESS_MIN = 20
BRIGHTNESS_MAX = 255
BRIGHTNESS_STEP = 20


class View(str, Enum):
    PLATFORMS = VIEW_PLATFORMS
    GAMES = VIEW_GAMES


@dataclass
class Outcome:
    """What the app should do after handling one event."""

    redraw: bool = False
    quit: bool = False
    launch: Game | None = None
    #: System power request from the exit dialog: ``"reboot"`` or ``"poweroff"``.
    #: The app releases the display and then calls the platform, so the OS
    #: command never runs while we still own the SDL windows.
    power: str | None = None


@dataclass
class Session:
    """Everything the screens need to know what to draw."""

    library: Library
    config: Config
    translator: Translator
    clock: object = time.monotonic

    view: str = VIEW_PLATFORMS
    layout: str = "list"
    platform_index: int = 0
    game_index: int = 0
    sort: str = "name"

    #: 平台总览的预览条选中状态（SELECT 进入/退出，左右移动，A 进入游戏）。
    preview_mode: bool = False
    preview_index: int = 0
    #: preview_games() 的每帧缓存；任何输入都会走 invalidate() 清掉。
    _preview_cache: list | None = None

    modal: str = MODAL_NONE
    menu_index: int = 0
    exit_selected: int = 0
    #: ROM picker for multi-file games (Pegasus blocks listing several
    #: ``file:`` lines -- e.g. arcade hacks/clones).  ``rom_select_paths`` is
    #: ``[game.path, *game.variants]`` and ``rom_select_index`` the cursor.
    rom_select_index: int = 0
    rom_select_game: Game | None = None
    rom_select_paths: list = field(default_factory=list)

    #: Search state (SELECT+START).  ``search_origin`` is the system key (or
    #: aggregate key) the search started from: on the home page the search
    #: spans the whole library, inside a system just that system's games.
    search_text: str = ""
    search_kb: int = 0
    search_focus: str = "kb"      # "kb" or "results"
    search_result_index: int = 0
    search_origin: str = ""
    #: ``(text, results)`` -- filtering re-runs only when the query changed.
    _search_cache: tuple[str, list] | None = field(default=None, init=False, repr=False)

    #: Set when a settings row changed something that outlives the dialog: the
    #: app applies it (palette, backlight) and writes ``config.json``.  A
    #: Session has no business knowing where the SD card is mounted.
    settings_dirty: bool = False
    #: Menu transaction: ``(deep config copy, sort, filter, layout)`` taken
    #: when the dialog opens.  LEFT/RIGHT stage changes on the live fields (so
    #: the row labels follow); A commits the *effects* -- palette, backlight,
    #: language, library caches, restart flags -- in one go; B restores this
    #: snapshot and drops everything staged.
    _menu_stash: tuple | None = field(default=None, init=False, repr=False)
    #: Set when a change cannot take effect without new windows (screen mode).
    #: The app exits with ``EXIT_RESTART_UI`` and the bootstrap starts us again --
    #: rebuilding windows inside a running process is not an option (DESIGN §4.4).
    restart_requested: bool = False

    toast_message: str = ""
    toast_until: float = 0.0
    loaded: set = field(default_factory=set)

    # ------------------------------------------------------------------ #
    # Model access
    # ------------------------------------------------------------------ #

    def system_keys(self) -> list[str]:
        """Home-page entries: aggregates first, then systems that have ROMs."""
        order = {key: index for index, (key, _label, _zh) in enumerate(AGGREGATES)}
        keys = [key for key, _l, _z in AGGREGATES]
        with_roms = [key for key in self.library.system_keys() if key not in AGGREGATE_KEYS]
        return keys + sorted(with_roms, key=lambda key: (1, lookup(key).order, key))

    def system_count(self) -> int:
        return len(self.system_keys())

    def current_system_key(self) -> str:
        keys = self.system_keys()
        if not keys:
            return "ALL"
        return keys[self.platform_index % len(keys)]

    def is_aggregate(self) -> bool:
        return self.current_system_key() in AGGREGATE_KEYS

    def games(self) -> list[Game]:
        """The visible, filtered, sorted game list for the current system.

        Memoised for one frame: the top screen, the bottom screen and the input
        handler each ask for it, and sorting 600 ROMs three times per frame is
        measurable on the handheld.  Any input event drops the cache -- input is
        the only thing that can change what is visible.
        """
        if self._visible is None:
            self._visible = self._build_games()
        return self._visible

    def invalidate(self) -> None:
        """Drop the memoised list: the library changed without any input.

        Input normally invalidates it, but a background scan lands on its own
        -- without this the list kept showing whatever it had built before the
        scan finished.
        """
        self._visible = None
        self._preview_cache = None

    def _build_games(self) -> list[Game]:
        key = self.current_system_key()
        if key == "ALL":
            games = self.library.aggregate("ALL")
        elif key == "FAV":
            games = self.library.aggregate("FAV")
        elif key == "RECENT":
            games = self.library.aggregate("RECENT")
        else:
            games = list(self.library.resolve_all(key))

        if self.sort == "play":
            games.sort(key=lambda game: (-game.play_count, game.sort_key.casefold()))
        elif self.sort == "recent":
            games.sort(
                key=lambda game: (
                    -(game.last_played.timestamp() if game.last_played else 0),
                    game.sort_key.casefold(),
                )
            )
        else:
            games.sort(key=lambda game: game.sort_key.casefold())

        # Re-apply the hide rule here, not just when the library loads a system:
        # a game hidden a moment ago is still in that cached list, which was
        # filtered before the change.  Filtering again makes the entry vanish on
        # the very next frame instead of lingering until something else happens
        # to rebuild the cache -- and it costs one pass over the list, where a
        # rebuild would re-parse every metadata file for the system.
        if not self.config.show_hidden:
            games = [game for game in games if not game.hidden]

        if self._pending_game_key:
            for position, game in enumerate(games):
                if game.key == self._pending_game_key:
                    self.game_index = position
                    break
            self._pending_game_key = None
        return games

    def current_game(self) -> Game | None:
        games = self.games()
        if not games:
            return None
        return games[self.game_index % len(games)]

    # -- preview strip ----------------------------------------------------- #

    def preview_games(self) -> list[Game]:
        """预览条内容：当前列表的前 6 个，按 最近游玩 > 收藏 > 名称 排序。

        与 ``games()`` 同源（尊重筛选），但独立重排：预览优先露出玩家
        最近玩过的和收藏的。返回副本，不影响游戏列表自身的顺序。
        """
        if self._preview_cache is None:
            games = sorted(self.games(), key=self._preview_order)
            self._preview_cache = games[:6]
        return self._preview_cache

    @staticmethod
    def _preview_order(game: Game) -> tuple[int, float, str]:
        if game.last_played:
            return (0, -game.last_played.timestamp(), "")
        if game.favorite:
            return (1, 0.0, game.sort_key.casefold())
        return (2, 0.0, game.sort_key.casefold())

    # ------------------------------------------------------------------ #
    # Resume (DESIGN §8.1 step ① / §8.2)
    # ------------------------------------------------------------------ #

    def capture_resume(self) -> dict[str, str | None]:
        """Where the player is standing, in a form that survives a restart.

        Keyed by system and game rather than by index: the ROM count can change
        while the emulator is running and the aggregate views reorder
        themselves, so an index would point at a different game by the time we
        come back.
        """
        game = self.current_game() if self.view == VIEW_GAMES else None
        return {
            "view": self.view,
            "layout": self.layout,
            "sort": self.sort,
            "system": self.current_system_key(),
            "game": game.key if game is not None else None,
        }

    def apply_resume(self, data: Mapping[str, Any]) -> bool:
        """Restore a :meth:`capture_resume` snapshot; False when unusable.

        The system key is matched against what the scan actually found, so a
        card that no longer holds that system lands on the home page instead of
        on a wrong selection.
        """
        if not data:
            return False

        if data.get("layout") in LAYOUTS:
            self.layout = data["layout"]
        if data.get("sort") in SORTS:
            self.sort = data["sort"]

        keys = self.system_keys()
        system = data.get("system")
        if system not in keys:
            return False

        self.platform_index = keys.index(system)
        self.view = VIEW_GAMES if data.get("view") == VIEW_GAMES else VIEW_PLATFORMS
        if self.view == VIEW_GAMES and data.get("game"):
            self.game_index = 0
            self._pending_game_key = data["game"]
        self._visible = None
        return True

    # ------------------------------------------------------------------ #
    # Event handling
    # ------------------------------------------------------------------ #

    def handle(self, event: InputEvent) -> Outcome:
        """Dispatch one event; the UI redraws when ``Outcome.redraw`` is set."""
        self._visible = None  # any input may change what is on screen
        self._preview_cache = None  # 换平台后预览条必须换成新平台的预览

        # The rocker works everywhere, menus included: it is the one control a
        # player reaches for without looking at which screen they are on.
        if event.is_press and event.action in (
            InputAction.VOLUME_UP, InputAction.VOLUME_DOWN
        ):
            step = 1 if event.action is InputAction.VOLUME_UP else -1
            return self._adjust_volume(step)

        # Global escape hatch FIRST: MENU long press opens the quit dialog
        # from anywhere, modals included.  The search dialog used to swallow
        # it, which on the desktop left the window's close button dead -- the
        # close box synthesises exactly this event.
        if event.kind is InputKind.LONG_PRESS and event.action is InputAction.MENU:
            return self._open_exit_dialog()

        if self.modal == MODAL_EXIT:
            return self._handle_exit_modal(event)
        if self.modal == MODAL_MENU:
            return self._handle_menu_modal(event)
        if self.modal == MODAL_ROM_SELECT:
            return self._handle_rom_select_modal(event)
        if self.modal == MODAL_SEARCH:
            return self._handle_search_modal(event)

        if event.is_press and event.action is InputAction.SEARCH:
            return self._open_search()

        handlers = {
            VIEW_PLATFORMS: self._handle_platforms,
            VIEW_GAMES: self._handle_games,
        }
        return handlers[self.view](event)

    # -- home page -------------------------------------------------------- #

    def _handle_platforms(self, event: InputEvent) -> Outcome:
        action, kind = event.action, event.kind
        if not event.is_press:
            return Outcome()

        if self.preview_mode:
            # 预览行：左右移动选中，上键回平台行，A 进入游戏。
            if action in (InputAction.LEFT, InputAction.RIGHT, InputAction.L1, InputAction.R1):
                step = 1 if action in (InputAction.RIGHT, InputAction.R1) else -1
                count = len(self.preview_games())
                if count:
                    self.preview_index = max(0, min(count - 1, self.preview_index + step))
                return Outcome(redraw=True)
            if action is InputAction.A:
                return self._enter_preview_game()
            if action in (InputAction.UP, InputAction.B):
                self.preview_mode = False
                return Outcome(redraw=True)
            if action is InputAction.START:
                return self._open_menu()
            if action is InputAction.MENU:
                return self._open_exit_dialog()
            return Outcome()

        # 平台行：上/左右切换平台，下键进入预览选择。
        if action is InputAction.DOWN:
            if self.preview_games():
                self.preview_mode = True
                self.preview_index = 0
                return Outcome(redraw=True)
            return Outcome()
        if action is InputAction.UP:
            return self._move_platform(-1)
        if action in (InputAction.LEFT, InputAction.RIGHT, InputAction.L1, InputAction.R1):
            step = 1 if action in (InputAction.RIGHT, InputAction.R1) else -1
            return self._move_platform(step)
        if action is InputAction.A:
            return self._enter_games()
        if action is InputAction.START:
            return self._open_menu()
        if action is InputAction.MENU:
            return self._open_exit_dialog()
        return Outcome()

    def _enter_preview_game(self) -> Outcome:
        """直接启动预览选中的游戏。

        仍然先把视图切到该游戏（view / game_index），这样退出模拟器后的
        「回到上次玩的地方」记录的是这个游戏，而不是平台轮播。
        """
        previews = self.preview_games()
        if not previews:
            return Outcome()
        target = previews[min(self.preview_index, len(previews) - 1)]
        if target.is_multi:
            return self._open_rom_select(target)
        games = self.games()
        for position, game in enumerate(games):
            if game.path == target.path:
                self.view = VIEW_GAMES
                self.layout = self.config.layout
                self.game_index = position
                self.preview_mode = False
                return Outcome(launch=game)
        return Outcome()

    def _move_platform(self, step: int) -> Outcome:
        count = self.system_count()
        if count == 0:
            return Outcome()
        self.platform_index = (self.platform_index + step) % count
        self.game_index = 0
        return Outcome(redraw=True)

    def _enter_games(self) -> Outcome:
        self.view = VIEW_GAMES
        self.game_index = 0
        self.layout = self.config.layout
        return Outcome(redraw=True)

    # -- game page -------------------------------------------------------- #

    def _handle_games(self, event: InputEvent) -> Outcome:
        if not event.is_press:
            return Outcome()
        action = event.action
        games = self.games()
        count = len(games)
        if count == 0:
            if action is InputAction.B:
                return self._back_to_platforms()
            return Outcome()
        self.game_index %= count

        if action is InputAction.UP:
            return self._move_game(-self._vertical_step())
        if action is InputAction.DOWN:
            return self._move_game(self._vertical_step())
        if action in (InputAction.LEFT, InputAction.RIGHT):
            step = 1 if action is InputAction.RIGHT else -1
            return self._move_game(step * (LIST_PAGE if self.layout == "list" else 1))
        if action in (InputAction.L1, InputAction.R1):
            step = 1 if action is InputAction.R1 else -1
            return self._move_game(step * self._page_size())
        if action in (InputAction.L2, InputAction.R2):
            self.game_index = 0 if action is InputAction.L2 else count - 1
            return Outcome(redraw=True)
        if action is InputAction.A:
            return self._pick_or_launch(self.current_game())
        if action is InputAction.B:
            return self._back_to_platforms()
        if action is InputAction.Y:
            return self._toggle_favorite()
        if action is InputAction.HIDE:
            return self._toggle_hidden()
        if action is InputAction.X:
            return self._cycle_layout()
        if action is InputAction.START:
            return self._open_menu()
        if action is InputAction.MENU:
            return self._open_exit_dialog()
        return Outcome()

    def _vertical_step(self) -> int:
        if self.layout == "grid":
            return self._grid_cols()
        if self.layout == "carousel":
            return CAROUSEL_PAGE
        return 1

    def _page_size(self) -> int:
        if self.layout == "grid":
            return self._grid_cols() * self._grid_rows()
        return LIST_PAGE

    def _grid_cols(self) -> int:
        return self._metrics.grid_cols if self._metrics else 4

    def _grid_rows(self) -> int:
        return self._metrics.grid_rows(single=self._single) if self._metrics else 3

    #: Metrics are injected by the app; the session only needs them to know
    #: how many columns a grid page holds.
    _metrics: object | None = None
    _single: bool = False
    #: Storage cards present on this device, as ``(path, label)``, injected by
    #: the app.  Fewer than two means there is nothing to switch between and
    #: the row stays out of the menu.
    rom_roots: list[tuple[Path, str]] = field(default_factory=list)
    #: Which of :attr:`rom_roots` the library was built from.
    current_rom_root: Path | None = None
    #: Raised when the player picked the other card: the resume snapshot names
    #: a game on the card we are leaving, so the app has to drop it.
    card_changed: bool = False
    #: One frame's worth of :meth:`games`; see that method.
    _visible: list[Game] | None = field(default=None, init=False, repr=False, compare=False)
    #: Game key to select as soon as :meth:`games` can be built.  A resume
    #: snapshot is keyed, but at boot nothing is loaded yet, so the key has to
    #: wait for the first list build rather than be resolved eagerly.
    _pending_game_key: str | None = field(default=None, init=False, repr=False, compare=False)

    def attach_metrics(self, metrics, *, single: bool) -> None:
        """Give the session the metrics it needs for grid navigation."""
        self._metrics = metrics
        self._single = single

    def _move_game(self, step: int) -> Outcome:
        count = len(self.games())
        if count == 0:
            return Outcome()
        self.game_index = max(0, min(count - 1, self.game_index + step))
        return Outcome(redraw=True)

    def _back_to_platforms(self) -> Outcome:
        self.view = VIEW_PLATFORMS
        return Outcome(redraw=True)

    def _toggle_favorite(self) -> Outcome:
        game = self.current_game()
        if game is None:
            return Outcome()
        game.favorite = not game.favorite
        self.library.save_state(game, self.system_of(game))
        # Same reason as hiding: the FAV view filters on this very flag, and
        # ``current_game()`` rebuilt the frame cache before it changed.
        self.invalidate()
        key = "btn.unfavorite" if not game.favorite else "btn.favorite"
        self.notify(self.translator(key) + " " + game.display_name)
        return Outcome(redraw=True)

    def _toggle_hidden(self) -> Outcome:
        """Hide / unhide the game under the cursor, persisted like favourites.

        While ``show_hidden`` is on the entry stays where it is, so a mis-press
        can be undone right there instead of after hunting for it in a menu.  It
        drops out of the list again as soon as the switch is off.
        """
        game = self.current_game()
        if game is None:
            return Outcome()
        game.hidden = not game.hidden
        self.library.save_state(game, self.system_of(game))
        # ``current_game()`` above rebuilt the frame cache while the game was
        # still in its old state, so without this the list -- and the preview
        # strip, which is built from the same list -- would go on showing it
        # until some later input happened to drop the cache.
        self.invalidate()
        key = "toast.unhidden" if not game.hidden else "toast.hidden"
        self.notify(self.translator(key) + " " + game.display_name)
        return Outcome(redraw=True)

    @staticmethod
    def system_of(game: Game) -> str:
        """The system a game belongs to -- not the view it was opened from."""
        return game.key.split("/", 1)[0]

    def _cycle_layout(self, direction: int = 1) -> Outcome:
        self.layout = _cycle(LAYOUTS, self.layout, direction)
        self.config.layout = self.layout
        count = len(self.games())
        if count:
            self.game_index = min(self.game_index, count - 1)
        self.notify(self.translator(f"games.layout_{self.layout}"))
        return Outcome(redraw=True)

    #: Volume step for the rocker.  A hundred steps is far too fine for a button
    #: you hold down, and 5 lands on the round numbers people expect.
    _VOLUME_STEP = 5

    def _adjust_volume(self, direction: int) -> Outcome:
        """Move the preview volume and say where it landed.

        The number on screen is the point: a rocker that changes nothing
        audible right now (no clip on the selection) still has to answer, or it
        reads as broken -- which is exactly how it felt before it was wired up.
        """
        current = int(self.config.video_volume)
        value = max(0, min(100, current + direction * self._VOLUME_STEP))
        if value != current:
            self.config.video_volume = value
            # Outlives the session, so the app persists it with the rest.
            self.settings_dirty = True
        self.notify(self.translator("toast.volume", value=value))
        return Outcome(redraw=True)

    # -- modals ------------------------------------------------------------- #

    def _open_menu(self) -> Outcome:
        self.modal = MODAL_MENU
        self.menu_index = 0
        # Transaction start: B restores this snapshot, A commits the whole pass.
        self._menu_stash = (copy.deepcopy(self.config), self.sort, self.layout)
        return Outcome(redraw=True)

    def _open_exit_dialog(self) -> Outcome:
        self.modal = MODAL_EXIT
        self.exit_selected = 0
        return Outcome(redraw=True)

    def _handle_menu_modal(self, event: InputEvent) -> Outcome:
        if not event.is_press:
            return Outcome()
        rows = self.menu_rows()
        if event.action is InputAction.UP:
            self.menu_index = (self.menu_index - 1) % len(rows)
        elif event.action is InputAction.DOWN:
            self.menu_index = (self.menu_index + 1) % len(rows)
        elif event.action is InputAction.A:
            return self._apply_menu(rows[self.menu_index][0])
        elif event.action in (InputAction.LEFT, InputAction.RIGHT):
            return self._adjust_menu(rows[self.menu_index][0],
                                     -1 if event.action is InputAction.LEFT else 1)
        elif event.action in (InputAction.B, InputAction.MENU):
            self._cancel_menu()
        return Outcome(redraw=True)

    def _apply_menu(self, key: str) -> Outcome:
        """A: commit everything the arrows staged, then close.

        The effects wait for this key on purpose -- palette, backlight,
        language, library caches and the saved config all change together
        here, and the restart-grade rows (screen mode, card) raise their flags
        at the same moment, so nothing bounces mid-adjustment.
        """
        if key == "hide_game":
            self.modal = MODAL_NONE
            self._menu_stash = None
            return self._toggle_hidden()
        if key == "clear_cache":
            self.modal = MODAL_NONE
            self._menu_stash = None
            return self._clear_cache()

        stashed = self._menu_stash[0] if self._menu_stash is not None else None
        if stashed is not None:
            if stashed.show_hidden != self.config.show_hidden:
                self.library.drop_games()
            if stashed.language != self.config.language:
                self.translator.set_language(self.config.language)
            if stashed.screen_mode != self.config.screen_mode:
                self.restart_requested = True
            if stashed.thumbnail_cache != self.config.thumbnail_cache:
                self.library.set_thumbnail_cache(self.config.thumbnail_cache)
        # The card comparison is against the menu's own baseline, never the
        # app's resolved root: config.rom_root may legitimately still be
        # "auto" (never committed), and comparing against the resolved path
        # would restart on every plain A press.
        baseline_rom_root = (
            str(stashed.rom_root) if stashed is not None else str(self.current_rom_root)
        )
        if str(self.config.rom_root) != baseline_rom_root:
            self.card_changed = True
            self.restart_requested = True
        self._menu_stash = None
        self.settings_dirty = True
        self.modal = MODAL_NONE
        return Outcome(redraw=True)

    def _clear_cache(self) -> Outcome:
        """A on "clear cache": empty every thumbnail cache on the card.

        Walking the media tree of a full card is seconds of ``stat`` calls --
        long enough that doing it on the input thread would drop frames the
        whole time -- so it runs like the background scan does.  The toast
        arrives when it is finished; :meth:`notify` only sets fields the next
        frame reads, which is the same contract ``library_changed`` uses.
        """
        self._menu_stash = None
        self.modal = MODAL_NONE
        self.notify(self.translator("toast.cache_clearing"))
        threading.Thread(
            target=self._clear_cache_worker, name="retrostation-clear-cache", daemon=True,
        ).start()
        return Outcome(redraw=True)

    def _clear_cache_worker(self) -> None:
        try:
            removed = self.library.clear_thumbnails()
        except Exception:  # noqa: BLE001 - a failed cleanup must not kill the UI
            log.exception("clearing the thumbnail cache failed")
            self.notify(self.translator("toast.cache_clear_failed"))
            return
        self.notify(self.translator("toast.cache_cleared", count=removed))
        # Whatever is on screen was drawn from the bitmaps we just dropped.
        self.invalidate()

    def _cancel_menu(self) -> None:
        """B: restore what the dialog opened with; nothing staged survives."""
        if self._menu_stash is not None:
            stashed_config, sort, layout = self._menu_stash
            for item in fields(type(stashed_config)):
                setattr(self.config, item.name, getattr(stashed_config, item.name))
            self.sort, self.layout = sort, layout
        self._menu_stash = None
        self.modal = MODAL_NONE

    def _toggle_menu_row(self, key: str, direction: int = 1) -> None:
        """**Stage** a row's value in place -- nothing takes effect yet.

        The palette, the backlight, the language, the library caches and the
        restart flags all wait for A (:meth:`_apply_menu`); B restores the
        snapshot.  Staging edits the live config fields so the row labels and
        the values on screen follow the arrows.  ``direction`` only matters
        for the cycling rows (LEFT steps backwards); the on/off rows flip.
        """
        if key == "screen":
            self.config.screen_mode = "single" if self.config.screen_mode != "single" else "dual"
        elif key == "card":
            paths = [path for path, _label in self.rom_roots]
            if len(paths) < 2 or self.current_rom_root is None:
                return
            try:
                index = paths.index(self.current_rom_root)
            except ValueError:
                index = -1
            self.config.rom_root = str(paths[(index + 1) % len(paths)])
        elif key == "layout":
            self._cycle_layout(direction)
        elif key == "bvideo":
            self.config.bottom_video = not self.config.bottom_video
        elif key == "sort":
            self.sort = _cycle(SORTS, self.sort, direction)
        elif key == "show_hidden":
            self.config.show_hidden = not self.config.show_hidden
        elif key == "search_by":
            self.config.search_by = _cycle(SEARCH_BY, self.config.search_by, direction)
            # The result cache is keyed by the query text only; a mode switch
            # must not leave it serving results from the old field set.
            self._search_cache = None
        elif key == "theme":
            self.config.theme = _cycle(THEMES, self.config.theme, direction)
        elif key == "variant":
            self.config.theme_variant = _cycle(VARIANTS, self.config.theme_variant, direction)
        elif key == "language":
            codes = ("auto", *available_builtin())
            if self.config.language in codes:
                self.config.language = _cycle(codes, self.config.language, direction)
            else:
                self.config.language = "auto"
        elif key == "video_sound":
            self.config.video_sound = not self.config.video_sound
        elif key == "brightness":
            self._step_brightness(BRIGHTNESS_STEP)
        elif key == "status_bar":
            self.config.show_status_bar = not self.config.show_status_bar
        elif key == "tcache":
            # Staged like the rest: the cache only actually switches off when A
            # commits, so flicking the switch back and forth costs nothing.
            self.config.thumbnail_cache = not self.config.thumbnail_cache
        elif key == "autostart":
            # Staged like the rest: the firmware hook is only patched when A
            # commits, so the flag file flips without rewriting anything yet.
            self.config.boot.enabled = not self.config.boot.enabled

    def _adjust_menu(self, key: str, direction: int) -> Outcome:
        """**Stage** a row's value with LEFT/RIGHT; the dialog stays open and
        nothing takes effect until A commits (:meth:`_apply_menu`).

        The numeric rows step by their own increment; the cycling rows share
        the staged switch.  Rows that are actions or app-level restarts
        (screen, card, hide_game) deliberately do not respond to the arrows.
        """
        if key == "brightness":
            self._step_brightness(direction * BRIGHTNESS_STEP)
        elif key == "video_volume":
            value = max(0, min(100, int(self.config.video_volume) + direction * self._VOLUME_STEP))
            self.config.video_volume = value
            self.notify(self.translator("toast.volume", value=value))
        elif key in _CYCLING_ROWS:
            self._toggle_menu_row(key, direction)
        return Outcome(redraw=True)

    def _step_brightness(self, delta: int) -> None:
        """Move both panels together -- they sit side by side and must match."""
        level = int(self.config.brightness.get("top", 140)) + delta
        level = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, level))
        self.config.brightness["top"] = level
        self.config.brightness["bottom"] = level

    def _cycle_language(self, direction: int = 1) -> None:
        """``auto`` first, then what we ship, so the default stays reachable."""
        codes = ["auto", *available_builtin()]
        current = self.config.language
        following = (
            codes[(codes.index(current) + direction) % len(codes)]
            if current in codes else "auto"
        )
        self.config.language = following
        self.translator.set_language(following)

    def _card_label(self) -> str:
        """Label of the card in use (TF1 / TF2) -- follows the staged choice."""
        for path, label in self.rom_roots:
            if str(path) == self.config.rom_root:
                return label
        # "auto" (or anything unresolved before the first commit): the card
        # the app actually mounted.
        for path, label in self.rom_roots:
            if path == self.current_rom_root:
                return label
        return "-"

    def menu_rows(self) -> list[tuple[str, str, str]]:
        """``(key, label, value)`` triples for the settings dialog."""
        config = self.config
        single = config.screen_mode == "single"
        rows: list[tuple[str, str, str]] = []
        # Leading row, and only from the game list: the menu also opens on the
        # home page, where there is nothing to hide.  This is how the handheld
        # reaches the hide action -- every one of its buttons is taken, so HIDE
        # cannot be bound to one.  It leads because it is usually the reason the
        # menu was opened, not one more setting to fiddle with.
        if self.view == VIEW_GAMES:
            game = self.current_game()
            if game is not None:
                # Named for what the row will do, not for what it is: the same
                # row both hides and un-hides, so a fixed label would be wrong
                # half the time.
                label = self.translator(
                    "menu.unhide_game" if game.hidden else "menu.hide_game"
                )
                rows.append(("hide_game", label, ""))
        rows += [
            ("screen", self.translator("menu.screen"),
             self.translator("value.single" if single else "value.dual")),
        ]
        # Only worth a row when there is somewhere to switch to: with one card
        # there is no alternative, and the row would just taunt the player.
        if len(self.rom_roots) > 1:
            rows.append(("card", self.translator("menu.card"), self._card_label()))
        rows += [
            ("layout", self.translator("menu.layout"), self.translator(f"games.layout_{self.layout}")),
            # Video plays in the detail strip on one screen too, so this row
            # reads the same whichever mode is active.
            ("bvideo", self.translator("menu.bvideo"),
             self.translator("value.on" if config.bottom_video else "value.off")),
            # Sound belongs with the video rows: it is the soundtrack of the
            # clip this pair of rows is about.
            ("video_sound", self.translator("menu.video_sound"),
             self.translator("value.on" if config.video_sound else "value.off")),
            ("video_volume", self.translator("menu.video_volume"),
             f"{int(config.video_volume)}"),
            ("sort", self.translator("menu.sort"), self.translator(f"value.sort_{self.sort}")),
            ("show_hidden", self.translator("menu.show_hidden"),
             self.translator("value.on" if config.show_hidden else "value.off")),
            # What the search matches against; cycles title -> rom -> both.
            ("search_by", self.translator("menu.search_by"),
             self.translator(f"value.search_{config.search_by}")),
            ("theme", self.translator("menu.theme"), self.translator(f"value.theme_{config.theme}")),
            ("variant", self.translator("menu.variant"),
             self.translator(f"value.variant_{config.theme_variant}")),
            ("language", self.translator("menu.language"),
             self.translator("value.auto") if config.language == "auto" else config.language),
            ("brightness", self.translator("menu.brightness"),
             f"{int(config.brightness.get('top', 140))}"),
            ("status_bar", self.translator("menu.status_bar"),
             self.translator("value.on" if config.show_status_bar else "value.off")),
            # The cache pair sits together and last but one: the switch is a
            # set-and-forget preference, and emptying the card is a rare,
            # deliberate act -- not something to land on while arrowing down.
            ("tcache", self.translator("menu.tcache"),
             self.translator("value.on" if config.thumbnail_cache else "value.off")),
            ("autostart", self.translator("menu.autostart"),
             self.translator("value.on" if config.boot.enabled else "value.off")),
            ("clear_cache", self.translator("menu.clear_cache"), ""),
            ("about", self.translator("menu.about"), f"v{__version__}"),
        ]
        return rows

    # -- ROM picker (multi-file games) ------------------------------------- #

    def _pick_or_launch(self, game: Game | None) -> Outcome:
        """Launch a single-file game now, or open the ROM picker for a block."""
        if game is None:
            return Outcome()
        if game.is_multi:
            return self._open_rom_select(game)
        return Outcome(launch=game)

    def _open_rom_select(self, game: Game) -> Outcome:
        self.rom_select_game = game
        self.rom_select_paths = [game.path, *game.variants]
        self.rom_select_index = 0
        self.modal = MODAL_ROM_SELECT
        return Outcome(redraw=True)

    def _handle_rom_select_modal(self, event: InputEvent) -> Outcome:
        if not event.is_press:
            return Outcome()
        count = len(self.rom_select_paths)
        if event.action is InputAction.UP:
            if count:
                self.rom_select_index = (self.rom_select_index - 1) % count
        elif event.action is InputAction.DOWN:
            if count:
                self.rom_select_index = (self.rom_select_index + 1) % count
        elif event.action in (InputAction.L1, InputAction.R1):
            # Step a page at a time through long clone lists.
            if count:
                step = -5 if event.action is InputAction.L1 else 5
                self.rom_select_index = (self.rom_select_index + step) % count
        elif event.action is InputAction.A:
            return self._confirm_rom_select()
        elif event.action in (InputAction.B, InputAction.MENU):
            self._close_rom_select()
        return Outcome(redraw=True)

    def _confirm_rom_select(self) -> Outcome:
        game = self.rom_select_game
        if game is None or not self.rom_select_paths:
            self._close_rom_select()
            return Outcome(redraw=True)
        chosen = self.rom_select_paths[self.rom_select_index]
        # Launch the chosen file but keep the parent game's metadata and state
        # key, so favourites / play count stay attached to the title.
        selected = game.copy(path=chosen)
        self._close_rom_select()
        return Outcome(launch=selected)

    def _close_rom_select(self) -> None:
        self.modal = MODAL_NONE
        self.rom_select_game = None
        self.rom_select_paths = []

    # -- search (SELECT) ---------------------------------------------------- #

    def _open_search(self) -> Outcome:
        """Search the context the player is looking at: the whole library from
        an aggregate page, one system's games from inside it."""
        self.search_origin = self.current_system_key()
        self.search_text = ""
        self.search_kb = 0
        self.search_focus = "kb"
        self.search_result_index = 0
        self._search_cache = None
        self.modal = MODAL_SEARCH
        # Build the game list now, while the player expects a beat of work:
        # the first keystroke then filters a ready list instead of freezing
        # mid-typing on a cold library.
        self.games()
        return Outcome(redraw=True)

    def _close_search(self) -> None:
        self.modal = MODAL_NONE
        self.search_text = ""
        self.search_focus = "kb"
        self.search_result_index = 0
        self._search_cache = None

    def search_results(self) -> list[Game]:
        """Games matching the query, prefix hits before containment hits."""
        if self._search_cache is None or self._search_cache[0] != self.search_text:
            self._search_cache = (self.search_text, self._build_search_results())
        return self._search_cache[1]

    def _build_search_results(self) -> list[Game]:
        query = self.search_text.strip().upper()
        if not query:
            return []
        # The list the player is already looking at -- loaded and cached, since
        # it is the page the search was opened from.  Filtering that in-memory
        # list keeps every keystroke instant; pulling a fresh aggregate here
        # instead re-walked every system's metadata on the first character and
        # froze the UI for seconds.
        games = self.games()
        scored: list[tuple[int, str, Game]] = []
        for game in games:
            rank = _search_rank(game, query, self.config.search_by)
            if rank < _RANK_MISS:
                scored.append((rank, game.sort_key.casefold(), game))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored]

    def _handle_search_modal(self, event: InputEvent) -> Outcome:
        if not event.is_press:
            return Outcome()
        if event.action is InputAction.SEARCH:
            # The combo that opened the search closes it again.
            self._close_search()
            return Outcome(redraw=True)
        if self.search_focus == "kb":
            return self._handle_search_kb(event)
        return self._handle_search_results(event)

    def _handle_search_kb(self, event: InputEvent) -> Outcome:
        # A real keyboard (desktop) rides the character on the event, and it
        # wins over the key's mapped action: typing "s" inserts "S" even though
        # the key maps to START.  Case does not matter; matching is
        # case-insensitive anyway.
        if event.text and event.text.isalnum():
            self._set_search_text(self.search_text + event.text.upper())
            return Outcome(redraw=True)
        if event.text == "\x1b":
            # Desktop Esc: leave the dialog outright, whatever is typed so far.
            self._close_search()
            return Outcome(redraw=True)
        action = event.action
        if action is InputAction.MENU:
            self._close_search()
            return Outcome(redraw=True)
        last = len(SEARCH_CODES) - 1
        if action is InputAction.LEFT:
            self.search_kb = max(0, self.search_kb - 1)
        elif action is InputAction.RIGHT:
            self.search_kb = min(last, self.search_kb + 1)
        elif action is InputAction.UP:
            self.search_kb = max(0, self.search_kb - SEARCH_COLS)
        elif action is InputAction.DOWN:
            self.search_kb = min(last, self.search_kb + SEARCH_COLS)
        elif action is InputAction.A:
            return self._apply_search_key(SEARCH_CODES[self.search_kb])
        elif action is InputAction.B:
            # B hops to the result list and back -- the arrows must stay free
            # to walk the letter grid, or the second letter of a query becomes
            # unreachable once the first one matches something.  The desktop's
            # Backspace key arrives as "\b" and always deletes instead: hopping
            # would make fixing a typo impossible.  With nothing to list, B
            # keeps its old meaning too: delete, then close.
            if event.text == "\b" or not self.search_results():
                if self.search_text:
                    return self._apply_search_key("BS")
                self._close_search()
            else:
                self.search_focus = "results"
                self.search_result_index = 0
        elif action is InputAction.Y:
            self._set_search_text("")
        elif action in (InputAction.L1, InputAction.R1):
            if self.search_results():
                self.search_focus = "results"
                self.search_result_index = 0
        return Outcome(redraw=True)

    def _handle_search_results(self, event: InputEvent) -> Outcome:
        results = self.search_results()
        if event.text == "\x1b":
            # Desktop Esc from the result list: one press, dialog gone.
            self._close_search()
            return Outcome(redraw=True)
        action = event.action
        if action is InputAction.MENU:
            self._close_search()
            return Outcome(redraw=True)
        # No typing here on purpose: the focus is not on the keyboard, so
        # every key acts instead -- B (and the letter that carries it) goes
        # back to the query, arrows move, A launches.
        if action in (InputAction.UP, InputAction.DOWN):
            if results:
                step = -1 if action is InputAction.UP else 1
                self.search_result_index = (
                    (self.search_result_index + step) % len(results)
                )
        elif action in (InputAction.L1, InputAction.R1, InputAction.B):
            self.search_focus = "kb"
        elif action is InputAction.A:
            if results:
                game = results[self.search_result_index % len(results)]
                self._close_search()
                return self._pick_or_launch(game)
        return Outcome(redraw=True)

    def _apply_search_key(self, code: str) -> Outcome:
        if code == "OFF":
            self._close_search()
        elif code == "BS":
            self._set_search_text(self.search_text[:-1])
        elif code == "CLR":
            self._set_search_text("")
        else:
            self._set_search_text(self.search_text + code)
        return Outcome(redraw=True)

    def _set_search_text(self, text: str) -> None:
        self.search_text = text
        self.search_result_index = 0

    def _handle_exit_modal(self, event: InputEvent) -> Outcome:
        if not event.is_press:
            return Outcome()
        options = self.exit_options()
        if event.action is InputAction.UP:
            self.exit_selected = (self.exit_selected - 1) % len(options)
            return Outcome(redraw=True)
        if event.action is InputAction.DOWN:
            self.exit_selected = (self.exit_selected + 1) % len(options)
            return Outcome(redraw=True)
        if event.action is InputAction.B:
            # B backs out of the dialog without doing anything -- the destructive
            # rows (reboot, power off) are only ever reached by a deliberate A.
            self.modal = MODAL_NONE
            return Outcome(redraw=True)
        if event.action in (InputAction.A, InputAction.MENU):
            # A / MENU confirm the highlighted row.  The default (index 0) is
            # "quit", so a long-press MENU followed by A still just exits -- the
            # old behaviour -- while reboot / power off need an explicit pick.
            key = options[self.exit_selected % len(options)][0]
            if key == "quit":
                return Outcome(quit=True)
            return Outcome(power=key)
        return Outcome()

    def exit_options(self) -> list[tuple[str, str]]:
        """``(key, label)`` for the power/quit dialog; see :meth:`_handle_exit_modal`.

        ``quit`` is first so the dialog opens on the harmless choice; reboot and
        power off follow.  All three are real system actions, not settings, so
        they are not part of the menu transaction.
        """
        t = self.translator
        return [
            ("quit", t("dialog.exit_option_quit")),
            ("reboot", t("dialog.exit_option_reboot")),
            ("poweroff", t("dialog.exit_option_poweroff")),
        ]

    # -- toasts -------------------------------------------------------------- #

    def notify(self, message: str) -> None:
        self.toast_message = message
        self.toast_until = self.clock() + TOAST_SECONDS

    def active_toast(self) -> str:
        if self.toast_message and self.clock() < self.toast_until:
            return self.toast_message
        return ""
