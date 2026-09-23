"""Tests for the P-SERVE FastAPI application."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orbital_har.runtime.store import Store
from orbital_har.server.app import app, configure, fanout


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    # Seed a procedure and a session
    s.upsert_procedure(
        id="test_proc", name="Test", version=1, vocabulary="vocab",
        rack_markers="m", source_path="p", yaml_content="content",
        step_count=1, steps=[{"step_id": "s1", "ordinal": 0, "name": "step",
                              "voice_prompt": "", "preconditions": [], "requires": []}]
    )
    s.create_session(
        session_id="sess-01", procedure_id="test_proc", procedure_version=1,
        session_dir=str(tmp_path / "sess-01")
    )
    yield s
    s.close()


@pytest.fixture()
def client(store: Store) -> TestClient:
    configure(store)
    return TestClient(app)


class TestServer:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_s" in data
        assert data["ws_clients"] == 0

    def test_list_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "sess-01"

    def test_get_session(self, client: TestClient, store: Store) -> None:
        store.insert_alert(
            session_id="sess-01", bus_seq=1, kind="skip", severity="high", message="test"
        )
        resp = client.get("/api/sessions/sess-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["id"] == "sess-01"
        assert len(data["alerts"]) == 1

    def test_get_procedure(self, client: TestClient) -> None:
        resp = client.get("/api/procedure")
        assert resp.status_code == 200
        data = resp.json()
        assert data["procedure"]["id"] == "test_proc"
        assert data["session_id"] == "sess-01"

    def test_acknowledge_alert(self, client: TestClient, store: Store) -> None:
        aid = store.insert_alert(
            session_id="sess-01", bus_seq=1, kind="skip", severity="high", message="test"
        )
        resp = client.post(f"/api/session/acknowledge?alert_id={aid}")
        assert resp.status_code == 200
        
        alerts = store.get_alerts("sess-01")
        assert alerts[0]["acknowledged_at"] is not None

    def test_verify_session(self, client: TestClient, tmp_path: Path, store: Store) -> None:
        from orbital_har.runtime.telemetry import TelemetryWriter
        
        sess_dir = tmp_path / "sess-01"
        sess_dir.mkdir(exist_ok=True)
        telem_path = sess_dir / "telemetry.jsonl"
        with TelemetryWriter(telem_path) as tw:
            tw.write("session_start", {})
            store.upsert_telemetry_chain(
                session_id="sess-01",
                genesis_hash=tw.genesis_hash,
                last_seq=tw.last_seq,
                last_hash=tw.last_hash,
                record_count=tw.record_count,
                bytes_written=tw.bytes_written,
            )
        
        resp = client.get("/api/sessions/sess-01/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["record_count"] == 1
        
        # Check store update
        chain = store.get_telemetry_chain("sess-01")
        assert chain is not None
        assert chain["verified_ok"] == 1


class TestWebSocketFanout:
    def test_websocket_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as _ws2:
            assert fanout.client_count == 2
            ws1.send_text("hello")
            assert fanout.client_count == 2
            
        assert fanout.client_count == 0

    @pytest.mark.asyncio
    async def test_fanout_broadcast(self) -> None:
        from unittest.mock import AsyncMock

        from fastapi import WebSocket
        
        mock_ws1 = AsyncMock(spec=WebSocket)
        mock_ws2 = AsyncMock(spec=WebSocket)
        
        await fanout.connect(mock_ws1)
        await fanout.connect(mock_ws2)
        
        await fanout.broadcast({"event": "test"})
        
        mock_ws1.send_text.assert_called_once_with('{"event": "test"}')
        mock_ws2.send_text.assert_called_once_with('{"event": "test"}')
        
        fanout.disconnect(mock_ws1)
        fanout.disconnect(mock_ws2)
