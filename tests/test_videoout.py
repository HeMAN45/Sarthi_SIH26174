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
from orbital_har.runtime.videoout import Recorder, playable

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

    def kill(self) -> None:
        pass

    terminate = kill


def _record(rec: Recorder, frames: int = 3) -> None:
    for _ in range(frames):
        rec._next_write = 0.0  # no pacing in a test
        rec.write(FRAME)


def test_a_dead_stream_reports_why_and_recording_carries_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(videoout.subprocess, "Popen", DeadFfmpeg)
    rec = Recorder(
        tmp_path / "run.mp4", (64, 48), rtsp_url="rtsp://10.0.0.9:8554/live", local="opencv"
    )
    _record(rec)
    assert not rec.rtsp_active
    assert rec.rtsp_error is not None and "connection refused" in rec.rtsp_error.lower()
    assert rec.frames == 3
    rec.close()


def test_no_ffmpeg_is_a_reason_too(tmp_path, monkeypatch) -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(videoout.subprocess, "Popen", missing)
    rec = Recorder(
        tmp_path / "run.mp4", (64, 48), rtsp_url="rtsp://10.0.0.9:8554/live", local="opencv"
    )
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
    rec = Recorder(
        tmp_path / "run.mp4", (64, 48), rtsp_url="rtsp://10.0.0.9:8554/live", local="opencv"
    )
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
    rec = Recorder(tmp_path / "run.mp4", (64, 48), local="opencv")
    assert not rec.rtsp_active and rec.rtsp_error is None
    rec.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_to_a_closed_port_says_connection_refused(tmp_path) -> None:
    # Loopback only (invariant #1): a port nothing listens on refuses at once.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    rec = Recorder(
        tmp_path / "run.mp4", (64, 48), rtsp_url=f"rtsp://127.0.0.1:{port}/live", local="opencv"
    )
    end = time.time() + 15
    while time.time() < end and rec.rtsp_error is None:
        _record(rec, 1)
        rec.rtsp_active  # noqa: B018 - the property is what notices a dead stream
        time.sleep(0.1)
    assert rec.rtsp_error is not None
    assert "refused" in rec.rtsp_error.lower() or "failed" in rec.rtsp_error.lower()
    rec.close()


# ----------------------------------------------------------- crash-safe recording

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _frames_in(path) -> int:
    import cv2

    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def _numbered(i: int) -> np.ndarray:
    frame = np.full((48, 64, 3), (i * 7) % 255, dtype=np.uint8)
    frame[:, : (i % 64)] = 255
    return frame


@needs_ffmpeg
def test_a_recording_killed_mid_run_is_still_playable(tmp_path) -> None:
    # A plain mp4 is indexed when it closes, so a kill used to lose the whole
    # run. The fragmented recording loses at most the fragment being written.
    rec = Recorder(tmp_path / "run.mp4", (64, 48))
    assert rec.crash_safe and rec.record_issue is None
    # Live, ffmpeg starts with the run and frames arrive at 12 FPS behind a
    # four-second queue. Here they come faster, so give a cold ffmpeg (the
    # first start on Windows is scanned) its moment before feeding it.
    time.sleep(1.0)
    for i in range(96):  # eight seconds at 12 FPS, four fragments
        rec._next_write = 0.0
        rec.write(_numbered(i))
        time.sleep(0.02)
    assert rec.frames == 96  # none dropped
    time.sleep(1.5)  # let ffmpeg take what it was given
    rec._local._proc.kill()  # the process dies with the run unclosed
    rec._local._proc.wait(timeout=5)
    assert playable(tmp_path / "run.mp4")
    assert _frames_in(tmp_path / "run.mp4") >= 96 - round(videoout.RECORD_FRAGMENT_S * 12)


@needs_ffmpeg
def test_a_recording_closed_normally_keeps_every_frame(tmp_path) -> None:
    rec = Recorder(tmp_path / "run.mp4", (64, 48))
    for i in range(30):
        rec._next_write = 0.0
        rec.write(_numbered(i))
    rec.close()
    assert rec.frames == 30
    assert _frames_in(tmp_path / "run.mp4") == 30


def test_without_ffmpeg_the_recording_says_it_is_not_crash_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(videoout.shutil, "which", lambda name: None)
    rec = Recorder(tmp_path / "run.mp4", (64, 48))
    assert not rec.crash_safe
    assert rec.record_issue is not None and "not crash-safe" in rec.record_issue
    for i in range(3):
        rec._next_write = 0.0
        rec.write(_numbered(i))
    rec.close()
    assert rec.frames == 3 and playable(tmp_path / "run.mp4")
