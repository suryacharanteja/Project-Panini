"""Proactivity: a notice inbox, watches that speak only on change, and suggestions.

Suggestions are computed locally from PANINI's own records (no model call):
- a goal asked for three or more times recently -> "schedule it?"
- a review that is about to expire -> "decide before it lapses"
- an agent run that verified several steps -> "save it as a skill?"
Each suggestion is deduplicated, can be accepted or dismissed, and never acts by itself.
"""

import hashlib
import json
import re
import time
from uuid import uuid4

from .memory import embed

NOTIFY = re.compile(r"^\s*NOTIFY\s*:\s*", re.I)
QUIET = re.compile(r"^\s*QUIET\s*:?", re.I)
REPEAT_WINDOW = 14 * 24 * 3600


def watch_goal(goal, condition):
    return (
        f"Check this: {goal}\n"
        f"Tell the person only if this condition holds: {condition}\n"
        "Finish with a message that starts with 'NOTIFY: ' and says what changed, or with "
        "'QUIET: ' and a one-line status when there is nothing worth telling them."
    )


class Notices:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.listeners = []

    def add(self, kind, title, body, *, task_id=None, action=None, dedupe=None):
        """Returns the notice, or None when an identical one already exists."""
        notice_id = uuid4().hex
        cursor = self.store.execute(
            "INSERT OR IGNORE INTO notices(id, kind, title, body, task_id, action, dedupe, created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                notice_id,
                kind,
                title[:200],
                body[:4000],
                task_id,
                json.dumps(action) if action else None,
                dedupe,
                self.clock(),
            ),
        )
        notice = self.get(notice_id) if cursor.rowcount else None
        if notice:
            for listener in self.listeners:
                listener("opened", notice)
        return notice

    def get(self, notice_id):
        row = self.store.one("SELECT * FROM notices WHERE id=?", (notice_id,))
        if not row:
            raise KeyError("Unknown notice")
        return {**row, "action": json.loads(row["action"]) if row["action"] else None}

    def list(self, include_closed=False, limit=100):
        where = "" if include_closed else "WHERE status IN ('unread','read')"
        rows = self.store.all(f"SELECT id FROM notices {where} ORDER BY created DESC LIMIT ?", (limit,))
        return [self.get(r["id"]) for r in rows]

    def unread(self):
        return self.store.one("SELECT COUNT(*) AS n FROM notices WHERE status='unread'")["n"]

    def mark(self, notice_id, status):
        if status not in ("read", "dismissed", "accepted"):
            raise ValueError("status must be read, dismissed or accepted")
        self.store.execute("UPDATE notices SET status=? WHERE id=?", (status, notice_id))
        notice = self.get(notice_id)
        for listener in self.listeners:
            listener("closed", notice)
        return notice


def watch_result(notices, task, message):
    """Notify only when the condition is met and the finding differs from the last one."""
    if not NOTIFY.match(message or ""):
        return None
    finding = NOTIFY.sub("", message).strip()
    digest = hashlib.sha256(finding.lower().encode()).hexdigest()[:24]
    return notices.add(
        "watch",
        f"Watch: {task['title'][:120]}",
        finding,
        task_id=task["id"],
        dedupe=f"watch:{task['id']}:{digest}",
    )


def suggest(store, notices, clock=time.time):
    """Create suggestion notices from local records. Returns the new ones."""
    now, created = clock(), []
    # 1. Repeated goals that are not yet scheduled.
    titles = [
        r["title"]
        for r in store.all(
            "SELECT title FROM sessions WHERE updated>? AND title<>'' ORDER BY updated DESC LIMIT 200",
            (now - REPEAT_WINDOW,),
        )
    ]
    scheduled = [
        json.loads(r["data"])["goal"]
        for r in store.all("SELECT data FROM tasks WHERE status IN ('queued','running')")
    ]
    vectors = [(t, embed(t)) for t in titles]
    done = set()
    for index, (title, vector) in enumerate(vectors):
        if index in done:
            continue
        group = [
            j
            for j, (_, other) in enumerate(vectors)
            if j not in done and sum(a * b for a, b in zip(vector, other, strict=True)) > 0.8
        ]
        done.update(group)
        if len(group) < 3:
            continue
        if any(sum(a * b for a, b in zip(vector, embed(goal), strict=True)) > 0.8 for goal in scheduled):
            continue
        key = hashlib.sha256(title.lower().encode()).hexdigest()[:16]
        notice = notices.add(
            "suggestion",
            "Make this a routine?",
            f"You asked “{title[:120]}” {len(group)} times recently. PANINI can do it every morning.",
            action={"type": "schedule", "goal": title, "every_minutes": 1440},
            dedupe=f"repeat:{key}",
        )
        if notice:
            created.append(notice)
    # 2. Reviews that expire within two hours.
    for review in store.all(
        "SELECT id, task_id, data, expires FROM reviews WHERE status='open' AND expires BETWEEN ? AND ?",
        (now, now + 7200),
    ):
        data = json.loads(review["data"])
        notice = notices.add(
            "suggestion",
            "A decision is about to lapse",
            f"{data.get('message', '')[:300]} — decide within two hours or it expires.",
            task_id=review["task_id"],
            action={"type": "open_review", "review_id": review["id"]},
            dedupe=f"expiring:{review['id']}",
        )
        if notice:
            created.append(notice)
    return created


def suggest_skill(notices, goal, steps):
    """After an agent run that verified several steps, offer to save it as a skill."""
    verified = [s for s in steps if s.get("outcome") == "verified_complete"]
    if len(verified) < 3:
        return None
    key = hashlib.sha256(goal.lower().encode()).hexdigest()[:16]
    outline = "\n".join(f"{i + 1}. {s['capability']}: {s['message'][:160]}" for i, s in enumerate(verified))
    return notices.add(
        "suggestion",
        "Save this as a skill?",
        f"“{goal[:120]}” took {len(verified)} verified steps. Save it so PANINI repeats it reliably.",
        action={"type": "skill", "goal": goal, "outline": outline},
        dedupe=f"skill:{key}",
    )
