"""Persistent conversations with compaction: older turns become a running summary."""

import json
import time
from uuid import uuid4

KEEP_ITEMS = 16


class Sessions:
    def __init__(self, store, budget_chars=160_000):
        self.store, self.budget = store, budget_chars

    def create(self, title=""):
        now = time.time()
        session_id = uuid4().hex
        self.store.execute(
            "INSERT INTO sessions(id, title, created, updated) VALUES (?,?,?,?)",
            (session_id, title[:200], now, now),
        )
        return session_id

    def get(self, session_id):
        row = self.store.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if not row:
            raise KeyError("Unknown session")
        return {**row, "items": json.loads(row["items"])}

    def list(self, limit=50):
        return self.store.all(
            "SELECT id, title, summary, created, updated FROM sessions ORDER BY updated DESC LIMIT ?",
            (limit,),
        )

    def save(self, session_id, items, summary=None, title=None):
        fields, params = ["items=?", "updated=?"], [json.dumps(items, ensure_ascii=False), time.time()]
        if summary is not None:
            fields.append("summary=?")
            params.append(summary[:20000])
        if title:
            fields.append("title=COALESCE(NULLIF(title,''), ?)")
            params.append(title[:200])
        self.store.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id=?", (*params, session_id))

    def needs_compaction(self, items):
        return len(json.dumps(items, ensure_ascii=False)) > self.budget

    @staticmethod
    def split(items):
        """Oldest items to summarise, and recent items to keep verbatim (never splitting a tool call)."""
        cut = max(0, len(items) - KEEP_ITEMS)
        while cut > 0 and items[cut].get("type") == "function_call_output":
            cut -= 1
        return items[:cut], items[cut:]


def transcript(items, limit=60000):
    lines = []
    for item in items:
        if item.get("role") == "user":
            lines.append(f"USER: {item.get('content')}")
        elif item.get("type") == "message":
            text = "".join(p.get("text", "") for p in item.get("content", []))
            lines.append(f"ASSISTANT: {text}")
        elif item.get("type") == "function_call":
            lines.append(f"TOOL CALL {item.get('name')}: {item.get('arguments', '')[:500]}")
        elif item.get("type") == "function_call_output":
            lines.append(f"TOOL RESULT: {str(item.get('output', ''))[:800]}")
    return "\n".join(lines)[-limit:]
