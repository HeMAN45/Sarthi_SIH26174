"""Tests for runtime.migrations — schema runner."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from orbital_har.runtime.migrations import migrate


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def migrations_dir() -> Path:
    """The real migrations directory."""
    return Path(__file__).resolve().parents[1] / "migrations"


class TestMigrate:
    def test_applies_initial_migration(self, tmp_db: Path, migrations_dir: Path) -> None:
        conn = migrate(tmp_db, migrations_dir=migrations_dir)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "sessions" in tables
        assert "step_runs" in tables
        assert "alerts" in tables
        assert "telemetry_chain" in tables
        assert "schema_version" in tables
        conn.close()

    def test_idempotent(self, tmp_db: Path, migrations_dir: Path) -> None:
        conn1 = migrate(tmp_db, migrations_dir=migrations_dir)
        version1 = conn1.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn1.close()

        conn2 = migrate(tmp_db, migrations_dir=migrations_dir)
        version2 = conn2.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn2.close()

        assert version1 == version2

    def test_wal_mode_enabled(self, tmp_db: Path, migrations_dir: Path) -> None:
        conn = migrate(tmp_db, migrations_dir=migrations_dir)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, tmp_db: Path, migrations_dir: Path) -> None:
        conn = migrate(tmp_db, migrations_dir=migrations_dir)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()

    def test_empty_migrations_dir(self, tmp_db: Path, tmp_path: Path) -> None:
        empty = tmp_path / "empty_migrations"
        empty.mkdir()
        conn = migrate(tmp_db, migrations_dir=empty)
        # Should succeed with no tables applied beyond what the runner needs.
        conn.close()

    def test_incremental_migration(self, tmp_db: Path, tmp_path: Path) -> None:
        """Simulate applying two separate migration files."""
        mdir = tmp_path / "migrations"
        mdir.mkdir()
        (mdir / "001_base.sql").write_text(
            textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL, applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_one (id INTEGER PRIMARY KEY);
        """)
        )
        conn = migrate(tmp_db, migrations_dir=mdir)
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 1
        conn.close()

        (mdir / "002_add_col.sql").write_text(
            "CREATE TABLE IF NOT EXISTS test_two (id INTEGER PRIMARY KEY);"
        )
        conn = migrate(tmp_db, migrations_dir=mdir)
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "test_two" in tables
        conn.close()
