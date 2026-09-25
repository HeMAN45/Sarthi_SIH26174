"""Tests for runtime.store — session lifecycle and queries."""

from __future__ import annotations

from pathlib import Path

import pytest

from orbital_har.runtime.store import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    # Seed a procedure so sessions can reference it.
    s.upsert_procedure(
        id="test_proc",
        name="Test Procedure",
        version=1,
        vocabulary="orbital-v1",
        rack_markers="DICT_4X4_50",
        source_path="procedures/test.yaml",
        yaml_content="procedure: test",
        step_count=3,
        steps=[
            {
                "step_id": "s1",
                "ordinal": 0,
                "name": "Step 1",
                "voice_prompt": "do step one",
                "preconditions": [],
                "requires": [],
            },
            {
                "step_id": "s2",
                "ordinal": 1,
                "name": "Step 2",
                "voice_prompt": "do step two",
                "preconditions": ["s1"],
                "requires": [],
            },
            {
                "step_id": "s3",
                "ordinal": 2,
                "name": "Step 3",
                "voice_prompt": "do step three",
                "preconditions": ["s2"],
                "requires": [],
            },
        ],
    )
    yield s
    s.close()


class TestProcedures:
    def test_upsert_procedure(self, store: Store) -> None:
        row = store.conn.execute("SELECT * FROM procedures WHERE id = 'test_proc'").fetchone()
        assert row is not None
        assert dict(row)["name"] == "Test Procedure"

    def test_procedure_steps_inserted(self, store: Store) -> None:
        rows = store.conn.execute(
            "SELECT * FROM procedure_steps WHERE procedure_id = 'test_proc' ORDER BY ordinal"
        ).fetchall()
        assert len(rows) == 3
        assert dict(rows[0])["step_id"] == "s1"

    def test_upsert_replaces_on_conflict(self, store: Store) -> None:
        store.upsert_procedure(
            id="test_proc",
            name="Updated Procedure",
            version=2,
            vocabulary="orbital-v1",
            rack_markers="DICT_4X4_50",
            source_path="procedures/test.yaml",
            yaml_content="procedure: test v2",
            step_count=1,
            steps=[
                {
                    "step_id": "s1",
                    "ordinal": 0,
                    "name": "Only Step",
                    "voice_prompt": "do it",
                    "preconditions": [],
                    "requires": [],
                },
            ],
        )
        row = store.conn.execute("SELECT * FROM procedures WHERE id = 'test_proc'").fetchone()
        assert dict(row)["name"] == "Updated Procedure"
        steps = store.conn.execute(
            "SELECT * FROM procedure_steps WHERE procedure_id = 'test_proc'"
        ).fetchall()
        assert len(steps) == 1


class TestSessionLifecycle:
    def test_create_and_get_session(self, store: Store) -> None:
        store.create_session(
            session_id="sess-01",
            procedure_id="test_proc",
            procedure_version=1,
            session_dir="data/sessions/sess-01",
            steps_total=3,
        )
        sess = store.get_session("sess-01")
        assert sess is not None
        assert sess["status"] == "running"
        assert sess["steps_total"] == 3

    def test_close_session(self, store: Store) -> None:
        store.create_session(
            session_id="sess-02",
            procedure_id="test_proc",
            procedure_version=1,
            session_dir="data/sessions/sess-02",
        )
        store.close_session(
            "sess-02",
            status="complete",
            steps_complete=3,
            duration_ms=45000,
        )
        sess = store.get_session("sess-02")
        assert sess is not None
        assert sess["status"] == "complete"
        assert sess["ended_at"] is not None
        assert sess["duration_ms"] == 45000

    def test_mark_crashed(self, store: Store) -> None:
        store.create_session(
            session_id="sess-crash",
            procedure_id="test_proc",
            procedure_version=1,
            session_dir="data/sessions/sess-crash",
        )
        store.mark_crashed("sess-crash")
        sess = store.get_session("sess-crash")
        assert sess is not None
        assert sess["status"] == "crashed"
        assert sess["crash_recovered"] == 1

    def test_list_sessions(self, store: Store) -> None:
        for i in range(3):
            store.create_session(
                session_id=f"sess-list-{i}",
                procedure_id="test_proc",
                procedure_version=1,
                session_dir=f"data/sessions/sess-list-{i}",
            )
        result = store.list_sessions(limit=2)
        assert len(result) == 2

    def test_get_nonexistent_session(self, store: Store) -> None:
        assert store.get_session("does-not-exist") is None


class TestStepRuns:
    def _make_session(self, store: Store, session_id: str = "sess-steps") -> None:
        store.create_session(
            session_id=session_id,
            procedure_id="test_proc",
            procedure_version=1,
            session_dir=f"data/sessions/{session_id}",
        )

    def test_upsert_step_run(self, store: Store) -> None:
        self._make_session(store)
        store.upsert_step_run(
            session_id="sess-steps",
            step_id="s1",
            ordinal=0,
            state="active",
            activated_at="2026-09-20T13:00:00Z",
        )
        runs = store.get_step_runs("sess-steps")
        assert len(runs) == 1
        assert runs[0]["state"] == "active"

    def test_upsert_updates_on_conflict(self, store: Store) -> None:
        self._make_session(store)
        store.upsert_step_run(
            session_id="sess-steps",
            step_id="s1",
            ordinal=0,
            state="active",
            activated_at="2026-09-20T13:00:00Z",
        )
        store.upsert_step_run(
            session_id="sess-steps",
            step_id="s1",
            ordinal=0,
            state="complete",
            confidence=0.91,
            resolved_at="2026-09-20T13:00:15Z",
            duration_ms=15000,
            evidence=["detect:red_box_open=ok@0.91"],
        )
        runs = store.get_step_runs("sess-steps")
        assert len(runs) == 1
        assert runs[0]["state"] == "complete"
        assert runs[0]["confidence"] == 0.91
        # activated_at should be preserved from the original insert.
        assert runs[0]["activated_at"] == "2026-09-20T13:00:00Z"


class TestAlerts:
    def _make_session(self, store: Store, session_id: str = "sess-alerts") -> None:
        store.create_session(
            session_id=session_id,
            procedure_id="test_proc",
            procedure_version=1,
            session_dir=f"data/sessions/{session_id}",
        )

    def test_insert_and_get_alerts(self, store: Store) -> None:
        self._make_session(store)
        aid = store.insert_alert(
            session_id="sess-alerts",
            bus_seq=42,
            kind="skip",
            severity="high",
            message="Step skipped: Step 2",
            step_id="s2",
        )
        assert aid is not None
        alerts = store.get_alerts("sess-alerts")
        assert len(alerts) == 1
        assert alerts[0]["kind"] == "skip"

    def test_acknowledge_alert(self, store: Store) -> None:
        self._make_session(store)
        aid = store.insert_alert(
            session_id="sess-alerts",
            bus_seq=10,
            kind="stall",
            severity="medium",
            message="No progress",
            step_id="s1",
        )
        store.acknowledge_alert(aid)
        alerts = store.get_alerts("sess-alerts")
        assert alerts[0]["acknowledged_at"] is not None


class TestTelemetryChain:
    def _make_session(self, store: Store, session_id: str = "sess-telem") -> None:
        store.create_session(
            session_id=session_id,
            procedure_id="test_proc",
            procedure_version=1,
            session_dir=f"data/sessions/{session_id}",
        )

    def test_upsert_and_get(self, store: Store) -> None:
        self._make_session(store)
        store.upsert_telemetry_chain(
            session_id="sess-telem",
            genesis_hash="0" * 64,
            last_seq=5,
            last_hash="abc123",
            record_count=5,
            bytes_written=1024,
        )
        chain = store.get_telemetry_chain("sess-telem")
        assert chain is not None
        assert chain["record_count"] == 5

    def test_set_verification_result(self, store: Store) -> None:
        self._make_session(store)
        store.upsert_telemetry_chain(
            session_id="sess-telem",
            genesis_hash="0" * 64,
            last_seq=5,
            last_hash="abc123",
            record_count=5,
            bytes_written=1024,
        )
        store.set_verification_result("sess-telem", verified_ok=True)
        chain = store.get_telemetry_chain("sess-telem")
        assert chain is not None
        assert chain["verified_ok"] == 1
        assert chain["first_bad_seq"] is None
