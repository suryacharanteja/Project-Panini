"""Expiring, server-timestamped presence. No location or conversation contents in signals."""

import json
import math
import time
from uuid import uuid4

from .. import v2flags


class Presence:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock, self.boot = store, clock, uuid4().hex

    def signal(self, source, data):
        if source == "pc":
            if set(data) != {"idle_s", "locked", "suspended", "avatar_visible"}:
                raise ValueError("PC signal needs idle, lock, suspend and visibility fields.")
            if (
                type(data["idle_s"]) not in {int, float}
                or not math.isfinite(data["idle_s"])
                or not 0 <= data["idle_s"] <= 604800
            ):
                raise ValueError("Invalid idle time.")
            if any(type(data[key]) is not bool for key in ("locked", "suspended", "avatar_visible")):
                raise ValueError("Invalid PC signal.")
        elif source.startswith("phone:"):
            if set(data) != {"foreground"} or type(data["foreground"]) is not bool:
                raise ValueError("Invalid phone signal.")
        elif source not in {"push", "channel"} or data:
            raise ValueError("Unknown presence source.")
        self.store.execute(
            "INSERT INTO presence_signals(source,data,seen,boot) VALUES (?,?,?,?) "
            "ON CONFLICT(source) DO UPDATE SET data=excluded.data,seen=excluded.seen,boot=excluded.boot",
            (source, json.dumps(data), self.clock(), self.boot),
        )
        return self.status()

    def status(self, *, on_call=False):
        now, signals = self.clock(), []
        for row in self.store.all("SELECT * FROM presence_signals ORDER BY source"):
            age = now - row["seen"]
            data = json.loads(row["data"])
            ttl = 30 if row["source"] == "pc" else 60 if row["source"].startswith("phone:") else 600
            fresh = row["boot"] == self.boot and 0 <= age < ttl
            signals.append(
                {
                    "source": row["source"],
                    "seen": row["seen"],
                    "fresh": fresh,
                    "data": data,
                    "recent": 0 <= age < 600,
                }
            )
        pc = next((s for s in signals if s["source"] == "pc" and s["fresh"]), None)
        state = "away"
        if any(s["recent"] and (s["source"].startswith("phone:") or s["source"] == "push") for s in signals):
            state = "away_with_phone"
        if pc and not pc["data"]["locked"] and not pc["data"]["suspended"]:
            idle = pc["data"]["idle_s"]
            state = "at_pc" if idle < 120 else "near_pc" if idle < 900 else state
        if on_call:
            state = "on_call"
        return {"enabled": v2flags.enabled("PANINI_PRESENCE"), "state": state, "signals": signals}
