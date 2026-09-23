"""Tests for runtime.telemetry — hash-chained writer and verifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orbital_har.runtime.telemetry import GENESIS_HASH, TelemetryWriter, verify


@pytest.fixture()
def telem_path(tmp_path: Path) -> Path:
    return tmp_path / "telemetry.jsonl"


class TestTelemetryWriter:
    def test_writes_records(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("session_start", {"procedure": "proc_a"})
            tw.write("step_active", {"step": "s1", "ord": 0})
            tw.write("step_complete", {"step": "s1", "conf": 0.91})

        lines = telem_path.read_text().strip().split("\n")
        assert len(lines) == 3
        first = json.loads(lines[0])
        assert first["seq"] == 1
        assert first["ev"] == "session_start"
        assert "hash" in first
        assert "prev" in first

    def test_genesis_hash(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("session_start", {"procedure": "proc_a"})
            tw.flush()
            record = json.loads(telem_path.read_text().strip())
            assert record["prev"] == GENESIS_HASH

    def test_chain_links(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("event1", {})
            tw.write("event2", {})

        lines = telem_path.read_text().strip().split("\n")
        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        assert len(r1["hash"]) == 64
        assert len(r2["prev"]) == 64
        assert r2["prev"] == r1["hash"]

    def test_chain_summary(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("a", {})
            tw.write("b", {})
            summary = tw.chain_summary()
            assert summary["record_count"] == 2
            assert summary["last_seq"] == 2
            assert summary["genesis_hash"] == GENESIS_HASH
            assert summary["bytes_written"] > 0

    def test_context_manager_closes(self, telem_path: Path) -> None:
        tw = TelemetryWriter(telem_path)
        with tw:
            tw.write("event", {})
        assert tw._fh.closed  # type: ignore[union-attr]

    def test_write_without_open_raises(self, telem_path: Path) -> None:
        tw = TelemetryWriter(telem_path)
        with pytest.raises(RuntimeError, match="not open"):
            tw.write("event", {})

    def test_restarts_from_existing_file(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            r1 = tw.write("event1", {})

        # Reopen
        with TelemetryWriter(telem_path) as tw2:
            assert tw2.last_seq == 1
            assert tw2.last_hash == r1["hash"]
            r2 = tw2.write("event2", {})

        assert r2["seq"] == 2
        assert r2["prev"] == r1["hash"]

    def test_restarts_bytes_written(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("event1", {})
            tw.write("event2", {})
        
        file_size = telem_path.stat().st_size
        assert file_size > 0
        
        with TelemetryWriter(telem_path) as tw2:
            assert tw2.bytes_written == file_size

    def test_restarts_corrupted_chain(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("event1", {})
            tw.write("event2", {})
        
        # Corrupt the file
        lines = telem_path.read_text().strip().split("\n")
        record = json.loads(lines[1])
        record["ev"] = "tampered"
        lines[1] = json.dumps(record)
        telem_path.write_text("\n".join(lines) + "\n")
        
        with (
            pytest.raises(RuntimeError, match="existing telemetry chain is corrupted"),
            TelemetryWriter(telem_path),
        ):
            pass


class TestVerifier:
    def test_verifies_intact_chain(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("session_start", {"procedure": "proc_a"})
            tw.write("step_complete", {"step": "s1", "conf": 0.91})
            tw.write("session_end", {"status": "complete"})

        result = verify(telem_path)
        assert result.ok is True
        assert result.record_count == 3
        assert result.first_bad_seq is None

    def test_detects_tampered_record(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("session_start", {"procedure": "proc_a"})
            tw.write("step_complete", {"step": "s1", "conf": 0.91})
            tw.write("session_end", {"status": "complete"})

        # Tamper with the second record.
        lines = telem_path.read_text().strip().split("\n")
        record = json.loads(lines[1])
        record["conf"] = 0.50  # Modify data
        lines[1] = json.dumps(record, separators=(",", ":"))
        telem_path.write_text("\n".join(lines) + "\n")

        result = verify(telem_path)
        assert result.ok is False
        assert result.first_bad_seq == 2

    def test_detects_deleted_record(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("event1", {})
            tw.write("event2", {})
            tw.write("event3", {})

        # Delete the middle record.
        lines = telem_path.read_text().strip().split("\n")
        telem_path.write_text(lines[0] + "\n" + lines[2] + "\n")

        result = verify(telem_path)
        assert result.ok is False

    def test_missing_file(self, tmp_path: Path) -> None:
        result = verify(tmp_path / "nonexistent.jsonl")
        assert result.ok is False
        assert "not found" in (result.error or "")

    def test_empty_file(self, telem_path: Path) -> None:
        telem_path.write_text("")
        result = verify(telem_path)
        assert result.ok is True
        assert result.record_count == 0

    def test_single_record(self, telem_path: Path) -> None:
        with TelemetryWriter(telem_path) as tw:
            tw.write("session_start", {"procedure": "test"})
        result = verify(telem_path)
        assert result.ok is True
        assert result.record_count == 1

    def test_malformed_json(self, telem_path: Path) -> None:
        telem_path.write_text("{this is not json}\n")
        result = verify(telem_path)
        assert result.ok is False
        assert "invalid JSON" in (result.error or "")

    def test_large_chain(self, telem_path: Path) -> None:
        """Verify a chain of 100 records stays intact."""
        with TelemetryWriter(telem_path) as tw:
            for i in range(100):
                tw.write(f"event_{i}", {"n": i})
        result = verify(telem_path)
        assert result.ok is True
        assert result.record_count == 100
