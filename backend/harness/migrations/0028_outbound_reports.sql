CREATE TABLE outbound_reports (
  call_id TEXT PRIMARY KEY REFERENCES outbound_briefs(id),
  state TEXT NOT NULL,
  source_digest TEXT NOT NULL,
  payload TEXT,
  receipt_id TEXT,
  review_interrupt_id TEXT,
  review_receipt_id TEXT,
  attempted_at REAL,
  reason TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
