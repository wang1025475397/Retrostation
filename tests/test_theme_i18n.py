"""Theme metrics and i18n tests."""

from __future__ import annotations

import pytest

from retrostation.core import theme
from retrostation.core.i18n import Translator
from retrostation.core.theme import Colors, Form, Metrics, metrics_for


class TestMetricsAtReferenceSize:
    """640x480 must reproduce the numbers in DESIGN §7.1/§7.2 exactly."""

    @pytest.fixture
    def metrics(self) -> Metrics:
        return metrics_for(640, 480)

    def test_chrome(self, metrics: Metrics) -> None:
        assert metrics.status_h == 28
        assert metrics.head_h == 44
        assert metrics.bar_h == 30
        assert metrics.content_h() == 378

    def test_single_screen_content(self, metrics: Metrics) -> None:
        assert metrics.strip_h == 118
        assert metrics.content_h(single=True) == 260

    def test_list(self, metrics: Metrics) -> None:
        assert metrics.row_h == 34
        assert metrics.row_gap == 4
        assert metrics.thumb_w == 84  # wide, for the 4:1 logo
        assert metrics.thumb_h == 30
        assert metrics.rows_per_page() == 9
        assert metrics.rows_per_page(single=True) == 6

    def test_grid(self, metrics: Metrics) -> None:
        assert metrics.grid_cols == 4
        assert metrics.grid_rows() == 3
        assert metrics.grid_rows(single=True) == 2
        assert metrics.items_per_grid_page() == 12
        assert metrics.items_per_grid_page(single=True) == 8

    def test_carousel(self, metrics: Metrics) -> None:
        assert metrics.carousel_card_h() == 272
        assert metrics.carousel_card_w() == 196
        assert metrics.carousel_gap == 14

    def test_bottom_screen(self, metrics: Metrics) -> None:
        assert metrics.media_w == 336
        assert metrics.media_h == 264
        assert metrics.logo_strip_h == 72
        # 2*12 padding + 14 gap + 336 media = 614, leaving 266 for metadata.
        assert metrics.meta_w == 266
        assert metrics.media_w + metrics.meta_w + 2 * metrics.body_padding + metrics.body_gap == 640
        assert metrics.bottom_body_h() == 380


class TestMetricsElsewhere:
    def test_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            metrics_for(0, 100)
        with pytest.raises(ValueError):
            metrics_for(100, -1)

    def test_hdmi_landscape(self) -> None:
        metrics = metrics_for(1920, 1080)
        assert metrics.scale == pytest.approx(2.25)
        assert metrics.status_h == 63
        # Columns stay sane instead of exploding on a wide screen.
        assert 3 <= metrics.grid_cols <= 6

    def test_tall_phone_screen(self) -> None:
        metrics = metrics_for(1080, 2400)
        # Everything scales from the width, so chrome stays proportionate.
        assert metrics.status_h == round(28 * 1.6875)
        assert metrics.content_h() > 0
        assert 3 <= metrics.grid_cols <= 6

    def test_every_dimension_is_positive(self) -> None:
        for width, height in ((320, 240), (640, 480), (1280, 720), (1080, 2400)):
            metrics = metrics_for(width, height)
            assert metrics.content_h() > 0
            assert metrics.row_h > 0
            assert metrics.thumb_w > 0
            assert metrics.media_w > 0
            assert metrics.meta_w > 0
            assert metrics.carousel_card_w() > 0
            assert metrics.bottom_body_h() > 0

    def test_bottom_columns_leave_room_for_gap(self) -> None:
        metrics = metrics_for(640, 480)
        assert metrics.media_w + metrics.meta_w < 640


class TestForms:
    """The detail view's arrangement, per screen form (DESIGN.ANDROID §5.5)."""

    def test_dual_reserves_nothing_because_the_other_panel_is_the_detail(self) -> None:
        metrics = metrics_for(640, 480, Form.DUAL)
        assert metrics.is_single is False
        # Content gets everything the chrome leaves; no strip is folded in.
        assert metrics.content_h() == 480 - (28 + 44 + 30)
        # The "detail box" is the whole panel: that is what bottom.py paints.
        assert metrics.detail_box() == (0, 0, 640, 480)

    def test_compact_keeps_the_measured_handheld_strip(self) -> None:
        """The regression guard for this refactor: 118px, exactly as before."""
        metrics = metrics_for(640, 480, Form.COMPACT)
        assert metrics.is_single is True
        assert metrics.strip_h == 118
        x, y, w, h = metrics.detail_box()
        assert (x, w, h) == (8, 640 - 16, 118)
        assert y == metrics.content_top + metrics.content_h(single=True)

    def test_portrait_takes_a_share_of_the_height_instead(self) -> None:
        """118 reference px would be a sliver on a 20:9 phone."""
        metrics = metrics_for(1080, 2400, Form.PORTRAIT)
        assert metrics.strip_h == round(2400 * 0.40)
        # And it must not eat the content area whole.
        assert metrics.content_h() > 0

    def test_single_defaults_to_what_the_form_implies(self) -> None:
        """Callers written before forms existed pass ``single=`` explicitly;
        new ones may leave it out and get the same answer."""
        compact = metrics_for(640, 480, Form.COMPACT)
        assert compact.content_h() == compact.content_h(single=True)
        assert compact.grid_rows() == compact.grid_rows(single=True) == 2

        dual = metrics_for(640, 480, Form.DUAL)
        assert dual.content_h() == dual.content_h(single=False)
        assert dual.grid_rows() == 3

    def test_an_explicit_single_still_overrides_the_form(self) -> None:
        """``_paint_full`` bakes the strip using ``single=True`` on a dual panel."""
        dual = metrics_for(640, 480, Form.DUAL)
        assert dual.content_h(single=True) < dual.content_h(single=False)


class TestColors:
    def test_named_lookup_matches_fields(self) -> None:
        colors = Colors()
        assert colors.as_dict()["accent"] == colors.accent
        assert len(colors.as_dict()) == 11

    def test_theme_switch_updates_the_palette_in_place(self) -> None:
        """A theme has to update the shared palette, not replace it.

        Every screen does ``from ...core.theme import COLORS``; handing out a
        new object would leave half the UI painting with the old palette.
        """
        colors = Colors()
        amber = colors.accent
        colors.apply("ice")
        assert colors.accent != amber
        colors.apply("amber")
        assert colors.accent == amber, "switching back must restore it"

    def test_variant_moves_the_neutrals_only(self) -> None:
        colors = Colors()
        accent = colors.accent
        colors.apply("amber", "light")
        assert colors.bg != Colors().bg
        assert colors.accent == accent, "the accent family must survive a variant change"

    def test_unknown_theme_falls_back_to_the_default(self) -> None:
        """A config written by a newer build must not stop the app painting."""
        colors = Colors()
        colors.apply("no-such-family", "no-such-surface")
        assert colors.accent == Colors().accent
        assert colors.bg == Colors().bg

    def test_module_level_instance(self) -> None:
        assert theme.COLORS.bg == (20, 20, 20, 255)


class TestI18n:
    def test_zh_lookup(self) -> None:
        translator = Translator("zh_CN")
        assert translator("btn.start") == "开始"
        assert translator("menu.title") == "系统菜单"

    def test_english_lookup(self) -> None:
        translator = Translator("en_US")
        assert translator("btn.start") == "Start"

    def test_parameters(self) -> None:
        translator = Translator("zh_CN")
        assert translator("home.platform_count", count=24) == "24 个平台"

    def test_unknown_language_falls_back_to_english(self) -> None:
        translator = Translator("xx_XX")
        assert translator("btn.start") == "Start"

    def test_unknown_key_returns_the_key(self) -> None:
        translator = Translator("zh_CN")
        assert translator("no.such.key") == "no.such.key"

    def test_user_dir_overrides_builtin(self, tmp_path) -> None:
        user = tmp_path / "lang"
        user.mkdir()
        (user / "zh_CN.json").write_text('{"btn.start": "开搞"}', encoding="utf-8")
        translator = Translator("zh_CN", lang_dir=user)
        assert translator("btn.start") == "开搞"
        assert translator("btn.back") == "返回"  # untouched keys still resolve

    def test_corrupt_user_file_does_not_break_lookup(self, tmp_path) -> None:
        user = tmp_path / "lang"
        user.mkdir()
        (user / "zh_CN.json").write_text("{ broken", encoding="utf-8")
        translator = Translator("zh_CN", lang_dir=user)
        assert translator("btn.start") == "开始"

    def test_set_language_switches_bundle(self) -> None:
        translator = Translator("zh_CN")
        assert translator("btn.start") == "开始"
        translator.set_language("en_US")
        assert translator("btn.start") == "Start"

    def test_available_languages(self) -> None:
        translator = Translator("zh_CN")
        codes = translator.available()
        assert "zh_CN" in codes and "en_US" in codes

    def test_bad_placeholder_still_returns_text(self) -> None:
        translator = Translator("zh_CN")
        translator.merge({"k": "value {missing}"})
        assert translator("k") == "value {missing}"

    def test_auto_resolves_to_fallback(self) -> None:
        translator = Translator("auto")
        assert translator.language == "en_US"
