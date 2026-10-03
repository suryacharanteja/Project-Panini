CREATE TABLE IF NOT EXISTS personas (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, instructions TEXT NOT NULL,
  voice TEXT, color TEXT NOT NULL, default_space_id TEXT,
  memory_scope TEXT NOT NULL DEFAULT 'persona', memory_write INTEGER NOT NULL DEFAULT 0,
  computer_id TEXT, model_tier TEXT, archived INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS persona_surfaces (
  surface TEXT PRIMARY KEY, persona_id TEXT NOT NULL REFERENCES personas(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS persona_active_names ON personas(lower(name)) WHERE archived=0;
CREATE TABLE IF NOT EXISTS persona_threads (
  thread_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL REFERENCES personas(id)
);
CREATE TABLE IF NOT EXISTS persona_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, run_id TEXT,
  persona_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL
);
