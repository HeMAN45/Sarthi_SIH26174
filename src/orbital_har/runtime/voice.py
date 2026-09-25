"""On-device voice output.

Until now the only voice in this system was the browser's ``speechSynthesis``,
which means **no open tab, no spoken alert**. For something sold as a
mission-critical on-board assistant, the safety-critical output cannot be a
side effect of somebody having a page open. Speech belongs to the device.

Three design points, all forced by measurement or by the TRD (section 8):

*Pre-synthesis.* Piper takes ~0.2 s to synthesize a sentence. That is far too
long to discover at the moment an alert fires, so every prompt a procedure can
utter is rendered to WAV when the procedure loads. At run time we only play a
file.

*Pre-emption.* An alert outranks a step prompt. "Step skipped" must not wait
politely behind "Using the tweezers, transfer the vial to the yellow box."

*Honest degradation.* If no voice model is installed, or no player exists on
this machine, the module reports the reason and the UI falls back to browser
speech. It never pretends to have spoken (invariant #10).

The voice model is a local file. Fetching one is a dev-time step, like
``uv sync`` -- nothing here reaches the network at run time.
"""

from __future__ import annotations

import hashlib
import platform
import queue
import subprocess
import threading
import wave
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Priorities. Higher pre-empts lower.
PROMPT = 1
ALERT = 2

DEFAULT_MODEL_DIR = Path("models/voices")


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------


class Player:
    """Plays a WAV file and can be interrupted.

    Backends in preference order: the platform's own (winsound / aplay /
    paplay), then ffplay, which we already depend on for the RTSP path.
    """

    def __init__(self) -> None:
        self.backend: str | None = None
        self.unavailable_reason: str | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._detect()

    def _detect(self) -> None:
        if platform.system() == "Windows":
            try:
                import winsound  # noqa: F401

                self.backend = "winsound"
                return
            except ImportError:
                pass
        for candidate in ("aplay", "paplay", "ffplay"):
            if _which(candidate):
                self.backend = candidate
                return
        self.unavailable_reason = "no audio player found (tried winsound, aplay, paplay, ffplay)"

    @property
    def available(self) -> bool:
        return self.backend is not None

    def play(self, path: Path) -> None:
        """Play to completion. Returns early if ``stop`` is called."""
        if self.backend is None:
            return
        if self.backend == "winsound":
            import winsound

            # SND_FILENAME is synchronous, which is what we want: the worker
            # thread should block until the utterance is done. stop() purges it.
            with suppress(RuntimeError):
                winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return

        cmd = {
            "aplay": ["aplay", "-q", str(path)],
            "paplay": ["paplay", str(path)],
            "ffplay": ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        }[self.backend]
        with self._lock:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc = self._proc
        try:
            proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def stop(self) -> None:
        """Cut playback short so a higher-priority utterance can take over."""
        if self.backend == "winsound":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except (ImportError, RuntimeError):
                pass
            return
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()


def _which(name: str) -> bool:
    from shutil import which

    return which(name) is not None


# --------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------


class PiperSynth:
    """Piper text-to-speech against a local .onnx voice.

    Chosen in TRD section 8: it runs offline, it is small enough for the edge
    target, and it exports the same way on DEV and on a Jetson.
    """

    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR) -> None:
        self.model_dir = Path(model_dir)
        self.model_path: Path | None = None
        self.unavailable_reason: str | None = None
        self._voice: Any = None
        self._lock = threading.Lock()
        self._resolve()

    def _resolve(self) -> None:
        if not self.model_dir.is_dir():
            self.unavailable_reason = f"no voice model directory at {self.model_dir}"
            return
        models = sorted(self.model_dir.glob("*.onnx"))
        if not models:
            self.unavailable_reason = f"no .onnx voice in {self.model_dir}"
            return
        self.model_path = models[0]

    @property
    def name(self) -> str:
        return self.model_path.stem if self.model_path else "none"

    def load(self) -> bool:
        if self._voice is not None:
            return True
        if self.model_path is None:
            return False
        try:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(self.model_path))
            self.unavailable_reason = None
            return True
        except Exception as exc:
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            self._voice = None
            return False

    @property
    def available(self) -> bool:
        return self.model_path is not None and self.unavailable_reason is None

    def synth(self, text: str, out: Path) -> bool:
        """Render ``text`` to a WAV at ``out``. False if synthesis is not possible."""
        if not self.load():
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        try:
            # One synthesis at a time: the ONNX session is not re-entrant.
            with self._lock, wave.open(str(tmp), "wb") as wav:
                self._voice.synthesize_wav(text, wav)
            tmp.replace(out)  # atomic, so a killed run leaves no half file
            return True
        except Exception as exc:
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            tmp.unlink(missing_ok=True)
            return False


# --------------------------------------------------------------------------
# Voice
# --------------------------------------------------------------------------


@dataclass(order=True)
class _Item:
    #: Negated so the queue pops the most urgent first.
    rank: int
    seq: int
    text: str = field(compare=False)
    tag: str = field(compare=False, default="")
    #: Shutdown sentinel. It is an _Item rather than None because a
    #: PriorityQueue orders whatever it is handed, and None cannot be compared
    #: against an _Item -- closing mid-utterance would raise instead of stop.
    stop: bool = field(compare=False, default=False)


#: Outranks every real utterance, so close() is never queued behind speech.
_STOP = _Item(rank=-99, seq=-1, text="", stop=True)


class Voice:
    """Pre-synthesized, pre-emptible speech for one procedure run."""

    def __init__(
        self,
        cache_dir: Path | str = Path("data/voice"),
        model_dir: Path | str = DEFAULT_MODEL_DIR,
        synth: PiperSynth | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.synth = synth or PiperSynth(model_dir)
        self.player = Player()
        self.spoken = 0
        self.prewarmed = 0

        #: Muting is the crew's call and must reach the device, not just the
        #: browser -- otherwise the mute button silences a voice that is not
        #: the one actually speaking.
        self.muted = False

        self._q: queue.PriorityQueue[_Item] = queue.PriorityQueue()
        self._seq = 0
        self._playing_rank = 0
        self._last_tag: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    # ----------------------------------------------------------- capability

    @property
    def available(self) -> bool:
        return self.synth.available and self.player.available

    @property
    def unavailable_reason(self) -> str | None:
        if not self.synth.available:
            return self.synth.unavailable_reason
        if not self.player.available:
            return self.player.unavailable_reason
        return None

    def status(self) -> dict[str, Any]:
        """What the UI needs to decide whether to speak in the browser instead."""
        return {
            "available": self.available,
            "muted": self.muted,
            "reason": self.unavailable_reason,
            "model": self.synth.name,
            "player": self.player.backend,
            "prewarmed": self.prewarmed,
            "spoken": self.spoken,
        }

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.player.stop()

    # --------------------------------------------------------------- cache

    def _path_for(self, text: str) -> Path:
        # Keyed on the voice too, so swapping models does not replay the old one.
        digest = hashlib.sha256(f"{self.synth.name}\x00{text}".encode()).hexdigest()[:20]
        return self.cache_dir / f"{digest}.wav"

    def prewarm(self, texts: list[str]) -> int:
        """Render every line a procedure can say. Returns how many are ready.

        Called at procedure load, off the per-frame path. Synthesis is ~0.2 s a
        line, so discovering that cost during an alert is not an option.
        """
        if not self.synth.available:
            return 0
        ready = 0
        for text in dict.fromkeys(t.strip() for t in texts if t and t.strip()):
            path = self._path_for(text)
            if path.exists() or self.synth.synth(text, path):
                ready += 1
        self.prewarmed = ready
        return ready

    # -------------------------------------------------------------- speech

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, name="Voice", daemon=True)
        self._thread.start()

    def say(self, text: str, priority: int = PROMPT, tag: str = "") -> bool:
        """Queue an utterance. False when this build cannot speak.

        A repeated ``tag`` at the same priority is dropped: the engine re-states
        the current step on every frame, and a voice that repeats itself thirty
        times a second is worse than silence.
        """
        if self.muted or not self.available or not text.strip():
            return False
        with self._lock:
            if tag and tag == self._last_tag:
                return False
            self._last_tag = tag or None
            self._seq += 1
            item = _Item(rank=-priority, seq=self._seq, text=text.strip(), tag=tag)
            preempt = priority > self._playing_rank
        self._q.put(item)
        if preempt:
            self.player.stop()
        return True

    def alert(self, message: str, tag: str = "") -> bool:
        return self.say(message, priority=ALERT, tag=tag)

    def reset(self) -> None:
        """Forget what was last said, so a new run re-announces step one."""
        with self._lock:
            self._last_tag = None

    def _worker(self) -> None:
        while self._running:
            item = self._q.get()
            if item.stop:
                break
            path = self._path_for(item.text)
            if not path.exists() and not self.synth.synth(item.text, path):
                continue  # already recorded as unavailable
            with self._lock:
                self._playing_rank = -item.rank
            try:
                self.player.play(path)
                self.spoken += 1
            finally:
                with self._lock:
                    self._playing_rank = 0

    def close(self) -> None:
        self._running = False
        self.player.stop()
        self._q.put(_STOP)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
