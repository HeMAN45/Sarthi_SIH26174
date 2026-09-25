"""FastAPI application — the P-SERVE process.

The single HTTP surface for the system. Bound to loopback by default
(docs/03-APP-FLOW.md section 11); RTSP is the only externally-bound port.

This module never contains domain logic. It reads from the Store, relays state
from the live session, and serves the built dashboard. Everything it exposes is
implemented somewhere in ``runtime`` or ``reasoning``.

It works with or without a live session. Configured with only a Store it serves
the history and verification endpoints — which is what the tests use, and what a
ground-ops review station would need. Configured with a :class:`LiveSession` it
also serves the camera, the live state socket and the run controls.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from orbital_har.runtime.session import LiveSession, build_procedure
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
_start_time: float = 0.0


def configure(
    store: Store,
    session: LiveSession | None = None,
    trainer: TrainManager | None = None,
    server: Any = None,
) -> None:
    """Wire in runtime dependencies. Called before uvicorn starts."""
    global _store, _session, _trainer, _server, _start_time
    _store = store
    _session = session
    _trainer = trainer
    _server = server
    _start_time = time.monotonic()


def get_store() -> Store:
    if _store is None:
        raise RuntimeError("Store not configured — call server.app.configure() first")
    return _store


def live() -> LiveSession:
    """The live session, or a 503 explaining that this build has no camera."""
    if _session is None:
        raise HTTPException(
            status_code=503, detail="no live session — this process is history-only"
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


@app.post("/api/restart")
async def api_restart(mode: str = "clean") -> JSONResponse:
    live().restart(mode)
    return JSONResponse({"ok": True, "mode": mode})


@app.post("/api/end-run")
async def api_end_run() -> JSONResponse:
    """Seal the current run and arm a fresh one. The camera keeps running."""
    live().end_run()
    return JSONResponse({"ok": True})


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


class BuildRequest(BaseModel):
    sequence: list[str]
    name: str | None = None


@app.post("/api/build")
async def api_build(req: BuildRequest) -> JSONResponse:
    session = live()
    seq = [c.strip() for c in req.sequence if c and c.strip()]
    # Open-vocab: accept any typed object. Fixed model: only its known classes.
    valid = set(seq) if session.detector.open_vocab else set(session.all_classes)
    try:
        proc = build_procedure(seq, valid, name=req.name or "Custom experiment")
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    session.load_procedure(proc)
    return JSONResponse({"ok": True, "steps": len(seq)})


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
        }
    )


@app.post("/api/train/class")
async def train_add_class(name: str = Form(...)) -> JSONResponse:
    return JSONResponse({"ok": True, "name": trainer().add_class(name)})


@app.delete("/api/train/class")
async def train_del_class(name: str) -> JSONResponse:
    trainer().delete_class(name)
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
    blob = live().capture_raw()
    if blob is None:
        return JSONResponse({"ok": False, "error": "no camera frame"}, status_code=400)
    return JSONResponse({"ok": True, "saved": trainer().add_images(name, [blob])})


@app.post("/api/train/start")
async def train_start(epochs: int = 15) -> JSONResponse:
    ok, msg = trainer().train(epochs)
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 400)


@app.get("/api/train/status")
async def train_status() -> JSONResponse:
    manager = trainer()
    return JSONResponse({"status": manager.status, "model_ready": manager.model_path is not None})


@app.post("/api/train/use")
async def train_use() -> JSONResponse:
    manager = trainer()
    if not manager.model_path:
        return JSONResponse({"ok": False, "error": "no trained model"}, status_code=400)
    live().use_classifier(manager.model_path)
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
# Static assets — the React dashboard
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
