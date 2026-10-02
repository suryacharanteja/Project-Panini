-- V2-03: policy grants, the grant epoch / pause switch, and shadow-mode disagreements with v1 approvals.
CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS grant_epochs(id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO grant_epochs(id, epoch, paused) VALUES (1, 1, 0);
CREATE TABLE IF NOT EXISTS policy_shadow(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tool TEXT NOT NULL,
  effect TEXT NOT NULL, v1_ask INTEGER NOT NULL, v2_action TEXT NOT NULL, v2_level TEXT NOT NULL, reasons TEXT NOT NULL);
