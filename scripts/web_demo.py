"""Locally-hosted web dashboard for ORBITAL-HAR (mentor demo).

A FastAPI server that runs the webcam -> YOLO -> reasoning-engine loop in a
background thread and serves:

  GET  /            the dashboard (single self-contained page, no CDN -> offline)
  GET  /video       annotated live camera feed as MJPEG
  WS   /ws          live procedure state / alerts (JSON, ~10 Hz)
  POST /api/skip    skip the current step in place
  POST /api/restart?mode=clean|strict   restart the run
  GET  /api/health  status

Detector is a pretrained COCO model used as a STAND-IN for the trained BAS model
(next milestone). The pipeline -- camera -> detection -> engine -> prompts /
alerts / telemetry -- is the real system. Voice is spoken by the browser.

Run:
    uv run python scripts/web_demo.py --procedure demo_live
then open http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orbital_har.core.types import (
    RESOLVED_STATES,
    AlertKind,
    Event,
    EventType,
    Severity,
    StepState,
)
from orbital_har.reasoning.engine import Engine, EngineConfig
from orbital_har.reasoning.schema import Procedure
from orbital_har.runtime.store import Store
from orbital_har.runtime.telemetry import TelemetryWriter, verify

_MODE_LOOKAHEAD = {"clean": 0, "strict": 1}

#: A classifier ALWAYS returns one of its classes (softmax sums to 1) -- it can
#: never say "nothing here". Without a negative class, a covered camera produces
#: a confident wrong class and completes steps. This class is that escape hatch:
#: images of the empty scene / covered lens / hands only. Predictions landing
#: here are ignored by the engine.
BACKGROUND_CLASS = "background"

#: Other names people reach for when they mean "nothing is being presented".
_BG_ALIASES = {"none", "nothing", "empty", "no_object", "noobject", "idle", "blank"}


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_background(name: str) -> bool:
    """True for the negative class, tolerating typos like 'backgraound'.

    Getting this wrong silently breaks the whole model (the class stops acting as
    the 'nothing here' escape hatch), so match generously -- no real object name
    lands within two edits of 'background'.
    """
    n = name.strip().lower().replace(" ", "_").replace("-", "_")
    return n in _BG_ALIASES or _edit_distance(n, BACKGROUND_CLASS) <= 2

#: Accept a classification only when it is confident AND clearly ahead of the
#: runner-up. Guards against the 50/50 garbage predictions seen on junk input.
CLS_MIN_CONF = 0.60
CLS_MIN_MARGIN = 0.15


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


def build_procedure(sequence: list[str], valid: set[str],
                    name: str = "Custom experiment") -> Procedure:
    """Build a Procedure from an ordered list of detector class names.

    One step per selected object, presented in order. Any class the detector
    knows (the 80 COCO classes) is allowed. The trained BAS model would replace
    these generic classes with real experiment-object states.
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
        steps.append({
            "id": f"s{i}",
            "name": f"Present the {c}",
            "voice": f"Step {i}. Please present the {c}.",
            "preconditions": [f"s{i - 1}"] if i > 1 else [],
            "requires": [{"detect": c, "hold_frames": 5}],
            "timeout_s": 120,
            "on_timeout": "stall",
        })

    raw = {
        "procedure": {"id": "custom", "name": name, "version": 1,
                      "rack_markers": "DICT_4X4_50", "vocabulary": "coco-standin"},
        "markers": [], "regions": [], "objects": objects, "steps": steps,
    }
    return Procedure.model_validate(raw)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "class"


class TrainManager:
    """Manages a per-class image dataset and trains a YOLO image classifier.

    Classification (not detection) is what lets the user just upload images per
    class -- no bounding boxes. Ideal for object *states* (open vs closed) shown
    one at a time to the camera, which is exactly this demo's interaction.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.images = root / "images"
        self.images.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.model_path: str | None = None
        self.status: dict = {"state": "idle", "message": "", "epoch": 0, "epochs": 0}

    # -------------------------------------------------------------- dataset

    def list_classes(self) -> list[dict]:
        out = []
        for d in sorted(p for p in self.images.iterdir() if p.is_dir()):
            n = sum(1 for _ in d.glob("*.jpg"))
            out.append({"name": d.name, "count": n, "background": is_background(d.name)})
        return out

    def add_class(self, name: str) -> str:
        slug = _slug(name)
        (self.images / slug).mkdir(parents=True, exist_ok=True)
        return slug

    def add_video(self, name: str, blob: bytes, max_frames: int = 40) -> int:
        """Harvest evenly-spaced frames from a recorded clip into a class.

        Recording a short video of the object/state and sampling it is the
        cheapest way to get the variety (angles, lighting, motion blur) that a
        classifier needs -- far faster than snapping images one at a time.
        """
        tmp = self.root / f"_upload_{uuid.uuid4().hex[:8]}.mp4"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        saved = 0
        try:
            cap = cv2.VideoCapture(str(tmp))
            if not cap.isOpened():
                return 0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
            if total <= 0:                      # some containers do not report it
                frames = []
                while len(frames) < max_frames * 6:
                    ok, f = cap.read()
                    if not ok:
                        break
                    frames.append(f)
                step = max(1, len(frames) // max_frames)
                picked = frames[::step][:max_frames]
            else:
                step = max(1, total // max_frames)
                picked = []
                for i in range(0, total, step):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                    ok, f = cap.read()
                    if ok:
                        picked.append(f)
                    if len(picked) >= max_frames:
                        break
            cap.release()
            blobs = []
            for f in picked:
                ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    blobs.append(buf.tobytes())
            saved = self.add_images(name, blobs)
        finally:
            tmp.unlink(missing_ok=True)
        return saved

    def delete_class(self, name: str) -> None:
        d = self.images / _slug(name)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    def add_images(self, name: str, blobs: list[bytes]) -> int:
        d = self.images / self.add_class(name)
        existing = sum(1 for _ in d.glob("*.jpg"))
        saved = 0
        for blob in blobs:
            arr = np.frombuffer(blob, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            cv2.imwrite(str(d / f"{existing + saved:04d}.jpg"), img)
            saved += 1
        return saved

    # -------------------------------------------------------------- training

    def can_train(self) -> tuple[bool, str]:
        cls = [c for c in self.list_classes() if c["count"] > 0]
        if len(cls) < 2:
            return False, "need at least 2 classes, each with images"
        if any(c["count"] < 3 for c in cls):
            return False, "each class needs at least 3 images"
        if not any(c["background"] for c in cls):
            return False, (
                f"add a '{BACKGROUND_CLASS}' class with shots of the EMPTY scene "
                "(and a covered lens). Without it the model must guess a real "
                "class even when nothing is there."
            )
        return True, ""

    def train(self, epochs: int = 15) -> tuple[bool, str]:
        if self.status["state"] == "training":
            return False, "already training"
        ok, msg = self.can_train()
        if not ok:
            return False, msg
        threading.Thread(target=self._train, args=(epochs,), daemon=True).start()
        return True, "started"

    def _build_split(self) -> Path:
        ds = self.root / "dataset"
        if ds.exists():
            shutil.rmtree(ds, ignore_errors=True)
        for cls in self.list_classes():
            if cls["count"] == 0:
                continue
            imgs = sorted((self.images / cls["name"]).glob("*.jpg"))
            n_val = max(1, len(imgs) // 5)
            val = set(imgs[:n_val])
            for split in ("train", "val"):
                (ds / split / cls["name"]).mkdir(parents=True, exist_ok=True)
            for img in imgs:
                # every image goes to train; a fifth also seeds val
                shutil.copy(img, ds / "train" / cls["name"] / img.name)
                if img in val:
                    shutil.copy(img, ds / "val" / cls["name"] / img.name)
        return ds

    def _train(self, epochs: int) -> None:
        from ultralytics import YOLO

        with self._lock:
            self.status = {"state": "training", "message": "preparing data",
                           "epoch": 0, "epochs": epochs}
        try:
            ds = self._build_split()
            model = YOLO("yolo11n-cls.pt")

            def on_epoch(trainer) -> None:
                self.status["epoch"] = int(getattr(trainer, "epoch", 0)) + 1
                self.status["message"] = "training"

            model.add_callback("on_train_epoch_end", on_epoch)
            model.train(data=str(ds), epochs=epochs, imgsz=224, verbose=False,
                        plots=False, project=str(self.root / "runs"), name="cls", exist_ok=True)
            best = Path(model.trainer.best)  # authoritative path from Ultralytics
            if not best.exists():
                raise FileNotFoundError(f"trained weights not found at {best}")
            self.model_path = str(best)
            self.status = {"state": "done", "message": f"trained: {best.name}",
                           "epoch": epochs, "epochs": epochs, "model": str(best)}
        except Exception as exc:  # pragma: no cover - demo aid
            self.status = {"state": "error", "message": str(exc), "epoch": 0, "epochs": epochs}


class Recorder:
    """Stores the run locally as mp4 and optionally republishes it over RTSP.

    Both halves of PS bullet 5. Either half degrades independently: losing the
    RTSP sink must never stop the recording, and losing video must never stop
    supervision (invariant #9), so every failure here is logged and swallowed.
    """

    def __init__(self, path: Path, size: tuple[int, int], fps: float = 20.0,
                 rtsp_url: str | None = None) -> None:
        self.path = path
        self.size = size
        self.fps = fps
        self.rtsp_url = rtsp_url
        self.frames = 0
        self._writer: cv2.VideoWriter | None = None
        self._proc: subprocess.Popen | None = None

        try:
            self._writer = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
            if not self._writer.isOpened():
                self._writer = None
        except Exception as exc:
            print(f"[rec] local recording disabled: {exc}")
            self._writer = None

        if rtsp_url:
            cmd = [
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{size[0]}x{size[1]}", "-r", str(int(fps)), "-i", "-",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-f", "rtsp", rtsp_url,
            ]
            try:
                self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
                print(f"[rec] RTSP publishing to {rtsp_url}")
            except FileNotFoundError:
                print("[rec] ffmpeg not found; RTSP disabled")
                self._proc = None

    @property
    def rtsp_active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def write(self, frame: np.ndarray) -> None:
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


def _procedure_rows(proc: Procedure) -> list[dict]:
    rows = []
    for i, s in enumerate(proc.steps):
        rows.append({
            "step_id": s.id, "ordinal": i, "name": s.name, "voice_prompt": s.voice,
            "group_id": s.group,
            "preconditions": list(s.preconditions),
            "requires": [p.model_dump(mode="json") for p in s.requires],
            "any_of": [p.model_dump(mode="json") for p in s.any_of],
            "timeout_s": s.timeout_s, "on_timeout": s.on_timeout,
        })
    return rows


class SessionLog:
    """One supervised run: hash-chained telemetry + SQLite rows + video.

    This is PS bullet 4 -- the timestamped, structured, lightweight record of
    what was actually done. It is append-only and tamper-evident; the chain can
    be re-verified at any time from the dashboard.
    """

    def __init__(self, root: Path, store: Store, proc: Procedure, mode: str,
                 size: tuple[int, int], rtsp_url: str | None, record: bool) -> None:
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
        store.upsert_procedure(
            id=meta.id, name=meta.name, version=meta.version, vocabulary=meta.vocabulary,
            rack_markers=meta.rack_markers, source_path=f"<runtime:{meta.id}>",
            yaml_content=json.dumps(_procedure_rows(proc), sort_keys=True),
            step_count=len(proc.steps), steps=_procedure_rows(proc),
        )
        store.create_session(
            session_id=self.id, procedure_id=meta.id, procedure_version=meta.version,
            mode="live", session_dir=str(self.dir), steps_total=len(proc.steps),
        )
        self.telemetry.write("session_start", {
            "session_id": self.id, "procedure": meta.id, "name": meta.name,
            "mode": mode, "steps_total": len(proc.steps),
        })

        self.recorder = (
            Recorder(self.dir / "run.mp4", size, rtsp_url=rtsp_url) if record else None
        )

    def on_verdict(self, ev: Event) -> None:
        if self.closed:
            return
        p = ev.payload
        iso = datetime.fromtimestamp(ev.t, UTC).isoformat()
        if ev.type == EventType.STEP_STATE.value:
            self.telemetry.write("step_state", p, t=iso)
            self.store.upsert_step_run(
                session_id=self.id, step_id=p["step_id"], ordinal=p["ordinal"],
                state=p["state"], confidence=p.get("confidence"),
                evidence=p.get("evidence"), reason=p.get("reason"),
            )
        elif ev.type == EventType.ALERT.value:
            self.alerts += 1
            self.telemetry.write("alert", p, t=iso)
            self.store.insert_alert(
                session_id=self.id, bus_seq=ev.seq, kind=p["kind"],
                severity=p["severity"], message=p["message"],
                step_id=p.get("step_id"), expected_step_id=p.get("expected_step_id"),
            )

    def write_frame(self, frame: np.ndarray) -> None:
        if self.recorder is not None and not self.closed:
            self.recorder.write(frame)

    @property
    def stats(self) -> dict:
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
        summary = {"counts": counts, "complete": engine.is_complete}
        try:
            self.telemetry.write("session_end", summary)
            chain = self.telemetry.chain_summary()
            self.telemetry.close()
            self.store.close_session(
                self.id, status="complete" if engine.is_complete else "aborted",
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


def _placeholder(text: str) -> np.ndarray:
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    img[:] = (30, 25, 20)
    cv2.putText(img, text, (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
    return img


class DemoSession:
    """Runs the perception + engine loop and exposes the latest frame and state."""

    #: COCO 80 -- shown in the builder as quick suggestions even in open-vocab
    #: mode (where any typed object also works).
    COCO80 = sorted([
        "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
        "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
        "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
        "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
        "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
        "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
        "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
        "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
        "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
        "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
        "toothbrush",
    ])

    def __init__(self, proc: Procedure, model, camera: int, min_area: float,
                 open_vocab: bool = False, data_root: Path = Path("data"),
                 rtsp_url: str | None = None, record: bool = True) -> None:
        self.data_root = data_root
        self.sessions_root = data_root / "sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.store = Store(data_root / "sarthi.db")
        self.rtsp_url = rtsp_url
        self.record = record
        self.log: SessionLog | None = None
        self.proc = proc
        self.model = model
        self.open_vocab = open_vocab
        self.camera = camera
        self.min_area = min_area
        self.wanted = proc.vocabulary_classes
        if open_vocab:
            self._apply_classes(self.wanted)
        self.names = model.names
        self.all_classes = self.COCO80 if open_vocab else sorted(set(model.names.values()))

        self.classifier = None  # a YOLO cls model; when set, drives detection
        self.mode = "clean"
        self.engine = make_engine(proc, self.mode)
        self._lock = threading.Lock()
        self._jpeg = self._encode(_placeholder("starting camera ..."))
        self._raw: np.ndarray | None = None   # clean frame, for training capture
        self._state: dict = {}
        self._alert: dict | None = None
        self._alert_seq = 0
        self._alert_count = 0
        self._pending: list = []
        self._new_session = True   # a fresh run needs a fresh logged session
        self.running = False
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="DemoSession", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    # ------------------------------------------------------------------ control

    def skip(self) -> None:
        self._pending.append(("skip",))

    def restart(self, mode: str) -> None:
        self._pending.append(("restart", mode if mode in _MODE_LOOKAHEAD else "clean"))

    def load_procedure(self, proc: Procedure) -> None:
        self._pending.append(("load", proc))

    def use_classifier(self, model_path: str) -> None:
        self._pending.append(("classifier", model_path))

    def _apply_classes(self, classes) -> None:
        """Tell an open-vocabulary model which objects to look for."""
        cls = sorted(classes) or ["object"]
        self.model.set_classes(cls)
        self.names = self.model.names

    # ------------------------------------------------------------------- reads

    def jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg

    def capture_raw(self) -> bytes | None:
        """A clean (un-annotated) JPEG of the current frame, for training data."""
        with self._lock:
            raw = None if self._raw is None else self._raw.copy()
        if raw is None:
            return None
        ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return buf.tobytes() if ok else None

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    # -------------------------------------------------------------- internals

    @staticmethod
    def _encode(frame: np.ndarray) -> bytes:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else b""

    def _apply_pending(self, t: float) -> list[Event]:
        out: list[Event] = []
        while self._pending:
            cmd = self._pending.pop(0)
            if cmd[0] == "skip":
                out += self._skip_current(t)
            elif cmd[0] == "restart":
                self.mode = cmd[1]
                self.engine = make_engine(self.proc, self.mode)
                self._alert = None
                self._alert_count = 0
                self._new_session = True
            elif cmd[0] == "load":
                self.proc = cmd[1]
                self.wanted = self.proc.vocabulary_classes
                if self.open_vocab:
                    self._apply_classes(self.wanted)
                self.mode = "clean"
                self.engine = make_engine(self.proc, self.mode)
                self._alert = None
                self._alert_count = 0
                self._new_session = True
            elif cmd[0] == "classifier":
                from ultralytics import YOLO
                m = YOLO(cmd[1])
                self.classifier = m
                self.open_vocab = False
                # 'background' is the negative class -- never a procedure step.
                names = [m.names[i] for i in sorted(m.names) if not is_background(m.names[i])]
                self.all_classes = sorted(set(names))
                try:
                    self.proc = build_procedure(names, set(names), name="Trained-states demo")
                    self.wanted = self.proc.vocabulary_classes
                    self.mode = "clean"
                    self.engine = make_engine(self.proc, self.mode)
                except ValueError:
                    pass
                self._alert = None
                self._alert_count = 0
                self._new_session = True
        return out

    def _skip_current(self, t: float) -> list[Event]:
        e = self.engine
        rt = next((r for r in e.runtimes if r.state == StepState.ACTIVE), None) or e.next_step
        if rt is None or rt.state in RESOLVED_STATES:
            return []
        rt.state = StepState.SKIPPED
        rt.resolved_t = t
        rt.reason = "crew skipped (object unavailable)"
        out = [
            e._emit_state(rt, t),
            e._emit_alert(AlertKind.SKIP, Severity.HIGH, t,
                          step_id=rt.step.id, message=f"Step skipped: {rt.step.name}"),
        ]
        out += e._activate_ready(t)
        out += e._ensure_active(t)
        return out

    def _start_session(self, size: tuple[int, int]) -> None:
        """Begin a new logged run. Closes any previous one first."""
        if self.log is not None and not self.log.closed:
            self.log.close(self.engine)
        try:
            self.log = SessionLog(self.sessions_root, self.store, self.proc,
                                  self.mode, size, self.rtsp_url, self.record)
        except Exception as exc:
            print(f"[session] could not start: {exc}")
            self.log = None

    def _handle(self, evs: list[Event], t: float) -> None:
        for ev in evs:
            if self.log is not None:
                self.log.on_verdict(ev)
            if ev.type == EventType.ALERT.value:
                p = ev.payload
                self._alert_seq += 1
                self._alert_count += 1
                self._alert = {
                    "kind": p["kind"], "message": p["message"],
                    "severity": p["severity"], "seq": self._alert_seq,
                }

    def _loop(self) -> None:
        cap = cv2.VideoCapture(self.camera, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.camera)
        have_cam = cap.isOpened()
        fid = 0
        seq = 0
        fps_t = time.time()
        fps = 0.0

        while self.running:
            now = time.time()
            fid += 1

            self._handle(self._apply_pending(now), now)

            if have_cam:
                ok, frame = cap.read()
                if not ok:
                    frame = _placeholder("camera read failed")
                    have_cam = False
            else:
                frame = _placeholder(f"camera {self.camera} unavailable")

            raw_frame = frame.copy() if have_cam else None

            if self._new_session and have_cam:
                self._start_session((frame.shape[1], frame.shape[0]))
                self._new_session = False

            objects = []
            if have_cam and self.classifier is not None:
                # Classification mode: the whole presented object is classified
                # (e.g. open_book vs closed_book). Top class -> engine detection.
                r = self.classifier.predict(frame, verbose=False)[0]
                probs = r.probs.data.tolist()
                order = sorted(range(len(probs)), key=lambda i: -probs[i])
                cname = self.classifier.names[order[0]]
                conf = float(probs[order[0]])
                second = float(probs[order[1]]) if len(order) > 1 else 0.0
                margin = conf - second

                # Reject: the negative class, low confidence, or a close call.
                if is_background(cname):
                    why = "nothing presented"
                elif conf < CLS_MIN_CONF:
                    why = f"low confidence {conf:.2f}"
                elif margin < CLS_MIN_MARGIN:
                    why = f"ambiguous (margin {margin:.2f})"
                else:
                    why = ""

                col = (0, 220, 0) if not why else (0, 170, 255)
                cv2.rectangle(frame, (6, 6), (frame.shape[1] - 6, frame.shape[0] - 6), col, 3)
                cv2.putText(frame, f"{cname}  {conf:.2f}  (margin {margin:.2f})", (18, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
                if why:
                    cv2.putText(frame, f"ignored: {why}", (18, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 170, 255), 2)
                else:
                    objects.append({"cls": cname, "conf": round(conf, 3),
                                    "bbox": [0.0, 0.0, float(frame.shape[1]), float(frame.shape[0])],
                                    "track_id": None})
            elif have_cam:
                res = self.model.predict(frame, conf=0.35, verbose=False)[0]
                frame_area = float(frame.shape[0] * frame.shape[1])
                cands = []
                for box in res.boxes:
                    cls_name = self.names[int(box.cls[0])]
                    if cls_name not in self.wanted:
                        continue
                    conf = float(box.conf[0])
                    x0, y0, x1, y1 = (float(v) for v in box.xyxy[0])
                    af = max(0.0, x1 - x0) * max(0.0, y1 - y0) / frame_area
                    cands.append((af, cls_name, conf, (x0, y0, x1, y1)))
                cands.sort(reverse=True)
                presented = cands[0] if cands and cands[0][0] >= self.min_area else None
                for i, (af, cls_name, conf, (x0, y0, x1, y1)) in enumerate(cands):
                    is_p = presented is not None and i == 0
                    color = (0, 220, 0) if is_p else (110, 110, 110)
                    cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)),
                                  color, 3 if is_p else 1)
                    tag = f"{cls_name} {conf:.2f} {af*100:.0f}%"
                    if not is_p and i == 0:
                        tag += " (hold closer)"
                    cv2.putText(frame, tag, (int(x0), int(y0) - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                if presented is not None:
                    _, cls_name, conf, (x0, y0, x1, y1) = presented
                    objects.append({"cls": cls_name, "conf": round(conf, 3),
                                    "bbox": [x0, y0, x1, y1], "track_id": None})

            # Drive the engine (verdicts arrive on the frame call; collect both).
            seq += 1
            v = self.engine.on_event(Event(t=now, seq=seq, src="capture",
                                           type=EventType.FRAME.value,
                                           payload={"frame_id": fid, "w": frame.shape[1],
                                                    "h": frame.shape[0]}))
            seq += 1
            v += self.engine.on_event(Event(t=now, seq=seq, src="detect",
                                            type=EventType.DETECTION.value,
                                            payload={"frame_id": fid, "objects": objects}))
            self._handle(v, now)

            if now - fps_t > 0.5:
                fps = 1.0 / max(now - (getattr(self, "_last_t", now)), 1e-6)
                fps_t = now
            self._last_t = now

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

        cap.release()

    def _build_state(self, fps: float) -> dict:
        e = self.engine
        steps = []
        counts: dict[str, int] = {}
        resolved_ts = []
        for rt in e.runtimes:
            counts[rt.state.value] = counts.get(rt.state.value, 0) + 1
            if rt.resolved_t is not None:
                resolved_ts.append(rt.resolved_t)
            steps.append({
                "id": rt.step.id, "name": rt.step.name, "voice": rt.step.voice,
                "state": rt.state.value, "confidence": round(rt.confidence, 2),
            })
        nxt = e.next_step
        duration = 0.0
        if e.started_t is not None and resolved_ts:
            duration = round(max(resolved_ts) - e.started_t, 1)
        return {
            "procedure": e.procedure.procedure.name,
            "mode": self.mode,
            "open_vocab": self.open_vocab,
            "fps": fps,
            "session": (self.log.stats | {"closed": self.log.closed}) if self.log else None,
            "steps": steps,
            "next": ({"id": nxt.step.id, "name": nxt.step.name, "voice": nxt.step.voice}
                     if nxt is not None else None),
            "alert": self._alert,
            "complete": e.is_complete,
            "summary": {
                "total": len(e.runtimes),
                "complete": counts.get("complete", 0),
                "skipped": counts.get("skipped", 0),
                "out_of_order": counts.get("out_of_order", 0),
                "unverified": counts.get("unverified", 0),
                "alerts": max(self._alert_count,
                              counts.get("skipped", 0) + counts.get("out_of_order", 0)
                              + counts.get("unverified", 0) + counts.get("stalled", 0)),
                "duration": duration,
            },
        }


# ---------------------------------------------------------------------------
# Dashboard (single self-contained page -- no external CDN, stays offline)
# ---------------------------------------------------------------------------

_INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ORBITAL-HAR — live</title>
<style>
  :root{
    --bg:#0b0f14; --panel:#121821; --panel2:#0e141c; --line:#1e2a38;
    --txt:#e6edf3; --dim:#8aa0b4; --accent:#00d1ff; --ok:#22c55e; --warn:#f59e0b;
    --bad:#ef4444; --mag:#c084fc;
    --mono:'Consolas','SFMono-Regular',Menlo,monospace;
    --sans:'Segoe UI',system-ui,-apple-system,sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:var(--sans);}
  header{display:flex;align-items:center;gap:16px;padding:12px 20px;
    background:linear-gradient(90deg,#0d1b2a,#0b0f14);border-bottom:1px solid var(--line);}
  header h1{font-size:18px;margin:0;letter-spacing:3px;font-weight:700;}
  header .sub{color:var(--dim);font-size:12px;letter-spacing:1px;}
  .spacer{flex:1}
  .pill{font-family:var(--mono);font-size:12px;padding:4px 10px;border-radius:20px;
    border:1px solid var(--line);color:var(--dim);}
  .pill.live{color:var(--ok);border-color:#14532d;}
  .pill.off{color:var(--bad);border-color:#7f1d1d;}
  main{display:grid;grid-template-columns:1.4fr 1fr;gap:16px;padding:16px;max-width:1400px;margin:0 auto;}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;}
  .videowrap{position:relative;background:#000;}
  .videowrap img{width:100%;display:block;}
  .vtag{position:absolute;left:10px;top:10px;font-family:var(--mono);font-size:11px;
    background:rgba(0,0,0,.55);padding:4px 8px;border-radius:6px;color:var(--dim);}
  .banner{position:absolute;left:0;right:0;top:0;background:var(--bad);color:#fff;
    padding:12px 16px;font-weight:700;transform:translateY(-120%);transition:.25s;}
  .banner.show{transform:translateY(0);}
  .panel-pad{padding:16px;}
  .next{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px;}
  .next .lbl{color:var(--accent);font-size:11px;letter-spacing:2px;}
  .next .txt{font-size:18px;margin-top:6px;min-height:24px;}
  .steps{display:flex;flex-direction:column;gap:8px;}
  .step{display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid var(--line);
    border-radius:10px;background:var(--panel2);}
  .step .dot{width:10px;height:10px;border-radius:50%;background:#334;flex:none;}
  .step .nm{flex:1;font-size:14px;}
  .chip{font-family:var(--mono);font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line);color:var(--dim);text-transform:uppercase;}
  .conf{font-family:var(--mono);font-size:11px;color:var(--dim);}
  .s-pending{}
  .s-active .dot{background:var(--accent);box-shadow:0 0 10px var(--accent);}
  .s-active .chip{color:var(--accent);border-color:#0e7490;}
  .s-complete .dot{background:var(--ok);}
  .s-complete .chip{color:var(--ok);border-color:#14532d;}
  .s-skipped .dot{background:var(--bad);}
  .s-skipped .chip{color:var(--bad);border-color:#7f1d1d;}
  .s-out_of_order .dot{background:var(--bad);}
  .s-out_of_order .chip{color:var(--bad);border-color:#7f1d1d;}
  .s-unverified .dot{background:var(--warn);}
  .s-unverified .chip{color:var(--warn);border-color:#78350f;}
  .s-overridden .dot{background:var(--mag);}
  .controls{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;}
  button{font-family:var(--sans);font-size:13px;font-weight:600;color:var(--txt);
    background:#17212e;border:1px solid var(--line);border-radius:8px;padding:10px 14px;cursor:pointer;}
  button:hover{border-color:var(--accent);}
  button.skip{border-color:#7f1d1d;} button.skip:hover{background:#2a1212;}
  .meta{display:flex;gap:16px;color:var(--dim);font-family:var(--mono);font-size:12px;margin-top:14px;flex-wrap:wrap;}
  .summary{margin-top:16px;border:1px solid var(--ok);border-radius:10px;padding:14px;background:#0d1a12;display:none;}
  .summary.show{display:block;}
  .summary h3{margin:0 0 10px;color:var(--ok);letter-spacing:2px;}
  .srow{display:flex;justify-content:space-between;padding:4px 0;font-family:var(--mono);font-size:13px;border-bottom:1px dashed #1c2a1f;}
  .srow b{color:var(--txt);}
  .val-bad{color:var(--bad);} .val-ok{color:var(--ok);}
  .note{color:var(--dim);font-size:11px;margin-top:6px;}
  .hdrbtn{background:#0e7490;border:1px solid #0891b2;color:#fff;font-weight:700;
    border-radius:8px;padding:8px 14px;cursor:pointer;letter-spacing:.5px;}
  .hdrbtn:hover{background:#0891b2;}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;
    justify-content:center;z-index:50;padding:20px;}
  .modal.show{display:flex;}
  .modal-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
    width:min(920px,96vw);max-height:92vh;overflow:hidden;display:flex;flex-direction:column;}
  .modal-head{display:flex;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line);}
  .modal-head h2{margin:0;font-size:16px;letter-spacing:2px;flex:1;}
  .modal-head .x{background:none;border:none;color:var(--dim);font-size:18px;cursor:pointer;}
  .builder-grid{display:grid;grid-template-columns:1fr 1fr;gap:0;min-height:0;flex:1;}
  .bcol{padding:14px;display:flex;flex-direction:column;min-height:0;}
  .bcol:first-child{border-right:1px solid var(--line);}
  .bcol-h{color:var(--accent);font-size:11px;letter-spacing:2px;margin-bottom:10px;}
  .search{width:100%;background:var(--panel2);border:1px solid var(--line);border-radius:8px;
    color:var(--txt);padding:9px 12px;font-family:var(--sans);font-size:13px;margin-bottom:10px;}
  .classlist{display:flex;flex-wrap:wrap;gap:6px;overflow:auto;max-height:46vh;align-content:flex-start;}
  .cchip{background:#17212e;border:1px solid var(--line);color:var(--txt);border-radius:16px;
    padding:5px 11px;font-size:12px;cursor:pointer;}
  .cchip:hover{border-color:var(--accent);color:var(--accent);}
  .seqlist{display:flex;flex-direction:column;gap:6px;overflow:auto;max-height:40vh;margin-bottom:10px;}
  .seqrow{display:flex;align-items:center;gap:8px;background:var(--panel2);border:1px solid var(--line);
    border-radius:8px;padding:6px 8px;}
  .seqrow .num{font-family:var(--mono);color:var(--accent);width:20px;text-align:center;}
  .seqrow .nm{flex:1;font-size:13px;}
  .seqrow button{background:#17212e;border:1px solid var(--line);color:var(--dim);border-radius:6px;
    padding:3px 7px;font-size:11px;cursor:pointer;}
  .seqrow button.rm{border-color:#7f1d1d;color:#fca5a5;}
  .seqrow button.snap{border-color:#0891b2;color:#67e8f9;font-weight:700;}
  .seqrow button.snap:hover{background:#082f3a;}
  .start{background:var(--ok);border:none;color:#04150a;font-weight:800;border-radius:9px;
    padding:11px;cursor:pointer;font-size:14px;letter-spacing:.5px;}
  .start:hover{filter:brightness(1.1);}
  .berr{color:var(--bad);font-size:12px;min-height:16px;margin-bottom:6px;}
  .empty{color:var(--dim);font-size:12px;}
</style>
</head>
<body>
<header>
  <h1>ORBITAL&nbsp;·&nbsp;HAR</h1>
  <span class="sub">on-board procedure supervision · live</span>
  <div class="spacer"></div>
  <button class="hdrbtn" onclick="openTrainer()" style="background:#6d28d9;border-color:#7c3aed;">🧠 Train model</button>
  <button class="hdrbtn" onclick="openBuilder()">＋ Build experiment</button>
  <span class="pill" id="mode">mode —</span>
  <span class="pill" id="fps">— fps</span>
  <span class="pill off" id="conn">connecting</span>
</header>

<main>
  <section class="card videowrap">
    <div class="banner" id="banner"></div>
    <img src="/video" alt="live feed"/>
    <div class="vtag">detector: pretrained COCO stand-in · trained BAS model = next milestone</div>
  </section>

  <section class="card panel-pad">
    <div class="next">
      <div class="lbl">NEXT STEP</div>
      <div class="txt" id="next">—</div>
    </div>
    <div class="steps" id="steps"></div>

    <div class="controls">
      <button onclick="cmd('/api/restart?mode=clean')">↻ Restart</button>
      <button class="skip" onclick="cmd('/api/skip')">⤼ Skip current step</button>
      <button onclick="cmd('/api/restart?mode=strict')">⚠ Out-of-order run</button>
    </div>
    <div class="meta">
      <span id="proc">—</span>
      <span>present ONE object, held close to the camera</span>
    </div>

    <div class="summary" id="summary">
      <h3>RUN COMPLETE</h3>
      <div id="summaryBody"></div>
    </div>
  </section>
</main>

<div class="modal" id="trainer">
  <div class="modal-card">
    <div class="modal-head">
      <h2>TRAIN A MODEL · object states</h2>
      <button class="x" onclick="closeTrainer()">✕</button>
    </div>
    <div class="builder-grid">
      <div class="bcol">
        <div class="bcol-h">CLASSES · one per object or state (e.g. open_book, closed_book)</div>
        <div style="display:flex;gap:6px;margin-bottom:10px;">
          <input class="search" id="newClass" style="margin:0;flex:1;" placeholder="new class name…"
                 onkeydown="if(event.key==='Enter')addTrainClass()"/>
          <button class="hdrbtn" onclick="addTrainClass()">Add</button>
        </div>
        <div class="seqlist" id="trainClasses" style="max-height:48vh;"></div>
      </div>
      <div class="bcol">
        <div class="bcol-h">CAMERA · frame the object, then Snap</div>
        <img id="trainPreview" src="" alt="live preview"
             style="width:100%;border-radius:8px;border:1px solid var(--line);background:#000;
                    max-height:30vh;object-fit:contain;margin-bottom:12px;"/>
        <div class="bcol-h">TRAIN · needs ≥2 classes + a background class</div>
        <p class="note" style="margin-top:0;line-height:1.6;">
          <b style="color:#f59e0b;">Use 📷 ×10 — not file uploads.</b> Capturing from the live
          camera makes training images match what the model sees at run time. Aim for
          <b>20–30 per class</b>, moving/rotating the object between snaps.<br><br>
          <b style="color:#ef4444;">You must add a class named <code>background</code></b> and
          capture the empty scene + a covered lens. A classifier always picks one of its
          classes — without <code>background</code> it will confidently guess a real object
          when nothing is there (and complete steps by itself).
        </p>
        <button class="hdrbtn" style="width:100%;margin-bottom:8px;"
                onclick="document.getElementById('newClass').value='background';addTrainClass()">
          ＋ Add the background class</button>
        <div style="display:flex;gap:8px;align-items:center;margin:8px 0;">
          <span class="note">epochs</span>
          <input class="search" id="epochs" style="margin:0;width:80px;" value="15"/>
          <button class="start" style="flex:1;" onclick="startTrain()">⚙ Train model</button>
        </div>
        <div id="trainStatus" class="note" style="min-height:40px;font-family:var(--mono);"></div>
        <button class="hdrbtn" id="useBtn" style="width:100%;background:#059669;border-color:#10b981;display:none;"
                onclick="useTrained()">▶ Use this trained model live</button>
      </div>
    </div>
  </div>
</div>

<div class="modal" id="builder">
  <div class="modal-card">
    <div class="modal-head">
      <h2>BUILD EXPERIMENT</h2>
      <button class="x" onclick="closeBuilder()">✕</button>
    </div>
    <div class="builder-grid">
      <div class="bcol">
        <div class="bcol-h" id="objHead">OBJECTS · click to add to the sequence</div>
        <div id="customRow" style="display:none;gap:6px;margin-bottom:10px;">
          <input class="search" id="customInput" style="margin:0;flex:1;"
                 placeholder="type ANY object, e.g. wrench, red box…"
                 onkeydown="if(event.key==='Enter')addCustom()"/>
          <button class="hdrbtn" onclick="addCustom()">Add</button>
        </div>
        <input class="search" id="search" placeholder="search suggestions…" oninput="renderClasses()"/>
        <div class="classlist" id="classlist"></div>
      </div>
      <div class="bcol">
        <div class="bcol-h">SEQUENCE · steps run in this order</div>
        <input class="search" id="expname" placeholder="experiment name (optional)"/>
        <div class="seqlist" id="seqlist"><div class="empty">No objects yet — pick some on the left.</div></div>
        <div class="berr" id="berr"></div>
        <button class="start" onclick="startExperiment()">▶ Start this experiment</button>
      </div>
    </div>
  </div>
</div>

<script>
const STATE_LABEL = {pending:'pending',active:'active',complete:'complete',skipped:'skipped',
  out_of_order:'out of order',unverified:'unverified',overridden:'overridden',stalled:'stalled'};
let lastAlertSeq = 0, lastSpokenStep = null, voiceReady = false;

function speak(text){
  if(!('speechSynthesis' in window) || !text) return;
  try{ const u = new SpeechSynthesisUtterance(text); u.rate = 1.0; speechSynthesis.speak(u); }catch(e){}
}
// Browsers may gate speech until a user gesture.
window.addEventListener('click', ()=>{ voiceReady = true; }, {once:true});

async function cmd(url){ try{ await fetch(url,{method:'POST'}); }catch(e){} }

// ---- model trainer ----
let trainPoll = null;
function openTrainer(){
  document.getElementById('trainer').classList.add('show');
  document.getElementById('trainPreview').src = '/video';   // live feed to frame shots
  loadTrainClasses();
}
function closeTrainer(){
  document.getElementById('trainer').classList.remove('show');
  document.getElementById('trainPreview').src = '';          // stop the extra stream
  if(trainPoll){clearInterval(trainPoll);trainPoll=null;}
}
async function loadTrainClasses(){
  try{
    const r = await fetch('/api/train/classes'); const d = await r.json();
    const box = document.getElementById('trainClasses'); box.innerHTML='';
    if(!d.classes.length){ box.innerHTML='<div class="empty">No classes yet. Add one, e.g. open_book.</div>'; }
    d.classes.forEach(c=>{
      const row = document.createElement('div'); row.className='seqrow';
      const isBg = !!c.background;
      row.innerHTML = `<span class="nm">${isBg?'🚫 ':''}${c.name}</span>`+
        `<span class="conf" style="${c.count<10?'color:#f59e0b':''}">${c.count} imgs</span>`+
        `<button class="snap" onclick="captureShots('${c.name}',1)" title="take one picture now">📷 Snap</button>`+
        `<button onclick="captureShots('${c.name}',10)" title="take 10 pictures, ~0.35s apart">📷 ×10</button>`+
        `<label class="hdrbtn" style="cursor:pointer;padding:4px 8px;">＋ files`+
        `<input type="file" accept="image/*" multiple style="display:none" `+
        `onchange="uploadImgs('${c.name}',this)"></label>`+
        `<button class="rm" onclick="delTrainClass('${c.name}')">✕</button>`;
      box.appendChild(row);
    });
    renderTrainStatus(d.status, d.model_ready);
  }catch(e){}
}
async function addTrainClass(){
  const inp = document.getElementById('newClass'); const v=(inp.value||'').trim(); if(!v) return;
  const fd = new FormData(); fd.append('name', v);
  await fetch('/api/train/class',{method:'POST',body:fd}); inp.value=''; loadTrainClasses();
}
async function delTrainClass(name){
  await fetch('/api/train/class?name='+encodeURIComponent(name),{method:'DELETE'}); loadTrainClasses();
}
async function captureShots(name, n){
  const st = document.getElementById('trainStatus');
  const prev = document.getElementById('trainPreview');
  let ok = 0;
  for(let i=1;i<=n;i++){
    if(n>1) st.textContent = `📷 capturing ${i}/${n} for "${name}" — keep moving the object…`;
    try{
      const r = await fetch('/api/train/capture?name='+encodeURIComponent(name),{method:'POST'});
      const j = await r.json();
      if(j.ok) ok++; else st.textContent = '✗ '+(j.error||'capture failed');
    }catch(e){}
    // quick flash so a single Snap gives visible feedback
    prev.style.outline = '4px solid #67e8f9';
    setTimeout(()=>{ prev.style.outline='none'; }, 120);
    if(n>1) await new Promise(r=>setTimeout(r,350));
  }
  if(ok) st.textContent = ok===1 ? `✓ took 1 picture for "${name}"`
                                 : `✓ took ${ok} pictures for "${name}"`;
  loadTrainClasses();
}
async function uploadImgs(name, input){
  if(!input.files.length) return;
  const fd = new FormData(); fd.append('name', name);
  for(const f of input.files) fd.append('files', f);
  const st = document.getElementById('trainStatus'); st.textContent = 'uploading '+input.files.length+' images…';
  await fetch('/api/train/upload',{method:'POST',body:fd});
  input.value=''; loadTrainClasses();
}
async function startTrain(){
  const ep = parseInt(document.getElementById('epochs').value||'15');
  const r = await fetch('/api/train/start?epochs='+ep,{method:'POST'});
  const j = await r.json();
  if(!j.ok){ document.getElementById('trainStatus').textContent = '✗ '+j.message; return; }
  if(trainPoll) clearInterval(trainPoll);
  trainPoll = setInterval(async()=>{
    const s = await (await fetch('/api/train/status')).json();
    renderTrainStatus(s.status, s.model_ready);
    if(s.status.state==='done'||s.status.state==='error'){ clearInterval(trainPoll); trainPoll=null; }
  }, 1500);
}
function renderTrainStatus(st, ready){
  const el = document.getElementById('trainStatus'); if(!st){el.textContent='';return;}
  let t = '';
  if(st.state==='training') t = `⚙ training… epoch ${st.epoch}/${st.epochs}`;
  else if(st.state==='done') t = '✓ '+st.message;
  else if(st.state==='error') t = '✗ '+st.message;
  else t = 'idle';
  el.textContent = t;
  document.getElementById('useBtn').style.display = ready ? 'block' : 'none';
}
async function useTrained(){
  const r = await fetch('/api/train/use',{method:'POST'}); const j = await r.json();
  if(j.ok){ lastSpokenStep=null; lastAlertSeq=0; closeTrainer(); }
}

// ---- experiment builder ----
let allClasses = [], sequence = [], openVocab = false;
async function loadClasses(){
  try{ const r = await fetch('/api/classes'); allClasses = await r.json(); renderClasses(); }catch(e){}
}
function openBuilder(){
  document.getElementById('builder').classList.add('show');
  document.getElementById('customRow').style.display = openVocab ? 'flex' : 'none';
  document.getElementById('objHead').textContent = openVocab
    ? 'OBJECTS · type any object, or click a suggestion'
    : 'OBJECTS · click to add (this detector knows 80 objects)';
  if(!allClasses.length) loadClasses();
}
function addCustom(){
  const inp = document.getElementById('customInput');
  const v = (inp.value||'').trim();
  if(v){ sequence.push(v); renderSeq(); inp.value=''; inp.focus(); }
}
function closeBuilder(){ document.getElementById('builder').classList.remove('show'); }
function renderClasses(){
  const q = (document.getElementById('search').value||'').toLowerCase();
  const box = document.getElementById('classlist'); box.innerHTML='';
  allClasses.filter(c=>c.includes(q)).forEach(c=>{
    const b = document.createElement('button'); b.className='cchip'; b.textContent=c;
    b.onclick = ()=>{ sequence.push(c); renderSeq(); };
    box.appendChild(b);
  });
}
function renderSeq(){
  const box = document.getElementById('seqlist'); box.innerHTML='';
  if(!sequence.length){ box.innerHTML='<div class="empty">No objects yet — pick some on the left.</div>'; return; }
  sequence.forEach((c,i)=>{
    const row = document.createElement('div'); row.className='seqrow';
    row.innerHTML = `<span class="num">${i+1}</span><span class="nm">${c}</span>`+
      `<button onclick="mv(${i},-1)">▲</button><button onclick="mv(${i},1)">▼</button>`+
      `<button class="rm" onclick="rm(${i})">✕</button>`;
    box.appendChild(row);
  });
}
function mv(i,d){ const j=i+d; if(j<0||j>=sequence.length) return;
  [sequence[i],sequence[j]]=[sequence[j],sequence[i]]; renderSeq(); }
function rm(i){ sequence.splice(i,1); renderSeq(); }
async function startExperiment(){
  const err = document.getElementById('berr'); err.textContent='';
  if(!sequence.length){ err.textContent='Add at least one object.'; return; }
  const name = document.getElementById('expname').value || undefined;
  try{
    const r = await fetch('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sequence, name})});
    const j = await r.json();
    if(!j.ok){ err.textContent = j.error || 'failed to build'; return; }
    lastSpokenStep = null; lastAlertSeq = 0;
    closeBuilder();
  }catch(e){ err.textContent = 'network error'; }
}

function render(s){
  openVocab = !!s.open_vocab;
  document.getElementById('mode').textContent = (s.open_vocab?'open-vocab · ':'') + 'mode: ' + (s.mode||'—');
  document.getElementById('fps').textContent = (s.fps??'—') + ' fps';
  document.getElementById('proc').textContent = s.procedure||'—';
  document.getElementById('next').textContent = s.next ? s.next.name : (s.complete ? 'procedure complete' : '—');

  // Speak the next prompt when it changes.
  if(s.next && s.next.id !== lastSpokenStep){ lastSpokenStep = s.next.id; speak(s.next.voice); }
  if(!s.next) lastSpokenStep = null;

  // Steps.
  const box = document.getElementById('steps'); box.innerHTML='';
  (s.steps||[]).forEach(st=>{
    const el = document.createElement('div');
    el.className = 'step s-'+st.state;
    el.innerHTML = `<span class="dot"></span><span class="nm">${st.name}</span>`+
      (st.confidence?`<span class="conf">${st.confidence.toFixed(2)}</span>`:'')+
      `<span class="chip">${STATE_LABEL[st.state]||st.state}</span>`;
    box.appendChild(el);
  });

  // Alert banner + voice.
  const banner = document.getElementById('banner');
  if(s.alert && s.alert.seq && s.alert.seq !== lastAlertSeq){
    lastAlertSeq = s.alert.seq;
    banner.textContent = '⚠ ' + s.alert.message;
    banner.classList.add('show');
    speak('Attention. ' + s.alert.message);
    clearTimeout(window._bt); window._bt = setTimeout(()=>banner.classList.remove('show'), 4500);
  }

  // Summary.
  const sum = document.getElementById('summary');
  if(s.complete && s.summary){
    const m = s.summary;
    document.getElementById('summaryBody').innerHTML =
      row('Steps total', m.total)+
      row('Complete', m.complete, 'val-ok')+
      row('Skipped', m.skipped, m.skipped?'val-bad':'')+
      row('Out of order', m.out_of_order, m.out_of_order?'val-bad':'')+
      row('Unverified', m.unverified, m.unverified?'val-bad':'')+
      row('Alerts raised', m.alerts)+
      row('Duration', m.duration+'s');
    sum.classList.add('show');
  } else { sum.classList.remove('show'); }
}
function row(k,v,cls){ return `<div class="srow"><span>${k}</span><b class="${cls||''}">${v}</b></div>`; }

function connect(){
  const proto = location.protocol==='https:'?'wss':'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const conn = document.getElementById('conn');
  ws.onopen = ()=>{ conn.textContent='live'; conn.className='pill live'; };
  ws.onclose = ()=>{ conn.textContent='reconnecting'; conn.className='pill off'; setTimeout(connect, 1000); };
  ws.onmessage = ev=>{ try{ render(JSON.parse(ev.data)); }catch(e){} };
}
connect();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ORBITAL-HAR live web demo")
_session: DemoSession | None = None
_trainer: TrainManager | None = None


def _get() -> DemoSession:
    assert _session is not None
    return _session


def _tr() -> TrainManager:
    assert _trainer is not None
    return _trainer


#: Built React SPA. When present it is the UI; otherwise the single-file
#: dashboard below is served instead, so the demo works even with no npm build.
_UI_DIST = Path(__file__).resolve().parents[1] / "ui" / "dist"
_SPA_INDEX = _UI_DIST / "index.html"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if _SPA_INDEX.is_file():
        return _SPA_INDEX.read_text(encoding="utf-8")
    return _INDEX_HTML


@app.get("/classic", response_class=HTMLResponse)
def classic() -> str:
    """The single-file dashboard, always available as a fallback."""
    return _INDEX_HTML


@app.get("/video")
def video() -> StreamingResponse:
    def gen():
        boundary = b"--frame\r\n"
        while True:
            jpg = _get().jpeg()
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(1 / 20)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/health")
def health() -> JSONResponse:
    s = _get()
    return JSONResponse({"ok": True, "running": s.running, "open_vocab": s.open_vocab})


@app.post("/api/skip")
def api_skip() -> JSONResponse:
    _get().skip()
    return JSONResponse({"ok": True})


@app.post("/api/restart")
def api_restart(mode: str = "clean") -> JSONResponse:
    _get().restart(mode)
    return JSONResponse({"ok": True, "mode": mode})


@app.get("/api/classes")
def api_classes() -> JSONResponse:
    return JSONResponse(_get().all_classes)


class BuildRequest(BaseModel):
    sequence: list[str]
    name: str | None = None


@app.post("/api/build")
def api_build(req: BuildRequest) -> JSONResponse:
    s = _get()
    seq = [c.strip() for c in req.sequence if c and c.strip()]
    # Open-vocab: accept any typed object. Fixed model: only its known classes.
    valid = set(seq) if s.open_vocab else set(s.all_classes)
    try:
        proc = build_procedure(seq, valid, name=req.name or "Custom experiment")
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    s.load_procedure(proc)
    return JSONResponse({"ok": True, "steps": len(seq)})


# ------------------------------------------------------------------- sessions

@app.get("/api/sessions")
def api_sessions(limit: int = 25) -> JSONResponse:
    return JSONResponse(_get().store.list_sessions(limit=limit))


@app.get("/api/sessions/{session_id}")
def api_session(session_id: str) -> JSONResponse:
    s = _get()
    sess = s.store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "session": sess,
        "steps": s.store.get_step_runs(session_id),
        "alerts": s.store.get_alerts(session_id),
        "chain": s.store.get_telemetry_chain(session_id),
    })


@app.get("/api/sessions/{session_id}/verify")
def api_verify(session_id: str) -> JSONResponse:
    """Re-walk the hash chain. This is the tamper-evidence demo."""
    s = _get()
    sess = s.store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    res = verify(Path(sess["session_dir"]) / "telemetry.jsonl")
    s.store.set_verification_result(session_id, verified_ok=res.ok,
                                    first_bad_seq=res.first_bad_seq)
    return JSONResponse({"ok": res.ok, "record_count": res.record_count,
                         "first_bad_seq": res.first_bad_seq, "error": res.error})


@app.get("/api/sessions/{session_id}/telemetry")
def api_telemetry(session_id: str):
    s = _get()
    sess = s.store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    p = Path(sess["session_dir"]) / "telemetry.jsonl"
    if not p.exists():
        return JSONResponse({"error": "no telemetry"}, status_code=404)
    return FileResponse(p, media_type="application/x-ndjson",
                        filename=f"{session_id}-telemetry.jsonl")


@app.get("/api/sessions/{session_id}/video")
def api_video_file(session_id: str):
    s = _get()
    sess = s.store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    p = Path(sess["session_dir"]) / "run.mp4"
    if not p.exists():
        return JSONResponse({"error": "no recording"}, status_code=404)
    return FileResponse(p, media_type="video/mp4", filename=f"{session_id}.mp4")


# -------------------------------------------------------------------- training

@app.get("/api/train/classes")
def train_classes() -> JSONResponse:
    t = _tr()
    return JSONResponse({
        "classes": t.list_classes(),
        "status": t.status,
        "model_ready": t.model_path is not None,
    })


@app.post("/api/train/class")
def train_add_class(name: str = Form(...)) -> JSONResponse:
    return JSONResponse({"ok": True, "name": _tr().add_class(name)})


@app.delete("/api/train/class")
def train_del_class(name: str) -> JSONResponse:
    _tr().delete_class(name)
    return JSONResponse({"ok": True})


@app.post("/api/train/upload")
async def train_upload(name: str = Form(...), files: list[UploadFile] = File(...)) -> JSONResponse:
    blobs = [await f.read() for f in files]
    saved = _tr().add_images(name, blobs)
    return JSONResponse({"ok": True, "saved": saved})


@app.post("/api/train/video")
async def train_video(name: str = Form(...), frames: int = Form(40),
                      file: UploadFile = File(...)) -> JSONResponse:
    """Upload a clip; evenly-spaced frames become training images for a class."""
    blob = await file.read()
    saved = _tr().add_video(name, blob, max_frames=max(1, min(frames, 200)))
    if saved == 0:
        return JSONResponse({"ok": False, "error": "could not read that video"},
                            status_code=400)
    return JSONResponse({"ok": True, "saved": saved})


@app.post("/api/train/capture")
def train_capture(name: str) -> JSONResponse:
    """Grab the current camera frame straight into a training class.

    Training images captured from the live feed match what the model will see at
    run time -- the single biggest accuracy win over uploading unrelated photos.
    """
    blob = _get().capture_raw()
    if blob is None:
        return JSONResponse({"ok": False, "error": "no camera frame"}, status_code=400)
    saved = _tr().add_images(name, [blob])
    return JSONResponse({"ok": True, "saved": saved})


@app.post("/api/train/start")
def train_start(epochs: int = 15) -> JSONResponse:
    ok, msg = _tr().train(epochs)
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 400)


@app.get("/api/train/status")
def train_status() -> JSONResponse:
    t = _tr()
    return JSONResponse({"status": t.status, "model_ready": t.model_path is not None})


@app.post("/api/train/use")
def train_use() -> JSONResponse:
    t = _tr()
    if t.model_path is None:
        return JSONResponse({"ok": False, "error": "no trained model yet"}, status_code=400)
    _get().use_classifier(t.model_path)
    return JSONResponse({"ok": True})


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    import asyncio
    await sock.accept()
    try:
        while True:
            await sock.send_json(_get().state())
            await asyncio.sleep(0.1)
    except (WebSocketDisconnect, Exception):
        return


if _UI_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_UI_DIST / "assets")), name="assets")


@app.get("/{path:path}", response_class=HTMLResponse)
def spa_fallback(path: str) -> HTMLResponse:
    """Client-side routes (/build, /train, /sessions) all serve the SPA shell.

    Registered last so real endpoints win; API paths still 404 honestly rather
    than silently returning HTML.
    """
    if path.startswith("api/") or path in ("ws", "video"):
        return HTMLResponse('{"error":"not found"}', status_code=404)
    if _SPA_INDEX.is_file():
        return HTMLResponse(_SPA_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse(_INDEX_HTML)


def main() -> int:
    global _session, _trainer
    ap = argparse.ArgumentParser(description="ORBITAL-HAR live web demo")
    ap.add_argument("--procedure", default="demo_live")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--world", action="store_true",
                    help="use YOLO-World open-vocabulary detector (any typed object)")
    ap.add_argument("--world-model", default="yolov8s-worldv2.pt")
    ap.add_argument("--min-area", type=float, default=0.06)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 makes the live feed reachable from the network")
    ap.add_argument("--rtsp", default=None,
                    help="RTSP URL to publish to, e.g. rtsp://192.168.1.50:8554/live")
    ap.add_argument("--no-record", action="store_true", help="disable local mp4 recording")
    args = ap.parse_args()

    proc_path = Path(args.procedure)
    if not proc_path.exists():
        proc_path = Path("procedures") / f"{args.procedure}.yaml"
    proc = Procedure.load(proc_path)

    if args.world:
        from ultralytics import YOLOWorld
        print("[web] loading YOLO-World open-vocabulary detector ...")
        model = YOLOWorld(args.world_model)
    else:
        from ultralytics import YOLO
        print("[web] loading detector ...")
        model = YOLO(args.model)
    _session = DemoSession(proc, model, args.camera, args.min_area, open_vocab=args.world,
                           rtsp_url=args.rtsp, record=not args.no_record)
    _session.start()
    _trainer = TrainManager(Path("data/custom"))

    print(f"[web] open  http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        print(f"[web] live feed on the network at http://<this-machine-ip>:{args.port}/video")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
