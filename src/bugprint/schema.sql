-- BugPrint SQLite schema.
-- WAL mode is set at connection time (not here).

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,              -- uuid4
  started_at TEXT NOT NULL,
  ended_at TEXT,
  project_path TEXT,
  git_commit TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('test_failed','test_passed','idle_gap','focus_lost')),
  test_name TEXT,
  result TEXT,                      -- 'failed' | 'passed' | NULL
  error_text TEXT,                  -- trimmed to 2000 chars, secrets redacted
  files_changed TEXT,               -- JSON list from git (may be empty)
  duration_ms INTEGER,
  gap_seconds INTEGER,              -- for idle_gap
  status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processed','skipped','error'))
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  question_id TEXT NOT NULL,        -- 'next_tool' | 'failure_kind' | 'fix_match'
  options TEXT NOT NULL,            -- JSON list offered to Decider
  chosen TEXT NOT NULL,             -- what Decider picked
  probs TEXT NOT NULL,              -- JSON map option -> probability
  confidence REAL NOT NULL,
  threshold REAL,                   -- threshold applied
  final_action TEXT NOT NULL,       -- what Bugprint actually did
  overridden_reason TEXT,           -- NULL | 'low_confidence' | 'incompatible_with_event' | 'no_open_defects'
  latency_ms REAL,
  is_fallback INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS defects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  test_name TEXT NOT NULL,
  summary TEXT NOT NULL,            -- first line of the error, trimmed
  failure_kind TEXT,                -- real_defect | flaky_test | environment_problem | unknown
  status TEXT NOT NULL CHECK (status IN ('open','fixed','dismissed')),
  confirmed_by_user INTEGER NOT NULL DEFAULT 0,
  opened_event_id INTEGER NOT NULL REFERENCES events(id),
  fixed_event_id INTEGER REFERENCES events(id),
  opened_at TEXT NOT NULL,
  fixed_at TEXT
);

CREATE TABLE IF NOT EXISTS confirmations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  proposed_action TEXT NOT NULL,    -- what Decider leaned toward
  proposed_args TEXT,               -- JSON, e.g. defect id candidate
  reason TEXT NOT NULL,             -- plain text shown to the developer
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS interruptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  started_at TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  model_id TEXT NOT NULL,
  input_stats TEXT NOT NULL,        -- JSON numbers computed in Python
  summary TEXT NOT NULL,
  suggestion TEXT NOT NULL,
  claims TEXT NOT NULL,             -- JSON list of {text, event_ids}
  validated INTEGER NOT NULL        -- 1 if every claim passed the validator
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_decisions_event ON decisions(event_id);
CREATE INDEX IF NOT EXISTS idx_defects_session ON defects(session_id);
CREATE INDEX IF NOT EXISTS idx_defects_status ON defects(status);
CREATE INDEX IF NOT EXISTS idx_confirmations_event ON confirmations(event_id);
CREATE INDEX IF NOT EXISTS idx_confirmations_status ON confirmations(status);
