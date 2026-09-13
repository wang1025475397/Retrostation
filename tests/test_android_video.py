"""Video on Android is composited by the host, not decoded in Python (§9.2).

ExoPlayer draws into its own surface under the UI frame, so the pipe reports
``external = True``: ``read_frame()`` stays ``None`` for the whole clip and the UI
leaves the media box empty instead of painting a cover over the video.  What the
pipe *does* own is the handshake -- open once, retune the volume, stop once -- and
that is what these pin, against a bridge that records the calls.
"""

from __future__ import annotations

from pathlib import Path

from retrostation.platform.android.video import SurfaceVideoPipe


class FakeBridge:
    """Records what the pipe asks the host to do."""

    def __init__(self) -> None:
        self.opened: list[tuple[Path, int, tuple[int, int, int, int]]] = []
        self.volumes: list[float] = []
        self.stops = 0

    def open_video(self, path: Path, index: int,
                   rect: tuple[int, int, int, int]) -> None:
        self.opened.append((path, index, rect))

    def set_video_volume(self, value: float) -> None:
        self.volumes.append(value)

    def stop_video(self) -> None:
        self.stops += 1


def test_it_is_external_and_hands_the_media_box_to_the_host() -> None:
    bridge = FakeBridge()
    rect = (10, 20, 320, 240)
    pipe = SurfaceVideoPipe(bridge, Path("/Roms/x/clip.mp4"), rect, 1)

    assert pipe.external is True
    assert bridge.opened == [(Path("/Roms/x/clip.mp4"), 1, rect)]
    assert pipe.size == (320, 240)      # the box, in canvas units


def test_it_never_hands_python_a_frame() -> None:
    # Pixels arriving here would mean the host is not compositing them; None is
    # what tells the UI to keep the media box empty for the whole clip.
    pipe = SurfaceVideoPipe(FakeBridge(), Path("clip.mp4"), (0, 0, 8, 8), 0)
    assert pipe.read_frame() is None


def test_the_volume_goes_straight_to_the_host() -> None:
    bridge = FakeBridge()
    pipe = SurfaceVideoPipe(bridge, Path("clip.mp4"), (0, 0, 8, 8), 0)
    pipe.set_volume(0.25)
    assert bridge.volumes == [0.25]


def test_closing_stops_the_host_once() -> None:
    # The UI reconfigures the decoder on every settings change; a second stop
    # must not reach the host again.
    bridge = FakeBridge()
    pipe = SurfaceVideoPipe(bridge, Path("clip.mp4"), (0, 0, 8, 8), 0)
    pipe.close()
    pipe.close()
    assert bridge.stops == 1
