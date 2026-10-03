"""Web Push (V2-14 §5.6), RFC 8291 (aes128gcm) with VAPID (RFC 8292), on `cryptography` alone.

The PC sends pushes straight to each subscription's push service. Payloads are encrypted to the device and carry
only {type, id, title, level}; details are fetched over the fabric after unlock.
"""

import json
import os
import struct
import time
from urllib.parse import urlsplit

import httpx

from . import crypto

RECORD_SIZE = 4096


def encrypt(payload: bytes, p256dh: str, auth: str, *, sender_key=None, salt: bytes | None = None) -> bytes:
    """RFC 8291 message encryption: the body of a Web Push request (Content-Encoding: aes128gcm)."""
    sender = sender_key or crypto.new_key()
    salt = salt or os.urandom(16)
    ua_public = crypto.unb64(p256dh)
    as_public = crypto.unb64(crypto.public_raw(sender))
    shared = crypto.ecdh(sender, p256dh)
    ikm = crypto.hkdf(shared, crypto.unb64(auth), b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = crypto.hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = crypto.hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    record = crypto.seal(cek, nonce, payload + b"\x02", b"")  # One record, last-record delimiter.
    header = salt + struct.pack(">I", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + record


def decrypt(body: bytes, ua_private, auth: str) -> bytes:
    """The subscriber's side (tests, and a reference for the app)."""
    salt, idlen = body[:16], body[20]
    as_public = body[21 : 21 + idlen]
    ua_public = crypto.unb64(crypto.public_raw(ua_private))
    shared = crypto.ecdh(ua_private, crypto.b64(as_public))
    ikm = crypto.hkdf(shared, crypto.unb64(auth), b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = crypto.hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = crypto.hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    plain = crypto.open_(cek, nonce, body[21 + idlen :], b"")
    return plain.rstrip(b"\x00")[:-1]


def vapid_header(vapid_key, endpoint: str, subject: str, *, now=None) -> str:
    """Authorization: vapid t=<ES256 JWT>, k=<public key>."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int((now or time.time()) + 12 * 3600),
        "sub": subject,
    }
    head = crypto.b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = crypto.b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = crypto.sign(vapid_key, f"{head}.{body}".encode())
    return f"vapid t={head}.{body}.{signature}, k={crypto.public_raw(vapid_key)}"


class Push:
    def __init__(self, store, secrets_store, *, subject="mailto:owner@panini.local", transport=None):
        self.store, self.secrets, self.subject, self.transport = store, secrets_store, subject, transport
        stored = secrets_store.get("fabric_vapid")
        if stored:
            self.vapid = crypto.load_private(stored)
        else:
            self.vapid = crypto.new_key()
            secrets_store.set("fabric_vapid", crypto.private_bytes(self.vapid))

    @property
    def public_key(self) -> str:
        return crypto.public_raw(self.vapid)

    async def send(self, message: dict, device_id: str | None = None) -> dict:
        """Send to one device's subscriptions, or to every active device. Gone subscriptions are removed."""
        allowed = {"type", "id", "title", "level"}
        payload = json.dumps({k: v for k, v in message.items() if k in allowed})[:3000].encode()
        query = (
            "SELECT s.* FROM push_subscriptions s JOIN devices_v2 d ON d.id = s.device_id WHERE d.status='active'"
            + (" AND s.device_id=?" if device_id else "")
        )
        rows = self.store.all(query, (device_id,) if device_id else ())
        sent, gone = 0, 0
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
            for row in rows:
                body = encrypt(payload, row["p256dh"], row["auth"])
                response = await client.post(
                    row["endpoint"],
                    content=body,
                    headers={
                        "Content-Encoding": "aes128gcm",
                        "TTL": "3600",
                        "Urgency": "high" if message.get("type") == "interrupt" else "normal",
                        "Authorization": vapid_header(self.vapid, row["endpoint"], self.subject),
                    },
                )
                if response.status_code in (404, 410):
                    self.store.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (row["endpoint"],))
                    gone += 1
                elif response.status_code < 300:
                    sent += 1
        return {"sent": sent, "removed": gone}
