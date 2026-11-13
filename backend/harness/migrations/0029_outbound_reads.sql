CREATE TABLE outbound_reads (
  call_id TEXT NOT NULL REFERENCES outbound_briefs(id),
  delegation_id TEXT NOT NULL,
  state TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  result TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  PRIMARY KEY(call_id, delegation_id)
);
