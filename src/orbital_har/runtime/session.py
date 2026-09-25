"""Live session orchestration — where the two halves are joined.

``perception`` produces observations and knows nothing about procedures.
``reasoning`` consumes observations and knows nothing about cameras. Neither
may import the other. This module is the composition root that owns both, plus
the store, the telemetry chain, the recorder and the voice.

It is the only place in the system that is allowed to know about all of them.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from orbital_har.core.types import Event, EventType, StepState
from orbital_har.perception.capture import Camera, placeholder
from orbital_har.perception.detect import Detector
from orbital_har.perception.pipeline import PerceptionPipeline
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.store import Store
from orbital_har.runtime.telemetry import TelemetryWriter
from orbital_har.runtime.videoout import DEFAULT_RECORD_HEIGHT, Recorder, record_size
from orbital_har.runtime.voice import Voice

#: Upper bound on the capture loop. Supervision needs tens of hertz, not
#: hundreds, and the spare time belongs to inference.
LOOP_PERIOD_S = 1.0 / 30.0

_MODE_LOOKAHEAD = {"clean": 0, "strict": 1}


# --------------------------------------------------------------------------
# Procedure introspection
# --------------------------------------------------------------------------


def make_engine(proc: Procedure, mode: str = "clean") -> Engine:
    return Engine(
        proc,
        EngineConfig(
            tau_complete=0.60,
            tau_abstain=0.35,
            window_capacity=120,
            strict_preconditions=(mode == "strict"),
            completion_lookahead=_MODE_LOOKAHEAD.get(mode, 0),
        ),
    )


def perception_needs(proc: Procedure) -> tuple[bool, bool]:
    """(rack, pose) — what a procedure's predicates actually require.

    Turning these on unconditionally would cost a pose inference per frame on a
    procedure that only says "detect the bottle". Reading it off the predicates
    means PROC-A lights up the full pipeline and the stand-in demo stays cheap,
    with nobody having to remember a flag.
    """
    kinds = {p.kind for s in proc.steps for p in (*s.requires, *s.any_of)}
    needs_rack = bool(proc.markers) or bool(kinds & {"near", "moved", "dwell"})
    needs_pose = "contact" in kinds
    return needs_rack, needs_pose


def voice_lines(proc: Procedure) -> list[str]:
    """Every line this procedure can speak, so all of it can be pre-rendered.

    The alert phrasings are duplicated from the engine on purpose: a
    pre-synthesized line only helps if it matches the string the engine will
    actually emit, character for character. The out-of-order alert names two
    steps and is combinatorial, so it is left to synthesize on demand — it is
    also the rarest.
    """
    lines: list[str] = []
    for step in proc.steps:
        lines.append(step.voice)
        lines.append(f"Step skipped: {step.name}")
        lines.append(f"Cannot verify: {step.name}. Please confirm.")
        lines.append(f"No progress on: {step.name}")
        lines.append(f"Step timed out and was skipped: {step.name}")
    return lines


def build_procedure(
    sequence: list[str], valid: set[str], name: str = "Custom experiment"
) -> Procedure:
    """Build a Procedure from an ordered list of detector class names.

    One step per selected object, presented in order. Any class the detector
    knows is allowed. The trained BAS model would replace these generic classes
    with real experiment-object states.
    """
    seq = [c for c in sequence if c in valid]
    if not seq:
        raise ValueError("sequence is empty or contains unknown classes")
    if len(seq) > 15:
        raise ValueError("keep the sequence to 15 steps or fewer")

    distinct: list[str] = []
    for c in seq:
        if c not in distinct:
            distinct.append(c)
    objects = [{"id": c.replace(" ", "_"), "classes": [c]} for c in distinct]

    steps = []
    for i, c in enumerate(seq, start=1):
        steps.append(
            {
                "id": f"s{i}",
                "name": f"Present the {c}",
                "voice": f"Step {i}. Please present the {c}.",
                "preconditions": [f"s{i - 1}"] if i > 1 else [],
                "requires": [{"detect": c, "hold_frames": 5}],
                "timeout_s": 120,
                "on_timeout": "stall",
            }
        )

    return Procedure.model_validate(
        {
            "procedure": {
                "id": "custom",
                "name": name,
                "version": 1,
                "rack_markers": "DICT_4X4_50",
                "vocabulary": "coco-standin",
            },
            "markers": [],
            "regions": [],
            "objects": objects,
            "steps": steps,
        }
    )


def procedure_rows(proc: Procedure) -> list[dict[str, Any]]:
    return [
        {
            "step_id": s.id,
            "ordinal": i,
            "name": s.name,
            "voice_prompt": s.voice,
            "group_id": s.group,
            "preconditions": list(s.preconditions),
            "requires": [p.model_dump(mode="json") for p in s.requires],
            "any_of": [p.model_dump(mode="json") for p in s.any_of],
            "timeout_s": s.timeout_s,
            "on_timeout": s.on_timeout,
        }
        for i, s in enumerate(proc.steps)
    ]


# --------------------------------------------------------------------------
# One supervised run
# --------------------------------------------------------------------------


class SessionLog:
    """One supervised run: hash-chained telemetry + SQLite rows + video.

    This is PS bullet 4 — the timestamped, structured, lightweight record of
    what was actually done. It is append-only and tamper-evident; the chain can
    be re-verified at any time from the dashboard.
    """

    def __init__(
        self,
        root: Path,
        store: Store,
        proc: Procedure,
        mode: str,
        size: tuple[int, int],
        rtsp_url: str | None,
        record: bool,
        record_height: int = DEFAULT_RECORD_HEIGHT,
    ) -> None:
        self.id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.dir = root / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.proc = proc
        self.closed = False
        self.alerts = 0
        self.started = time.time()

        self.telemetry = TelemetryWriter(self.dir / "telemetry.jsonl").open()
        meta = proc.procedure
        rows = procedure_rows(proc)
        store.upsert_procedure(
            id=meta.id,
            name=meta.name,
            version=meta.version,
            vocabulary=meta.vocabulary,
            rack_markers=meta.rack_markers,
            source_path=f"<runtime:{meta.id}>",
            yaml_content=json.dumps(rows, sort_keys=True),
            step_count=len(proc.steps),
            steps=rows,
        )
        store.create_session(
            session_id=self.id,
            procedure_id=meta.id,
            procedure_version=meta.version,
            mode="live",
            session_dir=str(self.dir),
            steps_total=len(proc.steps),
        )
        self.telemetry.write(
            "session_start",
            {
                "session_id": self.id,
                "procedure": meta.id,
                "name": meta.name,
                "mode": mode,
                "steps_total": len(proc.steps),
            },
        )

        # An RTSP URL alone used to produce no stream at all, because the whole
        # Recorder was gated on --record. Either half is reason enough to build it.
        self.recorder = (
            Recorder(self.dir / "run.mp4", record_size(size, record_height), rtsp_url=rtsp_url)
            if (record or rtsp_url)
            else None
        )

    def on_verdict(self, ev: Event) -> None:
        if self.closed:
            return
        p = ev.payload
        iso = datetime.fromtimestamp(ev.t, UTC).isoformat()
        if ev.type == EventType.STEP_STATE.value:
            self.telemetry.write("step_state", p, t=iso)
            self.store.upsert_step_run(
                session_id=self.id,
                step_id=p["step_id"],
                ordinal=p["ordinal"],
                state=p["state"],
                confidence=p.get("confidence"),
                evidence=p.get("evidence"),
                reason=p.get("reason"),
            )
        elif ev.type == EventType.ALERT.value:
            self.alerts += 1
            self.telemetry.write("alert", p, t=iso)
            self.store.insert_alert(
                session_id=self.id,
                bus_seq=ev.seq,
                kind=p["kind"],
                severity=p["severity"],
                message=p["message"],
                step_id=p.get("step_id"),
                expected_step_id=p.get("expected_step_id"),
            )

    def write_frame(self, frame: np.ndarray) -> None:
        if self.recorder is not None and not self.closed:
            self.recorder.write(frame)

    @property
    def stats(self) -> dict[str, Any]:
        # Real recorded size, so the downlink ratio is measured rather than
        # estimated -- it is the headline number, so it should be honest.
        video_bytes = 0
        mp4 = self.dir / "run.mp4"
        try:
            if mp4.exists():
                video_bytes = mp4.stat().st_size
        except OSError:
            pass
        return {
            "id": self.id,
            "records": self.telemetry.record_count,
            "bytes": self.telemetry.bytes_written,
            "frames": self.recorder.frames if self.recorder else 0,
            "video_bytes": video_bytes,
            "rtsp": bool(self.recorder and self.recorder.rtsp_active),
        }

    def close(self, engine: Engine) -> None:
        if self.closed:
            return
        self.closed = True
        counts: dict[str, int] = {}
        for rt in engine.runtimes:
            counts[rt.state.value] = counts.get(rt.state.value, 0) + 1
        try:
            self.telemetry.write("session_end", {"counts": counts, "complete": engine.is_complete})
            chain = self.telemetry.chain_summary()
            self.telemetry.close()
            self.store.close_session(
                self.id,
                status="complete" if engine.is_complete else "aborted",
                steps_complete=counts.get("complete", 0),
                steps_skipped=counts.get("skipped", 0),
                steps_out_of_order=counts.get("out_of_order", 0),
                steps_unverified=counts.get("unverified", 0),
                steps_overridden=counts.get("overridden", 0),
                alert_count=self.alerts,
                duration_ms=int((time.time() - self.started) * 1000),
            )
            self.store.upsert_telemetry_chain(session_id=self.id, **chain)
        except Exception as exc:
            print(f"[session] close failed: {exc}")
        if self.recorder is not None:
            self.recorder.close()


# --------------------------------------------------------------------------
# Live session
# --------------------------------------------------------------------------


class LiveSession:
    """Camera -> perception -> engine -> voice, telemetry, video and UI state.

    Control methods are queued rather than applied inline: the capture thread
    owns the engine, and swapping it from an HTTP worker is how you get a torn
    read.
    """

    def __init__(
        self,
        proc: Procedure,
        detector: Detector,
        camera: int = 0,
        min_area: float = 0.06,
        data_root: Path = Path("data"),
        rtsp_url: str | None = None,
        record: bool = True,
        record_height: int = DEFAULT_RECORD_HEIGHT,
    ) -> None:
        self.data_root = data_root
        self.sessions_root = data_root / "sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.store = Store(data_root / "sarthi.db")
        self.rtsp_url = rtsp_url
        self.record = record
        self.record_height = record_height
        self.min_area = min_area

        self.proc = proc
        self.detector = detector
        self.pipeline = PerceptionPipeline(detector)
        self.camera = Camera(camera)
        self.log: SessionLog | None = None

        self.wanted = proc.vocabulary_classes
        if detector.open_vocab:
            detector.set_classes(self.wanted)

        # Speech lives on the device, not in a browser tab.
        self.voice = Voice(cache_dir=data_root / "voice")
        self.voice.start()
        if not self.voice.available:
            print(f"[voice] on-device voice unavailable: {self.voice.unavailable_reason}")

        self.mode = "clean"
        self.engine = make_engine(proc, self.mode)
        self._configure_for(proc)

        self._lock = threading.Lock()
        self._jpeg = self._encode(placeholder("starting camera ..."))
        self._raw: np.ndarray | None = None
        self._state: dict[str, Any] = {}
        self._alert: dict[str, Any] | None = None
        self._alert_seq = 0
        self._alert_count = 0
        self._pending: list[tuple[Any, ...]] = []
        self._new_session = True
        self.running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="LiveSession", daemon=True)
        self._thread.start()

    def finalize(self) -> None:
        """Close the in-flight run so its chain and session row are complete.

        Idempotent. Anything that ends supervision must call this, or the
        session stays 'running' in SQLite forever and the telemetry chain is
        never sealed — which is exactly what Ctrl+C used to do.
        """
        if self.log is not None and not self.log.closed:
            self.log.close(self.engine)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop supervision, release the camera, finalize the open run."""
        self.running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self.voice.close()
        self.finalize()

    def recover_orphans(self) -> int:
        """Retire sessions left 'running' by a previous hard kill."""
        orphans = [r["id"] for r in self.store.list_sessions(limit=500) if r["status"] == "running"]
        for sid in orphans:
            self.store.mark_crashed(sid)
        return len(orphans)

    # ---------------------------------------------------------------- control

    def skip(self) -> None:
        self._pending.append(("skip",))

    def end_run(self) -> None:
        """Finish the current run and arm a fresh one; camera keeps running."""
        self._pending.append(("end_run",))

    def restart(self, mode: str) -> None:
        self._pending.append(("restart", mode if mode in _MODE_LOOKAHEAD else "clean"))

    def load_procedure(self, proc: Procedure) -> None:
        self._pending.append(("load", proc))

    def use_classifier(self, model_path: str) -> None:
        self._pending.append(("classifier", model_path))

    # ------------------------------------------------------------------ reads

    def jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg

    def capture_raw(self) -> bytes | None:
        """A clean (un-annotated) JPEG of the current frame, for training data."""
        import cv2

        with self._lock:
            raw = None if self._raw is None else self._raw.copy()
        if raw is None:
            return None
        ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return buf.tobytes() if ok else None

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    @property
    def all_classes(self) -> list[str]:
        return self.detector.all_classes

    # -------------------------------------------------------------- internals

    @staticmethod
    def _encode(frame: np.ndarray) -> bytes:
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else b""

    def _configure_for(self, proc: Procedure) -> None:
        want_rack, want_pose = perception_needs(proc)
        self.pipeline.configure(
            want_rack=want_rack,
            want_pose=want_pose,
            rack_dictionary=proc.procedure.rack_markers,
        )
        # ~0.2 s a line, so this runs off the capture thread. Until it finishes
        # lines synthesize on demand; they are simply slower to arrive.
        if self.voice.available:
            self.voice.reset()
            threading.Thread(
                target=self.voice.prewarm,
                args=(voice_lines(proc),),
                name="VoicePrewarm",
                daemon=True,
            ).start()

    def _apply_pending(self, t: float) -> list[Event]:
        out: list[Event] = []
        while self._pending:
            cmd = self._pending.pop(0)
            if cmd[0] == "skip":
                out += self.engine.skip(None, t, reason="crew skipped (object unavailable)")
            elif cmd[0] == "end_run":
                self.finalize()
                self.voice.reset()
                self.engine = make_engine(self.proc, self.mode)
                self._reset_run()
            elif cmd[0] == "restart":
                self.mode = cmd[1]
                self.voice.reset()
                self.engine = make_engine(self.proc, self.mode)
                self._reset_run()
            elif cmd[0] == "load":
                self.proc = cmd[1]
                self.wanted = self.proc.vocabulary_classes
                self.detector.set_classes(self.wanted)
                self.mode = "clean"
                self.engine = make_engine(self.proc, self.mode)
                self._configure_for(self.proc)
                self._reset_run()
            elif cmd[0] == "classifier":
                names = self.detector.use_classifier(cmd[1])
                try:
                    self.proc = build_procedure(names, set(names), name="Trained-states demo")
                    self.wanted = self.proc.vocabulary_classes
                    self.mode = "clean"
                    self.engine = make_engine(self.proc, self.mode)
                    self._configure_for(self.proc)
                except ValueError:
                    pass
                self._reset_run()
        return out

    def _reset_run(self) -> None:
        self._alert = None
        self._alert_count = 0
        self._new_session = True

    def _start_session(self, size: tuple[int, int]) -> None:
        """Begin a new logged run. Closes any previous one first."""
        if self.log is not None and not self.log.closed:
            self.log.close(self.engine)
        try:
            self.log = SessionLog(
                self.sessions_root,
                self.store,
                self.proc,
                self.mode,
                size,
                self.rtsp_url,
                self.record,
                self.record_height,
            )
        except Exception as exc:
            print(f"[session] could not start: {exc}")
            self.log = None

    def _handle(self, events: list[Event]) -> None:
        for ev in events:
            if self.log is not None:
                self.log.on_verdict(ev)
            p = ev.payload
            if ev.type == EventType.STEP_STATE.value:
                # Announce a step exactly once, when it becomes the crew's job.
                if p.get("state") == StepState.ACTIVE.value and p.get("voice"):
                    self.voice.say(p["voice"], tag=f"step:{p['step_id']}")
            elif ev.type == EventType.ALERT.value:
                self._alert_seq += 1
                self._alert_count += 1
                self._alert = {
                    "kind": p["kind"],
                    "message": p["message"],
                    "severity": p["severity"],
                    "seq": self._alert_seq,
                }
                # Outranks the step prompt: an alert that waits its turn behind
                # a long instruction has already lost its value.
                self.voice.alert(p["message"], tag=f"alert:{self._alert_seq}")

    def _loop(self) -> None:
        self.camera.open()
        fid = 0
        seq = 0
        last_t = time.time()
        fps = 0.0

        while self.running:
            now = time.time()
            fid += 1

            self._handle(self._apply_pending(now))

            raw_frame, frame = self.camera.frame_or_placeholder()
            have_cam = raw_frame is not None

            if self._new_session and have_cam:
                self._start_session((frame.shape[1], frame.shape[0]))
                self._new_session = False

            verdicts: list[Event] = []
            if have_cam:
                obs = self.pipeline.observe(frame, fid, wanted=self.wanted, min_area=self.min_area)
                frame = obs.frame
                for src, type_, payload in obs.emissions:
                    seq += 1
                    verdicts += self.engine.on_event(
                        Event(t=now, seq=seq, src=src, type=type_, payload=payload)
                    )
            self._handle(verdicts)

            # Exponential moving average. A single inter-frame gap sampled twice
            # a second reports hundreds of FPS whenever the loop briefly spins.
            dt, last_t = now - last_t, now
            if dt > 1e-6:
                inst = 1.0 / dt
                fps = inst if fps <= 0.0 else fps * 0.9 + inst * 0.1

            # Record the annotated frame: the video is evidence, so it should
            # show what the system saw and concluded.
            if self.log is not None:
                self.log.write_frame(frame)
                if self.engine.is_complete and not self.log.closed:
                    self.log.close(self.engine)

            jpeg = self._encode(frame)
            st = self._build_state(round(fps, 1))
            with self._lock:
                self._jpeg = jpeg
                self._state = st
                if raw_frame is not None:
                    self._raw = raw_frame

            # A camera paces us naturally, but a missing or stalled one does
            # not — and an uncapped loop redrawing a placeholder will happily
            # burn a core at several hundred hertz.
            spare = LOOP_PERIOD_S - (time.time() - now)
            if spare > 0:
                time.sleep(spare)

        self.camera.release()

    def _build_state(self, fps: float) -> dict[str, Any]:
        e = self.engine
        steps = []
        counts: dict[str, int] = {}
        resolved_ts = []
        for rt in e.runtimes:
            counts[rt.state.value] = counts.get(rt.state.value, 0) + 1
            if rt.resolved_t is not None:
                resolved_ts.append(rt.resolved_t)
            steps.append(
                {
                    "id": rt.step.id,
                    "name": rt.step.name,
                    "voice": rt.step.voice,
                    "state": rt.state.value,
                    "confidence": round(rt.confidence, 2),
                    "evidence": list(rt.evidence),
                    "reason": rt.reason,
                    "ordinal": rt.ordinal,
                    "duration_s": rt.duration_s,
                }
            )
        nxt = e.next_step
        duration = 0.0
        if e.started_t is not None and resolved_ts:
            duration = round(max(resolved_ts) - e.started_t, 1)
        return {
            "procedure": e.procedure.procedure.name,
            "mode": self.mode,
            "open_vocab": self.detector.open_vocab,
            "fps": fps,
            # Degradation is never silent (invariant #10).
            "perception": self.pipeline.status(),
            "voice": self.voice.status(),
            "session": (self.log.stats | {"closed": self.log.closed}) if self.log else None,
            "steps": steps,
            "next": (
                {"id": nxt.step.id, "name": nxt.step.name, "voice": nxt.step.voice}
                if nxt is not None
                else None
            ),
            "alert": self._alert,
            "complete": e.is_complete,
            "summary": {
                "total": len(e.runtimes),
                "complete": counts.get("complete", 0),
                "skipped": counts.get("skipped", 0),
                "out_of_order": counts.get("out_of_order", 0),
                "unverified": counts.get("unverified", 0),
                "alerts": max(
                    self._alert_count,
                    counts.get("skipped", 0)
                    + counts.get("out_of_order", 0)
                    + counts.get("unverified", 0)
                    + counts.get("stalled", 0),
                ),
                "duration": duration,
            },
        }
