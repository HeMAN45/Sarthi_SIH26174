"""On-device voice: caching, pre-emption, muting and honest degradation.

These run without a voice model and without an audio device. A fake synthesizer
stands in for Piper so the behaviour under test is the queueing and caching
policy, not ONNX. One test exercises real Piper and skips when no model is
installed.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import pytest

from orbital_har.runtime.voice import (
    ALERT,
    DEFAULT_MODEL_DIR,
    PROMPT,
    PiperSynth,
    Voice,
    _Item,
)


class FakeSynth:
    """Writes a marker file instead of audio. Never played."""

    def __init__(self, name: str = "fake-voice") -> None:
        self.name = name
        self.available = True
        self.unavailable_reason: str | None = None
        self.calls: list[str] = []

    def synth(self, text: str, out: Path) -> bool:
        self.calls.append(text)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"RIFF0000WAVE")
        return True


class FakePlayer:
    """Records what was played and how long each utterance took."""

    def __init__(self, duration: float = 0.0) -> None:
        self.backend = "fake"
        self.unavailable_reason = None
        self.available = True
        self.played: list[str] = []
        self.stops = 0
        self.duration = duration
        self._interrupt = threading.Event()

    def play(self, path: Path) -> None:
        self.played.append(path.name)
        if self.duration:
            self._interrupt.wait(self.duration)
            self._interrupt.clear()

    def stop(self) -> None:
        self.stops += 1
        self._interrupt.set()


@pytest.fixture
def voice(tmp_path: Path) -> Voice:
    v = Voice(cache_dir=tmp_path / "voice", synth=FakeSynth())  # type: ignore[arg-type]
    v.player = FakePlayer()  # type: ignore[assignment]
    return v


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------


def test_synth_reports_a_missing_model_directory(tmp_path: Path) -> None:
    synth = PiperSynth(tmp_path / "nope")
    assert not synth.available
    assert "no voice model directory" in (synth.unavailable_reason or "")


def test_synth_reports_an_empty_model_directory(tmp_path: Path) -> None:
    (tmp_path / "voices").mkdir()
    synth = PiperSynth(tmp_path / "voices")
    assert not synth.available
    assert "no .onnx voice" in (synth.unavailable_reason or "")


def test_voice_without_a_model_is_unavailable_and_says_why(tmp_path: Path) -> None:
    """Never pretend to have spoken (invariant #10)."""
    v = Voice(cache_dir=tmp_path / "c", model_dir=tmp_path / "nope")
    assert not v.available
    assert v.unavailable_reason
    assert v.status()["reason"] == v.unavailable_reason


def test_say_is_a_noop_when_unavailable(tmp_path: Path) -> None:
    v = Voice(cache_dir=tmp_path / "c", model_dir=tmp_path / "nope")
    assert v.say("Open the outer container.") is False
    assert v.prewarm(["anything"]) == 0


# --------------------------------------------------------------------------
# Pre-synthesis
# --------------------------------------------------------------------------


def test_prewarm_renders_each_unique_line_once(voice: Voice) -> None:
    n = voice.prewarm(["Open the box.", "Close the box.", "Open the box.", "  ", ""])
    assert n == 2
    assert sorted(voice.synth.calls) == ["Close the box.", "Open the box."]  # type: ignore[attr-defined]


def test_prewarm_reuses_the_cache_on_a_second_call(voice: Voice) -> None:
    voice.prewarm(["Open the box."])
    voice.synth.calls.clear()  # type: ignore[attr-defined]

    voice.prewarm(["Open the box."])
    assert voice.synth.calls == []  # type: ignore[attr-defined]


def test_cache_is_keyed_on_the_voice_as_well_as_the_text(tmp_path: Path) -> None:
    """Swapping voices must not replay the previous one's recordings."""
    a = Voice(cache_dir=tmp_path / "c", synth=FakeSynth("voice-a"))  # type: ignore[arg-type]
    b = Voice(cache_dir=tmp_path / "c", synth=FakeSynth("voice-b"))  # type: ignore[arg-type]
    assert a._path_for("Open the box.") != b._path_for("Open the box.")


def test_partial_files_are_not_left_behind_on_failure(tmp_path: Path) -> None:
    class Failing(FakeSynth):
        def synth(self, text: str, out: Path) -> bool:
            return False

    v = Voice(cache_dir=tmp_path / "c", synth=Failing())  # type: ignore[arg-type]
    assert v.prewarm(["boom"]) == 0
    assert list((tmp_path / "c").glob("*")) == []


# --------------------------------------------------------------------------
# Queueing
# --------------------------------------------------------------------------


def test_an_alert_outranks_a_prompt_in_the_queue() -> None:
    prompt = _Item(rank=-PROMPT, seq=1, text="step")
    alert = _Item(rank=-ALERT, seq=2, text="alert")
    assert alert < prompt, "the alert must pop first despite arriving second"


def test_a_repeated_tag_is_dropped(voice: Voice) -> None:
    """The engine re-states the active step every frame. Say it once."""
    assert voice.say("Open the box.", tag="step:s1") is True
    assert voice.say("Open the box.", tag="step:s1") is False
    assert voice.say("Open the box.", tag="step:s1") is False


def test_a_different_tag_speaks_again(voice: Voice) -> None:
    assert voice.say("Open the box.", tag="step:s1") is True
    assert voice.say("Close the box.", tag="step:s2") is True


def test_reset_lets_a_new_run_re_announce_step_one(voice: Voice) -> None:
    assert voice.say("Open the box.", tag="step:s1") is True
    assert voice.say("Open the box.", tag="step:s1") is False

    voice.reset()
    assert voice.say("Open the box.", tag="step:s1") is True


def test_muting_silences_new_speech_and_stops_playback(voice: Voice) -> None:
    voice.set_muted(True)
    assert voice.say("Open the box.", tag="a") is False
    assert voice.player.stops == 1  # type: ignore[attr-defined]

    voice.set_muted(False)
    assert voice.say("Open the box.", tag="b") is True


def test_status_exposes_what_the_ui_needs_to_decide(voice: Voice) -> None:
    status = voice.status()
    assert status["available"] is True
    assert status["muted"] is False
    assert status["model"] == "fake-voice"
    assert status["player"] == "fake"


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------


def test_queued_speech_reaches_the_player(voice: Voice) -> None:
    voice.start()
    try:
        voice.say("Open the box.", tag="s1")
        deadline = time.time() + 3.0
        while not voice.player.played and time.time() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.02)
        assert voice.player.played  # type: ignore[attr-defined]
        assert voice.spoken == 1
    finally:
        voice.close()


def test_an_alert_pre_empts_a_prompt_already_playing(tmp_path: Path) -> None:
    v = Voice(cache_dir=tmp_path / "voice", synth=FakeSynth())  # type: ignore[arg-type]
    v.player = FakePlayer(duration=5.0)  # type: ignore[assignment]
    v.start()
    try:
        v.say("A long instruction about tweezers.", tag="step:s5")
        deadline = time.time() + 3.0
        while not v.player.played and time.time() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.02)

        v.alert("Step skipped: Open the red box.", tag="alert:1")
        assert v.player.stops >= 1, "the alert must cut the prompt short"  # type: ignore[attr-defined]
    finally:
        v.close()


def test_close_is_idempotent(voice: Voice) -> None:
    voice.start()
    voice.close()
    voice.close()


# --------------------------------------------------------------------------
# Real Piper
# --------------------------------------------------------------------------


_HAS_MODEL = DEFAULT_MODEL_DIR.is_dir() and any(DEFAULT_MODEL_DIR.glob("*.onnx"))


@pytest.mark.skipif(not _HAS_MODEL, reason="no Piper voice model installed")
def test_piper_renders_a_playable_wav(tmp_path: Path) -> None:
    synth = PiperSynth(DEFAULT_MODEL_DIR)
    out = tmp_path / "line.wav"
    assert synth.synth("Open the outer container.", out)

    with wave.open(str(out), "rb") as wav:
        assert wav.getnframes() > 0
        assert wav.getsampwidth() == 2
        assert wav.getframerate() >= 16000


@pytest.mark.skipif(not _HAS_MODEL, reason="no Piper voice model installed")
def test_piper_synthesis_touches_no_network(tmp_path: Path, monkeypatch) -> None:
    """Invariant #1: the voice model is a local file, start to finish."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("outbound network call during synthesis")

    synth = PiperSynth(DEFAULT_MODEL_DIR)
    synth.load()  # model load is allowed to touch disk before we poison sockets

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert synth.synth("Close the red box and secure it to the rack.", tmp_path / "a.wav")
