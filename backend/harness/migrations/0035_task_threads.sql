-- V2-15: a background task can continue an existing owner conversation (phone turns on any surface's thread).
CREATE TABLE IF NOT EXISTS task_threads(task_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS task_threads_thread ON task_threads(thread_id);
