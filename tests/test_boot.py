"""Boot / end-to-end tests: drive the real App with synthetic input.

These run the actual frame loop headlessly and assert on rendered output, so a
layout regression is caught without a handheld attached.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.data.library import Library
from retrostation.platform.base import InputAction, InputEvent, InputKind
from retrostation.ui.app import EXIT_RESTART, App
from tests.conftest import FakePlatform


@pytest.fixture
def app(rom_root: Path) -> App:
    platform = FakePlatform(rom_root)
    config = Config()
    # A fake RA bootstrap so launch works off-device too.
    script = rom_root / "RA_launch.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    config.launcher.ra_script = str(script)

    library = Library(platform, config)
    library.scan()
    return App(platform, config, Translator(config.language), library)


def frames(app: App, platform: FakePlatform, n: int = 1):
    app.run(max_frames=n)
    return platform.canvases


def send(platform: FakePlatform, *events: InputEvent) -> None:
    platform.send(*events)


class TestBoot:
    def test_renders_two_screens(self, app: App) -> None:
        platform = app.platform
        assert isinstance(platform, FakePlatform)
        canvases = frames(app, platform, n=1)
        assert len(canvases) == 2
        assert canvases[0].size == (640, 480)

    def test_home_screen_has_content(self, app: App) -> None:
        platform = app.platform
        top = frames(app, platform, n=1)[0].pil_image
        colours = set(top.getdata())
        assert (20, 20, 20, 255) in colours
        assert any(p[0] > 170 for p in colours), "accent colour must be drawn"

    def test_home_lists_systems_after_scan(self, app: App) -> None:
        keys = app.session.system_keys()
        assert "ALL" in keys and "FC" in keys
        assert app.library.rom_count("FC") == 3


class TestNavigation:
    def test_enter_and_back(self, app: App) -> None:
        platform = app.platform
        send(platform, InputEvent(InputAction.A))
        frames(app, platform, n=1)
        assert app.session.view == "games"

        send(platform, InputEvent(InputAction.B))
        frames(app, platform, n=1)
        assert app.session.view == "platforms"

    def test_move_between_platforms(self, app: App) -> None:
        platform = app.platform
        first = app.session.current_system_key()
        send(platform, InputEvent(InputAction.RIGHT))
        frames(app, platform, n=1)
        assert app.session.current_system_key() != first

    def test_grid_page_size_uses_columns(self, app: App) -> None:
        platform = app.platform
        send(platform, InputEvent(InputAction.A))
        send(platform, InputEvent(InputAction.X))  # list -> grid
        frames(app, platform, n=1)
        assert app.session.layout == "grid"

        before = app.session.game_index
        send(platform, InputEvent(InputAction.DOWN))
        frames(app, platform, n=1)
        metrics = app.session._metrics  # noqa: SLF001 - asserting the contract
        total = len(app.session.games())
        assert app.session.game_index == min(before + metrics.grid_cols, total - 1)

    def test_long_press_menu_opens_exit(self, app: App) -> None:
        platform = app.platform
        send(platform, InputEvent(InputAction.MENU, InputKind.LONG_PRESS))
        frames(app, platform, n=1)
        assert app.session.modal == "exit"

        send(platform, InputEvent(InputAction.A))
        assert app.run(max_frames=1) == 0  # plain quit, no restart


class TestLaunch:
    def test_launch_reports_restart_and_records_command(self, app: App) -> None:
        platform = app.platform
        send(platform, InputEvent(InputAction.A))   # enter FC
        frames(app, platform, n=1)
        send(platform, InputEvent(InputAction.A))   # launch the first game
        code = app.run(max_frames=1)

        assert code == EXIT_RESTART
        assert platform.launched is not None
        assert platform.launched[0].endswith("RA_launch.sh")
        assert platform.launched[2].endswith(".nes")

    def test_favourite_is_written_back(self, app: App, rom_root: Path) -> None:
        platform = app.platform
        send(platform, InputEvent(InputAction.A))   # enter FC
        send(platform, InputEvent(InputAction.Y))   # favourite first game
        frames(app, platform, n=1)

        text = (rom_root / "FC" / "gamelist.xml").read_text(encoding="utf-8")
        assert "<favorite>true</favorite>" in text
