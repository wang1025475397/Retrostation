"""Android launch plans: which app is started, and with what.

The Android half of a launch is assembled in Python and handed to the host as
JSON, so it can be checked without a device.  Every case below was wrong on a
real handset first:

* RetroArch ships under three package ids, and the handset had the one the code
  did not name -- "install RetroArch" appeared over a running RetroArch;
* ``LIBRETRO`` was the core's *bare file name*, which is the Linux shape
  (``retroarch -L <name>``).  On Android a bare name opens nothing: the content
  loaded and rendered on a black screen;
* a system whose emulator is an app of its own (NDS -> DraStic) was pushed
  through RetroArch, or refused with "no emulator on Android";
* DraStic takes the ROM as the *storage provider's* ``content://`` URI, so the
  plan has to ask for that shape and the launch path needs the folder grant.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.data.library import Library
from retrostation.launcher.launch import (
    RETROARCH_ACTIVITY,
    RETROARCH_PACKAGES,
    LaunchError,
    android_missing_app,
    build_android_plan,
)
from retrostation.platform.targets import UnsupportedTarget
from retrostation.ui.app import App
from tests.conftest import FakePlatform


def plan(system: str, name: str = "rom.zip"):
    """The Android plan for ``<system>/<name>`` -- all a plan reads of a game."""
    rom = Path("/storage/emulated/0/Roms") / system / name
    return build_android_plan(SimpleNamespace(key=f"{system}/{name}", path=rom), Config())


class TestRetroArch:
    def test_the_build_most_phones_get_is_named_first(self) -> None:
        # The 64-bit "Plus" build is what the Play Store installs on most phones;
        # a hardcoded "com.retroarch" was the bug.
        assert RETROARCH_PACKAGES[0] == "com.retroarch.aarch64"

    def test_every_shipped_build_is_tried_in_order(self) -> None:
        target = plan("gb", "batman.gb").target
        tried = [target, *target.fallbacks]
        assert [candidate.package for candidate in tried] == list(RETROARCH_PACKAGES)
        assert {candidate.activity for candidate in tried} == {RETROARCH_ACTIVITY}

    def test_the_core_travels_as_a_path_not_a_name(self) -> None:
        core = dict(plan("gb", "batman.gb").target.extras)["LIBRETRO"]
        assert core == (
            "/data/user/0/com.retroarch.aarch64/cores/gambatte_libretro_android.so"
        )

    def test_each_build_names_its_own_core_directory(self) -> None:
        target = plan("gba", "x.gba").target
        for candidate in (target, *target.fallbacks):
            core = dict(candidate.extras)["LIBRETRO"]
            assert core.startswith(f"/data/user/0/{candidate.package}/cores/")
            assert core.endswith("_libretro_android.so")

    def test_the_rom_keeps_its_name(self) -> None:
        name = "Batman - Return of The Joker.gb"
        rom = Path("/storage/emulated/0/Roms") / "gb" / name
        assert dict(plan("gb", name).target.extras)["ROM"] == str(rom)

    def test_the_emulator_gets_a_task_of_its_own(self) -> None:
        # Started inside our task, the game and the frontend share one recents
        # card -- and swiping that card away kills the frontend with the game.
        assert "new_task" in plan("gb", "x.gb").target.flags

    def test_retroarch_is_not_reported_as_a_missing_app(self) -> None:
        # The shared message already names RetroArch; naming it twice reads wrong.
        assert android_missing_app(plan("gb", "x.gb")) == ""


class TestStandaloneApps:
    @pytest.mark.parametrize("system", ["nds", "NDS", "NDS Hack"])
    def test_nds_goes_to_drastic_however_the_directory_is_spelled(self, system: str) -> None:
        # ``lookup`` keeps the directory's own spelling ("NDS"), and a variant
        # directory carries its whole name -- both have to reach the table.
        target = plan(system, "007.zip").target
        assert (target.package, target.activity) == (
            "com.dsemu.drastic",
            "com.dsemu.drastic.DraSticActivity",
        )

    def test_the_rom_rides_as_intent_data(self) -> None:
        target = plan("NDS", "007 - x.zip").target
        # No MIME type: its filter matches the scheme alone, and a type made the
        # intent unresolvable on a real device.  Nothing rides as an extra
        # either -- a named activity with the ROM as its data is the whole shape.
        assert target.mime == ""
        assert target.extras == ()

    def test_the_rom_is_handed_over_as_the_providers_uri(self) -> None:
        rom = Path("/storage/emulated/0/Roms") / "NDS" / "007 - x.zip"
        target = plan("NDS", "007 - x.zip").target
        # ``file://`` is the path the host resolves to the storage provider's
        # ``content://`` URI; ``document_uri`` is what asks it to.  A raw path and
        # this app's own FileProvider URI both leave DraStic on its main menu.
        assert target.data_uri == f"file://{rom}"
        assert target.document_uri is True

    def test_it_gets_a_task_of_its_own(self) -> None:
        # Without these the emulator joins our task stack: recents shows one card
        # for both apps, and swiping it away kills the frontend with the game.
        assert plan("nds", "007.zip").target.flags == (
            "new_task", "clear_task", "clear_top")

    def test_the_missing_app_is_named_for_the_player(self) -> None:
        built = plan("nds", "007.zip")
        assert built.core_label == "DraStic"
        assert android_missing_app(built) == "DraStic"


class TestCardSpellings:
    def test_psx_is_playstation(self) -> None:
        # ``Roms/psx`` is what ES-DE and cards copied from the handheld use; it
        # used to fall through to "no emulator on Android".
        assert plan("psx", "disc.cue").target.package in RETROARCH_PACKAGES

    def test_a_system_with_no_android_emulator_is_refused(self) -> None:
        # The caller turns this into "this system is not supported on Android
        # yet" instead of the Linux launcher script the message would name.
        with pytest.raises(LaunchError):
            plan("psp", "game.iso")


class AndroidishPlatform(FakePlatform):
    """A fake that answers the way the Android adapter does.

    ``name`` is what selects the Android launch plan, and off Android
    ``rom_access_granted()`` is always true -- so the SAF gate below is only ever
    exercised with a platform like this one.
    """

    name = "android"

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.granted = False
        self.asked = 0
        self.targets: list[object] = []
        #: Raised by ``run_foreground`` when a test wants "nothing can take it".
        self.foreground_error: Exception | None = None

    # -- SAF (DESIGN.ANDROID §8.3) ---------------------------------------- #
    def rom_access_granted(self) -> bool:
        return self.granted

    def rom_access_label(self) -> str:
        return "Roms" if self.granted else ""

    def request_rom_access(self) -> None:
        self.asked += 1

    # -- launching --------------------------------------------------------- #
    def can_stay_resident(self) -> bool:
        return True                      # Android keeps its windows (§8.11)

    def launch_game(self, target) -> None:
        self.targets.append(target)

    def run_foreground(self, target) -> int | None:
        if self.foreground_error is not None:
            raise self.foreground_error
        self.targets.append(target)
        return 0


@pytest.fixture
def android_app(tmp_path: Path):
    """An app whose platform reports itself as Android, with games on the card."""
    root = tmp_path / "Roms"
    for system, name, blob in (("NDS", "007 - 血石.zip", b"zip"),
                               ("GB", "batman.gb", b"gb"),
                               ("PSP", "game.iso", b"iso")):
        (root / system).mkdir(parents=True, exist_ok=True)
        (root / system / name).write_bytes(blob)
    platform = AndroidishPlatform(root)
    config = Config()
    library = Library(platform, config)
    library.scan()
    app = App(platform, config, Translator(config.language), library)
    return app, platform, library


def game_of(library: Library, system: str):
    key = next(key for key in library.system_keys() if key.upper() == system)
    return library.resolve_all(key)[0]


class TestAndroidLaunchFlow:
    """What the player sees when A is pressed on Android (DESIGN.ANDROID §8.3)."""

    def test_a_content_uri_emulator_asks_for_the_folder_first(
            self, android_app, monkeypatch) -> None:
        # DraStic can only be handed a ``content://`` URI this app holds, so the
        # folder has to be authorised before anything is torn down -- and the
        # player has to be told why nothing started.
        app, platform, library = android_app
        monkeypatch.setattr("retrostation.ui.app.is_android_skin", lambda: True)

        app._launch(game_of(library, "NDS"))

        assert platform.asked == 1                      # the picker was opened
        assert app._launch_plan is None                 # nothing was started
        assert app.session.active_toast() == app.translator.t("launch.grant_rom")

    def test_with_the_grant_the_rom_goes_over_as_the_providers_uri(
            self, android_app) -> None:
        app, platform, library = android_app
        platform.granted = True

        app._launch(game_of(library, "NDS"))

        target = app._launch_plan.target
        assert target.package == "com.dsemu.drastic"
        assert target.document_uri is True
        assert target.data_uri.startswith("file://")
        assert platform.asked == 0                      # no picker this time

    def test_retroarch_is_not_gated_on_the_folder_grant(self, android_app) -> None:
        # A RetroArch launch carries the ROM in its extras, so it needs no SAF
        # grant -- asking for one would open a picker for nothing.
        app, platform, library = android_app

        app._launch(game_of(library, "GB"))

        assert platform.asked == 0
        assert app._launch_plan.target.package == RETROARCH_PACKAGES[0]

    def test_a_system_with_no_android_emulator_says_so(self, android_app, monkeypatch) -> None:
        app, platform, library = android_app
        monkeypatch.setattr("retrostation.ui.app.is_android_skin", lambda: True)

        app._launch(game_of(library, "PSP"))

        assert app._launch_plan is None
        assert app.session.active_toast() == app.translator.t("launch.unsupported_system")

    def test_nothing_that_can_take_the_intent_names_the_app_to_install(
            self, android_app) -> None:
        # The hand-over is where "no app can handle this" surfaces; for a system
        # with an emulator of its own the player is sent to *that* app.
        app, platform, library = android_app
        platform.foreground_error = UnsupportedTarget("android", None)
        built = build_android_plan(game_of(library, "NDS"), app.config)

        app._launch_resident(built)

        assert app.session.active_toast() == app.translator.t(
            "launch.need_app", app="DraStic")

    def test_a_missing_retroarch_keeps_the_retroarch_wording(self, android_app) -> None:
        app, platform, library = android_app
        platform.foreground_error = UnsupportedTarget("android", None)
        built = build_android_plan(game_of(library, "GB"), app.config)

        app._launch_resident(built)

        assert app.session.active_toast() == app.translator.t("launch.unsupported")
