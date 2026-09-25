"""Schema migration runner.

Applies numbered SQL files from the ``migrations/`` directory in order.
No ORM, no Alembic — the schema is small enough that hand-written SQL is
clearer (docs/05-BACKEND-SCHEMA.md §7).

Forward-only. Breaking a migration during development is resolved by deleting
``orbital.db`` and replaying sessions; session folders are the durable artifact,
not the database.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

_MIGRATION_RE = re.compile(r"^(\d{3})_.*\.sql$")


def _discover(directory: Path) -> list[tuple[int, Path]]:
    """Return ``(version, path)`` pairs sorted by version."""
    found: list[tuple[int, Path]] = []
    if not directory.is_dir():
        return found
    for p in sorted(directory.iterdir()):
        m = _MIGRATION_RE.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    return found


def _current_version(conn: sqlite3.Connection) -> int:
    """Return the latest applied migration version, or 0 if none."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0 if row else 0
    except sqlite3.OperationalError:
        return 0


def migrate(db_path: str | Path, *, migrations_dir: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the database and apply any pending migrations.

    Returns the open connection with WAL mode and foreign keys enabled.
    """
    migrations_dir = migrations_dir or MIGRATIONS_DIR
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    current = _current_version(conn)
    for version, path in _discover(migrations_dir):
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(UTC).isoformat()),
        )
        conn.commit()

    return conn
