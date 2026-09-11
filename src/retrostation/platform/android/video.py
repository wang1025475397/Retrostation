"""Externally-composited video preview (DESIGN.ANDROID §9.2).

ExoPlayer renders the clip into a surface the host places under the UI frame,
so decoded frames never reach Python.  The pipe therefore reports
``external = True``: ``read_frame()`` stays ``None`` for the whole clip and the
UI leaves the media box empty rather than drawing a cover over the video.
"""

from __future__ import annotations

from pathlib import Path

from ..base import VideoPipe


class SurfaceVideoPipe(VideoPipe):
    """A :class:`VideoPipe` whose pictures are composited by the host."""

    external = True

    def __init__(
        self,
        bridge: object,
        path: Path,
        rect: tuple[int, int, int, int],
        index: int,
    ) -> None:
        self._bridge = bridge
        self._closed = False
        #: The media box, in canvas units -- what we asked the host to fill.
        self.size = (rect[2], rect[3])
        self._bridge.open_video(path, index, rect)  # type: ignore[attr-defined]

    def read_frame(self) -> None:
        return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bridge.stop_video()  # type: ignore[attr-defined]
