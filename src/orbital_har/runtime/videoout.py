"""Video output - local recording and RTSP republishing.

Both halves of PS bullet 5. They degrade independently on purpose: losing the
RTSP sink must never stop the local recording, and losing video altogether must
never stop supervision (CLAUDE.md invariant #9). Every failure in here is
logged, swallowed and given a reason the HUD can show (invariant #10).

Recording is on by default because the PS requires storing the video locally.
The storage budget is respected by downscaling and pacing, not by switching the
requirement off.
"""

from __future__ import annotations

import contextlib
import queue
import re
import shutil
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
#: Frames buffered for ffmpeg. Past this they are dropped from the stream --
#: never by blocking the capture loop.
RTSP_QUEUE = 4
#: A stream that has taken no frame for this long is stalled and shut down.
RTSP_STALL_S = 8.0

#: The recording gets more slack than the stream: dropping from it loses video.
RECORD_QUEUE = 48
RECORD_STALL_S = 15.0
#: Seconds between keyframes, and so between the recording's fragments. A kill
#: loses at most the fragment being written.
RECORD_FRAGMENT_S = 2.0

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

    A plain mp4 is only indexed when it is closed, so a process killed mid-run
    (the terminal closed, the machine lost power) leaves frames no player can
    open. The fragmented recording ``Recorder`` makes with ffmpeg stays playable
    up to its last complete fragment; the OpenCV fallback does not.
    """
    if not path.exists():
        return False
    cap = cv2.VideoCapture(str(path))
    try:
        return cap.isOpened() and cap.read()[0]
    finally:
        cap.release()


class _FfmpegSink:
    """One ffmpeg process fed raw frames from its own thread.

    A write to a pipe ffmpeg has stopped reading blocks until it does. On the
    capture thread that froze supervision whenever a sink stalled (invariant
    #9); here it only stalls the sink, frames past its queue are dropped, and a
    sink that takes nothing for ``stall_s`` is stopped. ffmpeg prints why it
    stopped just before it does; the last lines are kept to say so.
    """

    def __init__(
        self,
        name: str,
        output_args: list[str],
        size: tuple[int, int],
        fps: float,
        queue_size: int,
        stall_s: float,
    ) -> None:
        self.name = name
        self.stall_s = stall_s
        #: Why the sink is not running; None while it is.
        self.error: str | None = None
        #: Frames it could not keep up with.
        self.dropped = 0
        self._stderr: deque[str] = deque(maxlen=8)
        self._outbox: queue.Queue[bytes | None] = queue.Queue(maxsize=queue_size)
        self._last_sent = time.monotonic()
        self._drain: threading.Thread | None = None
        self._sender: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
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
            # No lookahead and no frame threading: frames leave the encoder as
            # they arrive instead of piling up where a kill would lose them.
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            *output_args,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            self.error = "ffmpeg not found on PATH"
            return
        self._drain = threading.Thread(target=self._read_stderr, daemon=True)
        self._drain.start()
        self._sender = threading.Thread(target=self._send, daemon=True)
        self._sender.start()

    def _read_stderr(self) -> None:
        try:
            for raw in iter(self._proc.stderr.readline, b""):  # type: ignore[union-attr]
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr.append(line)
        except (OSError, ValueError, AttributeError):
            pass

    def _send(self) -> None:
        proc = self._proc
        while True:
            data = self._outbox.get()
            if data is None or proc is None:
                return
            try:
                proc.stdin.write(data)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
                self._last_sent = time.monotonic()
            except (OSError, ValueError):
                return  # the process died; ``alive`` notices and says why

    def _died(self, reason: str | None = None) -> None:
        """Record why the sink stopped, once."""
        proc, self._proc = self._proc, None
        if proc is None or self.error is not None:
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
        self.error = reason[:160]
        # Let the sender finish. If the queue is full it is stuck in a write,
        # which the dead process will fail.
        with contextlib.suppress(queue.Full):
            self._outbox.put_nowait(None)
        print(f"[rec] {self.name} stopped: {self.error}")

    @property
    def alive(self) -> bool:
        if self._proc is not None and self._proc.poll() is not None:
            self._died()
        return self._proc is not None

    def put(self, data: bytes) -> bool:
        """Queue a frame without ever blocking. False when it was not taken."""
        if not self.alive:
            return False
        try:
            self._outbox.put_nowait(data)
            return True
        except queue.Full:
            self.dropped += 1
            if time.monotonic() - self._last_sent > self.stall_s:
                self._died(f"stalled: no frame taken for {self.stall_s:.0f} s")
            return False

    def close(self, finish: bool, wait_s: float = 5.0) -> None:
        """Stop the sink. ``finish`` lets ffmpeg write what it has and close the
        file properly first; otherwise it is stopped at once."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if finish:
            with contextlib.suppress(queue.Full):
                self._outbox.put(None, timeout=wait_s)
            if self._sender is not None:
                self._sender.join(timeout=wait_s)
            with contextlib.suppress(OSError, ValueError):
                proc.stdin.close()  # type: ignore[union-attr]
            try:
                proc.wait(timeout=wait_s)
                return
            except subprocess.TimeoutExpired:
                pass
        with contextlib.suppress(Exception):
            proc.kill()  # first: a sender blocked in write then fails out
            proc.stdin.close()  # type: ignore[union-attr]
        with contextlib.suppress(queue.Full):
            self._outbox.put_nowait(None)
        if self._sender is not None:
            self._sender.join(timeout=1.0)


class Recorder:
    """Stores the run locally and optionally republishes it over RTSP.

    Locally, ffmpeg writes a fragmented H.264 mp4: playable as it grows, so a
    kill loses at most the last fragment (``RECORD_FRAGMENT_S``), and a browser
    can play it. Without ffmpeg an OpenCV mp4 is written instead, which a kill
    loses whole; the recorder says so in ``record_issue``.
    """

    def __init__(
        self,
        path: Path,
        size: tuple[int, int],
        fps: float = DEFAULT_RECORD_FPS,
        rtsp_url: str | None = None,
        local: str = "auto",
    ) -> None:
        self.path = path
        self.size = size
        self.fps = fps
        self.rtsp_url = rtsp_url
        self.frames = 0
        #: True when a kill can cost at most the last fragment of the video.
        self.crash_safe = False
        self._note: str | None = None
        self._local: _FfmpegSink | None = None
        self._writer: cv2.VideoWriter | None = None
        self._rtsp: _FfmpegSink | None = None
        # The capture loop runs faster than we record. Pace writes to ``fps`` so
        # the mp4's declared rate matches reality and playback is real-time.
        self._interval = 1.0 / fps if fps > 0 else 0.0
        self._next_write = 0.0

        if local == "auto":
            local = "ffmpeg" if shutil.which("ffmpeg") else "opencv"
            if local == "opencv":
                self._note = "not crash-safe: ffmpeg not found, a kill loses the video"
        if local == "ffmpeg":
            fragment = ["-g", str(max(1, round(RECORD_FRAGMENT_S * fps)))]
            movflags = ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
            self._local = _FfmpegSink(
                "recording",
                [*fragment, *movflags, "-flush_packets", "1", str(path)],
                size,
                fps,
                RECORD_QUEUE,
                RECORD_STALL_S,
            )
            if self._local.error is None:
                self.crash_safe = True
            else:
                self._note = f"not crash-safe: {self._local.error}"
                self._local = None
        if self._local is None:
            try:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self._writer = cv2.VideoWriter(str(path), fourcc, fps, size)
                if not self._writer.isOpened():
                    self._writer = None
            except Exception as exc:
                print(f"[rec] local recording disabled: {exc}")
                self._writer = None
        if self._note:
            print(f"[rec] recording {self._note}")

        if rtsp_url:
            self._rtsp = _FfmpegSink(
                "RTSP",
                ["-timeout", str(RTSP_CONNECT_TIMEOUT_US), "-f", "rtsp", rtsp_url],
                size,
                fps,
                RTSP_QUEUE,
                RTSP_STALL_S,
            )
            if self._rtsp.error is None:
                print(f"[rec] RTSP publishing to {rtsp_url}")
            else:
                print(f"[rec] RTSP disabled: {self._rtsp.error}")

    # ----------------------------------------------------------------- status

    @property
    def record_issue(self) -> str | None:
        """What is wrong with the local recording, if anything: a stopped
        recorder first, else a recording a kill would lose."""
        if self._local is not None and not self._local.alive:
            return f"recording stopped: {self._local.error}"
        return self._note

    @property
    def rtsp_active(self) -> bool:
        return self._rtsp is not None and self._rtsp.alive

    @property
    def rtsp_error(self) -> str | None:
        """Why the RTSP stream is not running, when one was asked for."""
        if self._rtsp is None:
            return None
        self._rtsp.alive  # noqa: B018 - noticing a dead stream sets its error
        return self._rtsp.error

    @property
    def rtsp_dropped(self) -> int:
        """Frames the stream could not keep up with. The recording has them all."""
        return self._rtsp.dropped if self._rtsp is not None else 0

    # ------------------------------------------------------------------ write

    def write(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if now < self._next_write:
            return
        self._next_write = max(now, self._next_write + self._interval)
        if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
            frame = cv2.resize(frame, self.size)
        data: bytes | None = None
        if self._local is not None:
            data = frame.tobytes()
            if self._local.put(data):
                self.frames += 1
        elif self._writer is not None:
            try:
                self._writer.write(frame)
                self.frames += 1
            except Exception:
                pass
        if self._rtsp is not None:
            self._rtsp.put(data if data is not None else frame.tobytes())

    def close(self) -> None:
        if self._local is not None:
            self._local.close(finish=True)
            self._local = None
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        if self._rtsp is not None:
            self._rtsp.close(finish=False)
