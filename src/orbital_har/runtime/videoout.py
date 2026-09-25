"""Video output — local recording and RTSP republishing.

Both halves of PS bullet 5. They degrade independently on purpose: losing the
RTSP sink must never stop the local recording, and losing video altogether must
never stop supervision (CLAUDE.md invariant #9). Every failure in here is
logged and swallowed.

Recording is on by default because the PS requires storing the video locally.
The storage budget is respected by downscaling and pacing, not by switching the
requirement off.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

#: Default recording height. Width follows the aspect ratio.
DEFAULT_RECORD_HEIGHT = 360
DEFAULT_RECORD_FPS = 12.0


def record_size(size: tuple[int, int], target_h: int = DEFAULT_RECORD_HEIGHT) -> tuple[int, int]:
    """Downscale to ``target_h``, keeping aspect. Dimensions stay even for x264.

    A full-resolution run is hundreds of megabytes and the storage budget is
    real (TRD section 1.1). 360p is plenty to show what the system saw, and
    telemetry, not video, is the deliverable.
    """
    w, h = size
    if target_h > 0 and h > target_h:
        w, h = round(w * target_h / h), target_h
    return (w - w % 2, h - h % 2)


class Recorder:
    """Stores the run locally as mp4 and optionally republishes it over RTSP."""

    def __init__(
        self,
        path: Path,
        size: tuple[int, int],
        fps: float = DEFAULT_RECORD_FPS,
        rtsp_url: str | None = None,
    ) -> None:
        self.path = path
        self.size = size
        self.fps = fps
        self.rtsp_url = rtsp_url
        self.frames = 0
        self._writer: cv2.VideoWriter | None = None
        self._proc: subprocess.Popen | None = None
        # The capture loop runs faster than we record. Pace writes to ``fps`` so
        # the mp4's declared rate matches reality and playback is real-time.
        self._interval = 1.0 / fps if fps > 0 else 0.0
        self._next_write = 0.0

        try:
            self._writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
            if not self._writer.isOpened():
                self._writer = None
        except Exception as exc:
            print(f"[rec] local recording disabled: {exc}")
            self._writer = None

        if rtsp_url:
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{size[0]}x{size[1]}",
                "-r",
                str(int(fps)),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-f",
                "rtsp",
                rtsp_url,
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[rec] RTSP publishing to {rtsp_url}")
            except FileNotFoundError:
                print("[rec] ffmpeg not found; RTSP disabled")
                self._proc = None

    @property
    def rtsp_active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def write(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if now < self._next_write:
            return
        self._next_write = max(now, self._next_write + self._interval)
        if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
            frame = cv2.resize(frame, self.size)
        if self._writer is not None:
            try:
                self._writer.write(frame)
                self.frames += 1
            except Exception:
                pass
        if self.rtsp_active:
            try:
                self._proc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            except Exception:
                self._proc = None  # sink died; recording continues

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
