-- V2-05: durable interrupts (approvals, questions, choices, reviews, takeovers).
CREATE TABLE IF NOT EXISTS interrupts(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, thread_id TEXT, task_id TEXT, kind TEXT NOT NULL,
  reason TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, preview TEXT NOT NULL, action TEXT, action_digest TEXT,
  response_schema TEXT, level TEXT NOT NULL, nonce TEXT NOT NULL, tool_call_id TEXT, created_at REAL NOT NULL,
  expires_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open', decision TEXT, decided_by TEXT, decided_at REAL, opened_by TEXT);
CREATE INDEX IF NOT EXISTS interrupts_open ON interrupts(status, expires_at);
CREATE INDEX IF NOT EXISTS interrupts_run ON interrupts(run_id);
