CREATE TABLE presence_signals (
  source TEXT PRIMARY KEY, data TEXT NOT NULL, seen REAL NOT NULL, boot TEXT NOT NULL
);
CREATE TABLE presence_settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
CREATE TABLE continuity_items (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, thread_id TEXT,
  created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE continuity_deliveries (
  item_id TEXT NOT NULL, route TEXT NOT NULL, due REAL NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued', detail TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(item_id,route)
);
CREATE INDEX continuity_due ON continuity_deliveries(state,due);
CREATE TABLE continuity_commands (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, item_id TEXT,
  data TEXT NOT NULL, expires REAL NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
);
