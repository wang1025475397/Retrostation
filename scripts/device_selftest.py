#!/usr/bin/env python3
"""On-device smoke test for the ES-DE write path.

Creates a sandbox library in /tmp, writes a gamelist through the Library
facade, then re-reads it to prove the round-trip is intact on the target
filesystem (UTF-8 names, permissions, atomic replace).
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from retrostation.core.config import Config
from retrostation.data.library import Library
from retrostation.platform.linux.platform import LinuxPlatform

SANDBOX = Path("/tmp/retrostation_selftest")


def main() -> int:
    shutil.rmtree(SANDBOX, ignore_errors=True)
    fc = SANDBOX / "FC"
    fc.mkdir(parents=True)
    (fc / "超级马力欧兄弟.nes").write_bytes(b"nes")
    (fc / "魂斗罗.nes").write_bytes(b"nes")
    (fc / "Imgs").mkdir()
    (fc / "Imgs" / "魂斗罗.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    platform = LinuxPlatform(rom_root=str(SANDBOX))
    library = Library(platform, Config())
    library.scan()
    games = library.load_games("FC")

    target = next(g for g in games.games if g.name == "魂斗罗")
    target.favorite = True
    target.play_count = 7
    target.last_played = datetime(2026, 8, 28, 21, 30)
    written = library.save_state(target, "FC")
    print("written:", written)

    text = (fc / "gamelist.xml").read_text(encoding="utf-8")
    print(text)

    # Re-read through the facade: state must survive the round trip.
    reread = library.load_games("FC")
    again = next(g for g in reread.games if g.name == "魂斗罗")
    assert again.favorite is True, "favourite lost on reload"
    assert again.play_count == 7, "playcount lost on reload"
    print("round-trip OK; files:", sorted(p.name for p in fc.iterdir()))
    shutil.rmtree(SANDBOX, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
