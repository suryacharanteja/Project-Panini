"""Non-secret account configuration in SQLite; credentials in the existing DPAPI vault."""

import json
import re
import time
from uuid import uuid4


class Accounts:
    def __init__(self, store, secrets, clock=time.time):
        self.store, self.secrets, self.clock = store, secrets, clock

    def list(self):
        return [
            self.get(row["id"]) for row in self.store.all("SELECT id FROM channel_accounts ORDER BY created")
        ]

    def get(self, identifier):
        row = self.store.one("SELECT * FROM channel_accounts WHERE id=?", (identifier,))
        if not row:
            raise KeyError("Channel account not found.")
        row["config"] = json.loads(row["config"])
        row["enabled"] = bool(row["enabled"])
        row["token_configured"] = bool(self.secrets.get(f"channel:{identifier}:token"))
        return row

    def save(self, *, label, owner_ids, group_ids=(), enabled=False, token=None, identifier=None):
        label = " ".join(str(label).split())
        if not 1 <= len(label) <= 80:
            raise ValueError("Name this Telegram account (1–80 characters).")
        owners = sorted(set(str(value).strip() for value in owner_ids))
        groups = sorted(set(str(value).strip() for value in group_ids))
        if (
            not owners
            or len(owners) > 20
            or any(not re.fullmatch(r"[1-9]\d{0,18}", value) for value in owners)
        ):
            raise ValueError("Enter 1–20 numeric Telegram owner IDs.")
        if len(groups) > 20 or any(not re.fullmatch(r"-[1-9]\d{0,18}", value) for value in groups):
            raise ValueError("Group IDs must be negative Telegram IDs (maximum 20).")
        if token is not None and token and not re.fullmatch(r"\d{3,20}:[A-Za-z0-9_-]{20,200}", token):
            raise ValueError("Enter a valid Telegram bot token.")
        if identifier:
            self.get(identifier)
        else:
            if self.store.one("SELECT COUNT(*) AS n FROM channel_accounts")["n"] >= 10:
                raise ValueError("Configure at most 10 channel accounts.")
            identifier = uuid4().hex
        key = f"channel:{identifier}:token"
        previous = self.secrets.get(key)
        candidate = token if token is not None else self.secrets.get(key)
        if enabled and not candidate:
            raise ValueError("Save a bot token before enabling this account.")
        if previous and candidate and previous.split(":", 1)[0] != candidate.split(":", 1)[0]:
            raise ValueError("Create a separate account for a different Telegram bot.")
        if candidate:
            for account in self.list():
                other = self.secrets.get(f"channel:{account['id']}:token")
                if (
                    account["id"] != identifier
                    and other
                    and other.split(":", 1)[0] == candidate.split(":", 1)[0]
                ):
                    raise ValueError("This Telegram bot already has an account.")
        if token is not None:
            self.secrets.set(key, token or None)
        now = self.clock()
        self.store.execute(
            "INSERT INTO channel_accounts(id,channel,label,config,enabled,created,updated) VALUES (?,'telegram',?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label,config=excluded.config,enabled=excluded.enabled,updated=excluded.updated",
            (
                identifier,
                label,
                json.dumps({"owner_ids": owners, "group_ids": groups}),
                int(enabled),
                now,
                now,
            ),
        )
        return self.get(identifier)

    def state(self, identifier, key, default=None):
        row = self.store.one(
            "SELECT value FROM channel_state WHERE account_id=? AND key=?", (identifier, key)
        )
        return json.loads(row["value"]) if row else default

    def set_state(self, identifier, key, value):
        self.store.execute(
            "INSERT INTO channel_state VALUES (?,?,?) ON CONFLICT(account_id,key) DO UPDATE SET value=excluded.value",
            (identifier, key, json.dumps(value)),
        )
