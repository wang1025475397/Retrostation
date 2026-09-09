"""The warm-up has to ask for the sizes the screen actually draws.

:func:`~retrostation.ui.screens.games.art_slots` exists so the idle pre-warm
can fill the cache ahead of the frame.  It is a second copy of the layout
arithmetic, so the two can drift -- and a drifted slot is worse than no
warm-up at all: the card fills up with sizes nobody asks for.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from retrostation.core.i18n import Translator
from retrostation.core.model import Game
from retrostation.core.theme import metrics_for
from retrostation.platform.linux.canvas import PilCanvas
from retrostation.ui.painter import Painter
from retrostation.ui.screens import games
from tests.conftest import FakePlatform


class SpyArt:
    """Records what the views ask for and hands back a blank bitmap."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, int, int, bool]] = []

    def thumbnail(self, game: Game, width: int, height: int, *,
                  prefer_logo: bool = False, cover: bool = False):
        kind = "logo" if prefer_logo else "cover"
        self.requests.append((kind, width, height, cover))
        return Image.new("RGBA", (max(1, width), max(1, height)), (20, 20, 24, 255))

    def placeholder(self, seed: str, width: int, height: int):
        return Image.new("RGBA", (max(1, width), max(1, height)), (40, 40, 44, 255))

    def backdrop(self, game: Game, width: int, height: int):
        return None


def _painter(single: bool) -> Painter:
    canvas = PilCanvas(640, 480)
    painter = Painter(canvas, metrics_for(640, 480), FakePlatform(Path(".")), Translator("en"))
    painter.single = single
    return painter


def _games(tmp_path: Path, count: int = 9) -> list[Game]:
    return [
        Game.from_rom("FC", tmp_path / f"game{index}.nes") for index in range(count)
    ]


def _asked(art: SpyArt) -> set[tuple[str, int, int, bool]]:
    return set(art.requests)


class TestSlotsMatchTheViews:
    def test_carousel(self, tmp_path: Path) -> None:
        painter = _painter(single=True)
        art = SpyArt()
        games.draw_carousel(painter, art, _games(tmp_path), 4)

        slots = set(games.art_slots(painter, "carousel"))
        assert _asked(art) <= slots
        # The card and each scaled neighbour are the point of the warm-up:
        # four sizes, one source.
        assert len({(w, h) for kind, w, h, _ in slots if kind == "cover"}) == 4
        assert {(w, h) for kind, w, h, _ in slots if kind == "cover"} == {
            (w, h) for kind, w, h, _ in _asked(art) if kind == "cover"
        }

    def test_grid(self, tmp_path: Path) -> None:
        painter = _painter(single=True)
        art = SpyArt()
        m = painter.metrics
        games.draw_grid(painter, art, _games(tmp_path), 2,
                        cols=m.grid_cols, rows=m.grid_rows(single=True))

        assert _asked(art) == set(games.art_slots(painter, "grid"))

    def test_list(self, tmp_path: Path) -> None:
        painter = _painter(single=True)
        art = SpyArt()
        m = painter.metrics
        games.draw_list(painter, art, _games(tmp_path), 2,
                        rows_per_page=m.rows_per_page(single=True))

        assert _asked(art) == set(games.art_slots(painter, "list"))

    def test_the_whole_set_covers_every_view(self, tmp_path: Path) -> None:
        """Warming and counting use :func:`games.all_slots`, so switching
        views can neither stutter nor make the progress figure drop."""
        painter = _painter(single=True)
        every = games.all_slots(painter)
        for layout in ("carousel", "grid", "list"):
            for slot in games.art_slots(painter, layout):
                assert slot in every, (layout, slot)
        assert len(every) == len(set(every))  # no size twice

    def test_sizes_are_positive_everywhere(self, tmp_path: Path) -> None:
        """A slot of 0x0 would be hashed into a name no frame can ask for."""
        for single in (True, False):
            for layout in ("list", "grid", "carousel"):
                for _kind, width, height, _cover in games.art_slots(_painter(single), layout):
                    assert width > 0 and height > 0, (single, layout)
