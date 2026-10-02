"""Numbered, forward-only SQLite migrations for the harness database.

Files are named NNNN_short_name.sql (or NNNN_short_name.py exposing apply(connection)). Each runs once,
in order, inside a transaction, and is recorded in schema_migrations. Never edit an applied migration:
add a new one. Destructive changes are a new table plus a copy.
"""

import importlib.util
import re
import time
from pathlib import Path

NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.(sql|py)$")


class MigrationError(RuntimeError):
    pass


def pending(directory: Path, applied: set[str]) -> list[Path]:
    files = sorted(p for p in directory.iterdir() if NAME.match(p.name))
    numbers = [NAME.match(p.name).group(1) for p in files]
    if len(numbers) != len(set(numbers)):
        raise MigrationError("Two migrations share a number.")
    return [p for p in files if p.stem not in applied]


def migrate(connection, directory: Path = Path(__file__).parent) -> list[str]:
    """Apply pending migrations; returns the ids applied now."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations(id TEXT PRIMARY KEY, applied REAL NOT NULL)"
    )
    applied = {row[0] for row in connection.execute("SELECT id FROM schema_migrations")}
    done = []
    for path in pending(directory, applied):
        try:
            connection.execute("BEGIN IMMEDIATE")
            if path.suffix == ".sql":
                for statement in _statements(path.read_text(encoding="utf-8")):
                    connection.execute(statement)
            else:
                spec = importlib.util.spec_from_file_location(f"panini_migration_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations(id, applied) VALUES (?,?)", (path.stem, time.time())
            )
            connection.execute("COMMIT")
        except Exception as exc:
            connection.execute("ROLLBACK")
            raise MigrationError(f"Migration {path.stem} failed: {exc}") from exc
        done.append(path.stem)
    return done


def _statements(sql):
    """Split on semicolons that end a statement, keeping trigger bodies (BEGIN ... END;) together."""
    import sqlite3

    statements, buffer = [], ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError("Migration ends with an incomplete statement.")
    return statements
