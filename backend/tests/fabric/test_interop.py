"""V2-14: the browser's WebCrypto envelope (phone app) and the PC's Python envelope speak the same protocol."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.fabric import envelope

PEER = Path(__file__).resolve().parents[3] / "panini-core/apps/phone/test/device-peer.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed.")


def test_a_webcrypto_device_pairs_handshakes_and_exchanges_envelopes_with_the_pc(fabric):
    devices, identity, _ = fabric
    peer = subprocess.Popen(["node", str(PEER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def send(value):
        peer.stdin.write(json.dumps(value) + "\n")
        peer.stdin.flush()
        return json.loads(peer.stdout.readline())

    try:
        keys = json.loads(peer.stdout.readline())
        code = devices.new_code()["code"]
        answer = devices.pair(code, "Browser", keys["signPub"], keys["agreePub"])
        pc = {"id": identity.pc_id, "signPub": identity.sign_pub, "agreePub": identity.agree_pub}
        shown = send({"step": "paired", "pc": pc, "device_id": answer["device_id"], "code": code})
        assert shown["sas"] == answer["sas"]  # Both screens show the same six digits.
        devices.confirm(answer["device_id"])
        hello = send({"step": "hello"})["message"]
        welcome, session = envelope.welcome(identity, devices, hello)
        request = send({"step": "welcome", "message": welcome})["envelope"]
        assert session.open(request)[2] == {"method": "GET", "path": "/status"}
        done = send(
            {"step": "response", "envelope": session.seal("res", {"status": 200}, msg_id=request["id"])}
        )
        assert done["reply"]["body"] == {"status": 200} and done["replay"] == "rejected"
    finally:
        peer.kill()
