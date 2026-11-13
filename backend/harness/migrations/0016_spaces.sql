CREATE TABLE spaces(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE space_access(space_id TEXT NOT NULL, persona_id TEXT NOT NULL, PRIMARY KEY(space_id, persona_id));
CREATE TABLE pages(id TEXT PRIMARY KEY, space_id TEXT NOT NULL, parent_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL, revision INTEGER NOT NULL, source_thread_id TEXT, created REAL NOT NULL, updated REAL NOT NULL, deleted_at REAL);
CREATE INDEX pages_space ON pages(space_id, parent_id, deleted_at);
CREATE TABLE page_revisions(page_id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, parent_id TEXT, deleted_at REAL, created REAL NOT NULL, PRIMARY KEY(page_id, revision));
CREATE VIRTUAL TABLE pages_fts USING fts5(title, content, content='pages', content_rowid='rowid');
CREATE TRIGGER pages_ai AFTER INSERT ON pages BEGIN
  INSERT INTO pages_fts(rowid,title,content) VALUES(new.rowid,new.title,new.content);
END;
CREATE TRIGGER pages_ad AFTER DELETE ON pages BEGIN
  INSERT INTO pages_fts(pages_fts,rowid,title,content) VALUES('delete',old.rowid,old.title,old.content);
END;
CREATE TRIGGER pages_au AFTER UPDATE OF title,content ON pages BEGIN
  INSERT INTO pages_fts(pages_fts,rowid,title,content) VALUES('delete',old.rowid,old.title,old.content);
  INSERT INTO pages_fts(rowid,title,content) VALUES(new.rowid,new.title,new.content);
END;
