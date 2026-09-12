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
    {"screen", "card", "layout", "bvideo", "video_sound", "sfx", "sort",
     "show_hidden", "search_by", "theme", "variant", "language", "status_bar",
     "vpad", "vpad_op", "tcache", "autostart",
     # The stepped rows belong here too: this is also the set touch consults to
     # decide a row is adjustable, and leaving them out made 背光/预览音量/按键音量
     # read as "info" rows -- a tap on them did nothing at all, while the one
     # stepped row that was listed answered every tap with a "+".
     "brightness", "video_volume", "sfx_volume"}
)

#: How each settings row is operated, for touch -- a finger has no left/right to
#: step a value with.  Booleans flip on a tap; a multi-value row opens a picker of
#: these options (translation key, value); actions run on a tap.  A row left out
#: keeps the keyboard's step, and "info" rows (about) do nothing.
_MENU_ACTIONS = frozenset({"hide_game", "clear_cache", "rom_dir"})
_MENU_TOGGLES = frozenset({"bvideo", "video_sound", "sfx", "tcache", "autostart",
                           "status_bar", "vpad", "show_hidden"})
_MENU_CHOICES: dict[str, tuple[tuple[str, str], ...]] = {
    "screen": (("value.dual", "dual"), ("value.single", "single")),
    "layout": (("games.layout_list", "list"), ("games.layout_grid", "grid"),
               ("games.layout_carousel", "carousel")),
    "sort": (("value.sort_name", "name"), ("value.sort_play", "play"),
             ("value.sort_recent", "recent")),
    "theme": tuple((f"value.theme_{name}", name) for name in THEMES),
    "variant": tuple((f"value.variant_{name}", name) for name in VARIANTS),
    "search_by": (("value.search_title", "title"), ("value.search_rom", "rom"),
                  ("value.search_both", "both")),
    # Raw codes, not labels: the row itself shows the code, so the picker does
    # too -- and it keeps working the day another language bundle lands.
    "language": (("value.auto", "auto"), ("zh_CN", "zh_CN"), ("en_US", "en_US")),
}


def _cycle(options: tuple, current, direction: int):
    """One step through ``options``, forwards or backwards, wrapping."""
    return options[(options.index(current) + direction) % len(options)]

TOAST_SECONDS = 2.0

#: Page sizes for the list view (rows) and the carousel.
LIST_PAGE = 10
CAROUSEL_PAGE = 10

#: 一次甩动最多滚过的行数（触摸，DESIGN.ANDROID §10.3）：不设上限时一次快速
#: 滑动会把列表甩掉大半页，感觉像"跳"而不是"滚"。
_FLING_MAX_ROWS = 6

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
    #: 触摸拖动累计的逻辑像素。小于一行的拖动记在这里，攒够一行才走一格，
    #: 否则慢速拖动（每帧不足一行）会毫无反应（DESIGN.ANDROID §10.3）。
    _touch_px: float = field(default=0.0, init=False, repr=False)

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
    #: The game list the search filters -- built once when the dialog opens so
    #: every keystroke stays instant.  ``None`` until the dialog is open.
    _search_games: list | None = field(default=None, init=False, repr=False)

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
            self._preview_cache = games[:14]
        return self._preview_cache

    def preview_games_for(self, key: str) -> list[Game]:
        """The same six, for any platform -- not just the selected one.

        The home page's warm-up needs them all: switching platforms pulls six
        covers that nothing has decoded yet, and six cold covers is most of a
        second.  ``preview_games`` cannot serve this -- it is pinned to the
        selected platform -- so the ordering lives in one place and both call
        it.
        """
        games = self.library.resolve_all(key)
        if not self.config.show_hidden:
            games = [game for game in games if not game.hidden]
        return sorted(games, key=self._preview_order)[:14]

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

        # 竖屏双画布：点下半屏（详情屏）= 启动当前游戏（DESIGN.ANDROID §10.3）。
        # 这条在掌机上从未落地（没有下屏触摸），Android 免费拿到。
        if event.is_press and event.action is InputAction.TAP and event.screen == 1:
            return self._tap_detail()

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

        # 触摸（DESIGN.ANDROID §10.3）：点卡片选中，再点已选中的卡片进入。
        if action is InputAction.TAP and event.x is not None:
            return self._tap_platform(event)
        # The platform row is horizontal, so a swipe walks it left and right --
        # the same follow-the-finger path the game carousel uses.
        if action is InputAction.DRAG:
            return self._drag_scroll(event.dy, event.dx)
        if action is InputAction.FLING:
            self._touch_px = 0.0
            return self._fling_scroll(event.dy, event.dx)

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
        # 触摸（DESIGN.ANDROID §10.3）：点行选中，再点已选中的行启动。
        if action is InputAction.TAP and event.x is not None:
            self._touch_px = 0.0
            return self._tap_game(event)
        if action is InputAction.DRAG:
            return self._drag_scroll(event.dy, event.dx)
        if action is InputAction.FLING:
            self._touch_px = 0.0
            return self._fling_scroll(event.dy, event.dx)
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

    # -- touch (DESIGN.ANDROID §10.3) -------------------------------------- #

    def _tap_platform(self, event: InputEvent) -> Outcome:
        """Tap on the home carousel: select the card; tap it again to enter."""
        from .screens.home import carousel_hit

        if self._metrics is None:
            return Outcome()
        hit = carousel_hit(self._metrics, self.system_count(),
                           self.platform_index, event.x, event.y)
        if hit is None:
            return Outcome()
        if hit == self.platform_index:
            return self._enter_games()
        self.platform_index = hit
        self.game_index = 0
        return Outcome(redraw=True)

    def _tap_game(self, event: InputEvent) -> Outcome:
        """Tap on a game: select it; tap it again to launch (§10.3)."""
        if self._metrics is None:
            return Outcome()
        games = self.games()
        if not games:
            return Outcome()
        hit = self._game_hit(len(games), event.x, event.y)
        if hit is None:
            return Outcome()
        if hit == self.game_index:
            return self._pick_or_launch(games[hit])
        self.game_index = hit
        return Outcome(redraw=True)

    def _game_hit(self, count: int, x: int, y: int) -> int | None:
        """The position a tap landed on, in whichever view is showing."""
        from .screens import games as view

        m = self._metrics
        if self.layout == "grid":
            return view.grid_hit(m, count, self.game_index,
                                 self._grid_cols(), self._grid_rows(),
                                 single=self._single, x=x, y=y,
                                 scroll=self.scroll_offset(count))
        if self.layout == "carousel":
            return view.carousel_hit(m, count, self.game_index,
                                     single=self._single, x=x, y=y,
                                     scroll=self.scroll_offset(count))
        return view.list_hit(m, count, self.game_index, self._page_size(), x, y,
                             scroll=self.scroll_offset(count))

    def _tap_detail(self) -> Outcome:
        """Tap on the detail canvas (portrait's lower screen): start the game.

        On the home page it means "open the selected platform" instead -- there
        is no game under the cursor yet.
        """
        if self.view == VIEW_GAMES:
            return self._pick_or_launch(self.current_game())
        return self._enter_games()

    def _scrolls_sideways(self) -> bool:
        """Whether the current list runs along the x axis.

        The platform page and the games carousel are rows of cards; the list and
        the grid are columns.  A drag has to follow the axis its view is laid out
        on -- measuring only ``dy`` is why a swipe across the carousel did
        nothing at all.
        """
        return self.view == VIEW_PLATFORMS or self.layout == "carousel"

    def _scroll_pitch(self) -> int:
        """Logical pixels one step covers along whichever axis scrolls."""
        m = self._metrics
        if m is None:
            return 0
        if self.view == VIEW_PLATFORMS:
            return m.platform_art + m.u(8)
        if self.layout == "carousel":
            return m.carousel_card_w(single=self._single) + m.carousel_gap
        return m.row_step

    def _move_steps(self, steps: int) -> Outcome:
        """Move this view's cursor by whole steps along its scrolling axis."""
        if self.view == VIEW_PLATFORMS:
            return self._move_platform(steps)
        if self._scrolls_sideways():
            return self._move_game(steps)
        return self._move_game(steps * self._vertical_step())

    def _drag_scroll(self, dy: int, dx: int) -> Outcome:
        """A drag follows the finger, one whole step at a time (§10.3).

        Sub-step movement is accumulated: a slow drag delivers a couple of
        logical pixels per frame, and discarding those would make it feel dead.
        """
        if self._pans():
            # The grid/list pan: the cells move under the finger and stop exactly
            # where they are let go (no snapping) -- a tap is what selects.  The
            # carousel pans sideways and follows with its cursor instead.
            if self.layout == "carousel":
                self.scroll_px -= dx
                self._anchor_carousel()
            else:
                self.scroll_px = self._clamp_scroll(self.scroll_px - dy)
            return Outcome(redraw=True)
        pitch = self._scroll_pitch()
        if pitch <= 0:
            return Outcome()
        self._touch_px += dx if self._scrolls_sideways() else dy
        steps = int(-self._touch_px / pitch)
        if steps == 0:
            return Outcome()
        self._touch_px += steps * pitch
        return self._move_steps(steps)

    def _fling_scroll(self, dy: int, dx: int) -> Outcome:
        """A quick swipe jumps by the inertia distance the bridge measured.

        Clamped hard: an unclamped flick launched the list most of a page at
        once, which read as "the list jumped" rather than "I scrolled".  Sideways
        it is the same distance on the other axis.
        """
        pitch = self._scroll_pitch()
        if pitch <= 0:
            return Outcome()
        if self._pans():
            # The flick's inertia pans the same way, then the carousel settles.
            if self.layout == "carousel":
                self.scroll_px -= dx
                self._anchor_carousel()
                self.scroll_px = 0.0
            else:
                self.scroll_px = self._clamp_scroll(self.scroll_px - dy)
            return Outcome(redraw=True)
        travel = dx if self._scrolls_sideways() else dy
        steps = int(-travel / pitch)
        steps = max(-_FLING_MAX_ROWS, min(_FLING_MAX_ROWS, steps))
        if steps == 0:
            steps = 1 if travel < 0 else -1
        return self._move_steps(steps)

    def _vertical_step(self) -> int:
        if self.layout == "grid":
            return self._grid_cols()
        # The carousel steps one game, not a page: it is a row of neighbours, so
        # a page-sized jump reads as "it skipped" rather than "I moved".
        return 1

    def _page_size(self) -> int:
        if self.layout == "grid":
            return self._grid_cols() * self._grid_rows()
        return LIST_PAGE

    def _grid_cols(self) -> int:
        return self._metrics.grid_cols if self._metrics else 4

    # -- list / grid viewport (smooth scrolling) --------------------------- #

    def _pans(self) -> bool:
        """Whether a drag pans this view rather than stepping its cursor."""
        return self.view == VIEW_GAMES and self.layout in ("grid", "list", "carousel")

    def _carousel_pitch(self) -> float:
        m = self._metrics
        if m is None:
            return 0.0
        return float(m.carousel_card_w(single=self._single) + m.carousel_gap)

    def _anchor_carousel(self) -> None:
        """Follow the pan with the cursor, a card at a time.

        The carousel has no free cursor: whatever sits in the middle *is* the
        selection (that is what the strip means), so a drag moves the cursor with
        it -- otherwise the strip would run out of drawn cards on the side the
        finger is heading for.
        """
        pitch = self._carousel_pitch()
        if pitch <= 0:
            return
        count = len(self.games())
        while self.scroll_px >= pitch / 2 and self.game_index < count - 1:
            self.scroll_px -= pitch
            self.game_index += 1
        while self.scroll_px <= -pitch / 2 and self.game_index > 0:
            self.scroll_px += pitch
            self.game_index -= 1

    def end_drag(self) -> None:
        """The finger lifted: the carousel settles onto its card.

        The pan offset is only ever half a card at most (the anchor above keeps
        it there), so snapping is a rounding, not a slide.
        """
        if self._metrics is not None and self.layout == "carousel" and self.view == VIEW_GAMES:
            self._anchor_carousel()
            self.scroll_px = 0.0

    def _cols(self) -> int:
        """Items per row: the grid's column count, one for a list."""
        return max(1, self._grid_cols()) if self.layout == "grid" else 1

    def _pad(self) -> float:
        m = self._metrics
        if m is None:
            return 0.0
        return float(m.grid_padding if self.layout == "grid" else m.u(8))

    def _gap(self) -> float:
        m = self._metrics
        return float(m.grid_gap) if (m is not None and self.layout == "grid") else 0.0

    def _pitch(self) -> float:
        """Distance between two rows, in logical pixels."""
        m = self._metrics
        if m is None:
            return 0.0
        if self.layout == "grid":
            return float(m.grid_cell_h(single=self._single) + m.grid_gap)
        return float(m.row_step)

    def _item_h(self) -> float:
        """Height of one item's own box (less the gap after it)."""
        m = self._metrics
        if m is None:
            return 0.0
        if self.layout == "grid":
            return float(m.grid_cell_h(single=self._single))
        return float(m.row_h)

    def _content_height(self, count: int) -> float:
        """How tall the whole list is, in the drawing's own coordinates."""
        if count <= 0 or self._pitch() <= 0:
            return 0.0
        cols = self._cols()
        rows = (count + cols - 1) // cols
        return self._pad() + rows * self._pitch() - self._gap()

    def _viewport(self) -> float:
        m = self._metrics
        return float(m.content_h(single=self._single)) if m is not None else 0.0

    def _clamp_scroll(self, value: float) -> float:
        limit = max(0.0, self._content_height(len(self.games())) - self._viewport())
        return max(0.0, min(value, limit))

    def scroll_offset(self, count: int) -> int:
        """The panned view's scroll position in whole pixels (0 elsewhere)."""
        if not self._pans() or self._metrics is None:
            return 0
        if self.layout == "carousel":
            # Sideways, and bounded by the cursor following it rather than by a
            # vertical extent: clamping it with the height would pin it to zero.
            return int(self.scroll_px)
        self.scroll_px = max(0.0, min(self.scroll_px,
                                      max(0.0, self._content_height(count) - self._viewport())))
        return int(self.scroll_px)

    def _scroll_cursor_into_view(self) -> None:
        """Keep the cursor on screen after a key move -- the drag is free to
        leave it anywhere, so the keyboard has to chase it."""
        if self._metrics is None or not self._pans():
            return
        if self.layout == "carousel":
            # The carousel draws the cursor's card centred, so there is nothing
            # to chase: whatever offset is left is a drag's, and chasing it here
            # computed an absolute position from the *index* (hundreds of cards
            # of pixels), which threw the strip far off screen on every key press.
            self.scroll_px = 0.0
            return
        pitch = self._pitch()
        if pitch <= 0:
            return
        top = self._pad() + (self.game_index // self._cols()) * pitch
        height = self._item_h()
        view = self._viewport()
        if top - self.scroll_px < 0:
            self.scroll_px = top
        elif top + height - self.scroll_px > view:
            self.scroll_px = top + height - view
        self.scroll_px = max(0.0, self.scroll_px)

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
    #: Pixel offset of the grid's viewport: dragging pans the cells past a fixed
    #: cursor instead of stepping the selection (see :meth:`_drag_scroll`).
    scroll_px: float = 0.0
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
        self._scroll_cursor_into_view()
        return Outcome(redraw=True)

    def _back_to_platforms(self) -> Outcome:
        self.view = VIEW_PLATFORMS
        self.scroll_px = 0.0
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
        """Move *both* volumes, and say where they landed.

        The rocker is the one control a player reaches for, and "I turned it
        up" has to mean the whole thing got louder -- a button blip that
        ignores it while a clip that is not even playing obeys reads as a
        broken key.  So the preview and the blips step together; both stay
        where the player left them relative to each other (the blips sit below
        the previews on purpose, and that gap is theirs to keep).

        The number on screen is still the point: a rocker that changes nothing
        audible right now (no clip on the selection) has to answer, or it reads
        as broken -- which is exactly how it felt before it was wired up.
        """
        step = direction * self._VOLUME_STEP
        moved = False
        for field in ("video_volume", "sfx_volume"):
            current = int(getattr(self.config, field))
            value = max(0, min(100, current + step))
            if value != current:
                setattr(self.config, field, value)
                moved = True
        if moved:
            # Outlives the session, so the app persists it with the rest.
            self.settings_dirty = True
        self.notify(self.translator("toast.volume",
                                    value=int(self.config.video_volume)))
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
        # One of the rows is open for picking: it owns the input until it closes.
        if getattr(self, "menu_choice_options", None):
            return self._handle_menu_choice_modal(event)
        rows = self.menu_rows()
        if event.action is InputAction.UP:
            self.menu_index = (self.menu_index - 1) % len(rows)
            self.menu_top = None  # a key press re-centres the window on the cursor
        elif event.action is InputAction.DOWN:
            self.menu_index = (self.menu_index + 1) % len(rows)
            self.menu_top = None
        elif event.action is InputAction.A:
            return self._apply_menu(rows[self.menu_index][0])
        elif event.action in (InputAction.LEFT, InputAction.RIGHT):
            return self._adjust_menu(rows[self.menu_index][0],
                                     -1 if event.action is InputAction.LEFT else 1)
        elif event.action in (InputAction.B, InputAction.MENU):
            self._cancel_menu()
        elif event.action is InputAction.TAP and event.x is not None:
            return self._tap_menu_row(event)
        elif event.action in (InputAction.DRAG, InputAction.FLING):
            # While an option list is open a drag belongs to nothing: it must not
            # scroll the settings behind it.  ``getattr`` because the picker state
            # only exists once a picker has been opened -- reading it directly
            # raised AttributeError on every drag and left the menu dead.
            if getattr(self, "menu_choice_options", None):
                return Outcome()
            return self._scroll_menu(event)
        return Outcome(redraw=True)

    def _tap_menu_row(self, event: InputEvent) -> Outcome:
        """Touch: run the control the finger landed on, else move the cursor.

        Every row carries its own control -- a switch, an option list, a button --
        because a phone has no left/right to step a value with.  The boxes come
        from the dialog that drew them (``dialog_hits``), so the geometry stays in
        one place.
        """
        rows = self.menu_rows()
        # The dialog's own buttons first: the keyboard reaches them with A and B,
        # and a finger has neither.  Confirm commits the pass the arrows staged --
        # and never fires an action row as a side effect of confirming, which A on
        # that row would; cancel drops the pass, exactly like B.
        button = self._dialog_button_at(event)
        if button is not None:
            if button == 0:
                self._cancel_menu()
            else:
                self._apply_menu("")
            return Outcome(redraw=True)
        # Then the stepper buttons, before the row is even looked up: they are
        # controls of their own, and the "+" sits at the far end of the row --
        # a tap there can miss the row's box, and testing for the row first ate
        # the tap ("− 80 +" went down but never up).
        stepper = self._dialog_stepper_at(event)
        if stepper is not None:
            index, direction = stepper
            if index < len(rows):
                self.menu_index = index
                return self._adjust_menu(rows[index][0], direction)
            return Outcome(redraw=True)
        row = self._dialog_row_at(event)
        if row is None or row >= len(rows):
            return Outcome()
        key, label, value = rows[row]
        self.menu_index = row
        kind = self._menu_row_kind(key)
        if kind == "action":
            return self._apply_menu(key)
        if kind == "toggle":
            # A switch is one tap; there is no right-arrow on a phone.
            return self._adjust_menu(key, 1)
        if kind == "choice":
            return self._open_menu_choice(key, label, value)
        return Outcome(redraw=True)

    def _dialog_button_at(self, event: InputEvent) -> int | None:
        """Which bottom button of the open dialog a tap landed on, if any."""
        if event.x is None or event.y is None:
            return None
        for (bx, by, bw, bh), index in getattr(self, "dialog_buttons", ()) or ():
            if bx <= event.x <= bx + bw and by <= event.y <= by + bh:
                return index
        return None

    def _dialog_stepper_at(self, event: InputEvent) -> tuple[int, int] | None:
        """Which stepper button of a stepped row a tap landed on: ``(row, ±1)``."""
        if event.x is None or event.y is None:
            return None
        for (bx, by, bw, bh), index, direction in getattr(self, "dialog_steppers", ()) or ():
            if bx <= event.x <= bx + bw and by <= event.y <= by + bh:
                return index, direction
        return None

    def _menu_row_kind(self, key: str) -> str:
        """How a row is operated, so touch knows whether to flip, pick or run it."""
        if key in _MENU_ACTIONS:
            return "action"
        if key in _MENU_TOGGLES:
            return "toggle"
        if key in _MENU_CHOICES:
            return "choice"
        return "cycle" if key in _CYCLING_ROWS else "info"

    def _open_menu_choice(self, key: str, label: str, value: str) -> Outcome:
        """Touch: a multi-value row opens its options rather than stepping them."""
        options = _MENU_CHOICES.get(key)
        if not options:
            return Outcome()
        labels = [self.translator.t(tkey) for tkey, _value in options]
        self.menu_choice_key = key
        self.menu_choice_title = label
        self.menu_choice_options = options
        self.menu_choice_labels = labels
        self.menu_choice_index = labels.index(value) if value in labels else 0
        return Outcome(redraw=True)

    def _handle_menu_choice_modal(self, event: InputEvent) -> Outcome:
        """Input for an open option list (a radio dialog)."""
        labels = getattr(self, "menu_choice_labels", ())
        if event.action is InputAction.UP:
            self.menu_choice_index = (self.menu_choice_index - 1) % max(1, len(labels))
        elif event.action is InputAction.DOWN:
            self.menu_choice_index = (self.menu_choice_index + 1) % max(1, len(labels))
        elif event.action is InputAction.A:
            return self._apply_menu_choice(self.menu_choice_index)
        elif event.action is InputAction.TAP and event.x is not None:
            row = self._dialog_row_at(event)
            if row is not None and row < len(labels):
                return self._apply_menu_choice(row)
            # Anything else -- the cancel button, the backdrop -- dismisses it.
            self._menu_choice_close()
        elif event.action in (InputAction.B, InputAction.MENU):
            self._menu_choice_close()
        return Outcome(redraw=True)

    def _apply_menu_choice(self, index: int) -> Outcome:
        """Step the row to the picked option, then go back to the menu.

        Stepping (rather than writing the field directly) reuses whatever the row
        already does for left/right -- the only place that knows how each value is
        stored and what else changes with it.  Direction-agnostic: step forward
        until the row reports the wanted label, at most one full cycle.
        """
        key = getattr(self, "menu_choice_key", "")
        labels = getattr(self, "menu_choice_labels", ())
        if key and 0 <= index < len(labels):
            target = labels[index]
            for _ in range(len(labels) + 1):
                if self._menu_row_value(key) == target:
                    break
                self._adjust_menu(key, 1)
        self._menu_choice_close()
        return Outcome(redraw=True)

    def _menu_choice_close(self) -> None:
        """Drop the option list and hand input back to the settings menu."""
        self.menu_choice_options = None
        self.menu_choice_labels = ()
        self.menu_choice_key = ""
        self.menu_choice_title = ""

    def _menu_row_value(self, key: str) -> str:
        """The value string a settings row currently shows."""
        for row_key, _label, value in self.menu_rows():
            if row_key == key:
                return value
        return ""

    def _scroll_menu(self, event: InputEvent) -> Outcome:
        """Touch: a drag moves the *list*, not the cursor.

        The cursor belongs to the keyboard; dragging a list should not walk it,
        which is exactly what made the menu feel wrong under a finger.  The
        cursor stays where it is until a direction key asks for it again.
        """
        rows = self.menu_rows()
        pitch = self._dialog_pitch()
        start, visible = getattr(self, "dialog_window", None) or (0, 0)
        if pitch <= 0 or not rows or visible <= 0:
            return Outcome()
        self._menu_drag_px = getattr(self, "_menu_drag_px", 0.0) + event.dy
        steps = int(-self._menu_drag_px / pitch)  # drag up (dy<0) walks down the list
        if steps == 0:
            return Outcome()
        self._menu_drag_px += steps * pitch
        top = max(0, min(max(0, len(rows) - visible), start + steps))
        if top == start:
            return Outcome()
        self.menu_top = top
        return Outcome(redraw=True)

    def _dialog_row_at(self, event: InputEvent) -> int | None:
        """Which drawn dialog row a touch landed on, if any."""
        box = self._dialog_box_at(event)
        if box is None:
            return None
        for candidate, index in getattr(self, "dialog_hits", ()) or ():
            if candidate == box:
                return index
        return None

    def _dialog_box_at(self, event: InputEvent) -> tuple[int, int, int, int] | None:
        """The box of the drawn dialog row under a touch, if any."""
        if event.x is None or event.y is None:
            return None
        for (bx, by, bw, bh), _index in getattr(self, "dialog_hits", ()) or ():
            if bx <= event.x <= bx + bw and by <= event.y <= by + bh:
                return (bx, by, bw, bh)
        return None

    def _dialog_pitch(self) -> int:
        """Row pitch of the drawn dialog, from the boxes it reported."""
        boxes = getattr(self, "dialog_hits", ()) or ()
        if len(boxes) >= 2:
            return boxes[1][0][1] - boxes[0][0][1]
        return boxes[0][0][3] if boxes else 0

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
        if key == "rom_dir":
            # Close first: the picker opens a system window over the frontend.
            self.modal = MODAL_NONE
            self._menu_stash = None
            request = getattr(self, "rom_access_request", None)
            if request is not None:
                request()
                self.notify(self.translator("toast.rom_dir"))
            return Outcome(redraw=True)

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
            # The volume rows are applied as they are moved, so putting the
            # number back is not enough -- the app has to be told to reapply.
            self.settings_dirty = True
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
        elif key == "sfx":
            self.config.sfx = not self.config.sfx
        elif key == "brightness":
            self._step_brightness(BRIGHTNESS_STEP)
        elif key == "status_bar":
            self.config.show_status_bar = not self.config.show_status_bar
        elif key == "vpad":
            self.config.virtual_pad = not self.config.virtual_pad
        elif key == "vpad_op":
            # Snap onto the 10% grid first: a value from a hand-edited
            # config.json can sit between steps, and ``_cycle`` needs a member.
            steps = tuple(range(20, 101, 10))
            current = min(steps, key=lambda v: abs(v - self.config.virtual_pad_opacity))
            self.config.virtual_pad_opacity = _cycle(steps, current, direction)
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
        elif key in ("video_volume", "sfx_volume"):
            # The two volumes are the exception to "nothing takes effect until
            # A": they are the only rows with something to hear, and a slider
            # you cannot hear while you move it has to be taken on faith.
            # Staged like the rest (A still commits and saves) but applied as
            # you go, so B puts them back -- see :meth:`_cancel_menu`.
            field = "video_volume" if key == "video_volume" else "sfx_volume"
            value = max(0, min(100, int(getattr(self.config, field))
                               + direction * self._VOLUME_STEP))
            setattr(self.config, field, value)
            self.settings_dirty = True
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
        # Android-only, injected by the app: the folder the player authorised for
        # emulators that take a ``content://`` URI.  Listed so it can be checked,
        # and A re-opens the picker to point it somewhere else.
        if getattr(self, "rom_access_label", None) is not None:
            rows.append(("rom_dir", self.translator("menu.rom_dir"),
                         self.rom_access_label() or "-"))
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
            # Button sounds sit right below the clip's own pair: both are
            # "does this thing make noise" and a player looking for one is
            # looking for the other.
            ("sfx", self.translator("menu.sfx"),
             self.translator("value.on" if config.sfx else "value.off")),
            ("sfx_volume", self.translator("menu.sfx_volume"),
             f"{int(config.sfx_volume)}"),
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
            # The on-screen pad: shown or hidden, and how solid it is while
            # shown.  Android-only in effect; on the handheld both rows are
            # inert, which is why they are not offered as keys there.
            ("vpad", self.translator("menu.virtual_pad"),
             self.translator("value.on" if config.virtual_pad else "value.off")),
            ("vpad_op", self.translator("menu.virtual_pad_opacity"),
             f"{int(config.virtual_pad_opacity)}%"),
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
        """Search the context the player is looking at.

        From the platform overview the search spans the *whole* library
        (global): the highlighted platform there is just a cursor, not a
        filter, so scoping to it would hide every other system's matches.
        Inside a platform's game list the search stays scoped to that system.
        The list is built once here -- while the player expects a beat of work
        -- so each keystroke filters a ready list instead of re-walking the
        library and freezing the UI for seconds.
        """
        self.search_origin = self.current_system_key()
        self.search_text = ""
        self.search_kb = 0
        self.search_focus = "kb"
        self.search_result_index = 0
        self._search_cache = None
        if self.view == VIEW_PLATFORMS:
            games = list(self.library.aggregate("ALL"))
        else:
            games = self.games()
        if not self.config.show_hidden:
            games = [game for game in games if not game.hidden]
        self._search_games = games
        self.modal = MODAL_SEARCH
        return Outcome(redraw=True)

    def _close_search(self) -> None:
        self.modal = MODAL_NONE
        self.search_text = ""
        self.search_focus = "kb"
        self.search_result_index = 0
        self._search_cache = None
        self._search_games = None

    def search_results(self) -> list[Game]:
        """Games matching the query, prefix hits before containment hits."""
        if self._search_cache is None or self._search_cache[0] != self.search_text:
            self._search_cache = (self.search_text, self._build_search_results())
        return self._search_cache[1]

    def _build_search_results(self) -> list[Game]:
        query = self.search_text.strip().upper()
        if not query:
            return []
        # ``_search_games`` was built when the dialog opened (see
        # :meth:`_open_search`) -- the whole library from the overview, the
        # current system's games from inside one.  Filtering that ready list
        # keeps every keystroke instant; rebuilding an aggregate here would
        # re-walk every system's metadata on the first character and freeze the
        # UI for seconds.
        games = self._search_games or []
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
