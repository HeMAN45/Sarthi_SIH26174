"""SQLite persistence layer.

Stores session metadata, step outcomes, alerts and artifacts — the relational
tier described in docs/05-BACKEND-SCHEMA.md §1.

Never on the per-frame path (invariant #7). Written on state transitions, health
timers and session lifecycle events only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orbital_har.runtime.migrations import migrate

# Re-export for convenience.
__all__ = ["Store"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    """Thin wrapper around SQLite for session lifecycle writes.

    The public API mirrors the writes in docs/03-APP-FLOW.md §3: create a
    session, advance step states, raise alerts, and close the session.
    """

    def __init__(self, db_path: str | Path, *, migrations_dir: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.conn = migrate(self.db_path, migrations_dir=migrations_dir)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- procedures

    def upsert_procedure(
        self,
        *,
        id: str,
        name: str,
        version: int,
        vocabulary: str,
        rack_markers: str,
        source_path: str,
        yaml_content: str,
        step_count: int,
        steps: list[dict[str, Any]],
    ) -> None:
        """Insert or replace a procedure and its steps.

        ``yaml_content`` is hashed for integrity tracking (``yaml_sha256``).
        """
        sha = hashlib.sha256(yaml_content.encode()).hexdigest()
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO procedures
                   (id, name, version, vocabulary, rack_markers, yaml_sha256,
                    source_path, step_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, name, version, vocabulary, rack_markers, sha, source_path, step_count, _now()),
            )
            self.conn.execute("DELETE FROM procedure_steps WHERE procedure_id = ?", (id,))
            for s in steps:
                self.conn.execute(
                    """INSERT INTO procedure_steps
                       (procedure_id, step_id, ordinal, name, voice_prompt,
                        group_id, preconditions, requires, any_of,
                        timeout_s, on_timeout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        id,
                        s["step_id"],
                        s["ordinal"],
                        s["name"],
                        s["voice_prompt"],
                        s.get("group_id"),
                        json.dumps(s.get("preconditions", [])),
                        json.dumps(s.get("requires", [])),
                        json.dumps(s["any_of"]) if s.get("any_of") else None,
                        s.get("timeout_s"),
                        s.get("on_timeout"),
                    ),
                )

    # ---------------------------------------------------------------- sessions

    def create_session(
        self,
        *,
        session_id: str,
        procedure_id: str,
        procedure_version: int,
        mode: str = "live",
        session_dir: str,
        device: str | None = None,
        rack_locked: bool = False,
        steps_total: int = 0,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO sessions
                   (id, procedure_id, procedure_version, mode, status,
                    started_at, rack_locked, device, session_dir, steps_total)
                   VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    procedure_id,
                    procedure_version,
                    mode,
                    _now(),
                    int(rack_locked),
                    device,
                    session_dir,
                    steps_total,
                ),
            )

    def close_session(
        self,
        session_id: str,
        *,
        status: str,
        steps_complete: int = 0,
        steps_skipped: int = 0,
        steps_out_of_order: int = 0,
        steps_unverified: int = 0,
        steps_overridden: int = 0,
        alert_count: int = 0,
        duration_ms: int | None = None,
        notes: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE sessions SET
                     status = ?, ended_at = ?, duration_ms = ?,
                     steps_complete = ?, steps_skipped = ?,
                     steps_out_of_order = ?, steps_unverified = ?,
                     steps_overridden = ?, alert_count = ?, notes = ?
                   WHERE id = ?""",
                (
                    status,
                    _now(),
                    duration_ms,
                    steps_complete,
                    steps_skipped,
                    steps_out_of_order,
                    steps_unverified,
                    steps_overridden,
                    alert_count,
                    notes,
                    session_id,
                ),
            )

    def mark_crashed(self, session_id: str) -> None:
        """Mark an open session as crashed and recoverable."""
        with self.conn:
            self.conn.execute(
                """UPDATE sessions SET status = 'crashed',
                     ended_at = ?, crash_recovered = 1
                   WHERE id = ? AND status = 'running'""",
                (_now(), session_id),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- step runs

    def upsert_step_run(
        self,
        *,
        session_id: str,
        step_id: str,
        ordinal: int,
        state: str,
        activated_at: str | None = None,
        resolved_at: str | None = None,
        duration_ms: int | None = None,
        confidence: float | None = None,
        evidence: list[str] | None = None,
        reason: str | None = None,
        override_actor: str | None = None,
        expected_step: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO step_runs
                   (session_id, step_id, ordinal, state, activated_at,
                    resolved_at, duration_ms, confidence, evidence, reason,
                    override_actor, expected_step)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (session_id, step_id)
                   DO UPDATE SET
                     state = excluded.state,
                     activated_at = COALESCE(excluded.activated_at, step_runs.activated_at),
                     resolved_at = excluded.resolved_at,
                     duration_ms = excluded.duration_ms,
                     confidence = excluded.confidence,
                     evidence = excluded.evidence,
                     reason = excluded.reason,
                     override_actor = excluded.override_actor,
                     expected_step = excluded.expected_step""",
                (
                    session_id,
                    step_id,
                    ordinal,
                    state,
                    activated_at,
                    resolved_at,
                    duration_ms,
                    confidence,
                    json.dumps(evidence) if evidence else None,
                    reason,
                    override_actor,
                    expected_step,
                ),
            )

    def get_step_runs(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM step_runs WHERE session_id = ? ORDER BY ordinal",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- alerts

    def insert_alert(
        self,
        *,
        session_id: str,
        bus_seq: int,
        kind: str,
        severity: str,
        message: str,
        step_id: str | None = None,
        expected_step_id: str | None = None,
    ) -> int:
        """Insert an alert and return its id."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO alerts
                   (session_id, bus_seq, kind, severity, step_id,
                    expected_step_id, message, raised_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, bus_seq, kind, severity, step_id, expected_step_id, message, _now()),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def acknowledge_alert(self, alert_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE alerts SET acknowledged_at = ? WHERE id = ?",
                (_now(), alert_id),
            )

    def get_alerts(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE session_id = ? ORDER BY raised_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- artifacts

    def insert_artifact(
        self,
        *,
        session_id: str,
        kind: str,
        path: str,
        size_bytes: int,
        sha256: str,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO artifacts
                   (session_id, kind, path, bytes, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, kind, path, size_bytes, sha256, _now()),
            )
            return cur.lastrowid  # type: ignore[return-value]

    # --------------------------------------------------------- telemetry chain

    def upsert_telemetry_chain(
        self,
        *,
        session_id: str,
        genesis_hash: str,
        last_seq: int,
        last_hash: str,
        record_count: int,
        bytes_written: int,
        raw_video_equiv_bytes: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO telemetry_chain
                   (session_id, genesis_hash, last_seq, last_hash,
                    record_count, bytes_written, raw_video_equiv_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    genesis_hash,
                    last_seq,
                    last_hash,
                    record_count,
                    bytes_written,
                    raw_video_equiv_bytes,
                ),
            )

    def set_verification_result(
        self,
        session_id: str,
        *,
        verified_ok: bool,
        first_bad_seq: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE telemetry_chain SET
                     verified_at = ?, verified_ok = ?, first_bad_seq = ?
                   WHERE session_id = ?""",
                (_now(), int(verified_ok), first_bad_seq, session_id),
            )

    def get_telemetry_chain(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM telemetry_chain WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------- health metrics

    def insert_metrics_sample(
        self,
        *,
        session_id: str,
        fps: float | None = None,
        latency_ms: float | None = None,
        mem_mb: float | None = None,
        gpu_util: float | None = None,
        power_w: float | None = None,
        dropped_frames: int | None = None,
        degraded_level: int = 0,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO metrics_samples
                   (session_id, t, fps, latency_ms, mem_mb, gpu_util,
                    power_w, dropped_frames, degraded_level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    _now(),
                    fps,
                    latency_ms,
                    mem_mb,
                    gpu_util,
                    power_w,
                    dropped_frames,
                    degraded_level,
                ),
            )

    # ------------------------------------------------------------ model registry

    def upsert_model(
        self,
        *,
        name: str,
        task: str,
        version: str,
        file_path: str,
        classes: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        trained_at: str | None = None,
        dataset_tag: str | None = None,
        notes: str | None = None,
    ) -> int:
        """Register a trained model and return its id.

        ``file_sha256`` is computed from the weights so a model referenced by a
        session can be proven to be the one that actually ran.
        """
        path = Path(file_path)
        sha = ""
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            sha = digest.hexdigest()

        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO models
                   (name, task, version, file_path, file_sha256, classes,
                    metrics, trained_at, dataset_tag, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    task,
                    version,
                    str(file_path),
                    sha,
                    json.dumps(classes) if classes is not None else None,
                    json.dumps(metrics) if metrics is not None else None,
                    trained_at or _now(),
                    dataset_tag,
                    notes,
                ),
            )
        row = self.conn.execute(
            "SELECT id FROM models WHERE name = ? AND version = ?", (name, version)
        ).fetchone()
        return int(row["id"])

    def get_model(self, model_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
        return dict(row) if row else None

    def list_models(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM models ORDER BY trained_at DESC").fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- calibration

    def insert_calibration(
        self,
        *,
        model_id: int,
        temperature: float,
        tau_complete: float,
        tau_abstain: float,
        dataset_hash: str,
        ece: float | None = None,
    ) -> int:
        """Record a fitted calibration (D-06).

        Append-only: a refit is a new row, never an edit. The τ values a session
        ran under must stay recoverable after they are superseded.
        """
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO calibrations
                   (model_id, temperature, tau_complete, tau_abstain,
                    dataset_hash, ece, fitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (model_id, temperature, tau_complete, tau_abstain, dataset_hash, ece, _now()),
            )
        return int(cur.lastrowid)

    def latest_calibration(self, model_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM calibrations WHERE model_id = ?
               ORDER BY fitted_at DESC, id DESC LIMIT 1""",
            (model_id,),
        ).fetchone()
        return dict(row) if row else None
