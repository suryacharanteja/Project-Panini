"""Non-secret account configuration in SQLite; credentials in the existing DPAPI vault.

Each channel validates its own owner IDs, allow-listed groups, settings and secrets. Provider IDs (never display
names) authorize the owner. Secrets are write-only: responses say only whether each one is configured.
"""

import json
import re
import time
from uuid import uuid4

CHANNELS = ("telegram", "slack", "whatsapp", "email")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")

# channel -> (owner pattern, group pattern, primary secret pattern, extra secrets: name -> pattern, settings rules)
SPECS = {
    "telegram": {
        "owners": (r"[1-9]\d{0,18}", "Enter 1–20 numeric Telegram owner IDs."),
        "groups": (r"-[1-9]\d{0,18}", "Group IDs must be negative Telegram IDs (maximum 20)."),
        "token": (r"\d{3,20}:[A-Za-z0-9_-]{20,200}", "Enter a valid Telegram bot token."),
        "secrets": {},
        "settings": {},
    },
    "slack": {
        "owners": (r"[UW][A-Z0-9]{6,20}", "Enter 1–20 Slack member IDs (U…)."),
        "groups": (r"[CG][A-Z0-9]{6,20}", "Channel IDs start with C or G (maximum 20)."),
        "token": (r"xoxb-[A-Za-z0-9-]{20,250}", "Enter the Slack bot token (xoxb-…)."),
        "secrets": {"app_token": (r"xapp-[A-Za-z0-9-]{20,250}", "Enter the Slack app-level token (xapp-…).")},
        "settings": {"team_id": (r"T[A-Z0-9]{6,20}", "Enter the Slack workspace ID (T…).", True)},
    },
    "whatsapp": {
        "owners": (r"[1-9]\d{7,14}", "Enter 1–20 owner phone numbers in international form (digits only)."),
        "groups": (None, "WhatsApp has no groups here."),
        "token": (r"[A-Za-z0-9_-]{20,500}", "Enter the WhatsApp Cloud API access token."),
        "secrets": {
            "app_secret": (r"[A-Za-z0-9]{16,128}", "Enter the Meta app secret."),
            "verify_token": (r"[A-Za-z0-9_-]{16,128}", "Enter a verify token of 16 or more characters."),
        },
        "settings": {"phone_number_id": (r"\d{5,20}", "Enter the WhatsApp phone number ID.", True)},
    },
    "email": {
        "owners": (EMAIL.pattern, "Enter 1–20 owner email addresses."),
        "groups": (None, "Email has no groups here."),
        "token": (r"[^\s]{4,200}", "Enter the mailbox password or app password."),
        "secrets": {},
        "settings": {
            "address": (EMAIL.pattern, "Enter PANINI's mailbox address.", True),
            "username": (r"[^\s]{1,200}", "Enter the mailbox user name.", True),
            "imap_host": (HOST.pattern, "Enter the IMAP server.", True),
            "smtp_host": (HOST.pattern, "Enter the SMTP server.", True),
            "imap_port": (r"\d{2,5}", "Enter the IMAP port.", False),
            "smtp_port": (r"\d{2,5}", "Enter the SMTP port.", False),
        },
    },
}


class Accounts:
    def __init__(self, store, secrets, clock=time.time):
        self.store, self.secrets, self.clock = store, secrets, clock

    def list(self):
        return [
            self.get(row["id"]) for row in self.store.all("SELECT id FROM channel_accounts ORDER BY created")
        ]

    def secret(self, identifier, name="token"):
        return self.secrets.get(f"channel:{identifier}:{name}")

    def get(self, identifier):
        row = self.store.one("SELECT * FROM channel_accounts WHERE id=?", (identifier,))
        if not row:
            raise KeyError("Channel account not found.")
        row["config"] = json.loads(row["config"])
        row["enabled"] = bool(row["enabled"])
        row["token_configured"] = bool(self.secret(identifier))
        extras = SPECS.get(row["channel"], {}).get("secrets", {})
        row["secrets_configured"] = {name: bool(self.secret(identifier, name)) for name in extras}
        return row

    def save(self, *, label, owner_ids, group_ids=(), enabled=False, token=None, identifier=None,
             channel="telegram", settings=None, secrets=None):  # fmt: skip
        if channel not in SPECS:
            raise ValueError("Unknown channel.")
        spec = SPECS[channel]
        label = " ".join(str(label).split())
        if not 1 <= len(label) <= 80:
            raise ValueError(f"Name this {channel.title()} account (1–80 characters).")
        normalize = (
            (lambda v: str(v).strip().lstrip("+")) if channel == "whatsapp" else (lambda v: str(v).strip())
        )
        owners = sorted(set(normalize(v).lower() if channel == "email" else normalize(v) for v in owner_ids))
        groups = sorted(set(str(v).strip() for v in group_ids))
        pattern, message = spec["owners"]
        if not owners or len(owners) > 20 or any(not re.fullmatch(pattern, v) for v in owners):
            raise ValueError(message)
        pattern, message = spec["groups"]
        if groups and (
            pattern is None or len(groups) > 20 or any(not re.fullmatch(pattern, v) for v in groups)
        ):
            raise ValueError(message)
        pattern, message = spec["token"]
        if token is not None and token and not re.fullmatch(pattern, token):
            raise ValueError(message)
        clean_settings = {}
        for name, (rule, message, required) in spec["settings"].items():
            value = str((settings or {}).get(name, "") or "").strip()
            if not value and not required:
                continue
            if not re.fullmatch(rule, value):
                raise ValueError(message)
            clean_settings[name] = value
        extra = {k: v for k, v in (secrets or {}).items() if v is not None}
        for name, value in extra.items():
            if name not in spec["secrets"]:
                raise ValueError("Unknown secret for this channel.")
            rule, message = spec["secrets"][name]
            if value and not re.fullmatch(rule, value):
                raise ValueError(message)
        if identifier:
            current = self.get(identifier)
            if current["channel"] != channel:
                raise ValueError("An account cannot change channel.")
        else:
            if self.store.one("SELECT COUNT(*) AS n FROM channel_accounts")["n"] >= 10:
                raise ValueError("Configure at most 10 channel accounts.")
            identifier = uuid4().hex
        key = f"channel:{identifier}:token"
        previous = self.secrets.get(key)
        candidate = token if token is not None else previous
        if enabled and not candidate:
            raise ValueError("Save a bot token before enabling this account." if channel == "telegram"
                             else "Save the access credentials before enabling this account.")  # fmt: skip
        if enabled:
            for name in spec["secrets"]:
                if not (extra.get(name) if name in extra else self.secret(identifier, name)):
                    raise ValueError("Save every credential before enabling this account.")
        if channel == "telegram":
            self._unique_bot(identifier, previous, candidate)
        if token is not None:
            self.secrets.set(key, token or None)
        for name, value in extra.items():
            self.secrets.set(f"channel:{identifier}:{name}", value or None)
        now = self.clock()
        config = {"owner_ids": owners, "group_ids": groups, **clean_settings}
        self.store.execute(
            "INSERT INTO channel_accounts(id,channel,label,config,enabled,created,updated) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label,config=excluded.config,enabled=excluded.enabled,updated=excluded.updated",
            (identifier, channel, label, json.dumps(config), int(enabled), now, now),
        )
        return self.get(identifier)

    def _unique_bot(self, identifier, previous, candidate):
        if previous and candidate and previous.split(":", 1)[0] != candidate.split(":", 1)[0]:
            raise ValueError("Create a separate account for a different Telegram bot.")
        if candidate:
            for account in self.list():
                other = self.secret(account["id"])
                if (
                    account["id"] != identifier
                    and account["channel"] == "telegram"
                    and other
                    and other.split(":", 1)[0] == candidate.split(":", 1)[0]
                ):
                    raise ValueError("This Telegram bot already has an account.")

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
