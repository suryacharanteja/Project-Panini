"""Append-only, hash-chained ledger entries (docs/panini-enhancements/lld/V2-04-receipts-ledger.md §5.2).

hash = sha256(prev_hash + "\\n" + kind + "\\n" + receipt_id + "\\n" + body), body in canonical JSON, the same
convention as the coordinator's digest(). Appends run inside BEGIN IMMEDIATE so writers serialize.
"""

import hashlib
import json
import time

GENESIS = "0" * 64


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def entry_hash(prev_hash: str, kind: str, receipt_id: str, body: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{kind}\n{receipt_id}\n{body}".encode()).hexdigest()


def append(store, receipt_id: str, kind: str, body: dict, *, clock=time.time) -> dict:
    text = canonical(body)
    with store.lock:
        db = store.db
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT hash FROM ledger_entries ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            value = entry_hash(prev, kind, receipt_id, text)
            cursor = db.execute(
                "INSERT INTO ledger_entries(receipt_id, kind, body, ts, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                (receipt_id, kind, text, clock(), prev, value),
            )
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return {"seq": cursor.lastrowid, "hash": value, "prev_hash": prev}


def verify(store, *, start: int = 0) -> dict:
    """Recompute the chain; returns {ok, entries, first_bad_seq}."""
    prev, count = GENESIS, 0
    if start:
        row = store.one("SELECT hash FROM ledger_entries WHERE seq < ? ORDER BY seq DESC LIMIT 1", (start,))
        prev = row["hash"] if row else GENESIS
    with store.lock:
        rows = store.db.execute(
            "SELECT seq, receipt_id, kind, body, prev_hash, hash FROM ledger_entries WHERE seq >= ? ORDER BY seq",
            (start,),
        ).fetchall()
    for seq, receipt_id, kind, body, prev_hash, value in rows:
        count += 1
        if prev_hash != prev or entry_hash(prev, kind, receipt_id, body) != value:
            return {"ok": False, "entries": count, "first_bad_seq": seq}
        prev = value
    return {"ok": True, "entries": count, "first_bad_seq": None, "head": prev}
