"""Device identity and pairing (V2-14 §5.2).

The PC has one long-term signing key and one key-agreement key (P-256), private halves in the DPAPI secret store.
A phone pairs once: Admin shows a QR code whose URL fragment carries the PC's public keys and a one-time code
(single use, 5 minutes, 5 attempts a minute). The phone sends its own public keys with the code; both screens then
show a six-digit SAS, and the device becomes active only after the person confirms the match on the PC. The SAS
defeats a relay or Wi-Fi attacker that swaps keys during pairing. Revocation is immediate.
"""

import base64
import json
import secrets
import time
import uuid
from dataclasses import dataclass

from . import crypto

CODE_TTL_S = 300
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # No 0/O or 1/I/L.
ATTEMPTS_PER_MINUTE = 5


class PairingError(ValueError):
    pass


@dataclass
class PcIdentity:
    sign_key: object
    agree_key: object

    @property
    def sign_pub(self) -> str:
        return crypto.public_raw(self.sign_key)

    @property
    def agree_pub(self) -> str:
        return crypto.public_raw(self.agree_key)

    @property
    def pc_id(self) -> str:
        return base64.b32encode(crypto.sha256(crypto.unb64(self.sign_pub))).decode().lower()[:26]

    @classmethod
    def load(cls, secrets_store) -> "PcIdentity":
        stored = secrets_store.get("fabric_pc_keys")
        if stored:
            data = json.loads(stored) if isinstance(stored, str) else stored
            return cls(crypto.load_private(data["sign"]), crypto.load_private(data["agree"]))
        identity = cls(crypto.new_key(), crypto.new_key())
        secrets_store.set(
            "fabric_pc_keys",
            json.dumps(
                {
                    "sign": crypto.private_bytes(identity.sign_key),
                    "agree": crypto.private_bytes(identity.agree_key),
                }
            ),
        )
        return identity


def sas(pc_sign_pub: str, device_sign_pub: str, code: str) -> str:
    """Six digits both screens show; the person confirms they match."""
    digest = crypto.sha256(crypto.unb64(pc_sign_pub) + crypto.unb64(device_sign_pub) + code.encode())
    number = int.from_bytes(digest[:8], "big") % 1_000_000
    return f"{number:06d}"[:3] + " " + f"{number:06d}"[3:]


class Devices:
    def __init__(self, store, identity: PcIdentity, *, clock=time.time):
        self.store, self.identity, self.clock = store, identity, clock
        self._codes: dict[str, float] = {}  # code -> expiry
        self._attempts: list[float] = []

    # Pairing ---------------------------------------------------------------------------------------
    def new_code(self) -> dict:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        expires = self.clock() + CODE_TTL_S
        self._codes = {c: e for c, e in self._codes.items() if e > self.clock()}
        self._codes[code] = expires
        return {"code": code, "expires": expires}

    def qr_url(self, origin: str, code: dict) -> str:
        i = self.identity
        return (
            f"{origin.rstrip('/')}/pair#v=1&pc={i.pc_id}&ks={i.sign_pub}&ke={i.agree_pub}"
            f"&c={code['code']}&exp={int(code['expires'])}"
        )

    def pair(self, code: str, name: str, sign_pub: str, agree_pub: str, kind: str = "phone") -> dict:
        now = self.clock()
        self._attempts = [t for t in self._attempts if now - t < 60]
        if len(self._attempts) >= ATTEMPTS_PER_MINUTE:
            raise PairingError("Too many pairing attempts. Wait a minute.")
        self._attempts.append(now)
        expires = self._codes.pop(str(code or "").upper(), None)
        if expires is None or expires <= now:
            raise PairingError("That pairing code is wrong or expired.")
        crypto.load_public(sign_pub)
        crypto.load_public(agree_pub)
        device_id = uuid.uuid4().hex
        shown = sas(self.identity.sign_pub, sign_pub, code.upper())
        self.store.execute(
            "INSERT INTO devices_v2(id, name, kind, sign_pub, agree_pub, status, created, sas) VALUES (?,?,?,?,?,?,?,?)",
            (device_id, str(name or "Phone")[:60], kind, sign_pub, agree_pub, "pending", now, shown),
        )
        statement = f"{device_id}|{sign_pub}|{agree_pub}|{self.identity.pc_id}".encode()
        return {
            "device_id": device_id,
            "pc_id": self.identity.pc_id,
            "pc_sign_pub": self.identity.sign_pub,
            "pc_agree_pub": self.identity.agree_pub,
            "signature": crypto.sign(self.identity.sign_key, statement),
            "sas": shown,
        }

    def confirm(self, device_id: str) -> dict:
        """The person confirmed the SAS on the PC."""
        changed = self.store.execute(
            "UPDATE devices_v2 SET status='active' WHERE id=? AND status='pending'", (device_id,)
        ).rowcount
        if not changed:
            raise PairingError("No device is waiting for confirmation.")
        return self.get(device_id)

    # Registry ---------------------------------------------------------------------------------------
    def get(self, device_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM devices_v2 WHERE id=?", (device_id,))
        return dict(row) if row else None

    def active(self, device_id: str) -> dict | None:
        device = self.get(device_id)
        return device if device and device["status"] == "active" else None

    def list(self) -> list[dict]:
        rows = self.store.all(
            "SELECT id, name, kind, status, created, last_seen, sas FROM devices_v2 ORDER BY created"
        )
        return [dict(r) for r in rows]

    def seen(self, device_id: str) -> None:
        self.store.execute("UPDATE devices_v2 SET last_seen=? WHERE id=?", (self.clock(), device_id))

    def revoke(self, device_id: str) -> bool:
        return bool(
            self.store.execute(
                "UPDATE devices_v2 SET status='revoked' WHERE id=? AND status!='revoked'", (device_id,)
            ).rowcount
        )

    def any_active(self) -> bool:
        return self.store.one("SELECT 1 FROM devices_v2 WHERE status='active' LIMIT 1") is not None
