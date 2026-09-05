"""Preview sound: the clip's track, playing only while the clip is previewed.

The properties that matter are lifecycle ones -- a soundtrack that outlives its
clip is worse than no sound at all, because it keeps playing behind whatever
the player launched.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from retrostation.core.config import Config
from retrostation.core.i18n import Translator
from retrostation.core.model import ASSET_VIDEO, Game
from retrostation.data.library import Library
from retrostation.data.video import VideoPlayer, VideoSettings
from retrostation.platform.base import InputAction, InputEvent
from retrostation.ui.session import Session
from tests.conftest import FakePlatform


class FakeAudioPipe:
    """Stands in for the ALSA player: remembers its volume and whether it stopped."""

    def __init__(self, volume: float = 1.0) -> None:
        self.closed = False
        self.volume = volume

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def close(self) -> None:
        self.closed = True


class FakeVideoPipe:
    """Duck-typed video pipe: two frames, then end of stream."""

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.duration = 1.0
        self.closed = False
        self._left = 2

    def read_frame(self) -> object | None:
        if self._left <= 0:
            return None
        self._left -= 1
        return object()

    def close(self) -> None:
        self.closed = True


class SoundPlatform(FakePlatform):
    """Decodes pictures and records every audio pipe it is asked for."""

    def __init__(self, root: Path, *, sound: bool = True) -> None:
        super().__init__(root)
        self.sound = sound
        self.audio_calls: list[tuple[Path, float]] = []
        self.audio_pipes: list[FakeAudioPipe] = []
        #: Was the card still taken when this one asked for it?  It cannot be
        #: opened twice, so every entry here must be ``False``.
        self.opened_while_busy: list[bool] = []

    def open_video_pipe(self, path, *, width, height, fps):
        return FakeVideoPipe((width, height))

    def open_audio_pipe(self, path, *, volume: float = 1.0):
        if not self.sound:
            return None
        self.audio_calls.append((Path(path), volume))
        self.opened_while_busy.append(any(not pipe.closed for pipe in self.audio_pipes))
        pipe = FakeAudioPipe()
        self.audio_pipes.append(pipe)
        return pipe

    def wait_for_sound(self, player=None, count: int = 1,
                       timeout: float = 3.0) -> bool:
        """Wait until the player actually *holds* the soundtrack.

        Counting the open call is not enough: the pipe is handed over after it
        returns, and a volume change or a switch landing in that gap finds the
        player empty-handed -- which is exactly the race this hides.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.audio_calls) < count:
                time.sleep(0.005)
                continue
            if player is None or player._audio is not None:  # noqa: SLF001
                return True
            time.sleep(0.005)
        return False


def clip(key: str = "魂斗罗") -> Game:
    rom = Path("e:/roms/FC") / f"{key}.nes"
    instance = Game(key=f"FC/{key}.nes", path=rom, name=key)
    instance.set_asset(ASSET_VIDEO, rom.parent / "video" / f"{key}.mp4")
    return instance


def player_for(platform: SoundPlatform, **settings) -> VideoPlayer:
    settings.setdefault("debounce", 0.0)
    return VideoPlayer(
        platform,
        VideoSettings(**settings),
        clock=lambda: 100.0,
        sleep=lambda _seconds: None,
    )


class TestPreviewSound:
    def test_starting_a_clip_opens_an_audio_pipe(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform, volume=0.5)
        player.select(clip())
        assert platform.wait_for_sound(player,), "the soundtrack never opened"

        assert len(platform.audio_calls) == 1
        path, volume = platform.audio_calls[0]
        assert path.name == "魂斗罗.mp4"
        assert volume == pytest.approx(0.5)

    def test_sound_off_never_opens_one(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform, sound=False)
        player.select(clip())

        assert platform.audio_calls == []
        # The pictures still play: sound is an addition, not a precondition.
        assert player._pipe is not None

    def test_stopping_the_clip_stops_the_sound(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform)
        player.select(clip())
        assert platform.wait_for_sound(player,)
        assert platform.audio_pipes[0].closed is False

        player.stop()
        assert platform.audio_pipes[0].closed is True

    def test_switching_clips_closes_the_previous_sound(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform)
        player.select(clip("魂斗罗"))
        assert platform.wait_for_sound(player,)
        first = platform.audio_pipes[0]

        player.select(clip("超级马力欧兄弟"))
        assert platform.wait_for_sound(player,2), "the second clip never got the card"
        player.stop()  # joins the background teardown
        assert first.closed is True
        assert len(platform.audio_pipes) == 2

    def test_a_platform_without_sound_stays_silent(self, tmp_path: Path) -> None:
        """No audio support anywhere: pictures play, nothing raises."""
        platform = SoundPlatform(tmp_path, sound=False)
        player = player_for(platform)
        player.select(clip())
        assert platform.audio_pipes == []


class TestSettings:
    def test_sound_and_volume_come_from_the_config(self) -> None:
        config = Config()
        config.video_volume = 35
        config.video_sound = False

        settings = VideoSettings.from_config(config)
        assert settings.volume == pytest.approx(0.35)
        assert settings.sound is False

    def test_the_default_is_on_but_moderate(self) -> None:
        settings = VideoSettings.from_config(Config())
        assert settings.sound is True
        assert 0 < settings.volume < 1

    def test_volume_out_of_range_is_rejected(self) -> None:
        from retrostation.core.config import ConfigError

        bad = Config(video_volume=400)
        with pytest.raises(ConfigError):
            bad.validate()


def session_for(rom_root: Path) -> Session:
    platform = FakePlatform(rom_root)
    config = Config()
    return Session(Library(platform, config), config, Translator(config.language))


class TestRetuning:
    """Changing the volume must not rebuild the pipe.

    A rebuild has to wait for the audio device, and that wait lands on the UI
    thread: the clip stutters each time the rocker moves.
    """

    def test_it_retunes_the_pipe_that_is_playing(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform, volume=0.7)
        player.select(clip())
        assert platform.wait_for_sound(player,)
        assert len(platform.audio_pipes) == 1

        player.configure(volume=0.3)
        assert len(platform.audio_pipes) == 1  # same pipe, not a new one
        assert platform.audio_pipes[0].volume == pytest.approx(0.3)
        assert platform.audio_pipes[0].closed is False

    def test_a_volume_set_before_playback_reaches_the_clip(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform, volume=0.7)
        player.configure(volume=0.2)  # nothing playing yet
        player.select(clip())
        assert platform.wait_for_sound(player,)

        assert platform.audio_calls[0][1] == pytest.approx(0.2)


class TestSoundHandover:
    """The card cannot be opened twice -- the next clip must wait for the last.

    Before this was serialised, whichever clip lost the race simply had no
    sound, and which one that was changed from switch to switch.  The handover
    now runs behind the pictures, so what is asserted is the invariant -- the
    card is never asked for while it is still taken -- not when it happens.
    """

    def test_the_outgoing_sound_is_gone_before_the_next_opens(self, tmp_path: Path) -> None:
        platform = SoundPlatform(tmp_path)
        player = player_for(platform)
        player.select(clip("魂斗罗"))
        assert platform.wait_for_sound(player,)
        first = platform.audio_pipes[0]

        player.select(clip("超级马力欧兄弟"))
        assert platform.wait_for_sound(player,2), "the next clip never got the card"

        assert first.closed is True
        assert len(platform.audio_pipes) == 2
        assert platform.opened_while_busy == [False, False]


class TestVolumeRocker:
    def test_up_raises_and_down_lowers(self, rom_root: Path) -> None:
        session = session_for(rom_root)
        start = session.config.video_volume

        session.handle(InputEvent(action=InputAction.VOLUME_UP))
        assert session.config.video_volume == start + 5

        session.handle(InputEvent(action=InputAction.VOLUME_DOWN))
        assert session.config.video_volume == start
        assert session.settings_dirty is True

    def test_it_stops_at_both_ends(self, rom_root: Path) -> None:
        session = session_for(rom_root)
        session.config.video_volume = 0
        session.handle(InputEvent(action=InputAction.VOLUME_DOWN))
        assert session.config.video_volume == 0

        session.config.video_volume = 100
        session.handle(InputEvent(action=InputAction.VOLUME_UP))
        assert session.config.video_volume == 100

    def test_the_number_reaches_the_player(self, rom_root: Path) -> None:
        """A rocker that changes nothing audible still has to answer."""
        session = session_for(rom_root)
        session.handle(InputEvent(action=InputAction.VOLUME_UP))
        assert str(session.config.video_volume) in session.toast_message

    def test_it_works_inside_the_settings_dialog_too(self, rom_root: Path) -> None:
        session = session_for(rom_root)
        session.modal = "menu"
        start = session.config.video_volume

        session.handle(InputEvent(action=InputAction.VOLUME_UP))
        assert session.config.video_volume == start + 5


class TestSettingsRows:
    def test_both_rows_are_offered(self, rom_root: Path) -> None:
        keys = [key for key, _label, _value in session_for(rom_root).menu_rows()]
        assert "video_sound" in keys
        assert "video_volume" in keys

    def test_the_sound_row_toggles(self, rom_root: Path) -> None:
        session = session_for(rom_root)
        before = session.config.video_sound
        session._adjust_menu("video_sound", 1)
        assert session.config.video_sound is not before

    def test_left_and_right_move_the_volume(self, rom_root: Path) -> None:
        session = session_for(rom_root)
        start = session.config.video_volume

        session._adjust_menu("video_volume", 1)
        assert session.config.video_volume == start + 5

        session._adjust_menu("video_volume", -1)
        assert session.config.video_volume == start
