"""M6: a single-screen device must lose nothing (DESIGN §11).

With one panel -- or "single" forced in the settings -- the bottom screen's
content is folded into the top: a shorter content area plus a detail strip.
Everything reachable in dual mode has to stay reachable here, which is what
this module pins down.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.core.theme import Form, metrics_for
from retrostation.data.library import Library
from retrostation.platform.base import InputAction, InputEvent, InputKind
from retrostation.ui.app import EXIT_OK, EXIT_RESTART, App
from retrostation.ui.session import MODAL_EXIT, MODAL_MENU
from tests.conftest import FakePlatform


def single_app(rom_root: Path) -> tuple[App, FakePlatform]:
    platform = FakePlatform(rom_root)
    config = Config()
    config.screen_mode = "single"
    # The carousel keeps the folded panel underneath the content; list and grid
    # move it beside the rows (DESIGN §11.3).  These tests are about the folded
    # strip, so they pin the arrangement that has one.
    config.layout = "carousel"
    script = rom_root / "RA_launch.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    config.launcher.ra_script = str(script)
    library = Library(platform, config)
    library.scan()
    return App(platform, config, Translator(config.language), library), platform


@pytest.fixture
def pair(rom_root: Path) -> tuple[App, FakePlatform]:
    return single_app(rom_root)


class TestSingleScreen:
    def test_one_canvas_only(self, pair) -> None:
        app, platform = pair
        app.run(max_frames=1)
        assert len(platform.canvases) == 1

    def test_video_plays_in_the_strip(self, pair) -> None:
        """One screen has no bottom panel, so the clip moves into the strip.

        It is decoded at the strip's own slot size, not at
        ``bottom.media_inner_size``: 288x216 down to 160x98 would cost a resize
        every frame for nothing.  The column has to be unfolded on the game page
        first -- a folded one has nowhere to play, so the decoder is parked.
        """
        app, platform = pair
        app.run(max_frames=2)
        assert app._video.enabled is False           # folded: no panel to play in
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)
        app._toggle_detail()                         # the fold tab, one tap
        app.run(max_frames=1)
        assert app._video.enabled is True
        box = app._strip_art_box(metrics_for(640, 480, Form.COMPACT))
        assert (app._video._settings.width, app._video._settings.height) == (box[2], box[3])

    def test_all_three_views_render(self, pair) -> None:
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)

        for layout in ("list", "grid", "carousel"):
            app.session.layout = layout
            app.run(max_frames=1)
            colours = set(platform.canvases[0].pil_image.getdata())
            assert len(colours) > 5, f"{layout} drew nothing in single mode"

    def test_settings_menu_works(self, pair) -> None:
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.START))
        app.run(max_frames=1)
        assert app.session.modal == MODAL_MENU

        platform.send(InputEvent(InputAction.A))     # toggle the focused row
        app.run(max_frames=1)
        assert app.session.modal == ""

    def test_launching_a_game_works(self, pair) -> None:
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # launch
        assert app.run(max_frames=1) == EXIT_RESTART
        assert platform.launched is not None

    def test_the_detail_strip_is_filled(self, pair) -> None:
        """118px under the list is reserved for it -- so it must be drawn into.

        ``content_h(single=True)`` gives the space back, but nothing painted
        there until M6: the strip was simply blank.
        """
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)

        metrics = metrics_for(640, 480, Form.COMPACT)
        top = metrics.content_top + metrics.content_h(single=True)
        strip = platform.canvases[0].pil_image.crop((0, top, 640, top + metrics.strip_h))
        assert len(set(strip.getdata())) > 3, "the detail strip is blank"

    def test_the_strip_survives_an_idle_frame(self, pair) -> None:
        """The strip is drawn on the top canvas, which every frame overwrites.

        Restoring the top from its cache covers the whole canvas, so a frame
        that repaints the top without repainting the strip erases it -- one
        blank flash per repaint, which is what the flicker was.
        """
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)

        metrics = metrics_for(640, 480, Form.COMPACT)
        top = metrics.content_top + metrics.content_h(single=True)
        app.run(max_frames=4)                        # idle: nothing changes
        strip = platform.canvases[0].pil_image.crop((0, top, 640, top + metrics.strip_h))
        assert len(set(strip.getdata())) > 3, "the strip was erased and not repainted"

    def test_the_strip_comes_back_with_the_cache(self, pair) -> None:
        """The top cache has to carry the strip, not just the list.

        The strip costs more than a frame budget to paint, so it is painted
        once -- when the panel is cached -- and a restore brings it back.
        Lose that and the choice is between a blank strip and 38 ms a frame.
        """
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)

        metrics = metrics_for(640, 480, Form.COMPACT)
        top = metrics.content_top + metrics.content_h(single=True)
        box = (0, top, 640, top + metrics.strip_h)
        # Blank the canvas: only a cache restore may bring the strip back.
        blank = Image.new("RGBA", (640, 480), (0, 0, 0, 255))
        app._painters[0].canvas.pil_image.paste(blank)
        app._reuse(app._painters[0])
        strip = platform.canvases[0].pil_image.crop(box)
        assert len(set(strip.getdata())) > 3, "the top cache does not carry the strip"

    def test_the_cached_panel_keeps_the_latest_strip(self, pair) -> None:
        """A restore must bring back the strip as it is *now*, not as it was baked.

        The strip is baked into the cached panel, but it changes on every clip
        frame while the panel is only recached on a full repaint.  The reuse path
        therefore repaints the strip and re-bakes it (the comment in ``App.run``
        says why only that path draws it); without that, a restore resurrected
        the stale copy, so the strip alternated between it and the live frame.
        """
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.A))     # into a system
        app.run(max_frames=1)

        metrics = metrics_for(640, 480, Form.COMPACT)
        x, y, w, h = metrics.detail_box()
        painter = app._painters[0]

        # A stale copy in the cache: the panel was baked while this was on it.
        painter.rect((x, y, w, h), fill=(255, 0, 0, 255))  # nothing else is red
        app._cache_strip(painter)

        # A reuse frame: the cache comes back, and the strip is repainted and
        # re-baked as it is now (this is the sequence App.run drives).
        app._reuse(painter)
        app._draw_detail_strip(painter)
        app._cache_strip(painter)

        painter.clear()
        app._reuse(painter)
        strip = painter.canvas.pil_image.crop((x, y, x + w, y + h))
        assert strip.getpixel((5, 5))[:3] != (255, 0, 0), "the stale strip came back"
        assert len(set(strip.getdata())) > 3, "the strip came back empty"

    def test_quitting_works(self, pair) -> None:
        app, platform = pair
        app.run(max_frames=1)
        platform.send(InputEvent(InputAction.MENU, InputKind.LONG_PRESS))
        app.run(max_frames=1)
        assert app.session.modal == MODAL_EXIT

        platform.send(InputEvent(InputAction.A))
        assert app.run(max_frames=1) == EXIT_OK
