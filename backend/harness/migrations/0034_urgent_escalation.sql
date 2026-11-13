ALTER TABLE notices ADD COLUMN urgent INTEGER NOT NULL DEFAULT 0 CHECK(urgent IN (0,1));
ALTER TABLE continuity_items ADD COLUMN urgent INTEGER NOT NULL DEFAULT 0 CHECK(urgent IN (0,1));
ALTER TABLE outbound_briefs ADD COLUMN standing_grant_id TEXT;
ALTER TABLE outbound_briefs ADD COLUMN urgent_item_id TEXT;
CREATE TABLE continuity_urgent_grants (
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL UNIQUE, scope_digest TEXT NOT NULL,
  config_digest TEXT NOT NULL, number TEXT NOT NULL, created REAL NOT NULL,
  expires REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('proposed','active','revoked'))
);
CREATE TABLE continuity_urgent_attempts (
  item_id TEXT PRIMARY KEY REFERENCES continuity_items(id), grant_id TEXT NOT NULL,
  brief_id TEXT NOT NULL UNIQUE REFERENCES outbound_briefs(id), created REAL NOT NULL
);
CREATE INDEX continuity_urgent_attempts_grant ON continuity_urgent_attempts(grant_id,created);
