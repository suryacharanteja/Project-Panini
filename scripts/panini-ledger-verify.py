"""Verify PANINI's hash-chained activity ledger (docs/panini-enhancements/lld/V2-04-receipts-ledger.md).

    .venv\\Scripts\\python.exe scripts/panini-ledger-verify.py [--db PATH]

Recomputes every entry's hash from the first entry, reports the first broken sequence number if any, and
compares the daily roots recorded in ledger-roots.txt. Exit status 1 means the chain does not verify.
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.ledger.chain import verify  # noqa: E402


class _Store:
    def __init__(self, path):
        import threading

        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]


def main():
    from backend.extension_pairing import data_directory

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=data_directory() / "harness.sqlite3")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"No harness database at {args.db}.")
        return 1
    store = _Store(args.db)
    started = time.monotonic()
    result = verify(store)
    elapsed = time.monotonic() - started
    if result["ok"]:
        print(
            f"Ledger verifies: {result['entries']} entries in {elapsed:.2f}s; head {result.get('head', '')[:16]}…"
        )
    else:
        print(f"LEDGER BROKEN at entry seq {result['first_bad_seq']} ({result['entries']} checked).")
    mismatched = []
    for row in store.all("SELECT day, last_seq, root_hash FROM ledger_roots ORDER BY day"):
        at = store.one("SELECT hash FROM ledger_entries WHERE seq=?", (row["last_seq"],))
        if not at or at["hash"] != row["root_hash"]:
            mismatched.append(row["day"])
    if mismatched:
        print("Daily roots that no longer match:", ", ".join(mismatched))
    return 0 if result["ok"] and not mismatched else 1


if __name__ == "__main__":
    raise SystemExit(main())
