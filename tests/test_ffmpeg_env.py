"""Decoder environment selection for the bundled ffmpeg (the TrimUI port).

Two devices pull the same code in opposite directions:

* the RG DS's stock launcher exports an ``LD_LIBRARY_PATH`` that hijacks
  libavformat, so the decoder must run in a **clean** environment;
* the TrimUI build we ship ourselves keeps its codecs in a ``lib/`` of ours
  and **cannot start at all** without that path.

``_picked_env()`` has to serve both: prefer clean when it works, fall back to
the inherited path when clean cannot even start, and stay undecided (clean)
when nothing could be asked.  ``subprocess`` is faked out -- these tests must
not depend on an ffmpeg being installed on the test host.
"""

from __future__ import annotations

import pytest

from retrostation.platform.linux import ffmpeg as ffmpeg_mod


@pytest.fixture(autouse=True)
def fresh_cache():
    """A decision cached by one test must not leak into the next."""
    ffmpeg_mod._ENV_CACHE.clear()
    yield
    ffmpeg_mod._ENV_CACHE.clear()


class _Probe:
    """Scripted ``_has_muxer``: answers by whether the env keeps the lib path."""

    def __init__(self, clean: bool | None, full: bool | None) -> None:
        self.clean = clean
        self.full = full
        self.envs: list[str | None] = []

    def __call__(self, env: dict[str, str], name: str) -> bool | None:
        self.envs.append(env.get("LD_LIBRARY_PATH"))
        return self.clean if "LD_LIBRARY_PATH" not in env else self.full


def test_clean_environment_when_it_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RG DS case: clean runs and has the muxer -- strip the lib path."""
    probe = _Probe(clean=True, full=True)
    monkeypatch.setattr(ffmpeg_mod, "_has_muxer", probe)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/stock/hijacked")

    env = ffmpeg_mod._picked_env("video", "rawvideo")

    assert "LD_LIBRARY_PATH" not in env


def test_bundled_decoder_keeps_the_library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TrimUI case: clean cannot start, the inherited path can."""
    probe = _Probe(clean=None, full=True)
    monkeypatch.setattr(ffmpeg_mod, "_has_muxer", probe)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/apps/retrostation/lib")

    env = ffmpeg_mod._picked_env("video", "rawvideo")

    assert env.get("LD_LIBRARY_PATH") == "/apps/retrostation/lib"


def test_undecided_falls_back_to_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing could be asked -- hand back the clean env and retry next time."""
    probe = _Probe(clean=None, full=False)
    monkeypatch.setattr(ffmpeg_mod, "_has_muxer", probe)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/somewhere")

    env = ffmpeg_mod._picked_env("video", "rawvideo")

    assert "LD_LIBRARY_PATH" not in env


def test_decision_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """One decision, then no more probing: the cache is the whole point."""
    probe = _Probe(clean=None, full=True)
    monkeypatch.setattr(ffmpeg_mod, "_has_muxer", probe)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/apps/retrostation/lib")

    first = ffmpeg_mod._picked_env("video", "rawvideo")
    second = ffmpeg_mod._picked_env("video", "rawvideo")

    assert first is second
    assert len(probe.envs) == 2  # clean + inherited, asked once each


def test_roles_are_cached_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Video and audio decide independently (they probe different muxers)."""
    probe = _Probe(clean=True, full=True)
    monkeypatch.setattr(ffmpeg_mod, "_has_muxer", probe)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/apps/retrostation/lib")

    ffmpeg_mod._picked_env("video", "rawvideo")
    assert len(probe.envs) == 1

    ffmpeg_mod._picked_env("audio", "s16le")
    assert len(probe.envs) == 2
