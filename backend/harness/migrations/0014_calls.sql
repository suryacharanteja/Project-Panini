-- V2-16: caller verification, bounded call history and delegation replay protection.
CREATE TABLE callers(number TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
  persona_id TEXT NOT NULL DEFAULT 'panini', salt TEXT, pin_hash TEXT, enabled INTEGER NOT NULL DEFAULT 1,
  created REAL NOT NULL);
CREATE TABLE calls(id TEXT PRIMARY KEY, number TEXT NOT NULL, persona_id TEXT NOT NULL,
  state TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, started REAL NOT NULL, ended REAL,
  duration_s REAL NOT NULL DEFAULT 0, thread_id TEXT, transcript_artifact_id TEXT, receipt_id TEXT,
  reason TEXT NOT NULL DEFAULT '', usage TEXT);
CREATE TABLE call_requests(call_id TEXT NOT NULL, delegation_id TEXT NOT NULL,
  state TEXT NOT NULL, result TEXT, PRIMARY KEY(call_id,delegation_id));
CREATE INDEX calls_started ON calls(started);
