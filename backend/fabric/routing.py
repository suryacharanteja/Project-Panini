"""Durable continuity deliveries; transport acceptance is recorded separately from human attention.

No approval authority is conveyed by a notification or a handoff. Ambiguous network effects are never
replayed after a crash. Owner calls deliberately remain reviewable V2-20 briefs until their bounded
standing authorization is qualified.
"""

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import v2flags
from .presence import Presence

logger = logging.getLogger(__name__)
STATES = ("at_pc", "near_pc", "away_with_phone", "away", "on_call")
ROUTES = {"avatar", "push", "channel", "inbox", "toast", "after_call"}
DEFAULT_RULES = {
    "tap": [
        [("avatar", 0)],
        [("avatar", 0), ("push", 30)],
        [("push", 0)],
        [("push", 0), ("channel", 0)],
        [("push", 0)],
    ],
    "verify": [[("avatar", 0)], [("avatar", 0), ("push", 0)], [("push", 0)], [("push", 0)], [("push", 0)]],
    "local": [[("avatar", 0)], [("avatar", 0)], [("push", 0)], [("push", 0)], [("push", 0)]],
    "result": [
        [("avatar", 0)],
        [("avatar", 0), ("push", 120)],
        [("push", 0)],
        [("channel", 0)],
        [("push", 0)],
    ],
    "background": [
        [("inbox", 0), ("toast", 0)],
        [("inbox", 0), ("toast", 0)],
        [("push", 0)],
        [("channel", 0)],
        [("after_call", 0)],
    ],
}


def defaults():
    return {
        "rules": {
            kind: dict(zip(STATES, rows, strict=True)) for kind, rows in copy.deepcopy(DEFAULT_RULES).items()
        },
        "quiet": {"enabled": True, "start": 22, "end": 7, "timezone": "local"},
        "preferred_channel": None,
    }


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != {"rules", "quiet", "preferred_channel"}:
        raise ValueError("Settings need rules, quiet hours and a preferred channel.")
    rules, quiet = value["rules"], value["quiet"]
    if not isinstance(rules, dict) or set(rules) != set(DEFAULT_RULES):
        raise ValueError("Configure every supported item kind.")
    for states in rules.values():
        if not isinstance(states, dict) or set(states) != set(STATES):
            raise ValueError("Configure all five presence states.")
        for routes in states.values():
            if not isinstance(routes, list) or len(routes) > len(ROUTES):
                raise ValueError("Invalid routing list.")
            seen = set()
            for entry in routes:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    raise ValueError("A route is [destination, delay seconds].")
                route, delay = entry
                if (
                    not isinstance(route, str)
                    or route not in ROUTES
                    or route in seen
                    or type(delay) is not int
                    or not 0 <= delay <= 86400
                ):
                    raise ValueError("Invalid or duplicate destination/delay.")
                seen.add(route)
    if not isinstance(quiet, dict) or set(quiet) != {"enabled", "start", "end", "timezone"}:
        raise ValueError("Invalid quiet hours.")
    if (
        type(quiet["enabled"]) is not bool
        or any(type(quiet[k]) is not int or not 0 <= quiet[k] <= 23 for k in ("start", "end"))
        or quiet["start"] == quiet["end"]
    ):
        raise ValueError("Quiet hours need distinct hours from 0 to 23.")
    if not isinstance(quiet["timezone"], str):
        raise ValueError("Invalid timezone.")
    if quiet["timezone"] != "local":
        try:
            ZoneInfo(quiet["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone.") from None
    channel = value["preferred_channel"]
    if channel is not None and (
        not isinstance(channel, dict)
        or set(channel) != {"account_id", "thread_key"}
        or any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in channel.values())
    ):
        raise ValueError("Choose a known owner channel conversation.")
    return value


def quiet_until(now, quiet):
    if not quiet["enabled"]:
        return now
    local = (
        datetime.fromtimestamp(now).astimezone()
        if quiet["timezone"] == "local"
        else datetime.fromtimestamp(now, ZoneInfo(quiet["timezone"]))
    )
    start, end = quiet["start"], quiet["end"]
    inside = start <= local.hour < end if start < end else local.hour >= start or local.hour < end
    if not inside:
        return now
    # Construct the next local wall-time boundary, preserving DST timezone transitions.
    tomorrow = start > end and local.hour >= start
    day = local.date() + timedelta(days=int(tomorrow))
    if quiet["timezone"] == "local":
        return datetime.combine(day, datetime.min.time()).replace(hour=end).astimezone().timestamp()
    return datetime.combine(day, datetime.min.time(), tzinfo=local.tzinfo).replace(hour=end).timestamp()


class Continuity:
    def __init__(self, harness, *, clock=time.time):
        self.h, self.store, self.clock = harness, harness.store, clock
        self.presence = Presence(self.store, clock=clock)
        self.fabric, self.loop, self.app = None, None, None
        self.lock = asyncio.Lock()
        self.h.interrupts.listeners.append(self.interrupt)
        self.h.notices.listeners.append(self.notice)
        from .urgent import UrgentEscalation

        self.urgent = UrgentEscalation(self)

    @property
    def enabled(self):
        return v2flags.enabled("PANINI_PRESENCE")

    def status(self):
        return {**self.presence_state(), **self.details()}

    def presence_state(self):
        phone = getattr(getattr(self.app, "state", None), "phone_line", None)
        call = getattr(phone, "active", None)
        # An unverified public caller is not evidence that the owner is on a call.
        on_call = bool(call and call.verified and call.caller.get("kind") == "owner" and not call.closing)
        return self.presence.status(on_call=on_call)

    def details(self):
        destinations = []
        if self.enabled:
            for row in self.store.all(
                "SELECT DISTINCT account_id,thread_key,sender_id FROM channel_jobs WHERE thread_key=sender_id ORDER BY created DESC LIMIT 50"
            ):
                try:
                    job = self.channel({"account_id": row["account_id"], "thread_key": row["thread_key"]})
                    if job:
                        destinations.append(
                            {"account_id": job["account_id"], "thread_key": job["thread_key"]}
                        )
                except (ValueError, KeyError):
                    pass
        return {
            "settings": self.settings(),
            "channel_destinations": destinations,
            "deliveries": self.store.all("SELECT * FROM continuity_deliveries ORDER BY rowid DESC LIMIT 100"),
            "pending_commands": self.store.one(
                "SELECT COUNT(*) AS n FROM continuity_commands WHERE acknowledged=0 AND expires>?",
                (self.clock(),),
            )["n"],
            "owner_call_escalation": self.urgent.status(),
        }

    def settings(self):
        row = self.store.one("SELECT data FROM presence_settings WHERE id=1")
        return json.loads(row["data"]) if row else defaults()

    def channel(self, choice):
        if not choice:
            return None
        job = self.store.one(
            "SELECT * FROM channel_jobs WHERE account_id=? AND thread_key=? ORDER BY created DESC LIMIT 1",
            (choice["account_id"], choice["thread_key"]),
        )
        # Only a previously authenticated owner's private conversation is a notification destination.
        if not job or not self.h.channels.permitted(job) or job["thread_key"] != job["sender_id"]:
            raise ValueError("Preferred channel must be an enabled owner's private conversation.")
        return job

    def configure(self, value):
        validate_settings(value)
        self.channel(value["preferred_channel"])
        self.store.execute(
            "INSERT INTO presence_settings(id,data) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (json.dumps(value),),
        )
        return self.status()

    def command(self, kind, data, item_id=None):
        return self.store.execute(
            "INSERT INTO continuity_commands(kind,item_id,data,expires) VALUES (?,?,?,?)",
            (kind, item_id, json.dumps(data), self.clock() + 600),
        ).lastrowid

    def commands(self):
        if not self.enabled:
            rows = self.store.all(
                "SELECT * FROM continuity_commands WHERE kind='retract' AND acknowledged=0 AND expires>? ORDER BY seq LIMIT 100",
                (self.clock(),),
            )
        else:
            rows = self.store.all(
                "SELECT * FROM continuity_commands WHERE acknowledged=0 AND expires>? ORDER BY seq LIMIT 100",
                (self.clock(),),
            )
        return [{**row, "data": json.loads(row["data"])} for row in rows]

    def acknowledge(self, sequence):
        # Acknowledge exactly handled commands, never an unchecked cursor range.
        self.store.execute("UPDATE continuity_commands SET acknowledged=1 WHERE seq=?", (sequence,))
        row = self.store.one("SELECT item_id,kind FROM continuity_commands WHERE seq=?", (sequence,))
        if row and row["kind"] == "notification":
            self.store.execute(
                "UPDATE continuity_deliveries SET state='handled_by_pc' WHERE item_id=? AND state='queued_pc'",
                (row["item_id"],),
            )

    def publish(self, identifier, kind, title, *, thread_id=None, expires=None, urgent=False):
        if not self.enabled:
            return
        now = self.clock()
        self.store.execute(
            "INSERT OR IGNORE INTO continuity_items(id,kind,title,thread_id,created,expires,urgent) VALUES (?,?,?,?,?,?,?)",
            (
                identifier,
                kind,
                title[:200],
                thread_id,
                now,
                expires if expires is not None else now + 86400,
                int(urgent),
            ),
        )

    def interrupt(self, event, view):
        identifier = "interrupt:" + view["id"]
        if event == "interrupt.opened":
            self.publish(
                identifier,
                view["level"],
                view.get("title", "PANINI needs you"),
                thread_id=view.get("thread_id"),
                expires=view["expires_at"],
            )
        elif event in {"interrupt.resolved", "interrupt.cancelled", "interrupt.expired"}:
            self.resolve(identifier)

    def notice(self, event, notice):
        if notice["kind"] == "approval":
            return  # Already represented by the original interrupt and nonce.
        identifier = "notice:" + notice["id"]
        if event == "opened":
            self.publish(identifier, "background", notice["title"], urgent=bool(notice["urgent"]))
        else:
            self.resolve(identifier)

    def resolve(self, identifier):
        changed = self.store.execute(
            "UPDATE continuity_items SET status='closed' WHERE id=? AND status='open'", (identifier,)
        ).rowcount
        if not changed:
            return
        self.store.execute(
            "UPDATE continuity_deliveries SET state='cancelled' WHERE item_id=? AND state IN ('queued','failed')",
            (identifier,),
        )
        self.store.execute(
            "UPDATE continuity_commands SET acknowledged=1 WHERE item_id=? AND kind='notification'",
            (identifier,),
        )
        self.urgent.cancel_item(identifier)
        self.command("retract", {"id": identifier.split(":", 1)[1]}, identifier)
        # Retractions have their own durable identity and bypass quiet hours.
        self.store.execute(
            "INSERT OR IGNORE INTO continuity_deliveries(item_id,route,due) VALUES (?,'retract',?)",
            (identifier, self.clock()),
        )

    def foreground_result(self, thread_id, run_id, title):
        if not self.enabled:
            return
        from uuid import uuid4

        self.publish("result:" + (run_id or uuid4().hex), "result", title, thread_id=thread_id)

    def handoff(self, thread_id, destination, *, surface="avatar"):
        if not self.enabled:
            raise ValueError("Continuity is off (PANINI_PRESENCE).")
        self.h.sessions.get(thread_id)  # Restore a real, existing thread; never create a replacement.
        if destination == "pc":
            seq = self.command("handoff", {"thread_id": thread_id, "prompt": "Continuing from your phone"})
            return {"status": "queued", "command_seq": seq, "thread_id": thread_id}
        if destination == "phone":
            from uuid import uuid4

            identifier = "handoff:" + uuid4().hex
            self.publish(identifier, "result", "Continue your PANINI conversation", thread_id=thread_id)
            self.store.execute(
                "INSERT OR IGNORE INTO continuity_deliveries(item_id,route,due) VALUES (?,'push',?)",
                (identifier, self.clock()),
            )
            return {"status": "queued", "thread_id": thread_id}
        if destination == "call":
            # Fixed facts only. The owner number comes from the acknowledged trunk configuration,
            # and the existing digest-bound review must be explicitly answered before one dial attempt.
            number = self.h.outbound.settings()["config"].get("owner_number")
            if not number:
                raise ValueError("Configure and acknowledge the owner call number in Phone settings.")
            brief = self.h.outbound.draft(
                {
                    "callee_name": "PANINI owner",
                    "number": number,
                    "number_source": "owner",
                    "purpose": "Ask the owner to open PANINI Inbox to review an item.",
                    "owner_first_name": "the owner",
                    "may_disclose": ["PANINI has an item awaiting review."],
                    "acceptable_outcomes": ["The owner opens PANINI Inbox."],
                    "may_commit": [],
                    "fallback": "end_politely",
                    "voicemail": "hang_up",
                    "max_minutes": 2,
                },
                thread_id=thread_id,
                surface=surface,
            )
            return {
                "status": "review_required",
                "thread_id": thread_id,
                "brief_id": brief["id"],
                "interrupt_id": brief["interrupt_id"],
            }
        raise ValueError("Handoff destination must be pc, phone or call.")

    def start(self):
        if self.loop:
            return
        self.store.execute("UPDATE continuity_deliveries SET state='unknown' WHERE state='sending'")
        self.loop = asyncio.create_task(self.maintain())

    async def close(self):
        if self.loop:
            self.loop.cancel()
            await asyncio.gather(self.loop, return_exceptions=True)
            self.loop = None

    async def maintain(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 -- transport failures cannot crash the harness.
                logger.warning("Continuity tick failed: %s", type(error).__name__)
            await asyncio.sleep(1)

    async def tick(self):
        async with self.lock:
            now = self.clock()
            # Catch approvals opened while disabled or before composition/restart.
            if self.enabled:
                for view in self.h.interrupts.list():
                    self.interrupt("interrupt.opened", view)
                for notice in self.h.notices.list():
                    if notice["status"] == "unread":
                        self.notice("opened", notice)
            for item in self.store.all("SELECT * FROM continuity_items WHERE status='open'"):
                if item["expires"] <= now:
                    self.resolve(item["id"])
                    continue
                if item["id"].startswith("interrupt:"):
                    current = self.store.one(
                        "SELECT status FROM interrupts WHERE id=?", (item["id"].split(":", 1)[1],)
                    ) or {"status": "missing"}
                    if current["status"] != "open":
                        self.resolve(item["id"])
                        continue
                elif item["id"].startswith("notice:"):
                    notice = self.store.one(
                        "SELECT status FROM notices WHERE id=?", (item["id"].split(":", 1)[1],)
                    )
                    if not notice or notice["status"] != "unread":
                        self.resolve(item["id"])
                        continue
                if not self.enabled:
                    continue
                state = self.presence_state()["state"]
                deferred = self.store.one(
                    "SELECT state FROM continuity_deliveries WHERE item_id=? AND route='after_call'",
                    (item["id"],),
                )
                routes = (
                    []
                    if deferred and deferred["state"] != "cancelled" and state != "on_call"
                    else self.settings()["rules"][item["kind"]][state]
                )
                desired = {route for route, _ in routes}
                if item["urgent"] and "push" not in desired:
                    routes = [*routes, ("push", 0)]
                    desired.add("push")
                # Cancel stale pending routes as presence changes. Already sent receipts stay durable.
                for pending in self.store.all(
                    "SELECT route FROM continuity_deliveries WHERE item_id=? AND state='queued'",
                    (item["id"],),
                ):
                    if (
                        pending["route"] not in desired
                        and pending["route"] != "after_call"
                        and not item["id"].startswith("handoff:")
                    ):
                        self.store.execute(
                            "UPDATE continuity_deliveries SET state='cancelled' WHERE item_id=? AND route=?",
                            (item["id"], pending["route"]),
                        )
                for route, delay in routes:
                    self.store.execute(
                        "INSERT INTO continuity_deliveries(item_id,route,due) VALUES (?,?,?) ON CONFLICT(item_id,route) DO UPDATE SET state='queued',due=excluded.due WHERE state='cancelled'",
                        (item["id"], route, item["created"] + delay),
                    )
            rows = self.store.all(
                "SELECT d.*,i.kind,i.title,i.thread_id,i.status FROM continuity_deliveries d JOIN continuity_items i ON i.id=d.item_id WHERE d.state='queued' AND d.due<=? ORDER BY d.due LIMIT 50",
                (now,),
            )
            for row in rows:
                if row["route"] != "retract" and (not self.enabled or row["status"] != "open"):
                    continue
                if row["route"] in {"push", "channel"}:
                    due = (
                        now
                        if row["route"] == "retract"
                        or self.store.one(
                            "SELECT urgent FROM continuity_items WHERE id=?", (row["item_id"],)
                        )["urgent"]
                        else quiet_until(now, self.settings()["quiet"])
                    )
                    if due > now:
                        self.store.execute(
                            "UPDATE continuity_deliveries SET due=? WHERE item_id=? AND route=?",
                            (due, row["item_id"], row["route"]),
                        )
                        continue
                if row["route"] == "after_call":
                    if self.presence_state()["state"] == "on_call":
                        continue
                    row["route"] = "toast"
                    self.command(
                        "notification",
                        {
                            "id": row["item_id"].split(":", 1)[1],
                            "item_id": row["item_id"],
                            "title": row["title"],
                            "interrupt": False,
                            "route": "after_call",
                        },
                        row["item_id"],
                    )
                    self.store.execute(
                        "UPDATE continuity_deliveries SET state='sent' WHERE item_id=? AND route='after_call'",
                        (row["item_id"],),
                    )
                    continue
                await self.dispatch(row)
            if self.enabled:
                self.urgent.tick()
            # A channel send may finish after a resolution raced it. Retry edits only when a provider id exists.
            for closed in self.store.all(
                "SELECT DISTINCT i.id FROM continuity_items i JOIN continuity_deliveries d ON d.item_id=i.id WHERE i.status='closed' AND d.route='channel' AND d.state='queued_channel'"
            ):
                await self.retract_channel(closed["id"])
            self.store.execute("DELETE FROM continuity_commands WHERE expires<?", (now - 86400,))

    async def dispatch(self, row):
        identifier, route = row["item_id"], row["route"]
        self.store.execute(
            "UPDATE continuity_deliveries SET state='sending' WHERE item_id=? AND route=?",
            (identifier, route),
        )
        detail, state = {}, "sent"
        try:
            short_id = identifier.split(":", 1)[1]
            if route in {"avatar", "toast"}:
                detail["command_seq"] = self.command(
                    "notification",
                    {
                        "id": short_id,
                        "item_id": identifier,
                        "route": route,
                        "title": row["title"],
                        "thread_id": row["thread_id"],
                        "interrupt": identifier.startswith("interrupt:"),
                    },
                    identifier,
                )
                state = "queued_pc"  # Native handling is separately acknowledged.
            elif route == "inbox":
                detail["status"] = "available_in_existing_inbox"
            elif route in {"push", "retract"}:
                if not self.fabric:
                    state, detail = "failed", {"reason": "Remote fabric is not running."}
                else:
                    message = {
                        "type": "interrupt_resolved"
                        if route == "retract"
                        else "interrupt"
                        if identifier.startswith("interrupt:")
                        else "continuity",
                        "id": short_id,
                        "title": "PANINI has a result" if row["kind"] == "result" else row["title"],
                        "level": row["kind"],
                        "thread_id": row["thread_id"],
                    }
                    detail = await self.fabric.push.send(message)
                    state = "accepted_by_push_service" if detail.get("sent") else "failed"
                    if detail.get("sent") and route == "push":
                        self.presence.signal("push", {})
                if route == "retract":
                    await self.retract_channel(identifier)
            elif route == "channel":
                job = self.channel(self.settings()["preferred_channel"])
                if not job:
                    state, detail = "failed", {"reason": "No preferred owner channel configured."}
                else:
                    ids = self.h.channels.enqueue(
                        job["account_id"],
                        job["thread_key"],
                        "PANINI needs your attention. Open your PC or phone Inbox. Ref: " + short_id,
                    )
                    detail = {
                        "outbox_ids": ids,
                        "account_id": job["account_id"],
                        "thread_key": job["thread_key"],
                    }
                    state = "queued_channel"
            self.store.execute(
                "UPDATE continuity_deliveries SET state=?,detail=? WHERE item_id=? AND route=?",
                (state, json.dumps(detail), identifier, route),
            )
        except asyncio.CancelledError:
            self.store.execute(
                "UPDATE continuity_deliveries SET state='unknown' WHERE item_id=? AND route=?",
                (identifier, route),
            )
            raise
        except Exception as error:  # noqa: BLE001 -- redact provider credentials and payloads.
            self.store.execute(
                "UPDATE continuity_deliveries SET state='unknown',detail=? WHERE item_id=? AND route=?",
                (json.dumps({"error_class": type(error).__name__}), identifier, route),
            )

    async def retract_channel(self, identifier):
        delivery = self.store.one(
            "SELECT detail FROM continuity_deliveries WHERE item_id=? AND route='channel'", (identifier,)
        )
        if not delivery:
            return
        data = json.loads(delivery["detail"])
        complete = True
        for outbox_id in data.get("outbox_ids", []):
            self.store.execute(
                "UPDATE channel_outbox SET state='cancelled' WHERE id=? AND state='queued'", (outbox_id,)
            )
            row = self.store.one("SELECT * FROM channel_outbox WHERE id=?", (outbox_id,))
            provider = self.h.channels.providers.get(data["account_id"])
            if row and row["state"] not in {"cancelled", "retracted"}:
                complete = False
            if (
                row
                and row["state"] == "sent"
                and row["provider_id"]
                and provider
                and hasattr(provider, "edit")
            ):
                await provider.edit(
                    row["thread_key"],
                    row["provider_id"],
                    "Resolved in PANINI. Ref: " + identifier.split(":", 1)[1],
                )
                self.store.execute("UPDATE channel_outbox SET state='retracted' WHERE id=?", (outbox_id,))
        if complete:
            self.store.execute(
                "UPDATE continuity_deliveries SET state='retracted' WHERE item_id=? AND route='channel'",
                (identifier,),
            )
