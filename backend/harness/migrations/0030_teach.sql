CREATE TABLE teach_recordings (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, browser TEXT NOT NULL, state TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE teach_versions (
  name TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL,
  description TEXT NOT NULL, state TEXT NOT NULL, review_id TEXT, base_version INTEGER NOT NULL,
  created REAL NOT NULL, approved REAL, PRIMARY KEY(name, version)
);
CREATE UNIQUE INDEX teach_review ON teach_versions(review_id) WHERE review_id IS NOT NULL;
CREATE TABLE skill_runs (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
  inputs_digest TEXT NOT NULL, heals INTEGER NOT NULL DEFAULT 0, failure TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '[]', started REAL NOT NULL, finished REAL
);
