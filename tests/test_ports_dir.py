"""The ports system must be found even when it is not under Roms.

Regression: this firmware keeps ports next to the ROM tree -- ``/mnt/mmc/Ports``
with one launcher ``.sh`` per game -- so the library scan, which lists
``Roms``' sub-directories, never discovered it and the system was invisible.
"""

from __future__ import annotations

from pathlib import Path

from retrostation.core.config import Config
from retrostation.data.scanner import scan_library
from retrostation.platform.linux.platform import resolve_ports_dir
from tests.conftest import FakePlatform


class SiblingPortsPlatform(FakePlatform):
    """A card where ports only exists as Roms' sibling ``Ports`` folder."""

    def system_dir(self, system_key: str) -> Path:
        if system_key == "ports":
            return self._root.parent / "Ports"
        return self._root / system_key

    def extra_system_keys(self) -> list[str]:
        if (self._root.parent / "Ports").is_dir():
            return ["ports"]
        return []


class TestResolvePortsDir:
    def test_primary_wins(self, tmp_path: Path) -> None:
        (tmp_path / "Roms" / "PORTS").mkdir(parents=True)
        (tmp_path / "Ports").mkdir()

        assert resolve_ports_dir(tmp_path / "Roms") == tmp_path / "Roms" / "PORTS"

    def test_sibling_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "Roms").mkdir()
        (tmp_path / "Ports").mkdir()

        assert resolve_ports_dir(tmp_path / "Roms") == tmp_path / "Ports"

    def test_none_when_neither_exists(self, tmp_path: Path) -> None:
        (tmp_path / "Roms").mkdir()

        assert resolve_ports_dir(tmp_path / "Roms") is None


class TestScanFindsSiblingPorts:
    def test_ports_sh_files_are_scanned(self, tmp_path: Path) -> None:
        roms = tmp_path / "Roms"
        roms.mkdir()
        ports = tmp_path / "Ports"
        ports.mkdir()
        (ports / "仙剑98.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (ports / "毁灭战士 1.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (ports / "Imgs").mkdir()  # artwork folder, never a game
        (ports / "doom").mkdir()  # game data sub-folder
        (ports / "doom" / "doom.sh").write_text("#!/bin/sh\n", encoding="utf-8")

        result = scan_library(SiblingPortsPlatform(roms), Config())

        names = [rom.name for rom in result.systems.get("ports", [])]
        assert sorted(names) == ["仙剑98.sh", "毁灭战士 1.sh"]

    def test_no_ports_entry_when_it_lives_under_roms(self, tmp_path: Path) -> None:
        roms = tmp_path / "Roms"
        (roms / "PORTS").mkdir(parents=True)
        (roms / "PORTS" / "quake.sh").write_text("#!/bin/sh\n", encoding="utf-8")

        result = scan_library(SiblingPortsPlatform(roms), Config())

        # The directory name is the system key (upper case, like every system).
        assert "quake.sh" in [rom.name for rom in result.systems["PORTS"]]
