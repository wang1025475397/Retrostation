"""Multi-source merging tests (DESIGN §6.8.4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from retrostation.core.model import Game
from retrostation.data.sources.base import merge_games


def make_game(name: str, **kwargs) -> Game:
    kwargs.setdefault("key", f"FC/{name}.nes")
    kwargs.setdefault("path", Path(f"/x/FC/{name}.nes"))
    kwargs.setdefault("name", name)
    return Game(**kwargs)


class TestDescriptiveFields:
    def test_first_source_wins(self) -> None:
        primary = make_game("a", description="来自 ES-DE")
        secondary = make_game("a", description="来自 Pegasus")
        merged = merge_games([primary, secondary])
        assert merged.description == "来自 ES-DE"

    def test_later_source_fills_gaps(self) -> None:
        primary = make_game("a", description="来自 ES-DE")
        secondary = make_game("a", genres=["动作"], publisher="卡普空")
        merged = merge_games([primary, secondary])
        assert merged.description == "来自 ES-DE"
        assert merged.genres == ["动作"]
        assert merged.publisher == "卡普空"

    def test_assets_filled_per_kind(self) -> None:
        primary = make_game("a")
        primary.set_asset("cover", Path("c.png"))
        secondary = make_game("a")
        secondary.set_asset("cover", Path("other.png"))
        secondary.set_asset("video", Path("v.mp4"))

        merged = merge_games([primary, secondary])
        assert merged.asset("cover") == Path("c.png")   # not overwritten
        assert merged.asset("video") == Path("v.mp4")   # filled in

    def test_provenance_and_extra_accumulate(self) -> None:
        first = make_game("a", extra={"id": "1"})
        second = make_game("a", extra={"source": "ScreenScraper"})
        merged = merge_games([first, second])
        assert merged.extra == {"id": "1", "source": "ScreenScraper"}

    def test_single_candidate_returns_as_is(self) -> None:
        game = make_game("a", favorite=True)
        assert merge_games([game]) is game

    def test_no_candidates_is_an_error(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            merge_games([])


class TestStateFields:
    def test_newer_last_played_wins_even_from_lower_priority(self) -> None:
        now = datetime(2026, 8, 28, 12, 0)
        primary = make_game("a", last_played=now - timedelta(days=30), play_count=5)
        secondary = make_game("a", last_played=now, play_count=1)

        merged = merge_games([primary, secondary])
        assert merged.last_played == now

    def test_higher_priority_state_kept_when_newer(self) -> None:
        now = datetime(2026, 8, 28, 12, 0)
        primary = make_game("a", last_played=now, play_count=5)
        secondary = make_game("a", last_played=now - timedelta(days=1), play_count=99)

        merged = merge_games([primary, secondary])
        assert merged.last_played == now
        assert merged.play_count == 5

    def test_without_dates_play_count_breaks_ties(self) -> None:
        primary = make_game("a", play_count=3)
        secondary = make_game("a", play_count=42)
        assert merge_games([primary, secondary]).play_count == 42

    def test_favourite_never_disappears(self) -> None:
        primary = make_game("a", favorite=False)
        secondary = make_game("a", favorite=True, play_count=9)
        assert merge_games([primary, secondary]).favorite is True
