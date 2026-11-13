"""Caller IDs select an identity; a salted PIN proves it for the current call."""

import hashlib
import hmac
import re
import secrets
import time

NUMBER = re.compile(r"^\+[1-9][0-9]{6,14}$")
PIN = re.compile(r"^[0-9]{6,8}$")
ITERATIONS = 600_000


def normalize(value):
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise ValueError("Use a full international number, for example +14155550123.")
    return value


def from_headers(headers):
    if not isinstance(headers, list):
        return None
    found = [
        str(h.get("value", ""))
        for h in headers
        if isinstance(h, dict) and str(h.get("name", "")).lower() == "from"
    ]
    if len(found) != 1:
        return None
    # Parse only a SIP/tel address, never a display name pretending to be the caller ID.
    value = found[0]
    match = re.search(r"(?:<|^)(?:sips?:|tel:)(\+[1-9][0-9]{6,14})(?:@|[;>]|$)", value)
    return match.group(1) if match else None


class Callers:
    def __init__(self, store):
        self.store = store

    def put(self, number, name, pin, *, kind="owner", persona_id="panini"):
        number = normalize(number)
        if (
            kind not in {"owner", "contact", "blocked"}
            or not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 80
        ):
            raise ValueError("Choose a caller name and supported caller type.")
        if kind != "blocked" and (not isinstance(pin, str) or not PIN.fullmatch(pin)):
            raise ValueError("Choose a 6–8 digit PIN.")
        if kind == "owner" and persona_id != "panini":
            raise ValueError("Owner callers use PANINI; contacts need a restricted specialist.")
        if kind == "contact" and persona_id == "panini":
            raise ValueError("Contacts need a restricted specialist.")
        salt = secrets.token_hex(16)
        hashed = hashlib.pbkdf2_hmac("sha256", (pin or "").encode(), bytes.fromhex(salt), ITERATIONS).hex()
        self.store.execute(
            "INSERT INTO callers(number,name,kind,persona_id,salt,pin_hash,created) VALUES (?,?,?,?,?,?,?) ON CONFLICT(number) DO UPDATE SET name=excluded.name,kind=excluded.kind,persona_id=excluded.persona_id,salt=excluded.salt,pin_hash=excluded.pin_hash,enabled=1",
            (number, name.strip(), kind, persona_id, salt, hashed, time.time()),
        )

    def get(self, number):
        return self.store.one("SELECT * FROM callers WHERE number=? AND enabled=1", (number,))

    def list(self):
        return self.store.all("SELECT number,name,kind,persona_id,enabled FROM callers ORDER BY name")

    def remove(self, number):
        self.store.execute("UPDATE callers SET enabled=0 WHERE number=?", (normalize(number),))

    def verify(self, caller, pin):
        if not PIN.fullmatch(pin):
            return False
        value = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(caller["salt"]), ITERATIONS).hex()
        return hmac.compare_digest(value, caller["pin_hash"])
