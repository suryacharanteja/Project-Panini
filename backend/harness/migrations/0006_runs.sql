-- V2-06: run records (one per turn or background task) with checkpoints, and surface -> thread bindings.
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, thread_id TEXT, persona_id TEXT NOT NULL DEFAULT 'panini',
  principal TEXT NOT NULL, mode TEXT NOT NULL, parent_run_id TEXT, task_id TEXT, state TEXT NOT NULL, tier TEXT,
  created REAL NOT NULL, updated REAL NOT NULL, budget TEXT NOT NULL DEFAULT '{}', usage TEXT NOT NULL DEFAULT '{}',
  checkpoint BLOB, result TEXT);
CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, created);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created);
CREATE TABLE IF NOT EXISTS thread_bindings(surface TEXT NOT NULL, external_id TEXT NOT NULL, thread_id TEXT NOT NULL,
  created REAL NOT NULL, PRIMARY KEY(surface, external_id));
ALTER TABLE sessions ADD COLUMN persona_id TEXT NOT NULL DEFAULT 'panini';
ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'avatar';
