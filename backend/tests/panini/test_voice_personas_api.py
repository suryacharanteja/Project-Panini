from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from . import test_api

runtime = test_api.runtime


def test_voice_identity_is_authenticated_and_reconnect_closes_old_media(runtime, monkeypatch):
    client, coordinator, sid, headers = runtime
    monkeypatch.setenv("PANINI_PERSONAS", "false")
    path = f"/api/panini/sessions/{sid}"
    assert client.get(path + "/voice-personas").status_code == 401
    assert client.get(path + "/voice-personas", headers=headers).json() == {"personas": []}
    made = []

    def factory(owner, session, principal, epoch):
        media = SimpleNamespace(
            start=AsyncMock(return_value="answer"), close=AsyncMock(), persona=session.voice_persona_id
        )
        made.append(media)
        return media

    coordinator.voice_factory = factory
    body = {
        "command_id": str(uuid4()),
        "media_generation": 1,
        "sdp": "v=0\r\n" + "a" * 30,
        "persona_id": "scout",
    }
    assert client.post(path + "/media", headers=headers, json=body).status_code == 409
    body["persona_id"] = "panini"
    assert client.post(path + "/media", headers=headers, json=body).status_code == 200
    assert client.post(path + "/media", headers=headers, json=body).status_code == 409
    assert (
        client.request(
            "DELETE",
            path + "/media",
            headers=headers,
            json={"command_id": str(uuid4()), "media_generation": 1},
        ).status_code
        == 200
    )
    made[0].close.assert_awaited_once()
    body["media_generation"] = 3
    assert client.post(path + "/media", headers=headers, json=body).status_code == 200
    assert coordinator.get(sid).state == "ready"
    assert (
        client.request(
            "DELETE",
            path + "/media",
            headers=headers,
            json={"command_id": str(uuid4()), "media_generation": 1},
        ).status_code
        == 409
    )
    made[1].close.assert_not_awaited()
