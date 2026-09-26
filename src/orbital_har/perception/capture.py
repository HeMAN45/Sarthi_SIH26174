"""Camera capture.

Deliberately thin. The only judgement here is that losing the camera is a
degradation to be *shown*, not an exception to crash on: the engine keeps its
state, the dashboard keeps rendering, and the placeholder frame says out loud
what is wrong (CLAUDE.md invariant #10).

Nothing here may import from ``orbital_har.reasoning``.
"""

from __future__ import annotations

import cv2
import numpy as np


def placeholder(text: str, size: tuple[int, int] = (640, 480)) -> np.ndarray:
    """A frame that explains itself. Used whenever there is nothing to show."""
    w, h = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (30, 25, 20)
    cv2.putText(img, text, (40, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
    return img


class Camera:
    """One video source, with an honest ``opened`` flag.

    On Windows the DirectShow backend opens far faster than the default, so it
    is tried first; everywhere else the fallback is the normal path.
    """

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self._cap: cv2.VideoCapture | None = None
        self.opened = False
        self.failure: str | None = None

    def open(self) -> bool:
        self.release()  # a retry must not leak the handle of the failed attempt
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.index)
        self._cap = cap
        self.opened = cap.isOpened()
        # A reopen that works clears the old reason; a stale one would report
        # a fault that no longer exists.
        self.failure = None if self.opened else f"camera {self.index} unavailable"
        return self.opened

    def read(self) -> np.ndarray | None:
        """Next frame, or None once the camera has stopped delivering.

        A failed read marks the camera closed rather than retrying forever: a
        camera that has gone away mid-run is a degradation the operator needs
        to see, not something to hide behind a retry loop.
        """
        if self._cap is None or not self.opened:
            return None
        ok, frame = self._cap.read()
        if not ok:
            self.opened = False
            self.failure = "camera read failed"
            return None
        return frame

    def frame_or_placeholder(self) -> tuple[np.ndarray | None, np.ndarray]:
        """``(raw, displayable)``. ``raw`` is None when there is no camera."""
        frame = self.read()
        if frame is None:
            return None, placeholder(self.failure or f"camera {self.index} unavailable")
        return frame, frame

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.opened = False
