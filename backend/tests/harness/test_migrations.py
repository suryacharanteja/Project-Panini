import sqlite3

import pytest

from backend.harness.migrations import MigrationError, migrate
from backend.harness.store import SCHEMA, Store


def test_store_applies_baseline_once(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    assert store.migrations == ["0001_baseline"]
    store.close()
    again = Store(tmp_path / "h.sqlite3")
    assert again.migrations == []
    assert [r["id"] for r in again.all("SELECT id FROM schema_migrations")] == ["0001_baseline"]
    again.close()


def test_v1_database_upgrades_in_place(tmp_path):
    path = tmp_path / "v1.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO sessions(id, title, created, updated) VALUES ('s', 'old', 1, 1)")
    db.commit()
    db.close()
    store = Store(path)
    assert store.migrations == ["0001_baseline"]
    assert store.one("SELECT title FROM sessions WHERE id='s'")["title"] == "old"
    store.close()


def _connection():
    return sqlite3.connect(":memory:", isolation_level=None)


def test_order_triggers_and_failure_rollback(tmp_path):
    (tmp_path / "0001_a.sql").write_text(
        "CREATE TABLE t(x);\nCREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; SELECT 2; END;\n",
        encoding="utf-8",
    )
    (tmp_path / "0002_b.sql").write_text("INSERT INTO t VALUES (1);\n", encoding="utf-8")
    (tmp_path / "0003_bad.sql").write_text(
        "INSERT INTO t VALUES (2);\nINSERT INTO missing VALUES (1);\n", encoding="utf-8"
    )
    db = _connection()
    with pytest.raises(MigrationError, match="0003_bad"):
        migrate(db, tmp_path)
    assert [r[0] for r in db.execute("SELECT id FROM schema_migrations ORDER BY id")] == ["0001_a", "0002_b"]
    assert [r[0] for r in db.execute("SELECT x FROM t")] == [1]  # The failed migration left nothing behind.


def test_python_migration_and_duplicate_numbers(tmp_path):
    (tmp_path / "0001_py.py").write_text(
        "def apply(c):\n    c.execute('CREATE TABLE p(x)')\n", encoding="utf-8"
    )
    db = _connection()
    assert migrate(db, tmp_path) == ["0001_py"]
    (tmp_path / "0001_other.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="share a number"):
        migrate(db, tmp_path)
