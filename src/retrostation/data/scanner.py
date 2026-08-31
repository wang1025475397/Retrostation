"""ROM scanning and the index cache.

Scanning is deliberately split from metadata loading: a scan only stats files
(cheap, parallelisable later), while metadata parsing can be slow and is done
lazily per system.

The index cache exists for cold-start speed (DESIGN §6.2): on the second run we
still list the directories -- that is how we *detect* changes -- but we skip
rebuilding objects for systems whose signature is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..core.config import Config
from ..platform.base import Platform
from .systems import AGGREGATE_KEYS, lookup

log = logging.getLogger(__name__)

#: Used for systems that are not in the table yet -- better than showing an
#: empty library just because we never heard of the directory.
_GENERIC_EXTENSIONS = frozenset(
    {"zip", "7z", "rom", "bin", "img", "iso", "cue", "chd", "m3u"}
)

#: Never treated as games, whatever the extension says.
_SKIPPED_NAMES = (".cache", "Imgs", "video", "logo", "media", "assets", ".retrostation")

INDEX_VERSION = 2

#: How many system directories are listed at once.  Listing one costs a stat()
#: per file, so the work is I/O bound and the card keeps up with a handful of
#: directories in parallel -- measured on the RG DS below.
_SCAN_WORKERS = 4


@dataclass(frozen=True)
class Rom:
    """One game file on disk."""

    name: str
    path: Path
    size: int
    mtime: float

    @property
    def key(self) -> str:
        return self.name


def signature(roms: Iterable[Rom]) -> str:
    """A cheap fingerprint of a system's file list."""
    payload = "|".join(f"{rom.name}:{rom.size}:{int(rom.mtime)}" for rom in sorted(roms, key=lambda r: r.name))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def accepted_extensions(system_key: str) -> frozenset[str]:
    """Extensions (lower-case, no dot) this system's directory may contain."""
    definition = lookup(system_key)
    if definition.extensions:
        return frozenset(ext.lower().lstrip(".") for ext in definition.extensions)
    return _GENERIC_EXTENSIONS


def is_rom(name: str, extensions: frozenset[str]) -> bool:
    """True when ``name`` looks like a game file."""
    if name.startswith("."):
        return False
    if name.startswith(_SKIPPED_NAMES):
        return False
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return suffix in extensions


def scan_system(platform: Platform, system_key: str) -> list[Rom]:
    """List the ROMs of one system directory (never raises)."""
    directory = platform.rom_root / system_key
    extensions = accepted_extensions(system_key)
    roms: list[Rom] = []

    for entry in platform.list_dir(directory):
        if entry.is_dir or not is_rom(entry.name, extensions):
            continue
        roms.append(
            Rom(name=entry.name, path=directory / entry.name, size=entry.size, mtime=entry.mtime)
        )
    roms.sort(key=lambda rom: _sort_name(rom.name))
    return roms


def _sort_name(name: str) -> str:
    """Case-insensitive sort that keeps CJK and latin names interleaved sanely."""
    return name.casefold()


# --------------------------------------------------------------------------- #
# Index cache
# --------------------------------------------------------------------------- #


class LibraryIndex:
    """JSON cache of scan results, keyed by a per-system signature."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": INDEX_VERSION, "systems": {}}
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            return {"version": INDEX_VERSION, "systems": {}}
        data.setdefault("systems", {})
        return data

    def get(self, system_key: str, expected: str) -> list[Rom] | None:
        """Cached ROMs for ``system_key`` if its signature still matches."""
        record = self._data["systems"].get(system_key)
        if not isinstance(record, dict) or record.get("signature") != expected:
            return None
        roms: list[Rom] = []
        for item in record.get("roms", []):
            try:
                roms.append(
                    Rom(
                        name=item["n"],
                        path=Path(item["p"]),
                        size=int(item["s"]),
                        mtime=float(item["m"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                return None
        return roms

    def matches(self, system_key: str, expected: str) -> bool:
        """True when the cached signature still matches, without rebuilding ROMs.

        A hit means the listing :func:`scan_library` just made is already
        exactly what the cache holds, so it keeps its own objects instead of
        deserialising thousands of them only to drop them.
        """
        record = self._data["systems"].get(system_key)
        return isinstance(record, dict) and record.get("signature") == expected

    def count(self, system_key: str) -> int:
        record = self._data["systems"].get(system_key)
        if not isinstance(record, dict):
            return -1
        return len(record.get("roms", ()))

    def restore(self) -> dict[str, list[Rom]]:
        """Everything the index holds, without checking it against the disk.

        Used to bring the first frame up with a populated library: listing the
        ROM tree costs a stat() per file (~0.7 s for 3.9k ROMs here) and the
        listing that actually detects changes runs behind the UI anyway.
        """
        systems: dict[str, list[Rom]] = {}
        for key, record in self._data["systems"].items():
            if not isinstance(record, dict):
                continue
            roms = self.get(key, record.get("signature", ""))
            if roms is not None:
                systems[key] = roms
        return systems

    def put(self, system_key: str, sig: str, roms: list[Rom]) -> None:
        self._data["systems"][system_key] = {
            "signature": sig,
            "roms": [
                {"n": rom.name, "p": str(rom.path), "s": rom.size, "m": rom.mtime}
                for rom in roms
            ],
        }

    def flush(self) -> None:
        """Write the cache atomically; failures are logged, never raised."""
        self._data["updated"] = int(time.time())
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            log.debug("index flush failed: %s", exc)

    def forget(self, system_key: str) -> None:
        self._data["systems"].pop(system_key, None)


# --------------------------------------------------------------------------- #
# Library scan
# --------------------------------------------------------------------------- #


@dataclass
class ScanResult:
    """What one library scan produced."""

    systems: dict[str, list[Rom]]
    duration: float
    cached: int = 0
    rescanned: int = 0

    @property
    def total_roms(self) -> int:
        return sum(len(roms) for roms in self.systems.values())


def _scan_all(platform: Platform, keys: list[str]) -> list[list[Rom]]:
    """List every system directory, several at a time.

    Listing one directory costs a stat() per file, so the work is I/O bound and
    the GIL is released throughout: on the RG DS, 3.9k ROMs across 54
    directories took 0.71 s serially and about a third of that with four
    workers.  Cold start is dominated by this, so it is worth the threads.
    """
    if len(keys) < 2:
        return [scan_system(platform, key) for key in keys]
    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as pool:
        return list(pool.map(lambda key: scan_system(platform, key), keys))


def scan_library(
    platform: Platform,
    config: Config,
    *,
    index_path: Path | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    cached_only: bool = False,
) -> ScanResult:
    """Scan every known system directory.

    ``on_progress(system_key, done, total)`` lets the UI draw a progress bar
    while the scan runs in the background.

    ``cached_only`` skips the directory listing entirely and hands back what
    the index remembers, so the first frame can come up with a full library
    instead of an empty one.  Nothing is verified against the disk in that
    mode -- the caller is expected to run a real scan behind it.
    """
    started = time.monotonic()
    index = LibraryIndex(index_path or platform.config_dir / "index.json")

    if cached_only:
        systems = index.restore()
        return ScanResult(systems=systems, duration=time.monotonic() - started,
                          cached=len(systems), rescanned=0)

    entries = sorted(platform.list_dir(platform.rom_root), key=lambda entry: entry.name)
    keys = [
        entry.name
        for entry in entries
        if entry.is_dir and entry.name not in AGGREGATE_KEYS and not lookup(entry.name).hidden
    ]

    systems: dict[str, list[Rom]] = {}
    cached = rescanned = 0
    total = len(keys)

    listings = _scan_all(platform, keys)
    for position, (key, roms) in enumerate(zip(keys, listings), start=1):
        sig = signature(roms)
        systems[key] = roms
        # The index answers one question -- did this system change?  On a hit
        # the listing above is already the answer, so there is nothing to read
        # back: deserialising ~3.9k ROMs to replace them with identical ones
        # used to cost a fifth of a second of cold start.
        if index.matches(key, sig) and len(roms) == index.count(key):
            cached += 1
        else:
            index.put(key, sig, roms)
            rescanned += 1
        if on_progress:
            on_progress(key, position, total)

    index.flush()
    return ScanResult(systems=systems, duration=time.monotonic() - started, cached=cached, rescanned=rescanned)
