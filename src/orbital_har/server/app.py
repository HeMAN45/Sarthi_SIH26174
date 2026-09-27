"""FastAPI application - the P-SERVE process.

The single HTTP surface for the system. Bound to loopback by default
(docs/03-APP-FLOW.md section 11); RTSP is the only externally-bound port.

This module never contains domain logic. It reads from the Store, relays state
from the live session, and serves the built dashboard. Everything it exposes is
implemented somewhere in ``runtime`` or ``reasoning``.

It works with or without a live session. Configured with only a Store it serves
the history and verification endpoints - which is what the tests use, and what a
ground-ops review station would need. Configured with a :class:`LiveSession` it
also serves the camera, the live state socket and the run controls.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orbital_har.reasoning.schema import Procedure, ProcedureError
from orbital_har.runtime.experiments import ExperimentStore, compose, spec_of
from orbital_har.runtime.session import LiveSession, build_procedure, perception_needs
from orbital_har.runtime.store import Store
from orbital_har.runtime.telemetry import verify
from orbital_har.runtime.training import TrainManager

_STATIC_DIR = Path(__file__).resolve().parents[3] / "ui" / "dist"
_SPA_INDEX = _STATIC_DIR / "index.html"

#: Shown when the dashboard has not been built. Honest beats a blank page.
_NO_UI = """<!doctype html><html><head><meta charset="utf-8">
<title>SARTHI</title><style>
body{background:#05080e;color:#e6eef6;font:15px/1.6 system-ui,sans-serif;
display:grid;place-items:center;height:100vh;margin:0}
div{max-width:34rem;padding:2rem;border:1px solid #1b2735;border-radius:4px}
code{background:#0f1822;padding:.2em .45em;border-radius:3px;color:#00e08a}
h1{font-size:17px;letter-spacing:4px;margin:0 0 1rem}
</style></head><body><div>
<h1>SARTHI</h1>
<p>The dashboard has not been built yet. The API is running; the UI is not.</p>
<p><code>cd ui &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p style="color:#5a6f84;font-size:13px">Then reload this page. The built assets
are served from <code>ui/dist</code>.</p>
</div></body></html>"""


def _lifespan_factory():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield
        # Seal the open run however the process dies -- Ctrl+C included.
        # Without this a killed run leaves its session row at status='running'
        # and its hash chain unsealed. For a system whose headline claim is a
        # verifiable audit trail, that is not acceptable.
        if _session is not None:
            _session.finalize()

    return _lifespan


app = FastAPI(
    title="SARTHI",
    description="On-board procedure supervision for BAS experiments (SIH26174)",
    version="0.1.0",
    lifespan=_lifespan_factory(),
)

_store: Store | None = None
_session: LiveSession | None = None
_trainer: TrainManager | None = None
_server: Any = None  # uvicorn.Server, held so /api/shutdown can exit
_procedures: Path | None = None
_experiments: ExperimentStore | None = None
_start_time: float = 0.0

#: A library id is a file stem, never a path.
_PROC_ID = re.compile(r"[A-Za-z0-9_\-]{1,64}")


def configure(
    store: Store,
    session: LiveSession | None = None,
    trainer: TrainManager | None = None,
    server: Any = None,
    procedures: Path | None = None,
    experiments: Path | None = None,
) -> None:
    """Wire in runtime dependencies. Called before uvicorn starts."""
    global _store, _session, _trainer, _server, _procedures, _experiments, _start_time
    _store = store
    _session = session
    _trainer = trainer
    _server = server
    _procedures = procedures
    _experiments = ExperimentStore(experiments) if experiments is not None else None
    _start_time = time.monotonic()


def get_store() -> Store:
    if _store is None:
        raise RuntimeError("Store not configured - call server.app.configure() first")
    return _store


def live() -> LiveSession:
    """The live session, or a 503 explaining that this build has no camera."""
    if _session is None:
        raise HTTPException(
            status_code=503, detail="no live session - this process is history-only"
        )
    return _session


def trainer() -> TrainManager:
    if _trainer is None:
        raise HTTPException(status_code=503, detail="training is not enabled")
    return _trainer


# ---------------------------------------------------------------------------
# WebSocket fan-out
# ---------------------------------------------------------------------------


class Fanout:
    """Broadcast events to all connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


fanout = Fanout()


# ---------------------------------------------------------------------------
# Health and procedure
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> JSONResponse:
    """Subsystem status (03-APP-FLOW.md section 11)."""
    store = get_store()
    body: dict[str, Any] = {
        "status": "ok",
        "uptime_s": round(time.monotonic() - _start_time, 1),
        "ws_clients": fanout.client_count,
        "store": store.db_path.name,
        "live": _session is not None,
    }
    if _session is not None:
        body["running"] = _session.running
        body["phase"] = _session.phase
        body["camera"] = _session.state().get("camera")
        body["open_vocab"] = _session.detector.open_vocab
    return JSONResponse(body)


@app.get("/api/procedure")
async def get_procedure() -> JSONResponse:
    """Current procedure and step states (if a session is active)."""
    store = get_store()
    sessions = store.list_sessions(limit=1)
    if not sessions:
        return JSONResponse({"procedure": None, "steps": []})

    active = sessions[0]
    steps = store.get_step_runs(active["id"]) if active["status"] == "running" else []
    return JSONResponse(
        {
            "procedure": {"id": active["procedure_id"], "version": active["procedure_version"]},
            "session_id": active["id"],
            "session_status": active["status"],
            "steps": steps,
        }
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.get("/api/sessions")
async def list_sessions(limit: int = 50, offset: int = 0) -> JSONResponse:
    return JSONResponse(get_store().list_sessions(limit=limit, offset=offset))


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> JSONResponse:
    store = get_store()
    sess = store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    chain = store.get_telemetry_chain(session_id)
    return JSONResponse(
        {
            "session": sess,
            "steps": store.get_step_runs(session_id),
            "alerts": store.get_alerts(session_id),
            # Both keys: "chain" is what the dashboard reads, "telemetry_chain" is
            # the documented name in 05-BACKEND-SCHEMA.md.
            "chain": chain,
            "telemetry_chain": chain,
        }
    )


@app.get("/api/sessions/{session_id}/verify")
async def verify_session(session_id: str) -> JSONResponse:
    """Re-walk the hash chain. This is the tamper-evidence demo."""
    store = get_store()
    sess = store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    result = verify(Path(sess["session_dir"]) / "telemetry.jsonl")
    store.set_verification_result(
        session_id, verified_ok=result.ok, first_bad_seq=result.first_bad_seq
    )
    return JSONResponse(
        {
            "ok": result.ok,
            "record_count": result.record_count,
            "first_bad_seq": result.first_bad_seq,
            "error": result.error,
        }
    )


@app.get("/api/sessions/{session_id}/telemetry")
async def download_telemetry(session_id: str):
    sess = get_store().get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = Path(sess["session_dir"]) / "telemetry.jsonl"
    if not path.exists():
        return JSONResponse({"error": "no telemetry"}, status_code=404)
    return FileResponse(
        path, media_type="application/x-ndjson", filename=f"{session_id}-telemetry.jsonl"
    )


@app.get("/api/sessions/{session_id}/video")
async def download_video(session_id: str):
    sess = get_store().get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = Path(sess["session_dir"]) / "run.mp4"
    if not path.exists():
        return JSONResponse({"error": "no recording"}, status_code=404)
    return FileResponse(path, media_type="video/mp4", filename=f"{session_id}.mp4")


@app.post("/api/session/acknowledge")
async def acknowledge_alert(alert_id: int) -> JSONResponse:
    get_store().acknowledge_alert(alert_id)
    return JSONResponse({"acknowledged": alert_id})


# ---------------------------------------------------------------------------
# Live camera and run control
# ---------------------------------------------------------------------------


@app.get("/video")
def video() -> StreamingResponse:
    session = live()

    def frames():
        boundary = b"--frame\r\n"
        while True:
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + session.jpeg() + b"\r\n"
            time.sleep(1 / 20)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/skip")
async def api_skip() -> JSONResponse:
    live().skip()
    return JSONResponse({"ok": True})


@app.post("/api/session/start")
async def api_session_start(mode: str = "clean") -> JSONResponse:
    """Begin a run: power the camera and start judging (03-APP-FLOW.md §3).

    During a live run this is a restart -- the old run is sealed first.
    """
    live().start_run(mode)
    return JSONResponse({"ok": True, "mode": mode})


@app.post("/api/session/stop")
async def api_session_stop() -> JSONResponse:
    """End the run: seal its hash chain and release the camera."""
    live().end_run()
    return JSONResponse({"ok": True})


@app.post("/api/camera")
async def api_camera(on: bool = True) -> JSONResponse:
    """Camera without a run -- the preview training capture needs."""
    live().set_camera(on)
    return JSONResponse({"ok": True, "on": on})


@app.post("/api/body")
async def api_body(on: bool = True) -> JSONResponse:
    """Body tracking -- pose, hands, contact, gestures. Off saves CPU on a weak
    machine; a procedure whose steps need it keeps it on regardless."""
    live().set_body(on)
    return JSONResponse({"ok": True, "on": on})


@app.post("/api/camera/mirror")
async def api_camera_mirror(on: bool = True) -> JSONResponse:
    """Selfie-style display. The picture only: coordinates never flip."""
    live().set_mirror(on)
    return JSONResponse({"ok": True, "mirror": on})


@app.post("/api/shutdown")
async def api_shutdown() -> JSONResponse:
    """Seal the run, release the camera and exit the process."""
    live().stop()
    if _server is not None:
        _server.should_exit = True
    return JSONResponse({"ok": True})


@app.post("/api/voice/mute")
async def api_voice_mute(muted: bool = True) -> JSONResponse:
    """Mute must reach the device, or it silences the wrong voice."""
    session = live()
    session.voice.set_muted(muted)
    return JSONResponse(session.voice.status())


# ---------------------------------------------------------------------------
# Experiment builder
# ---------------------------------------------------------------------------


@app.get("/api/classes")
async def api_classes() -> JSONResponse:
    return JSONResponse(live().all_classes)


class StepSpec(BaseModel):
    """One builder step: an object, a body action, or both, in own words.

    ``how`` the object is used -- show, hold, pour, move -- and which ``hand``
    must do it: any, left or right.
    """

    object: str | None = None
    gesture: str | None = None
    instruction: str | None = None
    hand: str = "any"
    how: str = "show"


class BuildRequest(BaseModel):
    sequence: list[str] = []
    steps: list[StepSpec] | None = None
    name: str | None = None
    start: bool = True


@app.post("/api/build")
async def api_build(req: BuildRequest) -> JSONResponse:
    session = live()
    name = req.name or "Custom experiment"
    try:
        if req.steps is not None:
            specs = [s.model_dump() for s in req.steps]
            # A step waiting on an object this detector cannot see would stall
            # the run for no reason the operator could guess (invariant #10).
            if not session.detector.open_vocab:
                known = set(session.all_classes)
                wanted = {(s["object"] or "").strip() for s in specs} - {""}
                unknown = sorted(wanted - known)
                if unknown:
                    raise ValueError(f"the detector does not know {', '.join(unknown)}")
            proc = compose(specs, name)
        else:
            seq = [c.strip() for c in req.sequence if c and c.strip()]
            # Open-vocab: accept any typed object. Fixed model: its classes only.
            valid = set(seq) if session.detector.open_vocab else set(session.all_classes)
            proc = build_procedure(seq, valid, name=name)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    session.load_procedure(proc, start=req.start)
    return JSONResponse({"ok": True, "steps": len(proc.steps)})


# ---------------------------------------------------------------------------
# Procedure library and hot-swap
# ---------------------------------------------------------------------------


def _library() -> Path:
    if _procedures is None or not _procedures.is_dir():
        raise HTTPException(status_code=503, detail="no procedure library configured")
    return _procedures


def _describe(path: Path) -> tuple[str, str]:
    """(title, summary) from a procedure file's leading comment block.

    The YAML comments are where each procedure already explains itself, so the
    library reads them rather than asking for the same words twice.
    """
    head: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        head.append(line.lstrip("#").strip())
    if not head:
        return path.stem, ""
    para: list[str] = []
    for line in head[1:]:
        if line:
            para.append(line)
        elif para:
            break
    return head[0], " ".join(para)


def _to_train(proc: Procedure) -> list[str]:
    """The classes a procedure needs that only training can provide.

    The stock detector's objects never need training -- a trained detector is
    added beside it, not instead of it.
    """
    stock = set(_session.detector.base_classes) if _session is not None else set()
    return sorted(proc.vocabulary_classes - stock)


def _library_files() -> list[tuple[Path, str]]:
    """Every procedure file with its source: built-in first, then saved."""
    files = [(p, "builtin") for p in sorted(_library().glob("*.yaml"))]
    if _experiments is not None:
        files += [(_experiments.path(i), "saved") for i in _experiments.ids()]
    return files


def _library_procedure(proc_id: str) -> Procedure | JSONResponse:
    """A library procedure by file stem, or the error response explaining why not."""
    if not _PROC_ID.fullmatch(proc_id):
        return JSONResponse({"ok": False, "error": "invalid procedure id"}, status_code=400)
    path = _library() / f"{proc_id}.yaml"
    if not path.is_file() and _experiments is not None:
        path = _experiments.path(proc_id)
    if not path.is_file():
        return JSONResponse({"ok": False, "error": f"no procedure '{proc_id}'"}, status_code=404)
    try:
        return Procedure.load(path)
    except ProcedureError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)


def _class_uses(proc: Procedure) -> dict[str, list[str]]:
    """Detector class -> the steps that look for it.

    Shown beside each class while capturing, so the operator knows what a
    photo of ``bottle_open`` has to prove (opened, then drunk from). Body
    actions name no class: there is nothing to train for them.
    """
    uses: dict[str, list[str]] = {}
    for step in proc.steps:
        for c in sorted(proc.step_classes(step)):
            names = uses.setdefault(c, [])
            if step.name not in names:
                names.append(step.name)
    return uses


@app.get("/api/procedures")
async def api_procedures() -> JSONResponse:
    """Every procedure in the library, and whether this detector can run it."""
    session = live()
    out: list[dict[str, Any]] = []
    for path, source in _library_files():
        title, summary = _describe(path)
        entry: dict[str, Any] = {
            "id": path.stem,
            "title": title,
            "summary": summary,
            "source": source,
        }
        try:
            proc = Procedure.load(path)
        except ProcedureError as exc:
            # Listed with its error, not hidden: a broken file is worth knowing about.
            out.append(entry | {"error": str(exc)})
            continue
        rack, pose = perception_needs(proc)
        out.append(
            entry
            | {
                "name": proc.procedure.name,
                "procedure_id": proc.procedure.id,
                "steps": [s.name for s in proc.steps],
                "rack": rack,
                "pose": pose,
                "classes": sorted(proc.vocabulary_classes),
                "train": _to_train(proc),
                "uses": _class_uses(proc),
                "missing": session.missing_classes(proc),
                "loaded": proc.procedure.id == session.proc.procedure.id,
            }
        )
    return JSONResponse(out)


class LoadRequest(BaseModel):
    id: str
    start: bool = False
    force: bool = False


@app.post("/api/procedure/load")
async def api_procedure_load(req: LoadRequest) -> JSONResponse:
    """Hot-swap to a library procedure (03-APP-FLOW.md §9).

    Refuses, naming the classes, when the detector cannot perceive what the
    procedure needs -- failing at step 3 in front of a jury is worse. ``force``
    is the operator saying they know, and is honoured.
    """
    session = live()
    proc = _library_procedure(req.id)
    if isinstance(proc, JSONResponse):
        return proc
    missing = session.missing_classes(proc)
    if missing and not req.force:
        return JSONResponse(
            {
                "ok": False,
                "missing": missing,
                "error": "the loaded detector cannot perceive: " + ", ".join(missing),
            },
            status_code=409,
        )
    session.load_procedure(proc, start=req.start)
    return JSONResponse({"ok": True, "name": proc.procedure.name, "missing": missing})


def _saved() -> ExperimentStore:
    if _experiments is None:
        raise HTTPException(status_code=503, detail="saving experiments is not enabled")
    return _experiments


class ExperimentRequest(BaseModel):
    name: str
    steps: list[StepSpec]
    #: Present when editing: overwrite this experiment instead of adding one.
    id: str | None = None


@app.post("/api/experiments")
async def api_experiment_save(req: ExperimentRequest) -> JSONResponse:
    """Save a builder experiment under its name, as a procedure file."""
    store = _saved()
    if not req.name.strip():
        return JSONResponse({"ok": False, "error": "give the experiment a name"}, status_code=400)
    if req.id is not None and (not _PROC_ID.fullmatch(req.id) or not store.path(req.id).is_file()):
        return JSONResponse(
            {"ok": False, "error": f"no saved experiment '{req.id}'"}, status_code=404
        )
    taken = {p.stem for p in _library().glob("*.yaml")}
    try:
        exp_id = store.save(
            req.name, [s.model_dump() for s in req.steps], exp_id=req.id, taken=taken
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "id": exp_id})


@app.get("/api/experiments/{exp_id}")
async def api_experiment_get(exp_id: str) -> JSONResponse:
    """A saved experiment as builder steps, to edit it."""
    store = _saved()
    if not _PROC_ID.fullmatch(exp_id) or not store.path(exp_id).is_file():
        return JSONResponse(
            {"ok": False, "error": f"no saved experiment '{exp_id}'"}, status_code=404
        )
    try:
        proc = Procedure.load(store.path(exp_id))
    except ProcedureError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return JSONResponse(
        {"ok": True, "id": exp_id, "name": proc.procedure.name, "steps": spec_of(proc)}
    )


@app.delete("/api/experiments/{exp_id}")
async def api_experiment_delete(exp_id: str) -> JSONResponse:
    store = _saved()
    if not _PROC_ID.fullmatch(exp_id) or not store.delete(exp_id):
        return JSONResponse(
            {"ok": False, "error": f"no saved experiment '{exp_id}'"}, status_code=404
        )
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# On-device training
# ---------------------------------------------------------------------------


@app.get("/api/train/classes")
async def train_classes() -> JSONResponse:
    manager = trainer()
    return JSONResponse(
        {
            "classes": manager.list_classes(),
            "status": manager.status,
            "model_ready": manager.model_path is not None,
            "model_kind": manager.kind,
            "advice": manager.advice(),
        }
    )


@app.post("/api/train/prepare")
async def train_prepare(procedure: str) -> JSONResponse:
    """Create the classes a library procedure needs, plus background."""
    manager = trainer()
    proc = _library_procedure(procedure)
    if isinstance(proc, JSONResponse):
        return proc
    created = manager.prepare(_to_train(proc))
    return JSONResponse({"ok": True, "created": created, "name": proc.procedure.name})


def _box_review(manager: TrainManager, summary: dict[str, Any]) -> JSONResponse:
    """The summary plus every photo the review grid shows, per class."""
    photos = {
        cls: sorted(manager.boxes.data.get("boxes", {}).get(cls, {})) for cls in summary["classes"]
    }
    for key in summary["flagged"]:  # background photos that seem to show the object
        cls, name = key.split("/", 1)
        photos.setdefault(cls, []).append(name)
    return JSONResponse({"ok": True, **summary, "photos": photos})


@app.post("/api/train/boxes/propose")
def train_boxes_propose() -> JSONResponse:
    """Let the stock detector find the object in every photo, for review."""
    manager = trainer()
    return _box_review(manager, manager.propose())


@app.get("/api/train/boxes")
def train_boxes() -> JSONResponse:
    """Proposed boxes per class, with exclusions and suspicious background photos."""
    manager = trainer()
    return _box_review(manager, manager.box_summary())


def _box_key(cls: str, name: str) -> tuple[str, str] | None:
    if not _PROC_ID.fullmatch(cls) or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}\.jpg", name):
        return None
    return cls, name


@app.get("/api/train/boxes/image")
def train_box_image(cls: str, name: str) -> Response:
    """One photo with its proposed box drawn -- the review grid's tiles."""
    key = _box_key(cls, name)
    blob = trainer().boxes.thumbnail(*key) if key else None
    if blob is None:
        return JSONResponse({"ok": False, "error": "no such photo"}, status_code=404)
    return Response(blob, media_type="image/jpeg")


@app.post("/api/train/boxes/toggle")
def train_box_toggle(cls: str, name: str) -> JSONResponse:
    """Exclude a wrong box from training, or bring it back."""
    key = _box_key(cls, name)
    if key is None:
        return JSONResponse({"ok": False, "error": "no such photo"}, status_code=404)
    return JSONResponse({"ok": True, "excluded": trainer().boxes.toggle(f"{cls}/{name}")})


@app.get("/api/train/predict")
def train_predict() -> JSONResponse:
    """The trained model's verdict on the current frame -- the test stage.

    Sync on purpose: inference is CPU work and belongs in the threadpool, not
    on the event loop that is also pushing live state.
    """
    manager = trainer()
    if manager.model_path is None:
        return JSONResponse({"ok": False, "error": "no trained model"}, status_code=400)
    session = live()
    blob = session.capture_raw()
    if blob is None:
        why = "camera is off -- turn it on first" if not session.camera_on else "no camera frame"
        return JSONResponse({"ok": False, "error": why}, status_code=400)
    # The same floor a run applies: a presentation counts only what is held
    # up close; a scene procedure counts everything in view.
    floor = 0.0 if session.proc.is_scene else session.min_area
    verdict = manager.predict(blob, min_area=floor)
    if verdict is None:
        return JSONResponse({"ok": False, "error": "could not run the model"}, status_code=500)
    return JSONResponse({"ok": True, **verdict})


@app.post("/api/train/class")
async def train_add_class(name: str = Form(...)) -> JSONResponse:
    return JSONResponse({"ok": True, "name": trainer().add_class(name)})


@app.delete("/api/train/class")
async def train_del_class(name: str) -> JSONResponse:
    """Delete a class and its photos; a deployed model stops reporting it too."""
    manager = trainer()
    gone = manager.delete_class(name)
    if _session is not None and gone:
        _session.retire_trained({gone})
    return JSONResponse({"ok": True})


@app.post("/api/train/upload")
async def train_upload(name: str = Form(...), files: list[UploadFile] = File(...)) -> JSONResponse:
    blobs = [await f.read() for f in files]
    return JSONResponse({"ok": True, "saved": trainer().add_images(name, blobs)})


@app.post("/api/train/video")
async def train_video(
    name: str = Form(...), file: UploadFile = File(...), frames: int = Form(40)
) -> JSONResponse:
    saved = trainer().add_video(name, await file.read(), max_frames=frames)
    if saved == 0:
        return JSONResponse({"ok": False, "error": "could not read that video"}, status_code=400)
    return JSONResponse({"ok": True, "saved": saved})


@app.post("/api/train/capture")
async def train_capture(name: str) -> JSONResponse:
    session = live()
    blob = session.capture_raw()
    if blob is None:
        why = "camera is off -- turn it on first" if not session.camera_on else "no camera frame"
        return JSONResponse({"ok": False, "error": why}, status_code=400)
    return JSONResponse({"ok": True, "saved": trainer().add_images(name, [blob])})


@app.post("/api/train/start")
async def train_start(epochs: int = 15, mode: str = "classify") -> JSONResponse:
    """Train on-device: ``detect`` learns where the object is, ``classify`` whole scenes."""
    ok, msg = trainer().train(epochs, mode)
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 400)


@app.get("/api/train/status")
async def train_status() -> JSONResponse:
    manager = trainer()
    return JSONResponse({"status": manager.status, "model_ready": manager.model_path is not None})


@app.post("/api/train/use")
def train_use(procedure: str | None = None) -> JSONResponse:
    """Deploy the trained classifier and start a run.

    With ``procedure`` the run follows that library procedure, whose classes
    the model must provide -- a detector together with the stock objects it
    joins, a classifier alone. Without, each trained class is one step.
    """
    manager = trainer()
    if not manager.model_path:
        return JSONResponse({"ok": False, "error": "no trained model"}, status_code=400)
    proc = None
    if procedure:
        proc = _library_procedure(procedure)
        if isinstance(proc, JSONResponse):
            return proc
        known = set(manager.model_classes())
        if manager.kind == "detect":
            known |= set(live().detector.base_classes)
        missing = sorted(proc.vocabulary_classes - known)
        if missing:
            return JSONResponse(
                {
                    "ok": False,
                    "missing": missing,
                    "error": "the model was not trained on: " + ", ".join(missing),
                },
                status_code=409,
            )
    live().use_classifier(manager.model_path, proc, manager.kind)
    return JSONResponse({"ok": True, "model": manager.model_path})


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Live state, alerts and health.

    With a live session this pushes state at ~10 Hz. Without one it simply
    holds the connection open for :class:`Fanout` broadcasts, which is what a
    replay or review process needs.
    """
    await fanout.connect(ws)
    try:
        if _session is None:
            while True:
                await ws.receive_text()
        else:
            while True:
                await ws.send_json(_session.state())
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        fanout.disconnect(ws)
    except Exception:
        fanout.disconnect(ws)


# ---------------------------------------------------------------------------
# Static assets - the React dashboard
# ---------------------------------------------------------------------------


if (_STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="assets")


@app.get("/{path:path}", response_class=HTMLResponse)
async def spa(path: str) -> HTMLResponse:
    """Serve the SPA shell for client-side routes.

    Registered last so real endpoints win. API paths still 404 honestly rather
    than silently returning HTML.
    """
    if path.startswith("api/") or path in ("ws", "video"):
        return HTMLResponse('{"error":"not found"}', status_code=404)
    if _SPA_INDEX.is_file():
        return HTMLResponse(_SPA_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse(_NO_UI)


@app.exception_handler(404)
async def spa_fallback(request: Request, exc: HTTPException) -> JSONResponse | HTMLResponse:
    path = request.url.path
    if path.startswith("/api/") or path == "/ws":
        return JSONResponse({"error": "not found"}, status_code=404)
    if _SPA_INDEX.is_file():
        return HTMLResponse(_SPA_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse(_NO_UI)
