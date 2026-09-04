"""Every system in the table must have a platform description.

Regression: the systems added in the identification-table batch (a2600, gw,
varcade, dos, mdcd) had no ``system.desc.*`` entry, so the platform overview
page showed only the core/format lines.  Also: bundles spell description keys
in mixed case (``segaCD``, ``APPS``) while lookups use the casefolded system
key -- the translator normalises them on load.
"""

from __future__ import annotations

import re
from pathlib import Path

from retrostation.core.i18n import FALLBACK_LANGUAGE, Translator, available_builtin
from retrostation.data.systems import AGGREGATE_KEYS

import pytest

LANG_DIR = Path(__file__).resolve().parents[1] / "src" / "retrostation" / "assets" / "lang"

FALLBACK_DESC_PREFIX = "system.desc."


def _table_keys() -> list[str]:
    text = (LANG_DIR.parents[1] / "data" / "systems.py").read_text(encoding="utf-8")
    return re.findall(r'SystemDef\(\s*"([^"]+)"', text)


@pytest.mark.parametrize("language", available_builtin())
def test_every_system_has_a_description(language: str) -> None:
    translator = Translator(language=language, lang_dir=LANG_DIR)

    for key in _table_keys():
        if key in AGGREGATE_KEYS:
            continue
        found = translator.t(f"{FALLBACK_DESC_PREFIX}{key.casefold()}")
        assert found != f"{FALLBACK_DESC_PREFIX}{key.casefold()}", (
            f"{language}: missing system.desc for {key}"
        )


def test_mixed_case_bundle_keys_are_normalised() -> None:
    """A bundle's ``segaCD`` must answer a ``segacd`` lookup."""
    translator = Translator(language=FALLBACK_LANGUAGE, lang_dir=LANG_DIR)

    assert translator.t("system.desc.segacd") != "system.desc.segacd"
    assert translator.t("system.desc.apps") != "system.desc.apps"
