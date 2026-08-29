"""Configuration tests."""

from __future__ import annotations

import json

import pytest

from retrostation.core.config import Config, ConfigError, LAYOUTS


def test_defaults_are_valid() -> None:
    Config().validate()  # must not raise


def test_round_trip(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = Config()
    config.layout = "carousel"
    config.bottom_video = False
    config.metadata.sources = ["esde"]
    config.save(path)

    loaded = Config.load(path)
    assert loaded == config


def test_missing_file_yields_defaults(tmp_path) -> None:
    loaded = Config.load(tmp_path / "nope.json")
    assert loaded == Config()


def test_unknown_keys_are_ignored(tmp_path) -> None:
    """Old configs and future fields must not break loading."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"layout": "grid", "future_setting": True, "deep": {"a": 1}}),
        encoding="utf-8",
    )
    loaded = Config.load(path)
    assert loaded.layout == "grid"


def test_invalid_layout_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"layout": "table"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


def test_partial_nested_section(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"metadata": {"sources": ["pegasus"]}}), encoding="utf-8")
    loaded = Config.load(path)
    assert loaded.metadata.sources == ["pegasus"]
    assert loaded.metadata.primary_write_source == "esde"  # default kept


def test_next_layout_cycles() -> None:
    config = Config(layout="list")
    assert config.next_layout() == "grid"
    config.layout = "grid"
    assert config.next_layout() == "carousel"
    config.layout = "carousel"
    assert config.next_layout() == "list"
    assert set(LAYOUTS) == {"list", "grid", "carousel"}


def test_save_is_atomic_and_utf8(tmp_path) -> None:
    path = tmp_path / "sub" / "config.json"
    config = Config()
    config.media_dirs["cover"] = "Imgs"
    config.save(path)

    # No temp file left behind.
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["config.json"]
    text = path.read_text(encoding="utf-8")
    assert "Imgs" in text


def test_corrupt_file_raises(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


def test_video_settings_validated(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"video_fps": 0}), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)
