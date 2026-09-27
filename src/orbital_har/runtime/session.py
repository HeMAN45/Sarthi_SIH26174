"""Live session orchestration - where the two halves are joined.

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
from orbital_har.runtime.experiments import compose
from orbital_har.runtime.store import Store
from orbital_har.runtime.telemetry import TelemetryWriter
from orbital_har.runtime.videoout import DEFAULT_RECORD_HEIGHT, Recorder, record_size
from orbital_har.runtime.voice import Voice

#: Upper bound on the capture loop. Supervision needs tens of hertz, not
#: hundreds, and the spare time belongs to inference.
LOOP_PERIOD_S = 1.0 / 30.0

#: Loop period while Ready. Nothing is captured or judged; the dashboard only
#: needs its state kept fresh, and that should cost nothing.
STANDBY_PERIOD_S = 0.1

_MODE_LOOKAHEAD = {"clean": 0, "strict": 1}

#: Session lifecycle (docs/03-APP-FLOW.md section 3). The camera is on in LIVE
#: and COMPLETE and off in READY -- ending a run releases the device.
PHASE_READY = "ready"
PHASE_LIVE = "live"
PHASE_COMPLETE = "complete"


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
    """(rack, pose) - what a procedure's predicates actually require.

    Turning these on unconditionally would cost a pose inference per frame on a
    procedure that only says "detect the bottle". Reading it off the predicates
    means PROC-A lights up the full pipeline and the stand-in demo stays cheap,
    with nobody having to remember a flag.
    """
    preds = [p for s in proc.steps for p in (*s.requires, *s.any_of)]
    kinds = {p.kind for p in preds}
    # Only millimetre predicates need the rack. ``dwell`` regions and a
    # ``moved`` measured in the picture are fractions of the image, so they
    # work on a plain webcam -- and must not report a rack they never needed.
    mm_moves = any(p.kind == "moved" and p.min_disp_mm is not None for p in preds)
    needs_rack = bool(proc.markers) or "near" in kinds or mm_moves
    needs_pose = bool(kinds & {"contact", "gesture"})
    return needs_rack, needs_pose


def voice_lines(proc: Procedure) -> list[str]:
    """Every line this procedure can speak, so all of it can be pre-rendered.

    The alert phrasings are duplicated from the engine on purpose: a
    pre-synthesized line only helps if it matches the string the engine will
    actually emit, character for character. The out-of-order alert names two
    steps and is combinatorial, so it is left to synthesize on demand - it is
    also the rarest.
    """
    lines: list[str] = []
    for step in proc.steps:
        lines.append(step.voice)
        lines.append(f"Step skipped: {step.name}")
        lines.append(f"Cannot verify: {step.name}. Please confirm.")
        lines.append(f"No progress on: {step.name}")
        lines.append(f"Step timed out and was skipped: {step.name}")
        for p in (*step.requires, *step.any_of):
            if getattr(p, "side", "any") in ("left", "right") and p.kind in ("gesture", "contact"):
                lines.append(f"Wrong hand: use your {p.side} hand. Now: {step.name}")
    return lines


def build_procedure(
    sequence: list[str], valid: set[str], name: str = "Custom experiment"
) -> Procedure:
    """Build a Procedure from an ordered list of detector class names.

    One step per selected object, presented in order. Any class the detector
    knows is allowed; richer steps -- body actions, own wording -- go through
    :func:`orbital_har.runtime.experiments.compose`, which this delegates to.
    """
    seq = [c for c in sequence if c in valid]
    if not seq:
        raise ValueError("sequence is empty or contains unknown classes")
    if len(seq) > 15:
        raise ValueError("keep the sequence to 15 steps or fewer")
    return compose([{"object": c} for c in seq], name, "custom")


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

    This is PS bullet 4 - the timestamped, structured, lightweight record of
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
        self.ended: float | None = None

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
    def elapsed_s(self) -> float:
        """Wall-clock run time; frozen at the moment the run was sealed."""
        return round((self.ended or time.time()) - self.started, 1)

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
            "elapsed_s": self.elapsed_s,
            # The chain head. Anyone holding it can later prove the log they are
            # shown is the log that was written.
            "head": self.telemetry.last_hash,
        }

    def close(self, engine: Engine) -> None:
        if self.closed:
            return
        self.closed = True
        self.ended = time.time()
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
                duration_ms=int((self.ended - self.started) * 1000),
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

    The lifecycle is Ready -> Live -> Complete (docs/03-APP-FLOW.md section 3).
    The camera belongs to a run: it is powered when a run starts and released
    when it ends, so an idle console is not a console silently watching the
    room. Between runs it can be powered on its own as a preview, which is what
    capturing training images needs.

    Control methods are queued rather than applied inline: the capture thread
    owns the engine, and swapping it from an HTTP worker is how you get a torn
    read.
    """

    def __init__(
        self,
        proc: Procedure,
        detector: Detector,
        camera: int | Camera = 0,
        min_area: float = 0.06,
        data_root: Path = Path("data"),
        rtsp_url: str | None = None,
        record: bool = True,
        record_height: int = DEFAULT_RECORD_HEIGHT,
        *,
        voice: Voice | None = None,
        mirror: bool = True,
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
        self.camera = camera if isinstance(camera, Camera) else Camera(camera)
        self.log: SessionLog | None = None

        self.wanted = proc.vocabulary_classes
        if detector.open_vocab:
            detector.set_classes(self.wanted)

        # Speech lives on the device, not in a browser tab.
        self.voice = voice if voice is not None else Voice(cache_dir=data_root / "voice")
        self.voice.start()
        if not self.voice.available:
            print(f"[voice] on-device voice unavailable: {self.voice.unavailable_reason}")

        self.mode = "clean"
        self.engine = make_engine(proc, self.mode)
        self._configure_for(proc)

        self.phase = PHASE_READY
        self.camera_on = False
        self._opening = False
        #: Selfie-style display. A webcam facing the operator reads backwards
        #: otherwise: move left and the picture moves right. Display only --
        #: perception and its coordinates keep the camera's true orientation.
        self.mirror = mirror

        self._lock = threading.Lock()
        self._standby = self._encode(placeholder("camera off"))
        self._jpeg = self._standby
        self._raw: np.ndarray | None = None
        self._state: dict[str, Any] = {}
        self._alert: dict[str, Any] | None = None
        self._alert_seq = 0
        self._alert_count = 0
        self._pending: list[tuple[Any, ...]] = []
        self._new_session = True
        self.running = False
        self._thread: threading.Thread | None = None
        self._publish(0.0)

    # ------------------------------------------------------------- lifecycle

    def start(self, *, autostart: bool = False) -> None:
        """Start the control loop. The camera stays off until a run begins,
        unless ``autostart`` asks for one straight away."""
        if autostart:
            self.start_run(self.mode)
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="LiveSession", daemon=True)
        self._thread.start()

    def finalize(self) -> None:
        """Close the in-flight run so its chain and session row are complete.

        Idempotent. Anything that ends supervision must call this, or the
        session stays 'running' in SQLite forever and the telemetry chain is
        never sealed - which is exactly what Ctrl+C used to do.
        """
        if self.log is not None and not self.log.closed:
            self.log.close(self.engine)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop supervision, release the camera, finalize the open run."""
        self.running = False
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self.camera.release()
        self.camera_on = False
        self.voice.close()
        self.finalize()

    def recover_orphans(self) -> int:
        """Retire sessions left 'running' by a previous hard kill."""
        orphans = [r["id"] for r in self.store.list_sessions(limit=500) if r["status"] == "running"]
        for sid in orphans:
            self.store.mark_crashed(sid)
        return len(orphans)

    # ---------------------------------------------------------------- control

    def start_run(self, mode: str = "clean") -> None:
        """Power the camera and begin a fresh supervised run.

        Starting while a run is live seals that run first, judged by its own
        engine -- a restart never erases what the previous run established.
        """
        self._pending.append(("start", mode if mode in _MODE_LOOKAHEAD else "clean"))

    def end_run(self) -> None:
        """Seal the run and release the camera. The system returns to Ready."""
        self._pending.append(("end",))

    def set_camera(self, on: bool) -> None:
        """Power the camera without a run: a preview, e.g. for training capture.

        Switching it off during a live run ends that run. Supervision that
        carries on with no camera is supervision in name only.
        """
        self._pending.append(("camera", bool(on)))

    def skip(self) -> None:
        self._pending.append(("skip",))

    def load_procedure(self, proc: Procedure, *, start: bool = False) -> None:
        """Hot-swap the procedure (docs/03-APP-FLOW.md section 9).

        A live run is sealed first; the new procedure never inherits its steps.
        """
        self._pending.append(("load", proc, start))

    def use_classifier(
        self, model_path: str, proc: Procedure | None = None, kind: str = "classify"
    ) -> None:
        """Deploy a model trained on this device and start a run with it.

        ``kind`` is "detect" (a detector, added beside the stock one) or
        "classify" (a whole-frame classifier, which replaces it). With ``proc``
        the run follows that procedure; without, every trained class becomes
        one step.
        """
        self._pending.append(("classifier", model_path, proc, kind))

    def add_trained(self, model_path: str, keep: set[str] | None = None) -> list[str]:
        """Put the operator's trained objects beside the stock ones, now.

        For start-up, before the loop runs: a restart must not drop the objects
        someone spent twenty minutes training. A model that will not load is
        reported and skipped, never fatal (invariant #10). Classes not in
        ``keep`` -- deleted in the Models tab since training -- stay hidden.
        """
        try:
            names = self.detector.use_detector(model_path)
        except Exception as exc:  # missing or corrupt weights
            print(f"[detector] could not load your trained objects ({model_path}): {exc}")
            return []
        if keep is not None:
            self.retire_trained(set(names) - keep)
            names = [n for n in names if n in keep]
        print(f"[detector] your trained objects: {', '.join(names)}")
        return names

    def retire_trained(self, names: set[str]) -> None:
        """Hide deleted trained classes from the object list and from detection."""
        if names:
            self.detector.retire(names)

    def set_mirror(self, on: bool) -> None:
        """Mirror the displayed picture. Takes effect on the next frame."""
        self.mirror = bool(on)

    def set_body(self, on: bool) -> None:
        """Body tracking on or off; a procedure whose steps need it keeps it on."""
        self._pending.append(("body", bool(on)))

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

    def missing_classes(self, proc: Procedure) -> list[str]:
        """Classes ``proc`` needs that the loaded detector cannot produce.

        An open-vocabulary model is told its classes, so it is never missing
        any. Anything else is a promise the procedure makes and this model
        cannot keep (docs/03-APP-FLOW.md section 9, step 4).
        """
        if self.detector.open_vocab:
            return []
        return sorted(proc.vocabulary_classes - set(self.detector.all_classes))

    # -------------------------------------------------------------- internals

    @staticmethod
    def _encode(frame: np.ndarray) -> bytes:
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else b""

    def _publish(self, fps: float) -> None:
        st = self._build_state(fps)
        with self._lock:
            self._state = st

    def _camera_status(self) -> dict[str, Any]:
        if not self.camera_on:
            state = "off"
        elif self._opening:
            state = "starting"
        elif self.camera.opened:
            state = "live"
        else:
            state = "unavailable"
        return {
            "on": self.camera_on,
            "state": state,
            "index": self.camera.index,
            # Never degrade silently (invariant #10): say why, not just that.
            "detail": self.camera.failure if state == "unavailable" else None,
            "mirror": self.mirror,
        }

    def _power(self, on: bool) -> None:
        """Open or release the camera device."""
        if not on:
            self.camera_on = False
            self.camera.release()
            with self._lock:
                self._jpeg = self._standby
                # A stale frame must not be captured as training data later.
                self._raw = None
            return
        self.camera_on = True
        if self.camera.opened:
            return
        # Opening can block for a second or two (DirectShow). Publish first so
        # the dashboard shows "starting" rather than a frozen screen.
        self._opening = True
        self._publish(0.0)
        try:
            self.camera.open()
        finally:
            self._opening = False

    def _seal(self) -> None:
        """Close the in-flight run, judged by the engine that ran it."""
        self.finalize()

    def _begin(self, mode: str) -> None:
        # Seal BEFORE replacing the engine. Sealing afterwards judged the old
        # run by the new, empty engine and archived every restart as 0 of N.
        self._seal()
        self.mode = mode
        self.voice.reset()
        self.engine = make_engine(self.proc, self.mode)
        self._reset_run()
        self._power(True)
        self.phase = PHASE_LIVE

    def _swap(self, proc: Procedure) -> None:
        self._seal()
        self.proc = proc
        self.wanted = proc.vocabulary_classes
        self.detector.set_classes(self.wanted)
        self.mode = "clean"
        self.engine = make_engine(proc, self.mode)
        self._configure_for(proc)
        self._reset_run()
        # The last run's debrief describes a procedure that is no longer loaded.
        self.log = None

    def _configure_for(self, proc: Procedure) -> None:
        want_rack, want_pose = perception_needs(proc)
        self.pipeline.configure(
            want_rack=want_rack,
            want_pose=want_pose,
            rack_dictionary=proc.procedure.rack_markers,
            regions=[(r.id, r.rect) for r in proc.regions],
            scene=proc.is_scene,
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
            kind = cmd[0]
            if kind == "skip":
                # Skipping is a verdict; there is nothing to skip between runs.
                if self.phase == PHASE_LIVE:
                    out += self.engine.skip(None, t, reason="crew skipped (object unavailable)")
            elif kind == "start":
                self._begin(cmd[1])
            elif kind == "end" or (kind == "camera" and not cmd[1]):
                self._seal()
                self._power(False)
                self.phase = PHASE_READY
            elif kind == "camera":
                self._power(True)
            elif kind == "load":
                self._swap(cmd[1])
                if cmd[2]:
                    self._begin("clean")
                else:
                    self._power(False)
                    self.phase = PHASE_READY
            elif kind == "body":
                self.pipeline.set_body(cmd[1])
            elif kind == "classifier":
                self._seal()
                if cmd[3] == "detect":
                    names = self.detector.use_detector(cmd[1])
                else:
                    names = self.detector.use_classifier(cmd[1])
                try:
                    self._swap(
                        cmd[2] or build_procedure(names, set(names), name="Trained-states demo")
                    )
                except ValueError as exc:
                    print(f"[session] trained model has no usable classes: {exc}")
                self._begin("clean")
        return out

    def _reset_run(self) -> None:
        self._alert = None
        self._alert_count = 0
        self._new_session = True

    def _start_session(self, size: tuple[int, int]) -> None:
        """Begin a new logged run. Every path here has already sealed the last."""
        self._seal()
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
        fid = 0
        seq = 0
        last_t = time.time()
        fps = 0.0

        while self.running:
            now = time.time()
            commanded = bool(self._pending)
            self._handle(self._apply_pending(now))
            if commanded:
                # A pressed button (or the alert it raised) should not wait
                # behind a whole frame of inference before the dashboard sees it.
                self._publish(round(fps, 1))

            if not self.camera_on:
                # Ready: no device held, nothing judged. Keep the dashboard's
                # state fresh and otherwise stay out of the way.
                fps, last_t = 0.0, now
                self._publish(0.0)
                time.sleep(STANDBY_PERIOD_S)
                continue

            fid += 1
            raw_frame, frame = self.camera.frame_or_placeholder()
            have_cam = raw_frame is not None
            live = self.phase == PHASE_LIVE

            if live and self._new_session and have_cam:
                self._start_session((frame.shape[1], frame.shape[0]))
                self._new_session = False

            verdicts: list[Event] = []
            if have_cam:
                obs = self.pipeline.observe(
                    frame, fid, wanted=self.wanted, min_area=self.min_area, mirror=self.mirror
                )
                frame = obs.frame
                # A preview perceives (the overlay is how you frame a shot) but
                # judges nothing: the engine is only fed during a run.
                if live:
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

            if live:
                # Record the annotated frame: the video is evidence, so it
                # should show what the system saw and concluded.
                if self.log is not None:
                    self.log.write_frame(frame)
                if self.engine.is_complete:
                    self._seal()
                    self.phase = PHASE_COMPLETE

            jpeg = self._encode(frame)
            st = self._build_state(round(fps, 1))
            with self._lock:
                self._jpeg = jpeg
                self._state = st
                if raw_frame is not None:
                    # Clean: perception draws on a copy, so this is exactly
                    # what the camera saw -- the right thing to train on.
                    self._raw = raw_frame

            # A camera paces us naturally, but a missing or stalled one does
            # not - and an uncapped loop redrawing a placeholder will happily
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
            "phase": self.phase,
            "camera": self._camera_status(),
            "procedure": e.procedure.procedure.name,
            "procedure_id": e.procedure.procedure.id,
            "mode": self.mode,
            "open_vocab": self.detector.open_vocab,
            "fps": fps,
            # Drawn on the confidence trace, so abstention is legible rather
            # than mysterious (docs/04-UIUX-BRIEF.md section 4).
            "thresholds": {"complete": e.config.tau_complete, "abstain": e.config.tau_abstain},
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
