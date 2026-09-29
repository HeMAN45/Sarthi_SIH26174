"""Video output - local recording and RTSP republishing.

Both halves of PS bullet 5. They degrade independently on purpose: losing the
RTSP sink must never stop the local recording, and losing video altogether must
never stop supervision (CLAUDE.md invariant #9). Every failure in here is
logged and swallowed.

Recording is on by default because the PS requires storing the video locally.
The storage budget is respected by downscaling and pacing, not by switching the
requirement off.
"""

from __future__ import annotations

import contextlib
import queue
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

#: Default recording height. Width follows the aspect ratio.
DEFAULT_RECORD_HEIGHT = 360
DEFAULT_RECORD_FPS = 12.0

#: How long ffmpeg may try to reach the RTSP server. Without it, a server that
#: is not there makes ffmpeg wait forever (measured: still waiting after 10 s).
RTSP_CONNECT_TIMEOUT_US = 5_000_000
#: Frames buffered for ffmpeg. Past this they are dropped from the stream -- never
#: from the recording, and never by blocking the capture loop.
RTSP_QUEUE = 4
#: A stream that has taken no frame for this long is stalled and shut down.
RTSP_STALL_S = 8.0

#: The ffmpeg line worth showing, of the several it prints on the way down.
_CAUSE = re.compile(r"refused|timed out|failed|unreachable|not found|denied|invalid", re.I)
#: ffmpeg's Windows build names some errors only by number.
_PLAIN = {
    "Error number -138 occurred": "timed out; is an RTSP server listening there?",
    "Connection refused": "connection refused; is an RTSP server listening there?",
}


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


#: What a run killed mid-recording leaves behind (see ``playable``).
VIDEO_LOST = "video lost: the process was killed before the recording was closed"


def playable(path: Path) -> bool:
    """Whether a recording can be played back.

    An mp4 is only indexed when it is closed. A process killed mid-run (the
    terminal closed, the machine lost power) leaves the frames on disk with no
    index, and no player can open them.
    """
    if not path.exists():
        return False
    cap = cv2.VideoCapture(str(path))
    try:
        return cap.isOpened() and cap.read()[0]
    finally:
        cap.release()


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
        #: Why the RTSP stream is not running, when one was asked for. The HUD
        #: shows it: a stream that stops without saying why is a silent
        #: degradation (invariant #10).
        self.rtsp_error: str | None = None
        self._stderr: deque[str] = deque(maxlen=8)
        self._drain: threading.Thread | None = None
        #: Frames waiting for ffmpeg, and the thread that feeds them to it.
        self._outbox: queue.Queue[bytes | None] = queue.Queue(maxsize=RTSP_QUEUE)
        self._sender: threading.Thread | None = None
        self._last_sent = time.monotonic()
        #: Frames the stream could not keep up with. The recording has them all.
        self.rtsp_dropped = 0
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
                "-timeout",
                str(RTSP_CONNECT_TIMEOUT_US),
                "-f",
                "rtsp",
                rtsp_url,
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._drain = threading.Thread(
                    target=self._read_stderr, args=(self._proc.stderr,), daemon=True
                )
                self._drain.start()
                self._sender = threading.Thread(target=self._send, args=(self._proc,), daemon=True)
                self._sender.start()
                print(f"[rec] RTSP publishing to {rtsp_url}")
            except FileNotFoundError:
                self.rtsp_error = "ffmpeg not found on PATH"
                print(f"[rec] RTSP disabled: {self.rtsp_error}")
                self._proc = None

    def _read_stderr(self, stream) -> None:
        """Keep ffmpeg's last words; it prints why it stopped just before it does."""
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr.append(line)
        except (OSError, ValueError):
            pass

    def _send(self, proc: subprocess.Popen) -> None:
        """Feed ffmpeg off the capture thread.

        A write to a pipe ffmpeg has stopped reading blocks until it does. On
        the capture thread that froze supervision whenever the stream stalled
        (invariant #9); here it only stalls the stream.
        """
        while True:
            data = self._outbox.get()
            if data is None:
                return
            try:
                proc.stdin.write(data)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
                self._last_sent = time.monotonic()
            except (OSError, ValueError):
                return  # the sink died; rtsp_active notices and says why

    def _sink_died(self, reason: str | None = None) -> None:
        """Record why the stream stopped, once. Local recording carries on."""
        proc, self._proc = self._proc, None
        if proc is None or self.rtsp_error is not None:
            return
        if reason is None:
            if self._drain is not None:
                self._drain.join(timeout=0.5)  # let it read the last lines
            lines = [re.sub(r"^\[[^\]]*\]\s*|\?timeout=\d+", "", ln) for ln in self._stderr]
            cause = next((ln for ln in lines if _CAUSE.search(ln)), lines[-1] if lines else None)
            reason = cause or f"ffmpeg exited with code {proc.poll()}"
            for code, plain in _PLAIN.items():
                reason = reason.replace(code, plain)
        else:
            proc.kill()
        self.rtsp_error = reason[:160]
        # Let the sender finish. If the queue is full it is stuck in a write,
        # which the dead process will fail.
        with contextlib.suppress(queue.Full):
            self._outbox.put_nowait(None)
        print(f"[rec] RTSP stopped: {self.rtsp_error}")

    @property
    def rtsp_active(self) -> bool:
        if self._proc is not None and self._proc.poll() is not None:
            self._sink_died()
        return self._proc is not None

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
                self._outbox.put_nowait(frame.tobytes())
            except queue.Full:
                self.rtsp_dropped += 1
                if time.monotonic() - self._last_sent > RTSP_STALL_S:
                    self._sink_died(f"stalled: no frame taken for {RTSP_STALL_S:.0f} s")

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        if self._proc is not None:
            try:
                self._proc.terminate()  # first: a sender blocked in write then fails out
                if self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass
            self._proc = None
        if self._sender is not None:
            with contextlib.suppress(queue.Full):
                self._outbox.put_nowait(None)
            self._sender.join(timeout=1.0)
