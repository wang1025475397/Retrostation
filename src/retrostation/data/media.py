"""Media resolution and thumbnail cache.

Resolution order (DESIGN §6.8.5), cheapest first:

1. a path a metadata source already resolved (nothing to do),
2. this app's own directories -- ``Imgs/`` / ``video/`` / ``logo/`` from
   ``config.media_dirs``, which is where tiny-scraper already writes covers,
3. the conventional directories of the metadata formats we read
   (ES-DE ``media/...``, Pegasus ``assets/...``, 天马 ``media/<game>/...``),
4. nothing -- callers fall back to a programmatic placeholder.

Thumbnails are written next to the originals in a dot-directory so the ROM
scanner never mistakes them for media.
"""

from __future__ import annotations

import functools
import hashlib
import io
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import Config
from ..core.model import ASSET_COVER, ASSET_FANART, ASSET_KEYS, ASSET_LOGO, ASSET_SCREENSHOT, ASSET_VIDEO, Game
from ..platform.base import Platform
from .systems import esde_system_name

log = logging.getLogger(__name__)

#: ES-DE's own sub-folder names, relative to the *media root* -- which is
#: ``downloaded_media/<system>`` when the player has an ES-DE tree and
#: ``<SYS>/media`` when they do not.  Same names either way, so a card moved
#: between the two layouts needs no renaming.  The first entry is the one we
#: write to and expect; the rest are aliases other tools leave behind.
_ESDE_TYPE_DIRS: dict[str, tuple[str, ...]] = {
    ASSET_COVER: ("covers", "box2d", "2dbox"),
    ASSET_LOGO: ("marquees", "wheel"),
    ASSET_SCREENSHOT: ("screenshots", "titleshots"),
    ASSET_VIDEO: ("videos",),
    ASSET_FANART: ("fanart",),
}

#: Candidate directories of other frontends, checked after the ES-DE layout.
_FORMAT_DIRS: dict[str, dict[str, tuple[str, ...]]] = {
    "pegasus": {
        ASSET_COVER: ("assets/box_front", "assets/box2d"),
        ASSET_LOGO: ("assets/marquee", "assets/wheel"),
        ASSET_SCREENSHOT: ("assets/screenshot", "assets/titlescreen"),
        ASSET_VIDEO: ("assets/videos", "assets/video"),
        ASSET_FANART: ("assets/fanart",),
    },
}

#: Files tried for each kind, in order.  Videos are separate on purpose.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".avi")

#: One folder per game, named after the game: ``<game>/boxFront.jpg`` (Pegasus,
#: and the 天马 packs built on it).  Here the *folder* is the game and the *file
#: name* is the asset kind -- the opposite of the flat ES-DE layout above.
#:
_PER_GAME_ROOTS = ("media", "assets")
#: Asset kind -> keywords a file name is matched against, best first.
#:
#: Matching is a case-insensitive substring test on the name without its
#: suffix, the way 天马's own tooling scans these folders: ``boxFront.jpg``,
#: ``box_front.png`` and ``cover.webp`` are all the cover, and a scraper that
#: renames to ``logo.png`` or ``marquee.png`` still lands on the logo.
_PER_GAME_KEYWORDS: dict[str, tuple[str, ...]] = {
    ASSET_COVER: ("boxfront", "box_front", "cover", "cartridge"),
    ASSET_LOGO: ("logo", "marquee", "wheel", "middle"),
    ASSET_SCREENSHOT: ("screenshot", "titlescreen"),
    ASSET_VIDEO: ("video",),
    ASSET_FANART: ("fanart", "background"),
}


def _suffixes(kind: str) -> tuple[str, ...]:
    return _VIDEO_SUFFIXES if kind == ASSET_VIDEO else _IMAGE_SUFFIXES


@dataclass
class MediaDirs:
    """Resolved media directories for one system, with a listing cache.

    Looking media up by *listing each directory once* instead of stat-ing every
    guess matters: opening a 600-ROM system used to cost ~5000 stats (and video
    probing would have doubled that), which is a visible hitch on the SD card.
    """

    root: Path
    by_kind: dict[str, Path]
    #: Fallback directories, **per kind**: ES-DE alias names, this app's older
    #: ``Imgs/`` convention, other frontends.  Grouped by kind on purpose --
    #: one mixed list let a cover directory answer a *logo* lookup, because a
    #: lookup only ever matches on the file name, not on what the file is.
    alternates: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    per_game_dirs: tuple[Path, ...] = ()
    platform: object = None
    _listings: dict[Path, dict[tuple[str, str], Path]] = field(default_factory=dict, repr=False)
    _order: dict[str, tuple[Path, ...]] = field(default_factory=dict, repr=False)
    _folders: dict[Path, set[str]] = field(default_factory=dict, repr=False)

    def directories(self, kind: str) -> tuple[Path, ...]:
        """Directories that may hold ``kind`` media, best guess first."""
        cached = self._order.get(kind)
        if cached is not None:
            return cached
        self._order[kind] = cached = tuple(self._build_directories(kind))
        return cached

    def _build_directories(self, kind: str) -> list[Path]:
        ordered: list[Path] = []
        own = self.by_kind.get(kind)
        if own is not None:
            ordered.append(own)
        ordered.extend(path for path in self.alternates.get(kind, ()) if path not in ordered)

        if kind == ASSET_VIDEO:
            # Measured on the RG DS: the scraper in use drops .mp4 files next to
            # the covers (``Imgs/``) instead of the ``video/`` we ask for, so the
            # cover directories are probed for video suffixes too (DESIGN §6.3)
            # -- but only after the video ones, so a real video/ still wins.
            cover = self.by_kind.get(ASSET_COVER)
            if cover is not None and cover not in ordered:
                ordered.append(cover)
            ordered.extend(
                path for path in self.alternates.get(ASSET_COVER, ()) if path not in ordered
            )
        return ordered

    def lookup(self, kind: str, stem: str) -> Path | None:
        """The media file for ``stem``, or ``None``.  No stat storm."""
        suffixes = _suffixes(kind)
        for directory in self.directories(kind):
            listing = self._listing(directory)
            for suffix in suffixes:
                hit = listing.get((stem, suffix))
                if hit is not None:
                    return hit
        return None

    def lookup_per_game(self, kind: str, names: tuple[str, ...]) -> Path | None:
        """Media in ``media/<game>/`` folders, one folder per game (天马).

        ``names`` are the folder spellings to try -- a pack may name the folder
        after the ROM file or after the scraped title, and the title is not
        always the file name.
        """
        keywords = _PER_GAME_KEYWORDS.get(kind)
        if not keywords or not self.per_game_dirs:
            return None
        suffixes = _suffixes(kind)
        for root in self.per_game_dirs:
            folders = self._per_game_folders(root)
            for name in names:
                if name not in folders:
                    continue
                listing = self._listing(root / name)
                for keyword in keywords:
                    for (file_stem, suffix), path in listing.items():
                        if suffix in suffixes and keyword in file_stem.lower():
                            return path
        return None

    def _per_game_folders(self, root: Path) -> set[str]:
        """Sub-folder names of one per-game media root; listed once, then cached."""
        cached = self._folders.get(root)
        if cached is None:
            entries = self.platform.list_dir(root) if self.platform else []
            cached = {entry.name for entry in entries if entry.is_dir}
            self._folders[root] = cached
        return cached

    def candidates(self, kind: str, stem: str) -> list[Path]:
        """Every path that *could* hold ``kind`` media for ``stem``.

        Kept for callers that want the whole search order; :meth:`lookup` is the
        fast path used by the resolver.
        """
        suffixes = _suffixes(kind)
        out: list[Path] = []
        for directory in self.directories(kind):
            out.extend(directory / f"{stem}{suffix}" for suffix in suffixes)
        for root in self.per_game_dirs:
            for keyword in _PER_GAME_KEYWORDS.get(kind, ()):
                out.extend(root / stem / f"{keyword}{suffix}" for suffix in suffixes)
        return out

    def _listing(self, directory: Path) -> dict[tuple[str, str], Path]:
        cached = self._listings.get(directory)
        if cached is not None:
            return cached
        cached = {}
        if self.platform is not None:
            for entry in self.platform.list_dir(directory):
                if entry.is_dir:
                    continue
                stem, _dot, suffix = entry.name.rpartition(".")
                cached.setdefault((stem, f".{suffix.lower()}"), directory / entry.name)
        self._listings[directory] = cached
        return cached


def media_dirs_for(platform: Platform, config: Config, system_key: str) -> MediaDirs:
    """Build the directory set for one system.

    The layout is ES-DE's either way -- ``covers/``, ``screenshots/``,
    ``videos/``, ``marquees/``, ``fanart/`` -- and only the *root* moves:
    ES-DE's shared ``downloaded_media/<system>`` when
    ``config.metadata.esde_root`` is set, otherwise ``<SYS>/media/`` right
    next to the ROMs.  Same sub-folder names either way, so a card moved
    between the two layouts needs no renaming.

    Everything this app used to look in (its own ``Imgs/`` convention, Pegasus
    ``assets/``, one-folder-per-game packs) stays as a *fallback*, so a card
    that was scraped before this change keeps showing its artwork.
    """
    root = platform.rom_root / system_key
    esde_root = Path(config.metadata.esde_root) if config.metadata.esde_root else None
    if esde_root is not None:
        media_root = esde_root / "downloaded_media" / esde_system_name(system_key)
    else:
        media_root = root / "media"

    # Primary directory for each kind: the media root's ES-DE sub-folder.
    by_kind = {kind: media_root / dirs[0] for kind, dirs in _ESDE_TYPE_DIRS.items()}

    alternates: dict[str, list[Path]] = {kind: [] for kind in ASSET_KEYS}

    def add(kind: str, path: Path) -> None:
        if path.is_dir() and path not in alternates[kind]:
            alternates[kind].append(path)

    # Aliases ES-DE and its scrapers also write (box2d, wheel, titleshots...).
    for kind, dirs in _ESDE_TYPE_DIRS.items():
        for name in dirs[1:]:
            add(kind, media_root / name)

    # With a shared ES-DE tree a ROM-directory media/ is still a valid place to
    # look -- that is where a card scraped in place keeps its artwork.
    if esde_root is not None:
        for kind, dirs in _ESDE_TYPE_DIRS.items():
            for name in dirs:
                add(kind, root / "media" / name)

    # This app's own convention (tiny-scraper writes Imgs/), then Pegasus.
    for kind in ASSET_KEYS:
        name = config.media_dirs.get(kind)
        if name:
            add(kind, root / name)
    for kind, dirs in _FORMAT_DIRS["pegasus"].items():
        for name in dirs:
            add(kind, root / name)

    per_game: list[Path] = []
    for name in _PER_GAME_ROOTS:
        path = root / name
        if path.is_dir():
            per_game.append(path)

    return MediaDirs(
        root=root,
        by_kind=by_kind,
        alternates={kind: tuple(paths) for kind, paths in alternates.items()},
        per_game_dirs=tuple(per_game),
        platform=platform,
    )


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def resolve_assets(game: Game, dirs: MediaDirs) -> Game:
    """Fill in the asset kinds the metadata sources did not provide.

    Returns a new :class:`Game`; existing (source-provided) paths win.
    """
    resolved = dict(game.assets)
    stem = game.path.stem
    names = _per_game_names(game)

    for kind in ASSET_KEYS:
        if resolved.get(kind):
            continue
        resolved[kind] = dirs.lookup(kind, stem) or dirs.lookup_per_game(kind, names)

    return game.copy(assets=resolved)


def _per_game_names(game: Game) -> tuple[str, ...]:
    """Folder spellings a per-game media directory may use.

    A 天马 pack names the folder after the game, but not always exactly like the
    ROM file: the scraped title can hold dots and characters the file name lost
    (``Vs. 女子高尔夫`` vs ``Vs 女子高尔夫``), so every spelling is tried.
    """
    names = [game.path.stem, game.path.stem.replace(".", "")]
    if game.name:
        names += [game.name, game.name.replace(".", "")]
    return tuple(dict.fromkeys(name for name in names if name))


def placeholder_bitmap(platform: Platform, seed: str, width: int, height: int) -> object:
    """Deterministic gradient tile used when a game has no artwork.

    Mirrors the prototype's ``coverStyle()`` so the two look the same.
    """
    from PIL import Image, ImageDraw

    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    hue_a = digest[0] * 360 // 255
    hue_b = (hue_a + 40 + digest[1] % 120) % 360

    def hsl(hue: int, sat: float, light: float) -> tuple[int, int, int]:
        import colorsys

        r, g, b = colorsys.hls_to_rgb(hue / 360.0, light, sat)
        return (round(r * 255), round(g * 255), round(b * 255))

    top = hsl(hue_a, 0.46, 0.26)
    bottom = hsl(hue_a, 0.52, 0.14)
    accent = hsl(hue_b, 0.72, 0.58)

    image = Image.new("RGB", (max(1, width), max(1, height)), top)
    draw = ImageDraw.Draw(image)
    for row in range(height):
        t = row / max(1, height - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        draw.line([(0, row), (width, row)], fill=color)

    motif = digest[2] % 4
    cx, cy = int(width * 0.66), int(height * 0.30)
    radius = min(width, height) // 5
    if motif == 0:
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=accent)
    elif motif == 1:
        draw.polygon([(cx, cy - radius), (cx + radius, cy + radius), (cx - radius, cy + radius)], fill=accent)
    elif motif == 2:
        draw.rectangle([cx - radius, cy - radius // 2, cx + radius, cy + radius // 2], fill=accent)
    else:
        draw.ellipse(
            [int(width * 0.30) - radius, int(height * 0.72) - radius,
             int(width * 0.30) + radius, int(height * 0.72) + radius],
            fill=accent,
        )
    return image


# --------------------------------------------------------------------------- #
# Thumbnail cache
# --------------------------------------------------------------------------- #


#: How many thumbnails may wait for the writer thread.  Dropping one is fine:
#: it only means the file is regenerated next time.
_WRITE_QUEUE = 64
#: Pause between writes.  The card is shared with the clip decoder and the
#: metadata reads, and writing flat out starved them: browsing a fresh library
#: measured ~1.9 s per frame, because every frame was waiting on I/O that the
#: writer had queued ahead of it.  One thumbnail at a time keeps the card
#: mostly free for whatever the frame actually needs.
_WRITE_PAUSE = 0.08
#: How long a source's stat() is trusted, and how many are remembered.
#: See :meth:`ThumbnailCache._stat`.
_STAT_TTL = 5.0
_STAT_LIMIT = 4000


#: How to encode a thumbnail for each suffix.  Format-specific on purpose:
#: passing ``quality`` to PNG does nothing, and PNG ignores it silently.
_SAVE_ARGS: dict[str, dict[str, object]] = {
    ".webp": {"format": "WEBP", "quality": 86, "method": 4},
    ".jpg": {"format": "JPEG", "quality": 86},
    ".jpeg": {"format": "JPEG", "quality": 86},
    ".png": {"format": "PNG", "optimize": True},
}

#: Sub-directory (inside ``.cache``) holding readable copies of sources that
#: Pillow on this device cannot open -- see :meth:`ThumbnailCache._readable_copy`.
_COPY_DIR = "src"

#: Upper bound for those copies.  Every slot the UI draws fits inside it, so
#: one copy can serve every layout.
_SOURCE_COPY_LIMIT = 512

#: Tried in order by :func:`cache_suffix`.
_CACHE_FORMATS: tuple[tuple[str, str], ...] = (
    (".webp", "WEBP"),
    (".png", "PNG"),
    (".jpg", "JPEG"),
)


@functools.lru_cache(maxsize=1)
def cache_suffix() -> str:
    """Suffix this device can both write **and read back**.

    The RG DS ships a Pillow built against libjpeg 9 headers but resolves
    libjpeg 6b at runtime, so every JPEG there dies with ``Wrong JPEG library
    version: library is 62, caller expects 90`` -- on write as well as on read.
    The symptom was invisible: each write failed and was swallowed, each read
    fell through, and the thumbnail cache quietly did nothing while every frame
    re-decoded the full-size cover.

    Probing once beats guessing, because a cache file we cannot read is worse
    than having no cache at all.
    """
    from PIL import Image

    probe = Image.new("RGB", (8, 8), (12, 34, 56))
    for suffix, name in _CACHE_FORMATS:
        buffer = io.BytesIO()
        try:
            probe.save(buffer, format=name)
            buffer.seek(0)
            with Image.open(buffer) as handle:
                handle.load()
        except Exception:  # noqa: BLE001 - any failure means "not this format"
            continue
        return suffix
    return ".png"


class ThumbnailCache:
    """On-disk cache for scaled-down artwork.

    Keyed by ``(kind, source file mtime, size)`` so editing a cover invalidates
    its thumbnails without any bookkeeping.  The on-disk format is whatever
    :func:`cache_suffix` reports this device can handle.
    """

    def __init__(self, platform: Platform, root: Path, enabled: bool = True) -> None:
        self._platform = platform
        self._root = Path(root)
        self._enabled = enabled
        self._memory: dict[tuple[str, int, int, int], object] = {}
        self._memory_order: list[tuple[str, int, int, int]] = []
        self._dirs: set[Path] = set()
        self._stats: dict[str, tuple[float, int, bool]] = {}
        # Saving a thumbnail to the SD card measured ~0.5 s, which the UI paid
        # in full the first time a game came on screen.  The bitmap is already
        # decoded by then, so hand the write to a background thread and keep
        # only the (much cheaper) decode on the frame path.
        self._pending: queue.Queue = queue.Queue(maxsize=_WRITE_QUEUE)
        self._writer = threading.Thread(
            target=self._write_loop, name="retrostation-thumbs", daemon=True
        )
        self._writer.start()

    # ------------------------------------------------------------------ #

    def _stat(self, source: Path) -> tuple[int, bool]:
        """``(mtime, is_file)``, re-asked at most every ``_STAT_TTL`` seconds.

        Both are round trips to the SD card, and :meth:`get` used to make them
        on every lookup: ~14 ms a time on this device, which is more than all
        the drawing in the strip combined, paid on every clip frame.  Artwork
        is edited rarely enough that remembering it for a few seconds is a
        good trade.
        """
        path = str(source)
        now = time.monotonic()
        entry = self._stats.get(path)
        if entry is not None and now - entry[0] < _STAT_TTL:
            return entry[1], entry[2]
        try:
            fresh = (now, int(source.stat().st_mtime), True)
        except OSError:
            fresh = (now, 0, False)
        if len(self._stats) >= _STAT_LIMIT:
            self._stats.clear()
        self._stats[path] = fresh
        return fresh[1], fresh[2]

    def get(self, kind: str, source: Path, width: int, height: int) -> object | None:
        """A scaled bitmap for ``source``, or ``None`` if it cannot be made."""
        mtime, exists = self._stat(source)
        if not exists:
            return None
        key = (str(source), width, height, mtime)

        cached = self._memory.get(key)
        if cached is not None:
            return cached

        bitmap = self._decode(source, width, height)
        if bitmap is None:
            return None

        self._remember(key, bitmap)
        return bitmap

    def _decode(self, source: Path, width: int, height: int) -> object | None:
        disk = self._disk_path(source, width, height)
        if disk is not None and disk.is_file():
            try:
                return self._platform.load_image(disk)
            except OSError:
                disk.unlink(missing_ok=True)

        try:
            original = self._platform.load_image(source)
        except OSError:
            # This device's Pillow cannot open JPEG at all (see cache_suffix),
            # and cover art is frequently JPEG.  Fall back to a readable copy
            # rather than showing the placeholder.
            readable = self._readable_copy(source)
            if readable is None:
                return None
            try:
                original = self._platform.load_image(readable)
            except OSError:
                return None

        scaled = fit_bitmap(original, width, height)
        if disk is not None and self._enabled:
            self._store(disk, scaled)
        return scaled

    def _readable_copy(self, source: Path) -> Path | None:
        """A Pillow-readable stand-in for a file Pillow cannot open.

        Cached on disk, so the external decoder (ffmpeg, ~80 ms a call) runs
        **once per cover** instead of once per layout: the per-size cache above
        is keyed by the requested size, and a player switching between list,
        grid and carousel would otherwise re-decode the same JPEG every time.

        The copy is kept at :data:`_SOURCE_COPY_LIMIT` -- comfortably larger
        than any slot the UI draws, small enough to stay cheap to store.
        """
        if not self._enabled:
            return None

        directory = source.parent / ".cache" / _COPY_DIR
        digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
        target = directory / f"{digest}{cache_suffix()}"
        if target.is_file():
            return target

        temporary = directory / f"{digest}.tmp.png"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        try:
            if not self._platform.transcode_image(
                source, temporary, _SOURCE_COPY_LIMIT, _SOURCE_COPY_LIMIT
            ):
                return None
            image = self._platform.load_image(temporary)
        except OSError:
            return None
        finally:
            temporary.unlink(missing_ok=True)

        # Through the same writer as the thumbnails, so both layers end up in a
        # format this device can actually read back.
        try:
            self._write(target, image)
        except Exception:  # noqa: BLE001 - a lost copy only costs time, not correctness
            log.debug("could not cache readable copy of %s", source)
            return None
        return target

    def _disk_path(self, source: Path, width: int, height: int) -> Path | None:
        """``Imgs/.cache/<hash>_<w>x<h><suffix>``; ``None`` when caching is off."""
        if not self._enabled:
            return None
        digest = hashlib.sha1(f"{source}|{width}x{height}".encode("utf-8")).hexdigest()[:16]
        suffix = cache_suffix()
        directory = source.parent / ".cache"
        # mkdir() is a round trip to the card on every miss; remember the ones
        # that already exist instead of asking again each time.
        if directory not in self._dirs:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                return None
            self._dirs.add(directory)
        return directory / f"{digest}_{width}x{height}{suffix}"

    def _store(self, target: Path, bitmap: object) -> None:
        """Queue the bitmap for the writer thread; never blocks the caller."""
        try:
            self._pending.put_nowait((target, bitmap))
        except queue.Full:
            log.debug("thumbnail write queue full, dropping %s", target)

    def flush(self) -> None:
        """Block until every queued thumbnail has been written.

        For tests and shutdown only: the point of the queue is that the frame
        loop never waits for the card.
        """
        self._pending.join()

    def _write_loop(self) -> None:
        while True:
            target, bitmap = self._pending.get()
            try:
                self._write(target, bitmap)
            except Exception:  # noqa: BLE001 - a lost thumbnail is not fatal
                log.debug("thumbnail write failed for %s", target, exc_info=True)
            finally:
                self._pending.task_done()
                time.sleep(_WRITE_PAUSE)

    def _write(self, target: Path, bitmap: object) -> None:
        from PIL import Image

        image: Image.Image = bitmap  # type: ignore[assignment]
        if image.mode in ("RGBA", "LA", "P"):
            # Flatten onto the app background: the cache format is chosen for
            # size, and keeping an alpha channel there costs more than it saves.
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (20, 20, 20))
            background.paste(image, mask=image.split()[-1])
            image = background

        kwargs = _SAVE_ARGS.get(target.suffix.lower()) or _SAVE_ARGS[".png"]
        image.save(target, **kwargs)  # type: ignore[arg-type]

    # -- memory LRU ------------------------------------------------------- #

    def _remember(self, key: tuple[str, int, int, int], bitmap: object) -> None:
        limit = 40
        self._memory[key] = bitmap
        self._memory_order.append(key)
        while len(self._memory_order) > limit:
            oldest = self._memory_order.pop(0)
            self._memory.pop(oldest, None)

    def clear_memory(self) -> None:
        self._memory.clear()
        self._memory_order.clear()


def fit_bitmap(bitmap: object, width: int, height: int) -> object:
    """Scale to fit inside ``width x height``, never upscaling.

    Shared by the game thumbnail cache and the shipped platform artwork, so
    both end up as RGBA bitmaps ready to paste.
    """
    from PIL import Image

    # ``Image.Resampling`` only exists on Pillow >= 9.1.0; older builds still
    # expose the deprecated ``Image.LANCZOS`` alias.
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    image: Image.Image = bitmap  # type: ignore[assignment]
    scale = min(width / image.width, height / image.height, 1.0)
    if scale < 1.0:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, resample)
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    return image
