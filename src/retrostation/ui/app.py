"""Application: owns the canvases, drives the session, renders every frame.

The frame loop is deliberately boring:

    events -> session.handle() -> build view models -> draw -> present

All data preparation (strings, artwork lookups) happens here, so the screen
modules stay free of data access and can be reviewed against the prototype
one-to-one.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..core.config import Config
from ..core.i18n import Translator
from ..core.model import ASSET_VIDEO, Game
from ..core.theme import COLORS, metrics_for
from ..data.library import Library
from ..data.systems import display_name, lookup
from ..launcher.launch import LaunchError, build_plan
from ..platform.base import InputAction, InputEvent, InputKind, Platform
from .art import ArtProvider
from .painter import Painter
from .session import MODAL_EXIT, MODAL_MENU, Session, VIEW_GAMES
from .screens import bottom, games, home, menu
from .widgets import button_bar, dialog, status_bar, toast

log = logging.getLogger(__name__)

#: Exit code the shell bootstrap treats as "a game just ran, restart me".
EXIT_RESTART = 42

_TARGET_FPS = 1 / 30
_BOTTOM_REFRESH = 0.09


class App:
    """Wires platform, library and session into a running frontend."""

    def __init__(
        self,
        platform: Platform,
        config: Config,
        translator: Translator,
        library: Library,
    ) -> None:
        self.platform = platform
        self.config = config
        self.translator = translator
        self.library = library
        self.art = ArtProvider(library, platform)
        self.session = Session(library, config, translator)
        self._canvases: list = []
        self._painters: list[Painter] = []
        self._running = True
        self._launch_plan = None

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
        self._painters = [
            Painter(canvas, metrics_for(*canvas.size), self.platform, self.translator)
            for canvas in canvases
        ]
        self._painters[0].single = not dual

        last_frame = 0.0
        frames = 0
        try:
            while self._running:
                for event in self.platform.poll_events(timeout=_TARGET_FPS):
                    self._handle(event)

                now = time.monotonic()
                if now - last_frame < _TARGET_FPS:
                    time.sleep(0.005)
                    continue
                last_frame = now
                self._draw()
                frames += 1
                if max_frames is not None and frames >= max_frames:
                    break
        finally:
            self.platform.shutdown()

        if self._launch_plan is not None:
            return EXIT_RESTART
        return 0

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    def _handle(self, event: InputEvent) -> None:
        outcome = self.session.handle(event)
        if outcome.quit:
            self._running = False
            return
        if outcome.launch is not None:
            self._launch(outcome.launch)

    def _notify(self, message: str) -> None:
        self.session.notify(message)

    # ------------------------------------------------------------------ #
    # Drawing
    # ------------------------------------------------------------------ #

    def _draw(self) -> None:
        for index, painter in enumerate(self._painters):
            if index == 0:
                self._draw_top(painter)
            else:
                self._draw_bottom(painter)
            self.platform.present(index)

    def _draw_top(self, painter: Painter) -> None:
        painter.clear()
        status_bar(painter, dual=len(self._canvases) > 1)
        if self.session.view == VIEW_GAMES:
            self._draw_games(painter)
        else:
            self._draw_home(painter)

        if self.session.modal == MODAL_MENU:
            menu.draw(painter, self.session)
        elif self.session.modal == MODAL_EXIT:
            menu.draw_exit(painter, self.session)

        message = self.session.active_toast()
        if message:
            toast(painter, message)

    def _draw_home(self, painter: Painter) -> None:
        session = self.session
        key = session.current_system_key()
        if key not in ("ALL", "FAV", "RECENT"):
            # Load lazily so the selected platform card can show real artwork.
            self.library.load_games(key)

        tiles = self._home_tiles()
        index = session.platform_index % max(1, len(tiles))

        home.draw(
            painter,
            self.art,
            tiles=tiles,
            index=index,
            info_title=display_name(key),
            info_subtitle=self._info_subtitle(key),
            info_right=self._info_right(key),
            previews=self._previews(key),
            hints=[
                ("A", self.translator("btn.enter")),
                ("Y", self.translator("btn.favorite")),
                ("START", self.translator("btn.menu")),
            ],
        )

    def _draw_games(self, painter: Painter) -> None:
        session = self.session
        all_games = session.games()
        title = self._system_title()
        hints = games.footer_hints(painter, session.layout, self.translator)

        if not all_games:
            games.header(painter, title=title, subtitle="0", right="")
            painter.text(
                (painter.width // 2, painter.height // 2),
                painter.translator("games.empty"),
                size=14, fill=(74, 74, 80, 255), anchor="mm",
            )
            button_bar(painter, hints)
            return

        subtitle = str(len(all_games))
        right = (f"{self.translator('games.filter_' + session.filter)} · "
                 f"{self.translator('games.layout_' + session.layout)}")
        games.header(painter, title=title, subtitle=subtitle, right=right)

        if session.layout == "grid":
            games.draw_grid(painter, self.art, all_games, session.game_index,
                            cols=painter.metrics.grid_cols,
                            rows=painter.metrics.grid_rows(single=painter.single))
        elif session.layout == "carousel":
            games.draw_carousel(painter, self.art, all_games, session.game_index)
        else:
            games.draw_list(painter, self.art, all_games, session.game_index,
                            rows_per_page=painter.metrics.rows_per_page(single=painter.single))
        button_bar(painter, hints)

    def _draw_bottom(self, painter: Painter) -> None:
        session = self.session
        game = session.current_game() if session.view == VIEW_GAMES else None
        meta = self._meta(game) if game is not None else None
        playing = bool(self.config.bottom_video and game and game.has_asset(ASSET_VIDEO))

        bottom.draw(
            painter,
            self.art,
            game,
            meta,
            key_label=display_name(session.current_system_key()),
            hints=self._bottom_hints(),
            playing_video=playing,
        )

    # ------------------------------------------------------------------ #
    # View models
    # ------------------------------------------------------------------ #

    def _home_tiles(self) -> list[home.Tile]:
        tiles: list[home.Tile] = []
        for key in self.session.system_keys():
            tiles.append(home.Tile(
                key=key,
                title=display_name(key),
                subtitle=self._tile_subtitle(key),
                artwork=self._representative(key),
            ))
        return tiles

    def _tile_subtitle(self, key: str) -> str:
        if key == "ALL":
            return str(self.library.last_scan.total_roms) if self.library.last_scan else ""
        if key in ("FAV", "RECENT"):
            return self.translator("bottom.games_total")
        return str(self.library.rom_count(key))

    def _representative(self, key: str):
        if key in ("ALL", "FAV", "RECENT"):
            games = self.library.aggregate(key)
            return games[0] if games else None
        library = self.library.system(key)
        return library.games[0] if library.games else None

    def _info_subtitle(self, key: str) -> str:
        if key in ("ALL", "FAV", "RECENT"):
            return ""
        return lookup(key).label

    def _info_right(self, key: str) -> str:
        if key in ("ALL", "FAV", "RECENT"):
            return ""
        definition = lookup(key)
        return "standalone" if definition.is_standalone else "RetroArch"

    def _previews(self, key: str) -> list[Game]:
        if key in ("ALL", "FAV", "RECENT"):
            return self.library.aggregate(key)[:6]
        return self.library.resolve_all(key)[:6]

    def _system_title(self) -> str:
        return display_name(self.session.current_system_key())

    def _meta(self, game: Game) -> bottom.Meta | None:
        system_key = self.session.current_system_key()
        core = lookup(system_key).core_label
        stars = game.rating_stars
        last = game.last_played
        last_text = self.translator("bottom.today") if last and _is_today(last) else (
            self.translator("bottom.days_ago", days=_days_since(last)) if last else "-"
        )
        return bottom.Meta(
            system_label=display_name(system_key),
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
            source_note=self.translator("bottom.source"),
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
        self._launch_plan = plan
        self._running = False
        self.platform.launch_game(plan.argv)


def _now():
    from datetime import datetime

    return datetime.now()


def _is_today(value) -> bool:
    from datetime import datetime

    return value.date() == datetime.now().date()


def _days_since(value) -> int:
    from datetime import datetime

    return max(0, (datetime.now() - value).days)
