"""M5: handing a game over to the shell bootstrap, and coming back.

The contract under test (DESIGN §8.1 / §8.2):

* the frontend records the launch command in a file and exits 42 -- it must
  **not** ``execv``, or the emulator's exit code becomes ours and the bootstrap
  reads a plain quit instead of "a game ran";
* it saves the place first, so the fresh process the bootstrap starts lands the
  player back on the game they launched.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.core.state import read_state
from retrostation.data.library import Library
from retrostation.launcher.launch import LAUNCH_CMD_PATH, build_plan, write_launch_cmd
from retrostation.platform.base import InputAction, InputEvent
from retrostation.ui.app import EXIT_RESTART, App
from retrostation.ui.session import VIEW_GAMES, VIEW_PLATFORMS
from tests.conftest import FakePlatform


def _make_app(rom_root: Path) -> App:
    """A scanned app with a fake RetroArch bootstrap."""
    platform = FakePlatform(rom_root)
    config = Config()
    script = rom_root / "RA_launch.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    config.launcher.ra_script = str(script)
    library = Library(platform, config)
    library.scan()
    return App(platform, config, Translator(config.language), library)


@pytest.fixture
def app(rom_root: Path) -> App:
    return _make_app(rom_root)


def _enter_fc(app: App) -> None:
    """Walk the home page to FC and open it.

    The aggregates (ALL / FAV / RECENT) come first, so plain "press A" lands on
    ALL -- tests that care which system they are in have to walk right.
    (DOWN now enters the preview strip, so platform navigation is horizontal.)
    """
    platform = app.platform
    steps = app.session.system_keys().index("FC")
    platform.send(
        *(InputEvent(InputAction.RIGHT) for _ in range(steps)),
        InputEvent(InputAction.A),
    )
    app.run(max_frames=1)


class TestLaunchCommandFile:
    def test_round_trips_arguments_that_need_quoting(self, tmp_path: Path) -> None:
        argv = [
            "/mnt/mod/ctrl/RA_launch.sh",
            "fceumm_libretro.so",
            "/mnt/mmc/Roms/FC/恶魔城 (Castlevania).nes",
        ]
        path = tmp_path / "launch.cmd"
        write_launch_cmd(argv, path)

        tokens = shlex.split(path.read_text(encoding="utf-8"))
        assert tokens[:2] == ["set", "--"]
        assert tokens[2:] == argv

    def test_replaces_a_previous_command(self, tmp_path: Path) -> None:
        path = tmp_path / "launch.cmd"
        write_launch_cmd(["first"], path)
        write_launch_cmd(["second"], path)
        assert shlex.split(path.read_text(encoding="utf-8"))[2:] == ["second"]

    def test_lands_where_the_bootstrap_looks(self) -> None:
        assert LAUNCH_CMD_PATH == Path("/tmp/retrostation_launch.cmd")


class TestStandaloneEmulators:
    def test_rom_is_expanded_into_the_launcher_command(self) -> None:
        """SATURN / DC / PSP / PICO ship their own launcher with a {rom} slot.

        It has to survive a path with a space and CJK in it: the template is a
        string, so an unquoted expansion would hand the launcher two arguments.
        """
        from retrostation.core.model import Game

        rom = Path("/mnt/mmc/Roms/SATURN/我的游戏 (J).cue")
        game = Game(key="SATURN/我的游戏 (J).cue", path=rom, name="我的游戏")

        plan = build_plan(game, Config())

        assert plan.argv[0] == "/mnt/vendor/deep/saturn/launch.sh"
        assert plan.argv[-1] == str(rom)


class TestLaunchHandsOff:
    def test_launch_records_the_command_and_returns_restart(self, app: App) -> None:
        platform = app.platform
        platform.send(InputEvent(InputAction.A))     # enter FC
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # launch the first game

        assert app.run(max_frames=1) == EXIT_RESTART
        assert platform.launched is not None
        assert platform.launched[0].endswith("RA_launch.sh")

    def test_launch_saves_where_the_player_was(self, app: App) -> None:
        platform = app.platform
        _enter_fc(app)
        platform.send(InputEvent(InputAction.DOWN))  # second game
        app.run(max_frames=1)
        wanted = app.session.current_game()

        platform.send(InputEvent(InputAction.A))     # launch it
        assert app.run(max_frames=1) == EXIT_RESTART

        state = read_state(Path(platform.config_dir) / "state.json")
        assert state["resume"]["game"] == wanted.key
        assert state["resume"]["system"] == "FC"
        assert state["resume"]["view"] == VIEW_GAMES


class TestResume:
    def test_a_fresh_process_returns_to_the_launched_game(self, rom_root: Path) -> None:
        """The M5 acceptance criterion: launch, play, quit, be back there."""
        app = _make_app(rom_root)
        platform = app.platform
        _enter_fc(app)
        platform.send(InputEvent(InputAction.DOWN))   # pick the second game
        app.run(max_frames=1)
        wanted = app.session.current_game().key

        platform.send(InputEvent(InputAction.A))      # launch
        assert app.run(max_frames=1) == EXIT_RESTART

        # The game runs, exits, and the bootstrap starts a brand new process.
        revived = _make_app(rom_root)
        revived.run(max_frames=1)

        assert revived.session.view == VIEW_GAMES
        assert revived.session.current_system_key() == "FC"
        assert revived.session.current_game().key == wanted

    def test_an_unknown_system_falls_back_to_the_home_page(self, app: App) -> None:
        """A card that no longer holds that system must not land anywhere odd."""
        assert app.session.apply_resume({"system": "NOSUCHSYSTEM", "view": VIEW_GAMES}) is False
        assert app.session.view == VIEW_PLATFORMS

    def test_resume_ignores_a_missing_game_key(self, app: App) -> None:
        assert app.session.apply_resume({"system": "FC", "view": VIEW_GAMES}) is True
        assert app.session.view == VIEW_GAMES


class TestLinuxHandOff:
    def test_launch_game_writes_a_file_instead_of_execing(self, tmp_path: Path) -> None:
        """The regression this milestone exists for.

        ``execv`` here replaced the process, so the emulator's exit code became
        the frontend's and the bootstrap stopped restarting us.  If it ever
        comes back, this test dies with it: there is no /bin/true on the test
        host, so an exec attempt raises instead of returning.
        """
        from retrostation.platform.linux.platform import LinuxPlatform

        platform = LinuxPlatform(rom_root=str(tmp_path), headless=True)
        target = tmp_path / "launch.cmd"
        platform.launch_cmd_path = str(target)

        platform.launch_game(["/bin/true", "a b"])

        assert shlex.split(target.read_text(encoding="utf-8"))[2:] == ["/bin/true", "a b"]
