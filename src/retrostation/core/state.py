"""Runtime state that is *not* user settings (DESIGN §8.1 step ①).

Deliberately separate from ``config.json``: this file is rewritten every time a
game starts, on a device that can lose power mid-write.  A truncated settings
file would strand the player with a frontend that refuses to boot; the worst a
truncated state file can do is start them on the home page instead of where
they left off.

Right now it holds exactly one thing -- the ``resume`` snapshot -- which is
what lets the shell bootstrap put the player back in front of the game they
just quit (DESIGN §8.2).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_state(path: Path | str) -> dict[str, Any]:
    """Load the state file; ``{}`` when it is missing or unreadable.

    A corrupt file is not worth failing a boot over -- see the module note.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def update_state(path: Path | str, **changes: Any) -> dict[str, Any]:
    """Merge ``changes`` into the state file and write it atomically."""
    target = Path(path)
    payload = read_state(target)
    payload.update(changes)

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return payload
