"""Hash-chained telemetry writer and verifier.

The telemetry file is the deliverable artifact — structured, append-only, and
tamper-evident.  One record per *state transition*, never per frame.

Chain algorithm (docs/05-BACKEND-SCHEMA.md §4.3):

    hash_n = SHA256( prev_hash || canonical_json(record minus "hash") )
    prev_hash_0 = "0" x 64

Canonical JSON: sorted keys, no whitespace, UTF-8.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def _canonical(record: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, no whitespace, UTF-8."""
    stripped = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _chain_hash(prev_hash: str, record: dict[str, Any]) -> str:
    payload = prev_hash + _canonical(record)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class TelemetryWriter:
    """Append-only hash-chained JSONL writer.

    Usage::

        with TelemetryWriter(path) as tw:
            tw.write("step_complete", {"step": "s1", "conf": 0.91, ...})
            tw.write("session_end", {...})

    The writer tracks chain state for the Store's ``telemetry_chain`` row.
    """

    path: Path
    _fh: Any = field(default=None, init=False, repr=False)
    _prev_hash: str = field(default=GENESIS_HASH, init=False)
    _seq: int = field(default=0, init=False)
    _bytes_written: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def open(self) -> TelemetryWriter:
        if self.path.exists() and self.path.stat().st_size > 0:
            from orbital_har.runtime.telemetry import verify

            res = verify(self.path)
            if not res.ok:
                raise RuntimeError(
                    f"Cannot append: existing telemetry chain is corrupted: {res.error}"
                )

            self._bytes_written = self.path.stat().st_size

            with self.path.open("r", encoding="utf-8") as f:
                last_line = ""
                for line in f:
                    if line.strip():
                        last_line = line.strip()
                if last_line:
                    try:
                        record = json.loads(last_line)
                        self._seq = record.get("seq", 0)
                        self._prev_hash = record.get("hash", GENESIS_HASH)
                    except json.JSONDecodeError:
                        pass
        self._fh = self.path.open("a", encoding="utf-8")
        return self

    def write(self, event: str, data: dict[str, Any], *, t: str | None = None) -> dict[str, Any]:
        """Append one record and return it (with its hash)."""
        if self._fh is None:
            raise RuntimeError("TelemetryWriter is not open; use open() or a context manager")

        self._seq += 1
        record: dict[str, Any] = {"seq": self._seq, "ev": event}
        if t is not None:
            record["t"] = t
        else:
            record["t"] = datetime.now(UTC).isoformat()
        record.update(data)
        record["prev"] = self._prev_hash

        h = _chain_hash(self._prev_hash, record)
        record["hash"] = h
        self._prev_hash = h

        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        self._fh.write(line)
        self._bytes_written += len(line.encode("utf-8"))
        return record

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None and not self._fh.closed:
            self._fh.flush()
            self._fh.close()

    @property
    def genesis_hash(self) -> str:
        return GENESIS_HASH

    @property
    def last_hash(self) -> str:
        return self._prev_hash

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def record_count(self) -> int:
        return self._seq

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    def chain_summary(self) -> dict[str, Any]:
        """Summary for the Store's ``telemetry_chain`` row."""
        return {
            "genesis_hash": self.genesis_hash,
            "last_seq": self.last_seq,
            "last_hash": self.last_hash,
            "record_count": self.record_count,
            "bytes_written": self.bytes_written,
        }

    def __enter__(self) -> TelemetryWriter:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------
# Verifier
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    record_count: int
    first_bad_seq: int | None = None
    error: str | None = None


def verify(path: str | Path) -> VerifyResult:
    """Re-walk a telemetry file and validate every link in the hash chain.

    Returns a result indicating whether the chain is intact and, if not, the
    first sequence number that failed verification.
    """
    p = Path(path)
    if not p.exists():
        return VerifyResult(ok=False, record_count=0, error=f"file not found: {p}")

    prev_hash = GENESIS_HASH
    count = 0

    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                return VerifyResult(
                    ok=False,
                    record_count=count,
                    first_bad_seq=count + 1,
                    error=f"line {lineno}: invalid JSON: {exc}",
                )

            stored_hash = record.get("hash", "")
            expected = _chain_hash(prev_hash, record)

            if expected != stored_hash:
                return VerifyResult(
                    ok=False,
                    record_count=count,
                    first_bad_seq=record.get("seq", count + 1),
                    error=(
                        f"line {lineno}: hash mismatch at seq {record.get('seq')}: "
                        f"stored {stored_hash!r}, expected {expected!r}"
                    ),
                )

            prev_hash = expected
            count += 1

    return VerifyResult(ok=True, record_count=count)
