-- V2-11: sandbox computers (one per persona) and their permission switches; actions are receipts in the ledger.
CREATE TABLE IF NOT EXISTS sandbox_computers(id TEXT PRIMARY KEY, persona_id TEXT, created REAL NOT NULL,
  network TEXT NOT NULL DEFAULT 'default', permissions TEXT NOT NULL DEFAULT '{}', last_state TEXT, last_seen REAL);
