"""Search state machine: combo trigger, letter grid, initials matching."""

from pathlib import Path

import pytest

from retrostation.core.config import Config, ConfigError
from retrostation.core.i18n import Translator
from retrostation.core.model import Game
from retrostation.platform.base import InputAction, InputEvent, InputKind
from retrostation.ui.session import (
    MODAL_NONE,
    MODAL_ROM_SELECT,
    MODAL_SEARCH,
    SEARCH_CODES,
    Session,
)


def press(action):
    return InputEvent(action, InputKind.PRESS)


class _StubLibrary:
    def __init__(self, by_system):
        self.by_system = by_system
        self.calls: list[tuple[str, str]] = []

    def system_keys(self):
        return list(self.by_system)

    def aggregate(self, key):
        self.calls.append(("aggregate", key))
        games: list[Game] = []
        for items in self.by_system.values():
            games.extend(items)
        return list(games)

    def resolve_all(self, key):
        self.calls.append(("resolve_all", key))
        return list(self.by_system.get(key, []))

    def save_state(self, game, system_key):
        return True

    def drop_games(self):
        pass


def _games() -> dict[str, list[Game]]:
    root = Path("/roms")
    return {
        "mame": [
            Game(key="mame/a.zip", path=root / "mame" / "a.zip",
                 name="龙与地下城2 汉化版", variants=[root / "mame" / "b.zip"]),
            Game(key="mame/s.zip", path=root / "mame" / "s.zip",
                 name="Street Fighter 2"),
        ],
        "snes": [
            Game(key="snes/m.zip", path=root / "snes" / "m.zip",
                 name="超级马里奥世界"),
        ],
        "fc": [
            Game(key="fc/h.zip", path=root / "fc" / "h.zip", name="魂斗罗"),
        ],
    }


def make_session(by_system=None) -> Session:
    session = Session(library=_StubLibrary(by_system or _games()),
                      config=Config(), translator=Translator("en_US"))
    session.handle(press(InputAction.SEARCH))
    assert session.modal == MODAL_SEARCH
    assert session.search_origin == "ALL"  # platform_index 0 is the ALL page
    return session


def type_text(session: Session, text: str) -> None:
    for ch in text:
        session.search_kb = SEARCH_CODES.index(ch)
        session.handle(press(InputAction.A))


def names(session: Session) -> list[str]:
    return [game.display_name for game in session.search_results()]


def test_combo_opens_search_over_current_context() -> None:
    session = make_session()
    assert session.search_text == ""
    assert session.search_focus == "kb"


def test_letters_type_into_the_query() -> None:
    session = make_session()
    type_text(session, "SF")
    assert session.search_text == "SF"
    assert names(session) == ["Street Fighter 2"]


def test_pinyin_initials_match_chinese_titles() -> None:
    session = make_session()
    type_text(session, "LYDXC")
    assert names(session) == ["龙与地下城2 汉化版"]


def test_aggregate_context_searches_the_whole_library() -> None:
    session = make_session()
    type_text(session, "HDL")  # 魂斗罗 lives in fc, not in the current page
    assert names(session) == ["魂斗罗"]
    assert ("aggregate", "ALL") in session.library.calls


def test_platform_context_searches_only_that_system() -> None:
    session = make_session()
    keys = session.system_keys()
    session.platform_index = keys.index("snes")
    session.handle(press(InputAction.SEARCH))   # close the dialog opened on ALL
    session.handle(press(InputAction.SEARCH))   # reopen from the snes page
    assert session.search_origin == "snes"
    type_text(session, "HDL")  # 魂斗罗 is not in snes
    assert session.search_results() == []
    session._set_search_text("")
    type_text(session, "CJML")
    assert names(session) == ["超级马里奥世界"]
    assert ("resolve_all", "snes") in session.library.calls


def test_up_off_the_top_row_jumps_into_the_results() -> None:
    session = make_session()
    type_text(session, "HDL")
    session.handle(press(InputAction.UP))       # cursor sits on row 0
    assert session.search_focus == "results"
    outcome = session.handle(press(InputAction.A))
    assert outcome.launch is not None
    assert outcome.launch.display_name == "魂斗罗"


def test_prefix_hits_rank_before_contains() -> None:
    root = Path("/roms")
    by_system = {"mame": [
        Game(key="mame/1.zip", path=root / "mame" / "1.zip", name="Sonic"),
        Game(key="mame/2.zip", path=root / "mame" / "2.zip", name="Mega Sonic"),
    ]}
    session = make_session(by_system)
    type_text(session, "SO")
    assert names(session) == ["Sonic", "Mega Sonic"]


def test_mixed_mode_ranks_title_before_filename() -> None:
    root = Path("/roms")
    by_system = {"mame": [
        Game(key="mame/kof97.zip", path=root / "mame" / "kof97.zip", name="拳皇97"),
        Game(key="mame/kofsky.zip", path=root / "mame" / "kofsky.zip",
             name="KOF Sky Stage"),
    ]}
    session = make_session(by_system)
    session.config.search_by = "both"
    type_text(session, "KOF")
    # Both hit as prefixes, but KOF Sky Stage matches on its title while
    # 拳皇97 only on its ROM name -- the title hit outranks it.
    assert names(session) == ["KOF Sky Stage", "拳皇97"]


def test_search_by_config_selects_the_field() -> None:
    root = Path("/roms")
    by_system = {"mame": [
        Game(key="mame/kof97.zip", path=root / "mame" / "kof97.zip", name="拳皇97"),
    ]}

    def search_as(mode: str) -> Session:
        session = make_session(by_system)
        session.config.search_by = mode
        return session

    # Default: titles only -- the ROM file name does not even match.
    session = search_as("title")
    type_text(session, "KOF")
    assert names(session) == []
    session._set_search_text("")
    type_text(session, "QH")          # 拼音首字母 still works, of course
    assert names(session) == ["拳皇97"]

    # ROM-name mode is the mirror image: the title does not match.
    session = search_as("rom")
    type_text(session, "KOF")
    assert names(session) == ["拳皇97"]
    session._set_search_text("")
    type_text(session, "QH")
    assert names(session) == []


def test_search_by_defaults_to_title_and_validates() -> None:
    config = Config()
    assert config.search_by == "title"
    config.search_by = "rom"
    assert Config.from_dict(config.to_dict()).search_by == "rom"
    with pytest.raises(ConfigError):
        Config.from_dict({**Config().to_dict(), "search_by": "whatever"}).validate()


def test_backspace_deletes_esc_closes_letter_b_types() -> None:
    # Desktop backspace (a B event carrying "\b") deletes, and closes only
    # once there is nothing left to delete.
    session = make_session()
    type_text(session, "AB")
    session.handle(InputEvent(InputAction.B, InputKind.PRESS, text="\b"))
    assert session.search_text == "A"
    session.handle(InputEvent(InputAction.B, InputKind.PRESS, text="\b"))
    assert session.search_text == ""
    session.handle(InputEvent(InputAction.B, InputKind.PRESS, text="\b"))
    assert session.modal == MODAL_NONE

    # The handheld's B button carries no character and keeps its meaning.
    session = make_session()
    type_text(session, "A")
    session.handle(press(InputAction.B))
    assert session.search_text == ""

    # Esc leaves outright, whatever is typed so far.
    session = make_session()
    type_text(session, "AB")
    session.handle(InputEvent(InputAction.B, InputKind.PRESS, text="\x1b"))
    assert session.modal == MODAL_NONE

    # The letter b is still a letter: it types into the query.
    session = make_session()
    session.handle(InputEvent(InputAction.B, InputKind.PRESS, text="b"))
    assert session.search_text == "B"
    assert session.modal == MODAL_SEARCH


def test_combo_again_closes() -> None:
    session = make_session()
    type_text(session, "A")
    session.handle(press(InputAction.SEARCH))
    assert session.modal == MODAL_NONE


def test_results_focus_and_multi_file_launch() -> None:
    session = make_session()
    type_text(session, "LYDXC")
    session.handle(press(InputAction.L1))
    assert session.search_focus == "results"
    outcome = session.handle(press(InputAction.A))
    # Multi-file game: the ROM picker opens, the search dialog is gone.
    assert outcome.launch is None
    assert session.modal == MODAL_ROM_SELECT
    assert session.rom_select_paths[0].name == "a.zip"


def test_results_launch_single_file() -> None:
    session = make_session()
    type_text(session, "HDL")
    session.handle(press(InputAction.L1))
    outcome = session.handle(press(InputAction.A))
    assert session.modal == MODAL_NONE
    assert outcome.launch is not None
    assert outcome.launch.display_name == "魂斗罗"


def test_physical_keyboard_text_rides_along() -> None:
    session = make_session()
    # On the desktop "s" maps to START and "a" to A -- in the search dialog the
    # character wins and the mapped action never fires.
    session.handle(InputEvent(InputAction.START, InputKind.PRESS, text="s"))
    assert session.search_text == "S"
    assert session.modal == MODAL_SEARCH
    session.handle(InputEvent(InputAction.A, InputKind.PRESS, text="f"))
    assert session.search_text == "SF"
    assert names(session) == ["Street Fighter 2"]


def test_letters_do_not_type_from_the_results_focus() -> None:
    session = make_session()
    type_text(session, "HDL")
    session.handle(press(InputAction.L1))
    assert session.search_focus == "results"
    # Focus is not on the keyboard, so keys act instead of typing: the letter
    # "b" (carrying the B action) goes back to the query, unchanged.
    session.handle(InputEvent(InputAction.B, InputKind.PRESS, text="b"))
    assert session.search_focus == "kb"
    assert session.search_text == "HDL"
    # Unmapped characters do nothing at all from the result list.
    session.handle(press(InputAction.L1))
    session.handle(InputEvent(InputAction.CHAR, InputKind.PRESS, text="w"))
    assert session.search_focus == "results"
    assert session.search_text == "HDL"
