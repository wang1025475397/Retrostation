"""The home page's artwork: platform art and the preview strip.

Both are drawn while the player is watching and neither used to be warmed --
the platform art ships with the app and has no on-disk cache at all, and the
preview covers are six cold decodes per platform switch.  That is the hitch
left once the game list itself is warm.
"""

from __future__ import annotations

from pathlib import Path

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.data.library import Library
from retrostation.ui.platform_art import _CACHE_LIMIT, PlatformArt
from retrostation.ui.session import Session
from tests.conftest import FakePlatform


def _session(rom_root: Path) -> Session:
    platform = FakePlatform(rom_root)
    library = Library(platform, Config())
    library.scan()
    session = Session(library, Config(), Translator("en"))
    # Home-page entries start with the aggregates (ALL/FAV/RECENT); stand on a
    # real system so the preview strip has something to show.
    session.platform_index = session.system_keys().index("FC")
    return session


class TestPreviewGamesForAnyPlatform:
    def test_matches_what_the_selected_platform_shows(self, rom_root: Path) -> None:
        session = _session(rom_root)
        key = session.current_system_key()

        assert session.preview_games_for(key) == session.preview_games()

    def test_is_capped_at_the_strip(self, rom_root: Path) -> None:
        session = _session(rom_root)
        for key in session.system_keys():
            assert len(session.preview_games_for(key)) <= 6

    def test_puts_what_the_player_knows_first(self, rom_root: Path) -> None:
        """Recently played, then favourites -- the same order as the strip."""
        session = _session(rom_root)
        key = session.current_system_key()
        games = session.preview_games_for(key)
        first = games[0]
        first.favorite = True
        first.last_played = None

        assert session.preview_games_for(key)[0].key == first.key


class TestPlatformArtCache:
    def test_holds_every_platform_at_once(self) -> None:
        """A card that scrolls away and back must not be decoded again.

        Three bitmaps a platform: the square background and the logo at both
        card widths (the selected card is wider).
        """
        assert _CACHE_LIMIT >= 60 * 3

    def test_a_warmed_size_is_served_without_decoding(self, tmp_path: Path) -> None:
        platform = FakePlatform(tmp_path)
        art = PlatformArt(platform, tmp_path / "art")
        assert art.background("FC", 150, 150) is None  # nothing shipped here

        decoded: list[Path] = []
        real = platform.load_image

        def spy(path):  # noqa: ANN001 - platform signature
            decoded.append(Path(path))
            return real(path)

        platform.load_image = spy  # type: ignore[method-assign]
        # Still nothing to decode: the miss is remembered like the hit is.
        assert art.background("FC", 150, 150) is None
        assert decoded == []
