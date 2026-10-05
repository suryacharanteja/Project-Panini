-- V2-25. Proposals remain inert until an owner resolves their exact review card.
CREATE TABLE triggers (
  id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1, review_id TEXT,
  last_fired REAL, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE trigger_events (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, dedupe_key TEXT NOT NULL,
  event TEXT NOT NULL, occurred REAL NOT NULL, received REAL NOT NULL,
  UNIQUE(source,dedupe_key)
);
CREATE INDEX trigger_events_received ON trigger_events(received);
CREATE TABLE trigger_firings (
  id TEXT PRIMARY KEY, event_id TEXT, rule_id TEXT, responsibility_id TEXT,
  task_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'queued',
  created REAL NOT NULL, due REAL NOT NULL,
  UNIQUE(event_id,rule_id)
);
CREATE INDEX trigger_firings_time ON trigger_firings(created,rule_id);
CREATE INDEX trigger_firings_due ON trigger_firings(due,rule_id);
CREATE TABLE trigger_self_writes (
  path TEXT PRIMARY KEY, at REAL NOT NULL, receipt_id TEXT
);
CREATE TABLE trigger_source_bindings (
  source TEXT PRIMARY KEY, server TEXT NOT NULL, tool TEXT NOT NULL,
  arguments TEXT NOT NULL, cursor TEXT, last_polled REAL, last_attempt REAL, last_error TEXT
);
CREATE TABLE trigger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE trigger_calendar_pending (
  id TEXT PRIMARY KEY, start REAL NOT NULL, data TEXT NOT NULL, received REAL NOT NULL
);
CREATE INDEX trigger_calendar_due ON trigger_calendar_pending(start);
CREATE TABLE responsibilities (
  id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
  review_id TEXT, health TEXT NOT NULL DEFAULT '{}',
  next_check REAL, next_report REAL, report_page_id TEXT,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE responsibility_runs (
  task_id TEXT PRIMARY KEY, responsibility_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL
);
CREATE INDEX responsibility_runs_time ON responsibility_runs(responsibility_id,created);
