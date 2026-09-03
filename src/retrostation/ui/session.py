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

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..core.config import LAYOUTS, Config
from ..core.i18n import Translator, available_builtin
from .. import __version__
from ..core.model import Game
from ..core.theme import THEMES, VARIANTS
from ..data.library import Library
from ..data.systems import AGGREGATE_KEYS, AGGREGATES, lookup
from ..platform.base import InputAction, InputEvent, InputKind

VIEW_PLATFORMS = "platforms"
VIEW_GAMES = "games"

MODAL_NONE = ""
MODAL_MENU = "menu"
MODAL_EXIT = "exit"

FILTERS = ("all", "covered", "missing")
SORTS = ("name", "play", "recent")

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
    filter: str = "all"
    sort: str = "name"

    #: 平台总览的预览条选中状态（SELECT 进入/退出，左右移动，A 进入游戏）。
    preview_mode: bool = False
    preview_index: int = 0
    #: preview_games() 的每帧缓存；任何输入都会走 invalidate() 清掉。
    _preview_cache: list | None = None

    modal: str = MODAL_NONE
    menu_index: int = 0
    exit_selected: int = 0

    #: Set when a settings row changed something that outlives the dialog: the
    #: app applies it (palette, backlight) and writes ``config.json``.  A
    #: Session has no business knowing where the SD card is mounted.
    settings_dirty: bool = False
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

        if self.filter == "covered":
            games = [game for game in games if game.has_asset("cover")]
        elif self.filter == "missing":
            games = [game for game in games if not game.has_asset("cover")]

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
            "filter": self.filter,
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
        if data.get("filter") in FILTERS:
            self.filter = data["filter"]
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

        if self.modal == MODAL_EXIT:
            return self._handle_exit_modal(event)
        if self.modal == MODAL_MENU:
            return self._handle_menu_modal(event)

        if event.kind is InputKind.LONG_PRESS and event.action is InputAction.MENU:
            return self._open_exit_dialog()

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
            return Outcome(launch=self.current_game())
        if action is InputAction.B:
            return self._back_to_platforms()
        if action is InputAction.Y:
            return self._toggle_favorite()
        if action is InputAction.X:
            return self._cycle_layout()
        if action is InputAction.SELECT:
            return self._cycle_filter()
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
        key = "btn.unfavorite" if not game.favorite else "btn.favorite"
        self.notify(self.translator(key) + " " + game.display_name)
        return Outcome(redraw=True)

    @staticmethod
    def system_of(game: Game) -> str:
        """The system a game belongs to -- not the view it was opened from."""
        return game.key.split("/", 1)[0]

    def _cycle_layout(self) -> Outcome:
        self.layout = LAYOUTS[(LAYOUTS.index(self.layout) + 1) % len(LAYOUTS)]
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

    def _cycle_filter(self) -> Outcome:
        self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
        self.game_index = 0
        names = {
            "all": "games.filter_all",
            "covered": "games.filter_covered",
            "missing": "games.filter_missing",
        }
        self.notify(self.translator(names[self.filter]))
        return Outcome(redraw=True)

    # -- modals ------------------------------------------------------------- #

    def _open_menu(self) -> Outcome:
        self.modal = MODAL_MENU
        self.menu_index = 0
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
        elif event.action is InputAction.B:
            self.modal = MODAL_NONE
        elif event.action is InputAction.MENU:
            self.modal = MODAL_NONE
        return Outcome(redraw=True)

    def _apply_menu(self, key: str) -> Outcome:
        """Toggle the row under the cursor.

        Anything that outlives the dialog -- or needs the platform, like the
        backlight -- only raises :attr:`settings_dirty`; the app applies and
        persists it.
        """
        if key == "screen":
            self.config.screen_mode = "single" if self.config.screen_mode != "single" else "dual"
            self.restart_requested = True
        elif key == "card":
            self._switch_card()
        elif key == "layout":
            self._cycle_layout()
        elif key == "bvideo":
            self.config.bottom_video = not self.config.bottom_video
        elif key == "sort":
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        elif key == "filter":
            self._cycle_filter()
        elif key == "theme":
            self.config.theme = THEMES[(THEMES.index(self.config.theme) + 1) % len(THEMES)]
        elif key == "variant":
            self.config.theme_variant = VARIANTS[
                (VARIANTS.index(self.config.theme_variant) + 1) % len(VARIANTS)
            ]
        elif key == "language":
            self._cycle_language()
        elif key == "video_sound":
            self.config.video_sound = not self.config.video_sound
        elif key == "brightness":
            self._step_brightness(BRIGHTNESS_STEP)
        elif key == "status_bar":
            self.config.show_status_bar = not self.config.show_status_bar
        self.settings_dirty = True
        self.modal = MODAL_NONE
        return Outcome(redraw=True)

    def _adjust_menu(self, key: str, direction: int) -> Outcome:
        """Nudge a numeric row with LEFT/RIGHT.

        The backlight and the preview volume are the numeric rows; every other
        row ignores this rather than leave the player wondering why nothing
        moved.
        """
        if key == "brightness":
            self._step_brightness(direction * BRIGHTNESS_STEP)
            self.settings_dirty = True
        elif key == "video_volume":
            # Same path as the rocker, so both stay in step.
            self._adjust_volume(direction)
        return Outcome(redraw=True)

    def _step_brightness(self, delta: int) -> None:
        """Move both panels together -- they sit side by side and must match."""
        level = int(self.config.brightness.get("top", 140)) + delta
        level = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, level))
        self.config.brightness["top"] = level
        self.config.brightness["bottom"] = level

    def _cycle_language(self) -> None:
        """``auto`` first, then what we ship, so the default stays reachable."""
        codes = ["auto", *available_builtin()]
        current = self.config.language
        following = codes[(codes.index(current) + 1) % len(codes)] if current in codes else "auto"
        self.config.language = following
        self.translator.set_language(following)

    def _card_label(self) -> str:
        """Label of the card in use (TF1 / TF2)."""
        for path, label in self.rom_roots:
            if path == self.current_rom_root:
                return label
        return "-"

    def _switch_card(self) -> None:
        """Browse the other card.

        The library is built around one ROM root, so this cannot be applied in
        place: the choice goes into the config and the app restarts us against
        it -- exit code 43, the same route the screen mode takes, for the same
        reason (DESIGN §4.4: rebuilding these inside a live process is what
        crashes under Wayland).
        """
        paths = [path for path, _label in self.rom_roots]
        if len(paths) < 2:
            return
        try:
            index = paths.index(self.current_rom_root)
        except ValueError:
            index = -1
        self.config.rom_root = str(paths[(index + 1) % len(paths)])
        # The resume snapshot names a game on the card we are leaving; keeping
        # it would restore us onto a ROM that is not mounted.
        self.card_changed = True
        self.restart_requested = True

    def menu_rows(self) -> list[tuple[str, str, str]]:
        """``(key, label, value)`` triples for the settings dialog."""
        config = self.config
        single = config.screen_mode == "single"
        rows = [
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
            ("filter", self.translator("menu.filter"), self.translator(f"games.filter_{self.filter}")),
            ("theme", self.translator("menu.theme"), self.translator(f"value.theme_{config.theme}")),
            ("variant", self.translator("menu.variant"),
             self.translator(f"value.variant_{config.theme_variant}")),
            ("language", self.translator("menu.language"),
             self.translator("value.auto") if config.language == "auto" else config.language),
            ("brightness", self.translator("menu.brightness"),
             f"{int(config.brightness.get('top', 140))}"),
            ("status_bar", self.translator("menu.status_bar"),
             self.translator("value.on" if config.show_status_bar else "value.off")),
            ("about", self.translator("menu.about"), f"v{__version__}"),
        ]
        return rows

    def _handle_exit_modal(self, event: InputEvent) -> Outcome:
        if not event.is_press:
            return Outcome()
        if event.action in (InputAction.A, InputAction.MENU):
            return Outcome(quit=True)
        if event.action is InputAction.B:
            self.modal = MODAL_NONE
            return Outcome(redraw=True)
        return Outcome()

    # -- toasts -------------------------------------------------------------- #

    def notify(self, message: str) -> None:
        self.toast_message = message
        self.toast_until = self.clock() + TOAST_SECONDS

    def active_toast(self) -> str:
        if self.toast_message and self.clock() < self.toast_until:
            return self.toast_message
        return ""
