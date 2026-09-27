-- 003 - the wrong-hand alert
--
-- A step may ask for the left or the right hand; the engine alerts when the
-- other one does it (AlertKind.WRONG_HAND). As in 002, SQLite cannot alter a
-- CHECK constraint, so alerts is rebuilt with every kind and all rows copied.

CREATE TABLE alerts_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    bus_seq         INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN
                      ('skip','out_of_order','wrong_object','wrong_hand','stall',
                       'unverified','free_float','degraded')),
    severity        TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    step_id         TEXT,
    expected_step_id TEXT,
    message         TEXT NOT NULL,
    raised_at       TEXT NOT NULL,
    acknowledged_at TEXT,
    auto_resolved   INTEGER NOT NULL DEFAULT 0,
    spoken          INTEGER NOT NULL DEFAULT 0
);

INSERT INTO alerts_new
    (id, session_id, bus_seq, kind, severity, step_id, expected_step_id, message,
     raised_at, acknowledged_at, auto_resolved, spoken)
SELECT id, session_id, bus_seq, kind, severity, step_id, expected_step_id, message,
       raised_at, acknowledged_at, auto_resolved, spoken
FROM alerts;

DROP TABLE alerts;
ALTER TABLE alerts_new RENAME TO alerts;

CREATE INDEX IF NOT EXISTS idx_alerts_session ON alerts(session_id, raised_at);
