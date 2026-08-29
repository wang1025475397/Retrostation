"""Minimal, dependency-free translator.

Keys are plain dot-separated strings (``"btn.start"``) and are kept stable on
purpose: the planned Android port can turn ``assets/lang/*.json`` into
``values/strings.xml`` mechanically.

Lookup order for a key:

1. the requested language file,
2. the fallback language (``en_US``),
3. the key itself -- so a missing translation is visible, never a crash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

FALLBACK_LANGUAGE = "en_US"

#: Directory shipped with the package.
BUILTIN_LANG_DIR = Path(__file__).resolve().parent.parent / "assets" / "lang"


class Translator:
    """Resolves i18n keys, with ``str.format`` style placeholders."""

    def __init__(
        self,
        language: str = "auto",
        *,
        lang_dir: Path | None = None,
        fallback: str = FALLBACK_LANGUAGE,
    ) -> None:
        self._dirs: list[Path] = []
        if lang_dir is not None:
            self._dirs.append(Path(lang_dir))
        self._dirs.append(BUILTIN_LANG_DIR)

        self.fallback = fallback
        self.language = self._resolve(language)

        self._bundles: dict[str, dict[str, str]] = {}
        self._reload()

    # ------------------------------------------------------------------ #

    def _resolve(self, language: str) -> str:
        """Turn ``"auto"`` into a concrete code; unknown codes fall back."""
        if language and language != "auto":
            return language
        return FALLBACK_LANGUAGE

    def _reload(self) -> None:
        wanted = {self.language, self.fallback}
        self._bundles = {code: self._load(code) for code in wanted}

    def _load(self, code: str) -> dict[str, str]:
        merged: dict[str, str] = {}
        # Later dirs are defaults; earlier dirs (user supplied) win.
        for directory in reversed(self._dirs):
            path = directory / f"{code}.json"
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # a corrupt bundle must not take the app down
            if isinstance(data, dict):
                merged.update({str(k): str(v) for k, v in data.items()})
        return merged

    # ------------------------------------------------------------------ #

    def set_language(self, language: str) -> None:
        self.language = self._resolve(language)
        self._reload()

    def t(self, key: str, **params: object) -> str:
        """Translate ``key``, formatting it with ``params`` when provided."""
        template = self._bundles.get(self.language, {}).get(key)
        if template is None:
            template = self._bundles.get(self.fallback, {}).get(key)
        if template is None:
            return key
        if not params:
            return template
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            # Half-translated placeholders should still show the text.
            return template

    #: Alias for use as ``_(key)`` in hot drawing code.
    __call__ = t

    # ------------------------------------------------------------------ #

    def available(self) -> list[str]:
        """Language codes we can actually load (builtin + user dirs)."""
        codes: set[str] = set()
        for directory in self._dirs:
            if not directory.is_dir():
                continue
            codes.update(p.stem for p in directory.glob("*.json"))
        return sorted(codes)

    def merge(self, overrides: Mapping[str, str]) -> None:
        """Apply in-memory overrides (used by tests and by the settings page)."""
        self._bundles.setdefault(self.language, {}).update(
            {str(k): str(v) for k, v in overrides.items()}
        )
