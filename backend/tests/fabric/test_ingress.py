"""V2-14 webhook ingress: verified before anything else; bad signatures, stale timestamps and repeats refused."""

import asyncio
import base64
import hashlib
import hmac

import pytest

from backend.fabric.ingress import Ingress

KEY = b"0123456789abcdef0123456789abcdef"
WHSEC = "whsec_" + base64.b64encode(KEY).decode()
NOW = 1_800_000_000


def standard_headers(delivery, body, stamp=NOW, key=KEY):
    sig = base64.b64encode(
        hmac.new(key, f"{delivery}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"webhook-id": delivery, "webhook-timestamp": str(stamp), "webhook-signature": f"v1,{sig}"}


@pytest.fixture
def ingress(fabric):
    devices, _, _ = fabric
    hooks = {
        "calls": {"kind": "openai", "path_secret": "p1", "secret": WHSEC},
        "wa": {"kind": "whatsapp", "path_secret": "p2", "secret": "app-secret", "verify_token": "vt"},
        "gen": {"kind": "generic", "path_secret": "p3", "secret": "gs"},
    }
    return Ingress(devices.store, hooks, clock=lambda: NOW)


def deliver(ingress, path, body=b"", headers=None, method="POST", query=""):
    return asyncio.run(ingress({"method": method, "path": path, "query": query, "headers": headers or {},
                                "body": base64.b64encode(body).decode()}))  # fmt: skip


def test_openai_standard_webhooks(ingress):
    got = []

    async def handler(hook, headers, body):
        got.append(body)
        return {"status": 200, "body": "ok"}

    ingress.on("openai", handler)
    body = b'{"type":"realtime.call.incoming"}'
    assert deliver(ingress, "/calls/p1", body, standard_headers("d1", body))["status"] == 200
    assert deliver(ingress, "/calls/p1", body, standard_headers("d1", body))["body"] == "duplicate"
    assert deliver(ingress, "/calls/p1", body, standard_headers("d2", body, key=b"x" * 32))["status"] == 401
    assert deliver(ingress, "/calls/p1", body, standard_headers("d3", body, stamp=NOW - 301))["status"] == 401
    assert deliver(ingress, "/calls/wrong", body, standard_headers("d4", body))["status"] == 401
    assert got == [body]  # Acted once.


def test_whatsapp_challenge_and_signature(ingress):
    ok = deliver(
        ingress, "/wa/p2", method="GET", query="hub.mode=subscribe&hub.verify_token=vt&hub.challenge=42"
    )
    assert ok == {"status": 200, "body": "42"}
    assert (
        deliver(
            ingress, "/wa/p2", method="GET", query="hub.mode=subscribe&hub.verify_token=no&hub.challenge=42"
        )["status"]
        == 401
    )
    body = b'{"entry":[]}'
    good = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()}
    assert deliver(ingress, "/wa/p2", body, good)["status"] == 202
    assert deliver(ingress, "/wa/p2", body, {"X-Hub-Signature-256": "sha256=00"})["status"] == 401


def test_generic_signed_hooks(ingress):
    body = b"{}"
    sig = hmac.new(b"gs", f"{NOW}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"X-PANINI-Signature": sig, "X-PANINI-Timestamp": str(NOW), "X-PANINI-Delivery": "g1"}
    assert deliver(ingress, "/gen/p3", body, headers)["status"] == 202
    assert (
        deliver(ingress, "/gen/p3", body, dict(headers, **{"X-PANINI-Signature": "0" * 64}))["status"] == 401
    )
