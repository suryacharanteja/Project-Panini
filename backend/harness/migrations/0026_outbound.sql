CREATE TABLE outbound_settings (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL, checklist TEXT, signed_at REAL);
CREATE TABLE outbound_briefs (id TEXT PRIMARY KEY, brief TEXT NOT NULL, digest TEXT NOT NULL, interrupt_id TEXT, thread_id TEXT NOT NULL, surface TEXT NOT NULL DEFAULT 'avatar', state TEXT NOT NULL DEFAULT 'review', created REAL NOT NULL, updated REAL NOT NULL, attempted_at REAL, session_id TEXT, receipt_id TEXT, transcript_artifact_id TEXT, outcome TEXT, reason TEXT);
CREATE INDEX outbound_briefs_state ON outbound_briefs(state,created);
CREATE TABLE outbound_do_not_call (number TEXT PRIMARY KEY, reason TEXT NOT NULL, created REAL NOT NULL);
