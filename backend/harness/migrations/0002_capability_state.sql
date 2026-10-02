-- V2-02: per-capability enablement (Admin → Capabilities) and the last readiness seen.
CREATE TABLE IF NOT EXISTS capability_state(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
  updated REAL NOT NULL, readiness TEXT);
