"""Tests for runtime.migrations - schema runner."""

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

    def test_wrong_object_alerts_are_storable_and_old_alerts_survive(
        self, tmp_db: Path, tmp_path: Path, migrations_dir: Path
    ) -> None:
        # A database from before 002: an alert is already on record.
        only_first = tmp_path / "m1"
        only_first.mkdir()
        (only_first / "001_initial.sql").write_text(
            (migrations_dir / "001_initial.sql").read_text(encoding="utf-8"), encoding="utf-8"
        )
        conn = migrate(tmp_db, migrations_dir=only_first)
        conn.execute(
            "INSERT INTO procedures (id, name, version, vocabulary, rack_markers, yaml_sha256, "
            "source_path, step_count, created_at) VALUES ('p', 'P', 1, 'v', 'd', 'x', 'p', 1, 't')"
        )
        conn.execute(
            "INSERT INTO sessions (id, procedure_id, procedure_version, mode, started_at, "
            "status, session_dir) VALUES ('s', 'p', 1, 'live', 't', 'running', 'd')"
        )
        conn.execute(
            "INSERT INTO alerts (session_id, bus_seq, kind, severity, message, raised_at) "
            "VALUES ('s', 1, 'skip', 'high', 'Step skipped', 't')"
        )
        conn.commit()
        conn.close()

        conn = migrate(tmp_db, migrations_dir=migrations_dir)
        for seq, kind in ((2, "wrong_object"), (3, "wrong_hand")):
            conn.execute(
                "INSERT INTO alerts (session_id, bus_seq, kind, severity, message, raised_at) "
                f"VALUES ('s', {seq}, '{kind}', 'high', 'x', 't')"
            )
        kinds = [r[0] for r in conn.execute("SELECT kind FROM alerts ORDER BY bus_seq")]
        assert kinds == ["skip", "wrong_object", "wrong_hand"]
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 3
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
