"""V2-14 envelopes: mutual handshake, AES-GCM messages, replay and tamper rejection, expiry, revocation."""

import pytest

from backend.fabric import crypto, envelope
from backend.fabric.envelope import EnvelopeError

from .conftest import paired


def connect(devices, identity, keys, clock=None):
    timing = {"clock": clock} if clock else {}
    hello, eph = envelope.hello(keys, identity.pc_id, **timing)
    welcome, pc = envelope.welcome(identity, devices, hello, **timing)
    phone = envelope.finish(keys, eph, welcome, identity.pc_id, identity.sign_pub, identity.agree_pub)
    return phone, pc


def test_both_sides_derive_the_same_session_and_talk(fabric):
    devices, identity, _ = fabric
    phone, pc = connect(devices, identity, paired(devices))
    message = phone.seal("req", {"method": "GET", "path": "/status"})
    assert "/status" not in str(message)  # Ciphertext only on the wire.
    assert pc.open(message)[2] == {"method": "GET", "path": "/status"}
    reply = pc.seal("res", {"status": 200}, msg_id=message["id"])
    assert phone.open(reply) == ("res", message["id"], {"status": 200})


def test_replays_reordering_and_tampering_are_rejected(fabric):
    devices, identity, _ = fabric
    phone, pc = connect(devices, identity, paired(devices))
    first, second = phone.seal("req", {"n": 1}), phone.seal("req", {"n": 2})
    pc.open(second)
    with pytest.raises(EnvelopeError, match="Replayed"):
        pc.open(first)
    with pytest.raises(EnvelopeError, match="Replayed"):
        pc.open(second)
    third = phone.seal("req", {"n": 3})
    third["kind"] = "evt"  # AAD covers the header.
    with pytest.raises(EnvelopeError):
        pc.open(third)


def test_unknown_revoked_or_forged_devices_cannot_open_a_session(fabric):
    devices, identity, _ = fabric
    keys = paired(devices)
    hello, _ = envelope.hello(keys, identity.pc_id)
    forged = dict(hello, sig=crypto.sign(crypto.new_key(), b"x"))
    with pytest.raises(EnvelopeError, match="not signed by this device"):
        envelope.welcome(identity, devices, forged)
    devices.revoke(keys.device_id)
    with pytest.raises(EnvelopeError, match="revoked"):
        envelope.welcome(identity, devices, hello)


def test_a_phone_rejects_a_welcome_not_signed_by_its_pc(fabric):
    devices, identity, _ = fabric
    keys = paired(devices)
    hello, eph = envelope.hello(keys, identity.pc_id)
    welcome, _ = envelope.welcome(identity, devices, hello)
    impostor = crypto.public_raw(crypto.new_key())
    with pytest.raises(EnvelopeError, match="not signed by your PC"):
        envelope.finish(keys, eph, welcome, identity.pc_id, impostor, identity.agree_pub)


def test_sessions_expire_after_twelve_hours(fabric):
    devices, identity, _ = fabric
    now = [1_000_000.0]
    phone, pc = connect(devices, identity, paired(devices), clock=lambda: now[0])
    pc.clock = lambda: now[0]
    hello_time = now[0]
    now[0] = hello_time + 12 * 3600 + 1
    with pytest.raises(EnvelopeError, match="expired"):
        pc.seal("evt", {})
