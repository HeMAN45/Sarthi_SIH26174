"""Video output: the RTSP stream says why it is down, and recording carries on.

PS bullet 5 has two halves that must fail apart (invariant #9), and a failure
must be surfaced with its reason, never swallowed (invariant #10).
"""

from __future__ import annotations

import io
import shutil
import socket
import threading
import time

import numpy as np
import pytest

from orbital_har.runtime import videoout
from orbital_har.runtime.videoout import Recorder

FRAME = np.zeros((48, 64, 3), dtype=np.uint8)


class DeadFfmpeg:
    """An ffmpeg that could not reach its server and has already exited."""

    def __init__(self, *args, **kwargs) -> None:
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO(
            b"[tcp @ 0x1] Connection to tcp://10.0.0.9:8554?timeout=0 failed: Connection refused\n"
            b"Could not write header (incorrect codec parameters ?): Connection refused\n"
        )

    def poll(self) -> int:
        return 1

    def terminate(self) -> None:
        pass


def _record(rec: Recorder, frames: int = 3) -> None:
    for _ in range(frames):
        rec._next_write = 0.0  # no pacing in a test
        rec.write(FRAME)


def test_a_dead_stream_reports_why_and_recording_carries_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(videoout.subprocess, "Popen", DeadFfmpeg)
    rec = Recorder(tmp_path / "run.mp4", (64, 48), rtsp_url="rtsp://10.0.0.9:8554/live")
    _record(rec)
    assert not rec.rtsp_active
    assert rec.rtsp_error is not None and "connection refused" in rec.rtsp_error.lower()
    assert rec.frames == 3
    rec.close()


def test_no_ffmpeg_is_a_reason_too(tmp_path, monkeypatch) -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(videoout.subprocess, "Popen", missing)
    rec = Recorder(tmp_path / "run.mp4", (64, 48), rtsp_url="rtsp://10.0.0.9:8554/live")
    assert not rec.rtsp_active
    assert rec.rtsp_error == "ffmpeg not found on PATH"
    _record(rec)
    assert rec.frames == 3
    rec.close()


class StuckFfmpeg:
    """An ffmpeg that stopped reading its input: every write blocks until killed."""

    def __init__(self, *args, **kwargs) -> None:
        self._killed = threading.Event()
        self.stderr = io.BytesIO(b"")
        self.stdin = self

    def write(self, data: bytes) -> None:
        self._killed.wait()
        raise BrokenPipeError

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def poll(self) -> int | None:
        return 1 if self._killed.is_set() else None

    def kill(self) -> None:
        self._killed.set()

    terminate = kill


def test_a_stalled_stream_never_stalls_the_capture_loop(tmp_path, monkeypatch) -> None:
    # This used to be a blocking pipe write on the capture thread: the first
    # frame ffmpeg did not read froze supervision along with the video.
    monkeypatch.setattr(videoout.subprocess, "Popen", StuckFfmpeg)
    monkeypatch.setattr(videoout, "RTSP_STALL_S", 0.3)
    rec = Recorder(tmp_path / "run.mp4", (64, 48), rtsp_url="rtsp://10.0.0.9:8554/live")
    start = time.monotonic()
    deadline = start + 5
    while rec.rtsp_error is None and time.monotonic() < deadline:
        _record(rec, 1)
        time.sleep(0.02)
    assert time.monotonic() - start < 2  # every write returned at once
    assert rec.rtsp_error is not None and rec.rtsp_error.startswith("stalled")
    assert rec.rtsp_dropped > 0
    assert rec.frames > 0  # the recording kept every frame
    rec.close()


def test_no_stream_asked_for_is_no_error(tmp_path) -> None:
    rec = Recorder(tmp_path / "run.mp4", (64, 48))
    assert not rec.rtsp_active and rec.rtsp_error is None
    rec.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_to_a_closed_port_says_connection_refused(tmp_path) -> None:
    # Loopback only (invariant #1): a port nothing listens on refuses at once.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    rec = Recorder(tmp_path / "run.mp4", (64, 48), rtsp_url=f"rtsp://127.0.0.1:{port}/live")
    end = time.time() + 15
    while time.time() < end and rec.rtsp_error is None:
        _record(rec, 1)
        rec.rtsp_active  # noqa: B018 - the property is what notices a dead stream
        time.sleep(0.1)
    assert rec.rtsp_error is not None
    assert "refused" in rec.rtsp_error.lower() or "failed" in rec.rtsp_error.lower()
    rec.close()
