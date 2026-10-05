"""Owner-bound read-only MCP polling for email and calendar metadata."""

import asyncio
import json
from datetime import datetime
from uuid import uuid4

from ..rules import TriggerEvent

INTERVAL = {"email": 60, "calendar": 300}


def bind(bus, source, server_name, tool_name, arguments):
    if source not in INTERVAL or not isinstance(arguments, dict) or len(json.dumps(arguments)) > 4000:
        raise ValueError("Choose email or calendar and small JSON tool arguments.")
    server, tool = bus.h.connectors.tool(server_name, tool_name)
    if not tool or not tool["read_only"] or tool_name not in (server.spec.get("background_read_only") or []):
        raise ValueError("Grant this exact read-only tool background access in Connectors first.")
    bus.store.execute(
        "INSERT INTO trigger_source_bindings(source,server,tool,arguments) VALUES (?,?,?,?) "
        "ON CONFLICT(source) DO UPDATE SET server=excluded.server,tool=excluded.tool,"
        "arguments=excluded.arguments,cursor=NULL,last_polled=NULL,last_attempt=NULL,last_error=NULL",
        (source, server_name, tool_name, json.dumps(arguments, ensure_ascii=False)),
    )


def unbind(bus, source):
    if source not in INTERVAL:
        raise ValueError("Unknown source.")
    bus.store.execute("DELETE FROM trigger_source_bindings WHERE source=?", (source,))


def _metadata(source, item):
    if not isinstance(item, dict):
        raise ValueError("Connector item must be an object.")
    if source == "email":
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("Email items need stable IDs.")
        return (
            "received",
            {
                "message_id": item["id"],
                "from": str(item.get("from", ""))[:300],
                "subject": str(item.get("subject", ""))[:500],
            },
            item["id"],
        )
    if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("start"), str):
        raise ValueError("Calendar items need stable IDs and start times.")
    kind = item.get("kind", "starting_soon")
    if kind not in {"starting_soon", "changed", "cancelled"}:
        raise ValueError("Unsupported calendar event kind.")
    return (
        kind,
        {"event_id": item["id"], "title": str(item.get("title", ""))[:500], "start": item["start"][:100]},
        f"{item['id']}:{kind}:{item['start']}",
    )


async def poll(bus):
    now = bus.clock()
    for row in bus.store.all("SELECT * FROM trigger_source_bindings"):
        source = row["source"]
        if source not in INTERVAL or (row["last_attempt"] and now - row["last_attempt"] < INTERVAL[source]):
            continue
        try:
            server, tool = bus.h.connectors.tool(row["server"], row["tool"])
            if (
                not tool
                or not tool["read_only"]
                or row["tool"] not in (server.spec.get("background_read_only") or [])
            ):
                raise ValueError("Background read-only grant was removed.")
            arguments = json.loads(row["arguments"])
            if row["cursor"]:
                arguments["cursor"] = row["cursor"]
            result = await asyncio.wait_for(server.call(row["tool"], arguments), 15)
            current, tool = bus.h.connectors.tool(row["server"], row["tool"])
            if (
                current is not server
                or not tool
                or not tool["read_only"]
                or row["tool"] not in (current.spec.get("background_read_only") or [])
            ):
                raise ValueError("Background read-only grant was removed.")
            if result["is_error"]:
                raise ValueError("Connector reported a read error.")
            payload = json.loads(result["text"])
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("items"), list)
                or len(payload["items"]) > 100
            ):
                raise ValueError("Connector must return up to 100 items.")
            cursor = payload.get("cursor")
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 512):
                raise ValueError("Connector cursor is invalid.")
            for item in payload["items"]:
                kind, data, dedupe = _metadata(source, item)
                event = TriggerEvent(source, kind, data, now, dedupe, True)
                if source == "email" and row["last_polled"] is None:
                    # A mailbox snapshot is a baseline, not newly arrived mail.
                    bus.store.execute(
                        "INSERT OR IGNORE INTO trigger_events(id,source,dedupe_key,event,occurred,received) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            uuid4().hex,
                            source,
                            dedupe,
                            json.dumps(event.record(), ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                elif source == "calendar" and kind == "starting_soon":
                    start = datetime.fromisoformat(data["start"].replace("Z", "+00:00"))
                    if start.tzinfo is None:
                        raise ValueError("Calendar start needs a timezone.")
                    stamp = start.timestamp()
                    if stamp > now + 600:
                        bus.store.execute(
                            "INSERT INTO trigger_calendar_pending(id,start,data,received) VALUES (?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET start=excluded.start,data=excluded.data",
                            (dedupe, stamp, json.dumps(data, ensure_ascii=False), now),
                        )
                    elif stamp >= now - 60:
                        await bus.emit(event)
                else:
                    if source == "calendar":
                        bus.store.execute(
                            "DELETE FROM trigger_calendar_pending WHERE json_extract(data,'$.event_id')=?",
                            (data["event_id"],),
                        )
                    await bus.emit(event)
            bus.store.execute(
                "UPDATE trigger_source_bindings SET cursor=?,last_polled=?,last_attempt=?,last_error=NULL WHERE source=?",
                (cursor or row["cursor"], now, now, source),
            )
        except Exception as exc:  # noqa: BLE001 -- a bad provider cannot stop other sources.
            bus.store.execute(
                "UPDATE trigger_source_bindings SET last_attempt=?,last_error=? WHERE source=?",
                (now, str(exc)[:200], source),
            )


async def drain_calendar(bus):
    now = bus.clock()
    bus.store.execute("DELETE FROM trigger_calendar_pending WHERE start<?", (now - 60,))
    for row in bus.store.all(
        "SELECT id,start,data FROM trigger_calendar_pending WHERE start<=? ORDER BY start LIMIT 100",
        (now + 600,),
    ):
        data = json.loads(row["data"])
        await bus.emit(TriggerEvent("calendar", "starting_soon", data, now, row["id"], True))
        bus.store.execute("DELETE FROM trigger_calendar_pending WHERE id=?", (row["id"],))
