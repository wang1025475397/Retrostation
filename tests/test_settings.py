"""M6: the settings dialog actually changes things.

Several rows used to be dead ends -- "language" had a row and no handler at
all, "brightness" had neither, and nothing was saved until a game happened to
be launched.  These drive the dialog the way a player would and check that the
app applies *and* persists the result.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.core.theme import COLORS, DEFAULT_THEME, DEFAULT_VARIANT
from retrostation.data.library import Library
from retrostation.platform.base import InputAction, InputEvent
from retrostation.ui.app import EXIT_RESTART_UI, App
from retrostation.ui.session import MODAL_MENU
from tests.conftest import FakePlatform


@pytest.fixture(autouse=True)
def _restore_palette():
    """The palette is a shared instance -- leave it as we found it."""
    yield
    COLORS.apply(DEFAULT_THEME, DEFAULT_VARIANT)


def settings_app(rom_root: Path):
    platform = FakePlatform(rom_root)
    config = Config()
    library = Library(platform, config)
    library.scan()
    app = App(platform, config, Translator(config.language), library)
    return app, platform, config


def press(app: App, platform: FakePlatform, *events: InputEvent) -> None:
    platform.send(*events)
    app.run(max_frames=1)


def choose(app: App, platform: FakePlatform, key: str) -> None:
    """Walk the cursor to ``key`` and confirm it (A -- for action rows)."""
    for _ in range(len(app.session.menu_rows())):
        if app.session.menu_rows()[app.session.menu_index][0] == key:
            break
        press(app, platform, InputEvent(InputAction.DOWN))
    assert app.session.menu_rows()[app.session.menu_index][0] == key, f"{key} is not a menu row"
    press(app, platform, InputEvent(InputAction.A))


def nudge(app: App, platform: FakePlatform, key: str, direction: int = 1) -> None:
    """Walk the cursor to ``key`` and cycle its value with LEFT/RIGHT."""
    for _ in range(len(app.session.menu_rows())):
        if app.session.menu_rows()[app.session.menu_index][0] == key:
            break
        press(app, platform, InputEvent(InputAction.DOWN))
    assert app.session.menu_rows()[app.session.menu_index][0] == key, f"{key} is not a menu row"
    press(app, platform, InputEvent(InputAction.RIGHT if direction > 0 else InputAction.LEFT))


def open_menu(app: App, platform: FakePlatform) -> None:
    press(app, platform, InputEvent(InputAction.START))
    assert app.session.modal == MODAL_MENU


class TestScreenMode:
    def test_switching_it_restarts_instead_of_rebuilding(self, rom_root: Path) -> None:
        """New windows cannot be built inside a live process (DESIGN §4.4).

        So the app hands back to the bootstrap with EXIT_RESTART_UI rather than
        calling init_display again -- which used to leave the player stuck in
        whichever mode they had switched to.
        """
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)

        press(app, platform, InputEvent(InputAction.START))   # screen is row 0
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.RIGHT))          # stage "single"
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))              # commit -> restart
        assert app.run(max_frames=1) == EXIT_RESTART_UI
        assert config.screen_mode == "single"

    def test_arrows_do_not_restart(self, rom_root: Path) -> None:
        """A stray LEFT/RIGHT stages the mode but must not bounce the
        frontend (exit 43) -- only A commits the restart."""
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        open_menu(app, platform)                     # screen is row 0
        press(app, platform, InputEvent(InputAction.RIGHT))
        assert config.screen_mode == "single"        # staged, not applied
        assert not app.session.restart_requested     # no restart yet
        assert app.session.modal == MODAL_MENU

    def test_plain_confirm_does_not_restart(self, rom_root: Path) -> None:
        """Opening the menu and confirming with nothing staged must not
        restart -- config.rom_root may still be "auto" while the app resolved
        a real card, and that difference is not a card switch."""
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        open_menu(app, platform)
        press(app, platform, InputEvent(InputAction.A))   # nothing staged
        assert app.run(max_frames=1) != EXIT_RESTART_UI
        assert not app.session.restart_requested
        assert app.session.modal == ""


class TestTheme:
    def test_switching_the_theme_repaints_with_it(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        before = COLORS.accent

        open_menu(app, platform)
        nudge(app, platform, "theme")
        press(app, platform, InputEvent(InputAction.A))   # commit

        assert COLORS.accent != before, "the shared palette must have moved"
        assert config.theme != DEFAULT_THEME

    def test_switching_the_variant_keeps_the_accent(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        accent = COLORS.accent

        open_menu(app, platform)
        nudge(app, platform, "variant")
        press(app, platform, InputEvent(InputAction.A))   # commit

        assert COLORS.bg != (20, 20, 20, 255)
        assert COLORS.accent == accent, "the accent family survives a light/dark switch"


class TestLanguage:
    def test_it_takes_effect_immediately(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)

        open_menu(app, platform)
        nudge(app, platform, "language")
        press(app, platform, InputEvent(InputAction.A))   # commit

        assert config.language == "en_US"          # auto -> first shipped bundle
        assert app.translator.language == "en_US"
        assert app.translator("btn.start") == "Start"


class TestBacklight:
    def test_it_reaches_the_platform(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        calls: list[tuple[int, int]] = []
        platform.set_brightness = lambda value, index=0: calls.append((value, index))  # type: ignore[method-assign]

        app.run(max_frames=1)
        open_menu(app, platform)
        nudge(app, platform, "brightness")
        press(app, platform, InputEvent(InputAction.A))   # commit

        assert calls, "the backlight must be pushed when it changes"
        assert calls[-1][0] == 160                 # 140 + one step

    def test_left_and_right_nudge_it(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        open_menu(app, platform)
        for _ in range(len(app.session.menu_rows())):
            if app.session.menu_rows()[app.session.menu_index][0] == "brightness":
                break
            press(app, platform, InputEvent(InputAction.DOWN))

        press(app, platform, InputEvent(InputAction.LEFT))
        assert config.brightness["top"] == 120
        press(app, platform, InputEvent(InputAction.LEFT))
        assert config.brightness["top"] == 100

    def test_it_cannot_be_dimmed_to_black(self, rom_root: Path) -> None:
        """A screen driven to 0 looks like a crash you cannot undo."""
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        open_menu(app, platform)
        for _ in range(len(app.session.menu_rows())):
            if app.session.menu_rows()[app.session.menu_index][0] == "brightness":
                break
            press(app, platform, InputEvent(InputAction.DOWN))

        for _ in range(20):
            press(app, platform, InputEvent(InputAction.LEFT))
        assert config.brightness["top"] >= 20


class TestPersistence:
    def test_settings_are_saved_without_waiting_for_a_launch(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)

        open_menu(app, platform)
        nudge(app, platform, "status_bar")
        press(app, platform, InputEvent(InputAction.A))   # commit

        saved = json.loads((Path(platform.config_dir) / "config.json").read_text(encoding="utf-8"))
        assert saved["show_status_bar"] is False


class TestThumbnailCache:
    """The switch and the "empty it" row.

    ``enabled`` is read once, when the library builds its cache, so the row has
    to reach into the live object -- a config field that nobody applies would
    look like a working switch and do nothing.
    """

    @staticmethod
    def _cover(rom_root: Path) -> Path:
        media = rom_root / "FC" / "Imgs"
        media.mkdir(parents=True, exist_ok=True)
        cover = media / "cover.png"
        Image.new("RGB", (300, 400), (10, 20, 30)).save(cover)
        return cover

    @staticmethod
    def _cache_dir(rom_root: Path) -> Path:
        return rom_root / "FC" / "Imgs" / ".cache"

    @staticmethod
    def _cache_files(rom_root: Path) -> list[str]:
        """Every cached file on the card, as stable relative paths.

        The home page renders covers before the test gets to the switch, so
        "the cache directory exists" proves nothing -- what matters is whether
        anything was added after the switch went off.
        """
        return sorted(
            str(path.relative_to(rom_root))
            for path in rom_root.rglob("*")
            if path.is_file() and ".cache" in path.parts
        )

    def test_both_rows_are_offered(self, rom_root: Path) -> None:
        app, _platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        keys = [key for key, _label, _value in app.session.menu_rows()]
        assert "tcache" in keys
        assert "clear_cache" in keys

    def test_the_switch_only_takes_effect_on_commit(self, rom_root: Path) -> None:
        app, platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        cache = app.session.library._thumbnails  # noqa: SLF001

        open_menu(app, platform)
        nudge(app, platform, "tcache")
        assert config.thumbnail_cache is False       # staged
        assert cache.enabled is True                 # not applied yet

        press(app, platform, InputEvent(InputAction.A))
        assert cache.enabled is False

    def test_switching_off_stops_writing_to_the_card(self, rom_root: Path) -> None:
        """Artwork still shows -- it is just decoded instead of cached."""
        app, platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        open_menu(app, platform)
        nudge(app, platform, "tcache")
        press(app, platform, InputEvent(InputAction.A))

        cover = self._cover(rom_root)
        cache = app.session.library._thumbnails  # noqa: SLF001
        before = self._cache_files(rom_root)
        assert cache.get("cover", cover, 40, 40) is not None
        cache.flush()
        assert self._cache_files(rom_root) == before, "wrote to the card with the cache off"

    def test_the_switch_survives_a_restart(self, rom_root: Path) -> None:
        app, platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        open_menu(app, platform)
        nudge(app, platform, "tcache")
        press(app, platform, InputEvent(InputAction.A))

        saved = json.loads((Path(platform.config_dir) / "config.json").read_text(encoding="utf-8"))
        assert saved["thumbnail_cache"] is False

    def test_clearing_removes_every_entry(self, rom_root: Path) -> None:
        app, _platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        cache = app.session.library._thumbnails  # noqa: SLF001
        self._cover(rom_root)
        assert cache.get("cover", self._cover(rom_root), 40, 40) is not None
        cache.flush()
        assert any(self._cache_dir(rom_root).iterdir())

        assert app.session.library.clear_thumbnails() >= 1
        assert not self._cache_dir(rom_root).exists()

    def test_the_cache_starts_over_after_being_cleared(self, rom_root: Path) -> None:
        """Its directories are gone, and so is our memory of them.

        Keeping the old ones would skip the ``mkdir`` and make the next write
        fail on a missing parent -- which would look exactly like a cache that
        silently does nothing.
        """
        app, _platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        cache = app.session.library._thumbnails  # noqa: SLF001
        cover = self._cover(rom_root)
        assert cache.get("cover", cover, 40, 40) is not None
        cache.flush()
        app.session.library.clear_thumbnails()

        assert cache.get("cover", cover, 40, 40) is not None
        cache.flush()
        assert any(self._cache_dir(rom_root).iterdir())

    def test_the_row_does_not_block_the_frame(self, rom_root: Path) -> None:
        """Walking a full card is seconds of stat() calls; the input thread
        hands it off and answers with a toast, like the background scan does."""
        app, platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        cover = self._cover(rom_root)
        cache = app.session.library._thumbnails  # noqa: SLF001
        assert cache.get("cover", cover, 40, 40) is not None
        cache.flush()
        expected = len(self._cache_files(rom_root))
        assert expected, "nothing to clear"

        open_menu(app, platform)
        choose(app, platform, "clear_cache")

        assert app.session.modal == ""
        # The worker is a daemon thread; give it frames rather than racing it.
        for _ in range(200):
            if app.session.toast_message != app.translator("toast.cache_clearing"):
                break
            app.run(max_frames=1)
        assert app.session.toast_message == app.translator(
            "toast.cache_cleared", count=expected
        )
        assert self._cache_files(rom_root) == []


class TestStorageCard:
    """Two cards are browsed separately, never merged into one library.

    The row only exists when there is somewhere to switch to, and the switch
    itself is a restart: the library is built around a single ROM root, and
    rebuilding it in a live process is what crashes under Wayland (§4.4).
    """

    @staticmethod
    def _two_cards(app: App, tmp_path: Path) -> tuple[Path, Path]:
        first, second = tmp_path / "card1", tmp_path / "card2"
        app.session.rom_roots = [(first, "TF1"), (second, "TF2")]
        return first, second

    def test_one_card_hides_the_row(self, rom_root: Path) -> None:
        app, _platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        app.session.rom_roots = app.session.rom_roots[:1]
        keys = [key for key, _label, _value in app.session.menu_rows()]
        assert "card" not in keys

    def test_two_cards_name_the_active_one(self, rom_root: Path, tmp_path: Path) -> None:
        app, _platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        first, _second = self._two_cards(app, tmp_path)
        app.session.current_rom_root = first
        values = {key: value for key, _label, value in app.session.menu_rows()}
        assert values["card"] == "TF1"

    def test_switching_picks_the_next_card(self, rom_root: Path, tmp_path: Path) -> None:
        app, _platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        first, second = self._two_cards(app, tmp_path)
        app.session.current_rom_root = first

        app.session._toggle_menu_row("card")  # noqa: SLF001 - stage the next card
        app.session._apply_menu("card")  # noqa: SLF001 - commit the restart

        assert config.rom_root == str(second)
        assert app.session.restart_requested is True
        assert app.session.card_changed is True

    def test_switching_wraps_back_to_the_first(self, rom_root: Path, tmp_path: Path) -> None:
        app, _platform, config = settings_app(rom_root)
        app.run(max_frames=1)
        first, second = self._two_cards(app, tmp_path)
        app.session.current_rom_root = second
        app.session._toggle_menu_row("card")  # noqa: SLF001
        app.session._apply_menu("card")  # noqa: SLF001
        assert config.rom_root == str(first)

    def test_each_card_keeps_its_own_index(self, tmp_path: Path) -> None:
        """A shared index would paint the old card's systems on the first frame.

        ``cached_only`` is what brings the first frame up populated, so with one
        index file a switch would show the wrong library until the background
        scan finished.
        """
        one, two = tmp_path / "card1", tmp_path / "card2"
        one.mkdir()
        two.mkdir()
        first = Library(FakePlatform(one), Config())
        second = Library(FakePlatform(two), Config())
        assert first._index_path() != second._index_path()  # noqa: SLF001

    def test_switching_restarts_and_is_persisted(self, rom_root: Path, tmp_path: Path) -> None:
        app, platform, _config = settings_app(rom_root)
        app.run(max_frames=1)
        first, second = self._two_cards(app, tmp_path)
        app.session.current_rom_root = first
        app.session._toggle_menu_row("card")  # noqa: SLF001
        app.session._apply_menu("card")  # noqa: SLF001

        assert app.run(max_frames=1) == EXIT_RESTART_UI
        saved = json.loads((Path(platform.config_dir) / "config.json").read_text(encoding="utf-8"))
        assert saved["rom_root"] == str(second)
