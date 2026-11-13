CREATE TABLE page_sources(space_id TEXT NOT NULL, kind TEXT NOT NULL, source_id TEXT NOT NULL, page_id TEXT NOT NULL, PRIMARY KEY(space_id,kind,source_id));
