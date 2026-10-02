-- V2-04: hash-chained ledger entries, the receipts view, artifacts and daily roots.
CREATE TABLE IF NOT EXISTS ledger_entries(seq INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT NOT NULL,
  kind TEXT NOT NULL, body TEXT NOT NULL, ts REAL NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS ledger_entries_receipt ON ledger_entries(receipt_id);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, thread_id TEXT, persona_id TEXT,
  principal TEXT NOT NULL, tool TEXT NOT NULL, capability TEXT NOT NULL, hand TEXT, target_label TEXT,
  operation_key TEXT NOT NULL, args_digest TEXT NOT NULL, args_view TEXT NOT NULL, effect TEXT NOT NULL,
  decision TEXT, interrupt_id TEXT, dispatched INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'pending',
  result_digest TEXT, result_view TEXT, evidence TEXT NOT NULL DEFAULT '[]', artifact_ids TEXT NOT NULL DEFAULT '[]',
  started_at REAL NOT NULL, finished_at REAL, cost TEXT);
CREATE INDEX IF NOT EXISTS receipts_run ON receipts(run_id, started_at);
CREATE INDEX IF NOT EXISTS receipts_opkey ON receipts(operation_key);
CREATE INDEX IF NOT EXISTS receipts_time ON receipts(started_at);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL, path TEXT NOT NULL, run_id TEXT, receipt_id TEXT, created REAL NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0, expires REAL);
CREATE TABLE IF NOT EXISTS ledger_roots(day TEXT PRIMARY KEY, last_seq INTEGER NOT NULL, root_hash TEXT NOT NULL);
