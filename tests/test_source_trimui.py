"""The TrimUI image source: covers from ``<card>/Imgs/<SYS>/<rom name>.png``.

The stock launcher names images after the ROM minus its extension and keeps
them in a tree that sits *next to* ``Roms``, not inside it -- the pairing is
by stem, and the source must never invent metadata beyond the cover.
"""

from __future__ import annotations

from pathlib import Path

from retrostation.core.model import ASSET_COVER
from retrostation.data.sources import SOURCES, _SourceBundle, build_games
from retrostation.data.sources.trimui import TrimuiSource


def _card(tmp_path: Path) -> Path:
    """A minimal card: Roms/FC with two ROMs, Imgs/FC with one cover."""
    roms = tmp_path / "Roms" / "FC"
    roms.mkdir(parents=True)
    (roms / "1942 (Japan, USA).zip").write_bytes(b"rom")
    (roms / "恶魔城.nes").write_bytes(b"rom")
    images = tmp_path / "Imgs" / "FC"
    images.mkdir(parents=True)
    (images / "1942 (Japan, USA).png").write_bytes(b"img")
    (images / "没有对应ROM.png").write_bytes(b"img")  # surplus: must be ignored
    return tmp_path


def test_detect_requires_the_sibling_imgs_tree(tmp_path: Path) -> None:
    _card(tmp_path)
    source = TrimuiSource()
    assert source.detect(tmp_path / "Roms" / "FC") is True

    bare = tmp_path / "Roms" / "GBA"
    bare.mkdir()
    assert source.detect(bare) is False


def test_load_pairs_roms_by_stem(tmp_path: Path) -> None:
    source = TrimuiSource()
    system_dir = _card(tmp_path) / "Roms" / "FC"

    entries = source.load(system_dir)

    # Only ROMs *with* an image get an entry; the surplus image is ignored.
    assert set(entries) == {"1942 (Japan, USA).zip"}
    assert entries["1942 (Japan, USA).zip"].missing is False


def test_to_game_sets_only_the_cover(tmp_path: Path) -> None:
    source = TrimuiSource()
    system_dir = _card(tmp_path) / "Roms" / "FC"
    entry = source.load(system_dir)["1942 (Japan, USA).zip"]

    game = source.to_game("FC", system_dir / "1942 (Japan, USA).zip", entry)

    assert game.asset(ASSET_COVER) == tmp_path / "Imgs" / "FC" / "1942 (Japan, USA).png"
    assert not game.summary  # media-only: no invented metadata


def test_build_games_fills_gaps_without_touching_the_rest(tmp_path: Path) -> None:
    """A ROM with an image gets one; the other stays a plain entry."""
    source = TrimuiSource()
    system_dir = _card(tmp_path) / "Roms" / "FC"
    bundle = _SourceBundle(source=source, entries=source.load(system_dir))

    games, _variants = build_games(
        "FC",
        [system_dir / "1942 (Japan, USA).zip", system_dir / "恶魔城.nes"],
        system_dir,
        [bundle],
    )

    covered = games["FC/1942 (Japan, USA).zip"]
    bare = games["FC/恶魔城.nes"]
    assert covered.has_asset(ASSET_COVER)
    assert not bare.has_asset(ASSET_COVER)


def test_registered_and_always_probed() -> None:
    """Media-only sources run even when ``metadata.sources`` omits them."""
    sources = SOURCES(None)
    assert sources[-1].name == "trimui"  # lowest priority: only fills gaps
    assert all(s.media_only == (s.name == "trimui") for s in sources)
