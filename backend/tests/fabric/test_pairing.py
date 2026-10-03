"""V2-14 pairing: one-time codes, SAS, pending until confirmed on the PC, revocation."""

import pytest

from backend.fabric import crypto
from backend.fabric.devices import PairingError, PcIdentity, sas

from .conftest import paired


def test_the_pc_identity_is_created_once_and_reloaded(fabric):
    devices, identity, secrets = fabric
    again = PcIdentity.load(secrets)
    assert again.pc_id == identity.pc_id and len(identity.pc_id) == 26
    url = devices.qr_url("https://pc.example.ts.net", devices.new_code())
    assert url.startswith("https://pc.example.ts.net/pair#v=1&pc=") and f"ks={identity.sign_pub}" in url


def test_pairing_shows_the_same_sas_and_waits_for_confirmation(fabric):
    devices, identity, _ = fabric
    sign, agree = crypto.new_key(), crypto.new_key()
    code = devices.new_code()["code"]
    answer = devices.pair(code, "Pixel", crypto.public_raw(sign), crypto.public_raw(agree))
    assert answer["sas"] == sas(identity.sign_pub, crypto.public_raw(sign), code)  # What the phone computes.
    statement = f"{answer['device_id']}|{crypto.public_raw(sign)}|{crypto.public_raw(agree)}|{identity.pc_id}"
    assert crypto.verify(identity.sign_pub, statement.encode(), answer["signature"])
    assert devices.active(answer["device_id"]) is None  # Pending until the person confirms.
    devices.confirm(answer["device_id"])
    assert devices.active(answer["device_id"])["name"] == "Pixel"
    with pytest.raises(PairingError):
        devices.confirm(answer["device_id"])


def test_codes_are_single_use_expire_and_are_rate_limited(fabric):
    devices, _, _ = fabric
    pub = crypto.public_raw(crypto.new_key())
    code = devices.new_code()["code"]
    devices.pair(code, "A", pub, pub)
    with pytest.raises(PairingError, match="wrong or expired"):
        devices.pair(code, "B", pub, pub)
    now = [devices.clock()]
    devices.clock = lambda: now[0]
    late = devices.new_code()["code"]
    now[0] += 301
    with pytest.raises(PairingError, match="wrong or expired"):
        devices.pair(late, "C", pub, pub)
    for _ in range(4):  # With the expired attempt above: five in this minute.
        with pytest.raises(PairingError):
            devices.pair("NOPE1234", "D", pub, pub)
    with pytest.raises(PairingError, match="Too many"):
        devices.pair(devices.new_code()["code"], "E", pub, pub)


def test_revocation_is_immediate(fabric):
    devices, _, _ = fabric
    keys = paired(devices)
    assert devices.revoke(keys.device_id) and devices.active(keys.device_id) is None
