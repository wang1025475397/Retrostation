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

from ..core.config import LAYOUTS, Config
from ..core.i18n import Translator
from ..core.model import Game
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

    modal: str = MODAL_NONE
    menu_index: int = 0
    exit_selected: int = 0

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
        """The visible, filtered, sorted game list for the current system."""
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
        return games

    def current_game(self) -> Game | None:
        games = self.games()
        if not games:
            return None
        return games[self.game_index % len(games)]

    # ------------------------------------------------------------------ #
    # Event handling
    # ------------------------------------------------------------------ #

    def handle(self, event: InputEvent) -> Outcome:
        """Dispatch one event; the UI redraws when ``Outcome.redraw`` is set."""
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

        if action in (InputAction.UP, InputAction.DOWN):
            return self._move_platform(1 if action is InputAction.DOWN else -1)
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
        elif event.action is InputAction.B:
            self.modal = MODAL_NONE
        elif event.action is InputAction.MENU:
            self.modal = MODAL_NONE
        return Outcome(redraw=True)

    def _apply_menu(self, key: str) -> Outcome:
        if key == "screen":
            self.config.screen_mode = "single" if self.config.screen_mode != "single" else "dual"
        elif key == "layout":
            self._cycle_layout()
        elif key == "bvideo":
            self.config.bottom_video = not self.config.bottom_video
        elif key == "sort":
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        elif key == "filter":
            self._cycle_filter()
        elif key == "bottom_live":
            self.config.show_status_bar = not self.config.show_status_bar
        self.modal = MODAL_NONE
        return Outcome(redraw=True)

    def menu_rows(self) -> list[tuple[str, str, str]]:
        """``(key, label, value)`` triples for the settings dialog."""
        config = self.config
        single = config.screen_mode == "single"
        return [
            ("screen", self.translator("menu.screen"),
             self.translator("value.single" if single else "value.dual")),
            ("layout", self.translator("menu.layout"), self.translator(f"games.layout_{self.layout}")),
            ("bvideo", self.translator("menu.bvideo"),
             self.translator("value.disabled_single" if single else
                             ("value.on" if config.bottom_video else "value.off"))),
            ("sort", self.translator("menu.sort"), self.translator(f"value.sort_{self.sort}")),
            ("filter", self.translator("menu.filter"), self.translator(f"games.filter_{self.filter}")),
            ("language", self.translator("menu.language"), config.language),
            ("about", self.translator("menu.about"), "v0.1.0"),
        ]

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
