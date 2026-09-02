"""The bottom panel's "metadata from" line must name the file really used.

It used to be a single hard-coded string reading ``gamelist.xml (ES-DE)``,
which quietly lied on every card that carries only a Pegasus
``metadata.pegasus.txt`` -- the very case that made the line useless.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.core.model import Game
from retrostation.data.library import Library
from retrostation.ui.app import App
from tests.conftest import FakePlatform


@pytest.fixture
def app(rom_root: Path) -> App:
    platform = FakePlatform(rom_root)
    config = Config()
    return App(platform, config, Translator(config.language), Library(platform, config))


def _game(rom_root: Path) -> Game:
    return Game.from_rom("FC", rom_root / "FC" / "魂斗罗.nes")


class TestSourceNote:
    def test_esde_only(self, app: App, rom_root: Path) -> None:
        game = _game(rom_root)
        game.sources["esde"] = "gamelist.xml"
        note = app._source_note(game)
        assert "gamelist.xml" in note
        assert "metadata.pegasus.txt" not in note

    def test_pegasus_only_names_its_own_file(self, app: App, rom_root: Path) -> None:
        """The bug: a Pegasus-only card still claimed to read gamelist.xml."""
        game = _game(rom_root)
        game.sources["pegasus"] = "metadata.pegasus.txt"
        note = app._source_note(game)
        assert "metadata.pegasus.txt" in note
        assert "gamelist.xml" not in note

    def test_both_are_named(self, app: App, rom_root: Path) -> None:
        game = _game(rom_root)
        game.sources["esde"] = "gamelist.xml"
        game.sources["pegasus"] = "metadata.pegasus.txt"
        note = app._source_note(game)
        assert "gamelist.xml" in note
        assert "metadata.pegasus.txt" in note

    def test_rom_without_metadata_does_not_claim_one(self, app: App, rom_root: Path) -> None:
        assert "gamelist.xml" not in app._source_note(_game(rom_root))
