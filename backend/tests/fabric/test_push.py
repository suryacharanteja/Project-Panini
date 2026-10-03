"""V2-14 Web Push: RFC 8291 encryption the subscriber can decrypt, VAPID tokens, minimal payloads, gone cleanup."""

import asyncio
import json
import os

import httpx

from backend.fabric import crypto, push

from .conftest import paired


def test_payloads_decrypt_for_the_subscriber_only():
    ua = crypto.new_key()
    auth = crypto.b64(os.urandom(16))
    body = push.encrypt(b'{"type":"interrupt"}', crypto.public_raw(ua), auth)
    assert push.decrypt(body, ua, auth) == b'{"type":"interrupt"}'
    assert b"interrupt" not in body


def test_vapid_token_is_signed_by_the_pc_key():
    key = crypto.new_key()
    header = push.vapid_header(key, "https://fcm.googleapis.com/fcm/send/abc", "mailto:o@x", now=1000)
    token = header.split("t=")[1].split(",")[0]
    head, claims, sig = token.split(".")
    assert json.loads(crypto.unb64(claims)) == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1000 + 43200,
        "sub": "mailto:o@x",
    }
    assert crypto.verify(crypto.public_raw(key), f"{head}.{claims}".encode(), sig)


def test_sending_reaches_active_devices_with_minimal_payloads_and_drops_gone_subscriptions(fabric):
    devices, _, secrets = fabric
    keys = paired(devices)
    ua, auth = crypto.new_key(), crypto.b64(os.urandom(16))
    for endpoint in ("https://push.example/ok", "https://push.example/gone"):
        devices.store.execute(
            "INSERT INTO push_subscriptions(device_id, endpoint, p256dh, auth, created) VALUES (?,?,?,?,0)",
            (keys.device_id, endpoint, crypto.public_raw(ua), auth),
        )
    seen = []

    def service(request):
        seen.append(json.loads(push.decrypt(request.content, ua, auth)))
        assert request.headers["authorization"].startswith("vapid t=")
        return httpx.Response(410 if request.url.path == "/gone" else 201)

    sender = push.Push(devices.store, secrets, transport=httpx.MockTransport(service))
    message = {
        "type": "interrupt",
        "id": "i1",
        "title": "Approve payment?",
        "level": "tap",
        "detail": "secret",
    }
    result = asyncio.run(sender.send(message))
    assert result == {"sent": 1, "removed": 1}
    assert seen[0] == {
        "type": "interrupt",
        "id": "i1",
        "title": "Approve payment?",
        "level": "tap",
    }  # No details.
    assert len(devices.store.all("SELECT * FROM push_subscriptions")) == 1


def test_opening_an_approval_pushes_it_to_paired_phones(fabric):
    from backend.fabric.runtime import Fabric
    from backend.interrupts import Interrupts, InterruptSpec

    devices, _, secrets = fabric
    from fastapi import FastAPI

    runtime = Fabric(
        FastAPI(), devices.store, secrets, settings_path=devices.store.path.parent / "fabric.json"
    )
    sent = []

    async def send(message, device_id=None):
        sent.append(message)

    runtime.push.send = send
    interrupts = Interrupts(devices.store)
    runtime.watch_interrupts(interrupts)

    async def scenario():
        interrupts.open(
            InterruptSpec("approval", "tool_call", "Send email", "Send?", action={"a": 1}), run_id="r"
        )
        paired(runtime.devices)
        card = interrupts.open(
            InterruptSpec("approval", "tool_call", "Pay", "Pay?", action={"a": 2}), run_id="r"
        )
        await asyncio.sleep(0)
        return card

    card = asyncio.run(scenario())
    assert sent == [
        {"type": "interrupt", "id": card["id"], "title": "Pay", "level": "tap"}
    ]  # Only once paired.
