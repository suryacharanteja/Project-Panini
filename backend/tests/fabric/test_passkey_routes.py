"""WebAuthn proofs pass through the real encrypted fabric and interrupt authority."""

import asyncio

from backend.fabric import crypto
from backend.fabric.passkeys import Passkeys
from backend.fabric.router import decision_statement
from backend.interrupts import InterruptSpec

from .conftest import paired
from .test_decisions import Phone
from .test_decisions import world as world
from .test_passkeys import ORIGIN, assertion, registration


def test_encrypted_passkey_decision_and_changed_action_refusal(world):
    app, devices, identity, interrupts = world
    keys = paired(devices)
    app.state.fabric.passkeys = Passkeys(devices.store, devices, lambda: ORIGIN)

    async def scenario():
        phone = Phone(app, identity, keys)
        await phone.connect()
        begun = await phone.call("POST", "/passkeys/register/options", {"origin": ORIGIN})
        key, credential = registration(begun["body"]["options"])
        assert (
            await phone.call(
                "POST",
                "/passkeys/register",
                {
                    "ceremony_id": begun["body"]["ceremony_id"],
                    "credential": credential,
                },
            )
        )["status"] == 200
        card = interrupts.open(
            InterruptSpec("approval", "test", "Send", "Send?", action={"to": "owner"}, level="verify"),
            run_id="test",
        )
        card = interrupts.get(card["id"], include_secret=True)
        statement = decision_statement(
            card["id"], card["nonce"], card["action_digest"], "resolved", {"approved": True}
        )
        decision = {
            "nonce": card["nonce"],
            "action_digest": card["action_digest"],
            "status": "resolved",
            "payload": {"approved": True},
            "signature": crypto.sign(keys.sign_key, statement),
        }
        assert (await phone.call("POST", f"/interrupts/{card['id']}/resolve", decision))["status"] == 403
        challenge = await phone.call(
            "POST", f"/interrupts/{card['id']}/passkey/options", {**decision, "origin": ORIGIN}
        )
        assert challenge["status"] == 200
        proof = assertion(challenge["body"]["options"], key, credential["id"])
        body = {**decision, "ceremony_id": challenge["body"]["ceremony_id"], "credential": proof}
        devices.store.execute("UPDATE interrupts SET action_digest=? WHERE id=?", ("changed", card["id"]))
        assert (await phone.call("POST", f"/interrupts/{card['id']}/resolve", body))["status"] == 409
        assert interrupts.get(card["id"])["status"] == "open"
        devices.store.execute(
            "UPDATE interrupts SET action_digest=? WHERE id=?", (card["action_digest"], card["id"])
        )
        assert (await phone.call("POST", f"/interrupts/{card['id']}/resolve", body))["status"] == 200
        assert interrupts.get(card["id"])["decided_by"]["method"] == "passkey"
        assert (await phone.call("POST", f"/interrupts/{card['id']}/resolve", body))["status"] == 409
        # A passkey never grants PC-only authority.
        local = interrupts.open(
            InterruptSpec(
                "approval", "test", "Delete", "Delete?", action={"delete": "document"}, level="local"
            ),
            run_id="test",
        )
        local = interrupts.get(local["id"], include_secret=True)
        local_statement = decision_statement(
            local["id"], local["nonce"], local["action_digest"], "resolved", {"approved": True}
        )
        local_decision = {
            **decision,
            "nonce": local["nonce"],
            "action_digest": local["action_digest"],
            "signature": crypto.sign(keys.sign_key, local_statement),
        }
        assert (await phone.call("POST", f"/interrupts/{local['id']}/resolve", local_decision))[
            "status"
        ] == 403
        assert interrupts.get(local["id"])["status"] == "open"

    asyncio.run(scenario())
