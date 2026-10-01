"""One local SQLite database (WAL) for memory, sessions, tasks, runs and reviews."""

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..extension_pairing import data_directory

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'global', vector BLOB, created REAL NOT NULL, used REAL,
  uses INTEGER NOT NULL DEFAULT 0);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='memories', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text); END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rowid, old.text); END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text); END;
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, summary TEXT NOT NULL DEFAULT '',
  items TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
  next_run REAL, lease_id TEXT, lease_until REAL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, title TEXT NOT NULL,
  goal TEXT NOT NULL, context TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_plans(project_id TEXT PRIMARY KEY, data TEXT NOT NULL,
  status TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS connector_receipts(id TEXT PRIMARY KEY, server TEXT NOT NULL,
  tool TEXT NOT NULL, arguments_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL,
  read_only INTEGER NOT NULL, ok INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
  at REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, task_id TEXT, action TEXT, dedupe TEXT UNIQUE, created REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'unread');
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, data TEXT NOT NULL,
  status TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL);
"""


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_directory() / "harness.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def execute(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params)

    def all(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def event(self, task_id, kind, detail):
        self.execute(
            "INSERT INTO events(task_id, at, kind, detail) VALUES (?,?,?,?)",
            (task_id, time.time(), kind, json.dumps(detail, ensure_ascii=False)[:8000]),
        )

    def close(self):
        with self.lock:
            self.db.close()
