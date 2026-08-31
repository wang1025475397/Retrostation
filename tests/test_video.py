"""Video playback tests (DESIGN §6.5).

Everything here runs without ffmpeg: the platform hands the player a fake
:class:`VideoPipe`, and the clock is injected, so the debounce and the
failure handling are exercised deterministically instead of with ``sleep``.

The one thing these tests cannot cover is real decoding -- that is what
``scripts/video_selftest.py`` does on the device.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from retrostation.core.config import Config
from retrostation.core.model import ASSET_VIDEO, Game
from retrostation.data.video import VideoPlayer, VideoSettings
from retrostation.platform.base import VideoPipe
from retrostation.platform.linux.video import build_command
from tests.conftest import FakePlatform

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakePipe(VideoPipe):
    """A pipe that hands out solid-colour bitmaps, then reports EOF."""

    def __init__(self, frames: int = 3, size: tuple[int, int] = (8, 6), duration: float = 0.0) -> None:
        self.size = size
        self._frames = frames
        self._duration = duration
        self.read_count = 0
        self.closed = False
        self.duration_read = threading.Event()

    def read_frame(self) -> object | None:
        if self.read_count >= self._frames:
            return None
        self.read_count += 1
        shade = (self.read_count * 37) % 256
        return Image.new("RGBA", self.size, (shade, 40, 90, 255))

    @property
    def duration(self) -> float:
        self.duration_read.set()
        return self._duration

    def close(self) -> None:
        self.closed = True


class VideoPlatform(FakePlatform):
    """A platform whose decoder always works."""

    def __init__(self, root: Path, *, frames: int = 3, duration: float = 0.0) -> None:
        super().__init__(root)
        self.frames = frames
        self.duration = duration
        self.calls: list[tuple[Path, int, int, int]] = []
        self.pipes: list[FakePipe] = []

    def open_video_pipe(self, path: Path, *, width: int, height: int, fps: int) -> VideoPipe:
        self.calls.append((path, width, height, fps))
        pipe = FakePipe(frames=self.frames, size=(width, height), duration=self.duration)
        self.pipes.append(pipe)
        return pipe


class SilentPlatform(FakePlatform):
    """A platform with no decoder at all -- the ffmpeg-missing case."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.calls: list[Path] = []

    def open_video_pipe(self, path: Path, *, width: int, height: int, fps: int) -> VideoPipe | None:
        self.calls.append(path)
        return None


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def game(key: str = "FC/魂斗罗.nes", *, video: str | None = "魂斗罗.mp4") -> Game:
    rom = Path("e:/roms") / key.split("/", 1)[1]
    instance = Game(key=key, path=rom, name=Path(rom).stem)
    if video is not None:
        instance.set_asset(ASSET_VIDEO, rom.parent / "video" / video)
    return instance


def make_player(platform, clock: Clock | None = None, **settings) -> VideoPlayer:
    clock = clock or Clock()
    return VideoPlayer(platform, VideoSettings(**settings), clock=clock, sleep=lambda _s: None)


def wait_until(predicate, timeout: float = 3.0) -> bool:
    """Poll instead of sleeping: the pump thread is as fast as the fake clock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def _no_leaked_threads():
    players: list[VideoPlayer] = []
    yield players
    for player in players:
        player.close()


# --------------------------------------------------------------------------- #
# Player behaviour
# --------------------------------------------------------------------------- #


class TestPending:
    """The window between "asked for a clip" and "first frame is on screen".

    The UI draws cover art when there is no frame -- without this state that
    meant a cover flashed on every game that has a clip, for as long as the
    decoder takes to start.
    """

    def test_pending_covers_the_gap(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)
        key = game().key

        assert player.is_pending(key) is False       # nothing asked for yet

        player.select(game())
        assert player.is_pending(key) is True        # asked, debounce not elapsed

        clock.advance(0.3)
        player.update()
        assert wait_until(lambda: player.frame() is not None)
        assert player.is_pending(key) is False       # the first frame is up
        player.stop()

    def test_pending_lapses_for_a_blacklisted_clip(self, rom_root: Path) -> None:
        """A clip that cannot be decoded must hand the slot back to the cover."""
        platform = VideoPlatform(rom_root)
        player = make_player(platform, Clock())
        key = game().key

        player._failed.add(key)                      # noqa: SLF001 - the blacklist itself
        player.select(game())
        assert player.is_pending(key) is False

    def test_other_games_are_not_pending(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        player = make_player(platform, Clock())
        player.select(game())
        assert player.is_pending("FC/some-other-game.nes") is False


class TestDebounce:
    def test_decoder_waits_for_the_selection_to_settle(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        assert platform.calls == [], "must not start on the very first frame"

        clock.advance(0.1)
        player.update()
        assert platform.calls == []

        clock.advance(0.2)  # 0.3s total > 0.25s debounce
        player.update()
        assert len(platform.calls) == 1
        assert wait_until(lambda: player.frame() is not None)
        player.stop()

    def test_scrolling_spawns_nothing(self, rom_root: Path) -> None:
        """500 ROMs flown past must not mean 500 ffmpeg processes."""
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        for index in range(20):
            player.select(game(f"FC/game{index}.nes", video=f"game{index}.mp4"))
            clock.advance(0.05)
            player.update()

        assert platform.calls == []
        assert player.frame() is None

    def test_settling_starts_exactly_one(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        for _ in range(10):
            clock.advance(0.05)
            player.update()

        assert len(platform.calls) == 1
        player.stop()


class TestSwitching:
    def test_new_selection_drops_the_old_frame(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game("FC/a.nes", video="a.mp4"))
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)

        player.select(game("FC/b.nes", video="b.mp4"))
        assert player.frame() is None, "the previous game's frame must not leak"
        assert platform.pipes[0].closed is True

        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)
        assert len(platform.pipes) == 2
        player.stop()

    def test_leaving_the_game_page_stops_the_decoder(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)

        player.select(None)
        player.update()
        assert platform.pipes[0].closed is True
        assert player.frame() is None

    def test_stop_closes_the_pipe_and_clears_the_frame(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)

        player.stop()
        assert platform.pipes[0].closed is True
        assert player.frame() is None
        assert player.is_playing() is False

    def test_stop_is_idempotent(self, rom_root: Path) -> None:
        player = make_player(VideoPlatform(rom_root), Clock())
        player.stop()
        player.stop()


class TestFailures:
    def test_no_decoder_means_cover_art(self, rom_root: Path) -> None:
        platform = SilentPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        for _ in range(6):
            player.select(game())
            clock.advance(0.5)
            player.update()

        assert player.frame() is None
        assert player.is_playing() is False
        assert len(platform.calls) == 1, "a failed file is not retried every frame"

    def test_missing_decoder_disables_the_feature(self, rom_root: Path) -> None:
        platform = SilentPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        for index in range(5):
            player.select(game(f"FC/g{index}.nes", video=f"g{index}.mp4"))
            clock.advance(0.5)
            player.update()

        assert player.enabled is False
        assert len(platform.calls) == 3, "give up after three files, not per frame"

    def test_silent_stream_is_blacklisted(self, rom_root: Path) -> None:
        """A pipe that yields nothing must degrade to the cover (§6.5)."""
        platform = VideoPlatform(rom_root, frames=0)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert len(platform.pipes) == 1
        assert player.frame() is None

        clock.advance(5.0)  # past the 3s stale window
        player.update()
        assert platform.pipes[0].closed is True

        # Selecting the same game again must not retry the broken file.
        player.select(game())
        clock.advance(1.0)
        player.update()
        assert len(platform.pipes) == 1

    def test_game_without_video_is_ignored(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        player = make_player(platform, Clock())

        for _ in range(4):
            player.select(game(video=None))
            player.update()
        assert platform.calls == []


class TestTiming:
    def test_progress_follows_the_clip_length(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root, frames=50, duration=10.0)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)
        assert platform.pipes[0].duration_read.wait(3.0)

        clock.advance(2.5)
        assert player.progress() == pytest.approx(0.25, abs=0.02)
        player.stop()

    def test_progress_is_none_without_a_duration(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root, duration=0.0)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)
        assert player.progress() is None
        player.stop()

    def test_frame_seq_only_moves_on_new_frames(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root, frames=2)
        player = make_player(platform, Clock())

        player.select(game())
        player.update()
        assert player.frame_seq == 0

        player._clock.advance(0.5)  # noqa: SLF001 - the debounce is the point
        player.update()
        assert wait_until(lambda: player.frame_seq >= 2)
        player.stop()


class TestConfiguration:
    def test_settings_come_from_the_config(self) -> None:
        config = Config()
        config.video_size = [320, 240]
        config.video_fps = 20
        settings = VideoSettings.from_config(config)
        assert (settings.width, settings.height, settings.fps) == (320, 240, 20)

    def test_decoding_at_the_draw_size(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)
        player.configure(size=(328, 256))

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert platform.calls[0][1:] == (328, 256, 15)
        player.stop()

    def test_disabling_stops_playback(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)

        player.configure(enabled=False)
        assert platform.pipes[0].closed is True
        assert player.frame() is None

        clock.advance(1.0)
        player.select(game())
        player.update()
        assert len(platform.pipes) == 1

    def test_a_closed_player_never_restarts(self, rom_root: Path) -> None:
        """After a launch (or shutdown) nothing may respawn ffmpeg."""
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.close()
        player.configure(enabled=True)  # the UI loop keeps asking
        player.select(game())
        clock.advance(1.0)
        player.update()

        assert platform.calls == []
        assert player.enabled is False

    def test_close_stops_everything(self, rom_root: Path) -> None:
        platform = VideoPlatform(rom_root)
        clock = Clock()
        player = make_player(platform, clock)

        player.select(game())
        clock.advance(0.5)
        player.update()
        assert wait_until(lambda: player.frame() is not None)

        player.close()
        assert platform.pipes[0].closed is True
        assert player.enabled is False


# --------------------------------------------------------------------------- #
# End to end: the app drives the player and the bottom screen shows frames
# --------------------------------------------------------------------------- #


def _app_with_video(rom_root: Path, platform, *, config: Config | None = None,
                    frames: int = 5, duration: float = 8.0):
    """An App whose bottom screen is positioned on the ROM that has a video."""
    from retrostation.core.i18n import Translator
    from retrostation.data.library import Library
    from retrostation.ui.app import App

    config = config or Config()
    library = Library(platform, config)
    library.scan()

    clock = Clock(step=0.1)  # every clock read advances past the debounce
    player = make_player(platform, clock, fps=15)
    app = App(platform, config, Translator("zh_CN"), library, video=player)

    app.session.view = "games"
    app.session.platform_index = app.session.system_keys().index("FC")
    index = next(i for i, g in enumerate(app.session.games()) if g.key.endswith("魂斗罗.nes"))
    app.session.game_index = index
    return app, player


class TestAppIntegration:
    def test_bottom_screen_shows_decoded_frames(self, rom_root: Path) -> None:
        # Found by the "video next to the covers" probe, exactly like the device.
        (rom_root / "FC" / "Imgs" / "魂斗罗.mp4").write_bytes(b"fake")

        platform = VideoPlatform(rom_root, frames=5, duration=8.0)
        app, _player = _app_with_video(rom_root, platform)
        app.run(max_frames=8)

        assert platform.calls[0][0].name == "魂斗罗.mp4"
        assert platform.pipes[0].read_count > 0, "the decoder must have been drained"

        bottom = app.platform.canvases[1].pil_image
        colours = set(bottom.getdata())
        assert any(pixel[1] == 40 and pixel[2] == 90 for pixel in colours), \
            "the decoded frame must be drawn into the media box"

    def test_without_video_the_cover_is_drawn_instead(self, rom_root: Path) -> None:
        """``bottom_video = false`` (or no decoder) must fall back to the cover."""
        (rom_root / "FC" / "Imgs" / "魂斗罗.mp4").write_bytes(b"fake")

        config = Config()
        config.bottom_video = False
        platform = VideoPlatform(rom_root, frames=5)
        app, _player = _app_with_video(rom_root, platform, config=config)
        app.run(max_frames=8)

        assert platform.calls == []
        bottom = app.platform.canvases[1].pil_image
        colours = set(bottom.getdata())
        assert not any(pixel[1] == 40 and pixel[2] == 90 for pixel in colours)

    def test_launching_a_game_stops_the_decoder(self, rom_root: Path) -> None:
        """DESIGN §8.1 step ③: never hand an ffmpeg over to the emulator."""
        from retrostation.platform.base import InputAction, InputEvent
        from retrostation.ui.app import EXIT_RESTART

        (rom_root / "FC" / "Imgs" / "魂斗罗.mp4").write_bytes(b"fake")
        script = rom_root / "RA_launch.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        platform = VideoPlatform(rom_root, frames=5)
        config = Config()
        config.launcher.ra_script = str(script)
        app, player = _app_with_video(rom_root, platform, config=config)
        app.run(max_frames=8)
        assert platform.pipes, "the video must have been playing before the launch"

        platform.send(InputEvent(InputAction.A))
        assert app.run(max_frames=2) == EXIT_RESTART
        assert platform.launched is not None
        assert platform.pipes[0].closed is True
        assert player.enabled is False


class SlowPipe(VideoPipe):
    """A decoder that is slow to shut down -- like ffmpeg ignoring SIGTERM."""

    def __init__(self, delay: float, frames: int = 3, size: tuple[int, int] = (8, 6)) -> None:
        self.size = size
        self.delay = delay
        self._frames = frames
        self._read = 0
        self.closed = threading.Event()

    def read_frame(self) -> object | None:
        if self._read >= self._frames or self.closed.is_set():
            return None
        self._read += 1
        return Image.new("RGBA", self.size, (10, 40, 90, 255))

    def close(self) -> None:
        time.sleep(self.delay)
        self.closed.set()


class SlowClosePlatform(VideoPlatform):
    """Hands out pipes that take a while to die."""

    def __init__(self, root: Path, *, delay: float = 0.4) -> None:
        super().__init__(root)
        self.delay = delay
        self.slow: list[SlowPipe] = []

    def open_video_pipe(self, path: Path, *, width: int, height: int, fps: int) -> VideoPipe:
        self.calls.append((path, width, height, fps))
        pipe = SlowPipe(self.delay, size=(width, height))
        self.slow.append(pipe)
        return pipe


class TestStoppingTheDecoder:
    """Switching games must not wait for the decoder that is on its way out.

    Measured on the RG DS: ffmpeg regularly ignores SIGTERM for a full second
    while it is mid-read on the SD card, and the player used to sit in that wait
    on the UI thread -- one second of frozen frontend per game switch.
    """

    def _playing(self, platform: SlowClosePlatform, clock: Clock) -> VideoPlayer:
        player = make_player(platform, clock)
        player.select(game("FC/a.nes", video="a.mp4"))
        clock.advance(0.3)          # past the debounce
        player.update()
        assert wait_until(lambda: player.frame() is not None)
        return player

    def test_switching_does_not_block_on_the_dying_decoder(self, rom_root: Path) -> None:
        platform = SlowClosePlatform(rom_root, delay=0.4)
        player = self._playing(platform, Clock())

        started = time.monotonic()
        player.select(game("FC/b.nes", video="b.mp4"))
        elapsed = time.monotonic() - started

        assert elapsed < 0.1, f"switching must not wait for the old decoder ({elapsed:.2f}s)"
        # It does get closed -- just not on our thread.
        assert wait_until(lambda: platform.slow[0].closed.is_set())
        player.close()

    def test_closing_still_waits_for_it(self, rom_root: Path) -> None:
        """Handing the device over must not leave an ffmpeg behind (§8.1 ③)."""
        platform = SlowClosePlatform(rom_root, delay=0.25)
        player = self._playing(platform, Clock())

        player.select(game("FC/b.nes", video="b.mp4"))   # queues a background stop
        player.close()

        assert platform.slow[0].closed.is_set(), "close() must wait for every decoder"


# --------------------------------------------------------------------------- #
# The ffmpeg command itself
# --------------------------------------------------------------------------- #


class TestFFmpegCommand:
    def test_shape(self) -> None:
        command = build_command(Path("/mnt/mmc/Roms/NDS/video/a.mp4"), width=288, height=216, fps=15)
        assert command[0] == "ffmpeg"
        assert command[-3:] == ["-f", "rawvideo", "-"]
        assert "-stream_loop" in command and "-1" in command
        assert "-an" in command
        assert "-pix_fmt" in command and command[command.index("-pix_fmt") + 1] == "rgb24"
        assert any("scale=288:216" in part for part in command)
        assert any("fps=15" in part for part in command)

    def test_there_is_no_input_rate(self) -> None:
        """``-r`` before ``-i`` retimes the clip, it does not drop frames.

        It tells the demuxer the file runs at our target rate, so a 30 fps
        clip played at half speed and a 60 fps one at a quarter -- slow motion.
        Hitting a frame rate is the ``fps`` filter's job, and it goes before
        ``scale`` so the frames it drops are never scaled.
        """
        command = build_command(Path("a.mp4"), width=100, height=100, fps=12)
        assert "-r" not in command, "an input -r slows the clip down"
        steps = command[command.index("-vf") + 1].split(",")
        assert steps[0] == "fps=12"
        assert steps[1].startswith("scale=")

    def test_path_is_one_argument(self) -> None:
        """Chinese filenames must survive: a list argv, never a shell string."""
        path = Path("/mnt/mmc/Roms/NDS/video/恶魔城-苍月的十字架.mp4")
        command = build_command(path, width=64, height=48, fps=10)
        index = command.index("-i")
        assert command[index + 1] == str(path)
        assert " " not in command[index + 1].replace("/mnt/mmc/Roms/NDS/video/", "")

    def test_letterboxes_instead_of_stretching(self) -> None:
        command = build_command(Path("a.mp4"), width=100, height=100, fps=12)
        video_filter = command[command.index("-vf") + 1]
        assert "force_original_aspect_ratio=decrease" in video_filter
        assert "pad=100:100" in video_filter
