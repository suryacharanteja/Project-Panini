"""Real cryptographic WebAuthn vectors, replay/device/action/origin and UV checks."""

import hashlib
import json

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException

from backend.fabric.passkeys import Passkeys

from .conftest import paired

ORIGIN = "https://panini.example.com"
RP = "panini.example.com"


@pytest.fixture
def setup(fabric):
    devices, _, _ = fabric
    phone = paired(devices)
    now = [1000.0]
    return Passkeys(devices.store, devices, lambda: ORIGIN, clock=lambda: now[0]), phone, now


def client_data(challenge, kind, origin=ORIGIN):
    return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()


def registration(options, *, uv=True, origin=ORIGIN):
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_numbers()
    credential_id = hashlib.sha256(public.x.to_bytes(32, "big")).digest()
    cose = cbor2.dumps(
        {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")}
    )
    auth = hashlib.sha256(RP.encode()).digest() + bytes([0x45 if uv else 0x41]) + bytes(4)
    auth += bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + cose
    encoded_id = bytes_to_base64url(credential_id)
    return key, {
        "id": encoded_id,
        "rawId": encoded_id,
        "type": "public-key",
        "response": {
            "clientDataJSON": bytes_to_base64url(
                client_data(options["challenge"], "webauthn.create", origin)
            ),
            "attestationObject": bytes_to_base64url(
                cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
            ),
        },
    }


def assertion(options, key, credential_id, *, count=1, uv=True, origin=ORIGIN, challenge=None):
    auth = hashlib.sha256(RP.encode()).digest() + bytes([5 if uv else 1]) + count.to_bytes(4, "big")
    client = client_data(challenge or options["challenge"], "webauthn.get", origin)
    signature = key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": {
            "authenticatorData": bytes_to_base64url(auth),
            "clientDataJSON": bytes_to_base64url(client),
            "signature": bytes_to_base64url(signature),
        },
    }


def enrolled(setup):
    service, phone, now = setup
    begun = service.begin(phone.device_id, ORIGIN)
    key, credential = registration(begun["options"])
    service.finish(phone.device_id, begun["ceremony_id"], credential)
    return service, phone, now, key, credential["id"]


def test_real_registration_and_exact_decision_assertion(setup):
    service, phone, _, key, cid = enrolled(setup)
    statement = b"i1|nonce|digest|resolved|payloadhash"
    begun = service.begin(phone.device_id, ORIGIN, statement=statement)
    proof = assertion(begun["options"], key, cid)
    assert service.finish(phone.device_id, begun["ceremony_id"], proof, statement=statement)["verified"]
    assert service.store.one("SELECT sign_count FROM device_passkeys")["sign_count"] == 1
    with pytest.raises(ValueError, match="already used"):
        service.finish(phone.device_id, begun["ceremony_id"], proof, statement=statement)


@pytest.mark.parametrize("attack", ["origin", "uv", "challenge", "signature", "rp", "counter"])
def test_invalid_authenticator_assertions_are_refused(setup, attack):
    service, phone, _, key, cid = enrolled(setup)
    begun = service.begin(phone.device_id, ORIGIN, statement=b"approved")
    proof = assertion(
        begun["options"],
        key,
        cid,
        origin="https://evil.example" if attack == "origin" else ORIGIN,
        uv=attack != "uv",
        challenge="wrong" if attack == "challenge" else None,
    )
    if attack == "signature":
        proof["response"]["signature"] = bytes_to_base64url(b"not signed")
    if attack == "rp":
        raw = base64url_to_bytes(proof["response"]["authenticatorData"])
        proof["response"]["authenticatorData"] = bytes_to_base64url(bytes(32) + raw[32:])
    if attack == "counter":
        service.store.execute("UPDATE device_passkeys SET sign_count=1")
    with pytest.raises(WebAuthnException):
        service.finish(phone.device_id, begun["ceremony_id"], proof, statement=b"approved")
    assert service.store.one("SELECT used FROM passkey_ceremonies")["used"] == 1


@pytest.mark.parametrize("attack", ["decision", "device", "expired", "revoked", "origin-config"])
def test_action_and_device_bound_ceremonies(setup, attack):
    service, phone, now, key, cid = enrolled(setup)
    begun = service.begin(phone.device_id, ORIGIN, statement=b"approved")
    proof = assertion(begun["options"], key, cid)
    device = paired(service.devices).device_id if attack == "device" else phone.device_id
    if attack == "expired":
        now[0] += 121
    if attack == "revoked":
        service.devices.revoke(phone.device_id)
        assert not service.list(phone.device_id)
    if attack == "origin-config":
        service.origin = lambda: "https://new.example.com"
    with pytest.raises(ValueError):
        service.finish(
            device,
            begun["ceremony_id"],
            proof,
            statement=b"declined" if attack == "decision" else b"approved",
        )


def test_registration_requires_uv_and_authoritative_origin(setup):
    service, phone, _ = setup
    with pytest.raises(ValueError, match="does not match"):
        service.begin(phone.device_id, "https://evil.example")
    begun = service.begin(phone.device_id, ORIGIN)
    _, credential = registration(begun["options"], uv=False)
    with pytest.raises(WebAuthnException):
        service.finish(phone.device_id, begun["ceremony_id"], credential)
    assert not service.list(phone.device_id)


def test_remove_invalidates_pending_assertion_and_is_scoped(setup):
    service, phone, _, _, cid = enrolled(setup)
    service.begin(phone.device_id, ORIGIN, statement=b"approved")
    other = paired(service.devices)
    service.remove(other.device_id, cid)
    assert service.list(phone.device_id)
    service.remove(phone.device_id, cid)
    assert not service.list(phone.device_id)
    assert not service.store.all("SELECT * FROM passkey_ceremonies WHERE device_id=?", (phone.device_id,))
