"""Browser-extension device pairing: one-time codes and mutual challenge-response.

Pairing happens once per browser profile. A trusted surface (the PANINI shell or
the signed-in web console) creates a short one-time code; the user types it into
the extension, which redeems it for a device secret. On every connection both
sides then prove possession of that secret, so neither an unpaired local process
nor an impostor server listening on the runtime port can take part.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # No 0/O, 1/I/L or U ambiguity.
CODE_TTL = 300
MAX_PENDING_CODES = 3
MAX_DEVICES = 8
MAX_FAILED_REDEMPTIONS = 10
FAILURE_WINDOW = 300


def data_directory():
    configured = os.getenv("PANINI_DATA_DIR")
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData/Local") / "PANINI"
    return Path.home() / ".local/share/panini"


def normalize_code(value):
    return "".join(ch for ch in str(value).upper() if ch in CODE_ALPHABET)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def proof(secret, role, first_nonce, second_nonce):
    """HMAC proof bound to the speaker's role and both connection nonces."""
    message = f"panini-extension-v1|{role}|{first_nonce}|{second_nonce}".encode()
    return hmac.new(bytes.fromhex(secret), message, hashlib.sha256).hexdigest()


class PairingError(Exception):
    pass


class PairingStore:
    def __init__(self, path=None, clock=time.time):
        self._path = Path(path) if path else None
        self.clock = clock
        self._lock = threading.Lock()
        self._devices = None
        self._codes = {}
        self._failures = []

    @property
    def path(self):
        return self._path or data_directory() / "extension-pairings.json"

    def _load(self):
        if self._devices is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._devices = {
                    device_id: record
                    for device_id, record in data.get("devices", {}).items()
                    if isinstance(record, dict) and len(record.get("secret", "")) == 64
                }
            except (OSError, ValueError, AttributeError):
                self._devices = {}
        return self._devices

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "devices": self._devices}), encoding="utf-8")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def create_code(self):
        with self._lock:
            now = self.clock()
            self._codes = {k: v for k, v in self._codes.items() if v > now}
            if len(self._codes) >= MAX_PENDING_CODES:
                # Keep the newest codes; an older code the user never used simply expires early.
                oldest = min(self._codes, key=self._codes.get)
                self._codes.pop(oldest)
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            self._codes[_digest(code)] = now + CODE_TTL
            return {"code": f"{code[:4]}-{code[4:]}", "expires_in": CODE_TTL}

    def redeem(self, code, origin="", label=""):
        with self._lock:
            now = self.clock()
            self._failures = [t for t in self._failures if t > now - FAILURE_WINDOW]
            if len(self._failures) >= MAX_FAILED_REDEMPTIONS:
                # Too many guesses: every outstanding code is burned, not just slowed down.
                self._codes.clear()
                raise PairingError("Too many incorrect pairing codes. Create a new code and try again.")
            key = _digest(normalize_code(code))
            if self._codes.pop(key, 0) <= now:
                self._failures.append(now)
                raise PairingError("This pairing code is incorrect or has expired.")
            devices = self._load()
            if len(devices) >= MAX_DEVICES:
                oldest = min(devices, key=lambda d: devices[d].get("created", 0))
                devices.pop(oldest)
            device_id = secrets.token_hex(16)
            secret = secrets.token_hex(32)
            devices[device_id] = {
                "secret": secret,
                "origin": str(origin)[:200],
                "label": str(label)[:100],
                "created": now,
            }
            self._save()
            return {"device_id": device_id, "secret": secret}

    def secret(self, device_id):
        with self._lock:
            record = self._load().get(str(device_id))
            return record["secret"] if record else None

    def origin(self, device_id):
        with self._lock:
            record = self._load().get(str(device_id))
            return record.get("origin", "") if record else ""

    def devices(self):
        with self._lock:
            return [
                {"device_id": device_id, "label": record.get("label", ""), "created": record.get("created")}
                for device_id, record in self._load().items()
            ]

    def revoke(self, device_id):
        with self._lock:
            removed = self._load().pop(str(device_id), None) is not None
            if removed:
                self._save()
            return removed
