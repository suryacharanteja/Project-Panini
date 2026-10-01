import asyncio
import socket

import httpx
from panini_cli.agent.model import ScriptedModel

from backend.harness.a2a import Remotes
from backend.harness.connectors import Connectors
from backend.harness.mobile import Mobile, private_client
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make(tmp_path):
    return Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "skills"),
        connectors=Connectors(tmp_path / "connectors.json"),
        remotes=Remotes(tmp_path / "agents.json"),
    )


def test_only_private_networks_may_connect():
    assert private_client("192.168.1.20") and private_client("10.0.0.5") and private_client("127.0.0.1")
    assert private_client("fe80::1")
    assert not private_client("8.8.8.8") and not private_client("not-an-ip")


def test_phone_pairs_then_asks_and_decides(tmp_path):
    h = make(tmp_path)
    port = free_port()
    h.mobile = Mobile(h, settings_path=tmp_path / "mobile.json", port=port)

    async def go():
        status = await h.mobile.enable()
        assert status["enabled"] and (tmp_path / "mobile.json").exists()
        base = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(base_url=base) as phone:
                page = await phone.get("/")
                assert page.status_code == 200 and "Pair this phone" in page.text
                assert "default-src 'self'" in page.headers["content-security-policy"]
                assert (await phone.get("/api/inbox")).status_code == 401
                # No code yet, then a wrong code, then the right one (single use).
                assert (await phone.post("/api/pair", json={"code": "ABCDEFGH"})).status_code == 403
                code = h.mobile.devices.new_code()["code"]
                assert (await phone.post("/api/pair", json={"code": "WRONG123"})).status_code == 403
                paired = await phone.post("/api/pair", json={"code": code.lower(), "name": "Pixel"})
                assert paired.status_code == 200 and "httponly" in paired.headers["set-cookie"].lower()
                async with httpx.AsyncClient(base_url=base) as stranger:
                    again = await stranger.post("/api/pair", json={"code": code})
                assert again.status_code == 403

                sent = await phone.post("/api/ask", json={"text": "Summarise today's AI news"})
                assert sent.json()["task"]["status"] == "queued"
                held = h.tasks.submit("Agent X wants a summary", origin="a2a:x", hold=True)
                inbox = (await phone.get("/api/inbox")).json()
                assert inbox["reviews"][0]["kind"] == "approval"
                decided = await phone.post(
                    f"/api/reviews/{inbox['reviews'][0]['id']}", json={"decision": "dismiss"}
                )
                assert decided.json()["task"]["status"] == "cancelled"
                assert h.tasks.get(held["id"])["rejected"]
                assert [d["name"] for d in h.mobile.status()["devices"]] == ["Pixel"]

                # Revoking the device locks the phone out.
                h.mobile.devices.revoke(h.mobile.status()["devices"][0]["id"])
                assert (await phone.get("/api/inbox")).status_code == 401
        finally:
            await h.mobile.disable()
        assert not h.mobile.running

    asyncio.run(go())


def test_pairing_attempts_are_rate_limited(tmp_path):
    h = make(tmp_path)
    devices = h.mobile.devices
    devices.new_code()
    for _ in range(5):
        try:
            devices.pair("WRONGXYZ", "x")
        except PermissionError:
            pass
    try:
        devices.pair(devices.code[0], "x")
        raise AssertionError("the sixth attempt within a minute must be refused")
    except PermissionError as exc:
        assert "Too many" in str(exc)
