"""Webhook ingress (V2-14 §5.5): provider deliveries reach the PC (Tailscale Funnel or the relay) and are verified
here before anything else. Unverified requests get 401 with no detail; the path secret only reduces noise.

| Kind     | Verification                                                                                     |
| openai   | Standard Webhooks: HMAC-SHA256 over "id.timestamp.body" with the whsec_ secret, 5-minute window, id dedupe |
| whatsapp | GET hub.verify_token -> echo hub.challenge; POST X-Hub-Signature-256 = sha256=HMAC(app_secret, body)  |
| generic  | X-PANINI-Signature = HMAC-SHA256(secret, "timestamp.body"), X-PANINI-Timestamp, X-PANINI-Delivery dedupe |

Consumers (calls in V2-16, channels in V2-17, triggers in V2-25) register a handler per kind with `on()`.
"""

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import parse_qs

TOLERANCE_S = 300
SEEN_TTL_S = 24 * 3600
DENIED = {"status": 401, "body": ""}


def _eq(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def verify_standard(secret: str, headers: dict, body: bytes, now: float) -> str | None:
    """Standard Webhooks (OpenAI). Returns the delivery id when valid."""
    h = {k.lower(): v for k, v in headers.items()}
    delivery, stamp, signatures = (
        h.get("webhook-id", ""),
        h.get("webhook-timestamp", ""),
        h.get("webhook-signature", ""),
    )
    try:
        if not delivery or abs(now - int(stamp)) > TOLERANCE_S:
            return None
    except ValueError:
        return None
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(
        hmac.new(key, f"{delivery}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    for part in signatures.split():
        version, _, value = part.partition(",")
        if version == "v1" and _eq(value, expected):
            return delivery
    return None


def verify_whatsapp(app_secret: str, headers: dict, body: bytes) -> bool:
    h = {k.lower(): v for k, v in headers.items()}
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return _eq(h.get("x-hub-signature-256", ""), expected)


def whatsapp_challenge(verify_token: str, query: str) -> str | None:
    q = {k: v[0] for k, v in parse_qs(query.lstrip("?")).items()}
    if q.get("hub.mode") == "subscribe" and _eq(q.get("hub.verify_token", ""), verify_token):
        return q.get("hub.challenge", "")
    return None


def verify_generic(secret: str, headers: dict, body: bytes, now: float) -> str | None:
    h = {k.lower(): v for k, v in headers.items()}
    stamp, delivery = h.get("x-panini-timestamp", ""), h.get("x-panini-delivery", "")
    try:
        if not delivery or abs(now - int(stamp)) > TOLERANCE_S:
            return None
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return delivery if _eq(h.get("x-panini-signature", ""), expected) else None


class Ingress:
    """hooks: {hook_id: {"kind": openai|whatsapp|generic, "path_secret": str, "secret": str, "verify_token": str}}"""

    def __init__(self, store, hooks: dict, *, clock=time.time):
        self.store, self.hooks, self.clock = store, hooks, clock
        self.handlers: dict[str, object] = {}

    def on(self, kind: str, handler) -> None:
        self.handlers[kind] = handler

    def _first_time(self, hook_id: str, delivery: str) -> bool:
        now = self.clock()
        self.store.execute("DELETE FROM ingress_seen WHERE seen < ?", (now - SEEN_TTL_S,))
        return bool(
            self.store.execute(
                "INSERT OR IGNORE INTO ingress_seen(hook, delivery_id, seen) VALUES (?,?,?)",
                (hook_id, delivery, now),
            ).rowcount
        )

    async def __call__(self, delivery: dict) -> dict:
        """A delivery from the relay ({method, path, query, headers, body (base64)}) or the Funnel route."""
        hook_id, _, path_secret = delivery.get("path", "").strip("/").partition("/")
        hook = self.hooks.get(hook_id)
        if hook is None or not _eq(path_secret, hook.get("path_secret", "")):
            return DENIED
        if hook["kind"] == "generic" and len(delivery.get("body") or "") > 90000:
            return {"status": 413, "body": ""}
        body = base64.b64decode(delivery.get("body") or "")
        headers, method, now = (
            delivery.get("headers") or {},
            delivery.get("method", "POST").upper(),
            self.clock(),
        )
        kind = hook["kind"]
        if kind == "whatsapp" and method == "GET":
            challenge = whatsapp_challenge(hook.get("verify_token", ""), delivery.get("query", ""))
            return {"status": 200, "body": challenge} if challenge is not None else DENIED
        if kind == "openai":
            delivery_id = verify_standard(hook["secret"], headers, body, now)
        elif kind == "whatsapp":
            delivery_id = (
                hashlib.sha256(body).hexdigest() if verify_whatsapp(hook["secret"], headers, body) else None
            )
        elif kind == "generic":
            delivery_id = verify_generic(hook["secret"], headers, body, now)
        else:
            delivery_id = None
        if delivery_id is None:
            return DENIED
        if not self._first_time(hook_id, delivery_id):
            return {"status": 200, "body": "duplicate"}  # Providers retry; answer success, act once.
        handler = self.handlers.get(kind)
        if handler is None:
            return {"status": 202, "body": "accepted"}
        try:
            answer = await handler(hook_id, headers, body)
        except Exception:
            # A failed internal hand-off must be retryable with the same signed delivery ID.
            self.store.execute(
                "DELETE FROM ingress_seen WHERE hook=? AND delivery_id=?", (hook_id, delivery_id)
            )
            raise
        if isinstance(answer, dict) and answer.get("status", 200) >= 500:
            self.store.execute(
                "DELETE FROM ingress_seen WHERE hook=? AND delivery_id=?", (hook_id, delivery_id)
            )
        return answer if isinstance(answer, dict) else {"status": 200, "body": ""}
