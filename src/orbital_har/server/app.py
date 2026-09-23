"""FastAPI application — the P-SERVE process.

Bound to loopback by default (docs/03-APP-FLOW.md §11). RTSP is the only
externally-bound port. Serves the static React dashboard and provides a
WebSocket fan-out for live state updates.

This module never contains domain logic — it reads from the Store and
relays events from the bus.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from orbital_har.runtime.store import Store

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parents[3] / "ui" / "dist"

app = FastAPI(
    title="ORBITAL-HAR",
    description="On-board procedure supervision for BAS experiments (SIH26174)",
    version="0.1.0",
)

# State: injected at startup or via dependency override.
_store: Store | None = None
_start_time: float = 0.0


def configure(store: Store) -> None:
    """Wire in runtime dependencies. Called before uvicorn starts."""
    global _store, _start_time
    _store = store
    _start_time = time.monotonic()


def get_store() -> Store:
    if _store is None:
        raise RuntimeError("Store not configured — call server.app.configure() first")
    return _store


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
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> JSONResponse:
    """Subsystem status, FPS, latency, memory (03-APP-FLOW.md §11)."""
    store = get_store()
    uptime = time.monotonic() - _start_time
    return JSONResponse({
        "status": "ok",
        "uptime_s": round(uptime, 1),
        "ws_clients": fanout.client_count,
        "store": store.db_path.name,
    })


# ---------------------------------------------------------------------------
# Procedure
# ---------------------------------------------------------------------------


@app.get("/api/procedure")
async def get_procedure() -> JSONResponse:
    """Current procedure and step states (if a session is active)."""
    store = get_store()
    sessions = store.list_sessions(limit=1)
    if not sessions:
        return JSONResponse({"procedure": None, "steps": []})

    active = sessions[0]
    steps = store.get_step_runs(active["id"]) if active["status"] == "running" else []
    return JSONResponse({
        "procedure": {
            "id": active["procedure_id"],
            "version": active["procedure_version"],
        },
        "session_id": active["id"],
        "session_status": active["status"],
        "steps": steps,
    })


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.get("/api/sessions")
async def list_sessions(limit: int = 50, offset: int = 0) -> JSONResponse:
    store = get_store()
    return JSONResponse(store.list_sessions(limit=limit, offset=offset))


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> JSONResponse:
    store = get_store()
    sess = store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    steps = store.get_step_runs(session_id)
    alerts = store.get_alerts(session_id)
    chain = store.get_telemetry_chain(session_id)
    return JSONResponse({
        "session": sess,
        "steps": steps,
        "alerts": alerts,
        "telemetry_chain": chain,
    })


@app.get("/api/sessions/{session_id}/verify")
async def verify_session(session_id: str) -> JSONResponse:
    """Run the hash chain verifier on a session's telemetry."""
    from orbital_har.runtime.telemetry import verify

    store = get_store()
    sess = store.get_session(session_id)
    if sess is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    telem_path = Path(sess["session_dir"]) / "telemetry.jsonl"
    result = verify(telem_path)
    store.set_verification_result(
        session_id,
        verified_ok=result.ok,
        first_bad_seq=result.first_bad_seq,
    )
    return JSONResponse({
        "ok": result.ok,
        "record_count": result.record_count,
        "first_bad_seq": result.first_bad_seq,
        "error": result.error,
    })


# ---------------------------------------------------------------------------
# Session control
# ---------------------------------------------------------------------------


@app.post("/api/session/acknowledge")
async def acknowledge_alert(alert_id: int) -> JSONResponse:
    store = get_store()
    store.acknowledge_alert(alert_id)
    return JSONResponse({"acknowledged": alert_id})


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Live state, alerts, health — fan-out (03-APP-FLOW.md §11)."""
    await fanout.connect(ws)
    try:
        while True:
            # Keep the connection open. Client can send control messages.
            _data = await ws.receive_text()
            # For now, echo back — future: handle crew_action messages.
    except WebSocketDisconnect:
        fanout.disconnect(ws)
    except Exception:
        fanout.disconnect(ws)


# ---------------------------------------------------------------------------
# Static assets — React dashboard
# ---------------------------------------------------------------------------


if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")

@app.exception_handler(404)
async def spa_fallback(request: Request, exc: HTTPException) -> JSONResponse | FileResponse:
    path = request.url.path
    if path.startswith("/api/") or path == "/ws":
        return JSONResponse({"error": "not found"}, status_code=404)
    index = _STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"error": "not found"}, status_code=404)
