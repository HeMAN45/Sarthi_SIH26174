-- 001_initial.sql — ORBITAL-HAR schema
-- Source of truth: docs/05-BACKEND-SCHEMA.md §3
-- Applied once by runtime.migrations; tracked in schema_version.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL
);

-- ---------------------------------------------------------------- procedures

CREATE TABLE IF NOT EXISTS procedures (
    id           TEXT PRIMARY KEY,
    name         TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    vocabulary   TEXT    NOT NULL,
    rack_markers TEXT    NOT NULL,
    yaml_sha256  TEXT    NOT NULL,
    source_path  TEXT    NOT NULL,
    step_count   INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    UNIQUE (id, version)
);

CREATE TABLE IF NOT EXISTS procedure_steps (
    procedure_id  TEXT    NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    step_id       TEXT    NOT NULL,
    ordinal       INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    voice_prompt  TEXT    NOT NULL,
    group_id      TEXT,
    preconditions TEXT    NOT NULL,
    requires      TEXT    NOT NULL,
    any_of        TEXT,
    timeout_s     INTEGER,
    on_timeout    TEXT CHECK (on_timeout IN ('stall','skip','ignore')),
    PRIMARY KEY (procedure_id, step_id)
);

-- ------------------------------------------------------------------ sessions

CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    procedure_id     TEXT NOT NULL REFERENCES procedures(id),
    procedure_version INTEGER NOT NULL,
    detect_model_id  INTEGER REFERENCES models(id),
    mode             TEXT NOT NULL CHECK (mode IN ('live','replay')),
    replay_of        TEXT REFERENCES sessions(id),
    status           TEXT NOT NULL CHECK (status IN
                        ('running','complete','aborted','crashed')),
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    duration_ms      INTEGER,
    rack_locked      INTEGER NOT NULL DEFAULT 0,
    operator_label   TEXT,
    device           TEXT,
    crash_recovered  INTEGER NOT NULL DEFAULT 0,
    steps_total      INTEGER,
    steps_complete   INTEGER,
    steps_skipped    INTEGER,
    steps_out_of_order INTEGER,
    steps_unverified INTEGER,
    steps_overridden INTEGER,
    alert_count      INTEGER,
    notes            TEXT,
    session_dir      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status  ON sessions(status);

-- ----------------------------------------------------------------- step runs

CREATE TABLE IF NOT EXISTS step_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    step_id        TEXT NOT NULL,
    ordinal        INTEGER NOT NULL,
    state          TEXT NOT NULL CHECK (state IN
                     ('pending','active','complete','skipped',
                      'out_of_order','unverified','overridden','stalled')),
    activated_at   TEXT,
    resolved_at    TEXT,
    duration_ms    INTEGER,
    confidence     REAL,
    evidence       TEXT,
    reason         TEXT,
    override_actor TEXT,
    expected_step  TEXT,
    UNIQUE (session_id, step_id)
);

CREATE INDEX IF NOT EXISTS idx_step_runs_session ON step_runs(session_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_step_runs_state   ON step_runs(state);

-- -------------------------------------------------------------------- alerts

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    bus_seq         INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN
                      ('skip','out_of_order','stall','unverified',
                       'free_float','degraded')),
    severity        TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    step_id         TEXT,
    expected_step_id TEXT,
    message         TEXT NOT NULL,
    raised_at       TEXT NOT NULL,
    acknowledged_at TEXT,
    auto_resolved   INTEGER NOT NULL DEFAULT 0,
    spoken          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_session ON alerts(session_id, raised_at);

-- ----------------------------------------------------------------- artifacts

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN
                 ('telemetry','event_stream','video_segment',
                  'config_snapshot','procedure_snapshot')),
    path       TEXT NOT NULL,
    bytes      INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id, kind);

-- ----------------------------------------------------------- telemetry chain

CREATE TABLE IF NOT EXISTS telemetry_chain (
    session_id     TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    genesis_hash   TEXT NOT NULL,
    last_seq       INTEGER NOT NULL,
    last_hash      TEXT NOT NULL,
    record_count   INTEGER NOT NULL,
    bytes_written  INTEGER NOT NULL,
    raw_video_equiv_bytes INTEGER,
    verified_at    TEXT,
    verified_ok    INTEGER,
    first_bad_seq  INTEGER
);

-- -------------------------------------------------------------------- models

CREATE TABLE IF NOT EXISTS models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    task        TEXT NOT NULL CHECK (task IN ('detect','pose','hands')),
    version     TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    classes     TEXT,
    metrics     TEXT,
    trained_at  TEXT,
    dataset_tag TEXT,
    notes       TEXT,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS calibrations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    temperature  REAL NOT NULL,
    tau_complete REAL NOT NULL,
    tau_abstain  REAL NOT NULL,
    dataset_hash TEXT NOT NULL,
    ece          REAL,
    fitted_at    TEXT NOT NULL
);

-- ------------------------------------------------------------ health samples

CREATE TABLE IF NOT EXISTS metrics_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    t           TEXT NOT NULL,
    fps         REAL,
    latency_ms  REAL,
    mem_mb      REAL,
    gpu_util    REAL,
    power_w     REAL,
    dropped_frames INTEGER,
    degraded_level INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_metrics_session ON metrics_samples(session_id, t);

-- --------------------------------------------------------- config snapshots

CREATE TABLE IF NOT EXISTS config_snapshots (
    session_id  TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    runtime_yaml TEXT NOT NULL,
    procedure_yaml TEXT NOT NULL,
    git_sha     TEXT,
    created_at  TEXT NOT NULL
);
