"""V2-12 live routes: loopback ingest with a session token, latest frame, take / give back, owner input."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import live
from backend.routes import live as routes


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    monkeypatch.setattr(live, "_hub", None)
    monkeypatch.setattr(live, "_takeover", None)
    app = FastAPI()
    app.include_router(routes.console_router)
    app.include_router(routes.ingest_router)
    return TestClient(app, client=("127.0.0.1", 5000))


def test_a_source_publishes_with_its_token_and_viewers_see_the_latest_frame(client):
    token = live.hub().ingest_token("managed", "default")
    assert client.get(f"/api/live/ingest/{token}/watch").json() == {"watched": False}
    sent = client.post(
        f"/api/live/ingest/{token}", content=b"\xff\xd8jpeg", headers={"x-live-url": "https://example.com"}
    )
    assert sent.json() == {"seq": 1}
    frame = client.get("/api/harness/live/managed/default/latest.jpg")
    assert frame.content == b"\xff\xd8jpeg" and frame.headers["content-type"] == "image/jpeg"
    assert client.get("/api/harness/live").json()["targets"][0]["url"] == "https://example.com"
    assert client.post("/api/live/ingest/not-a-token", content=b"x").status_code == 403


def test_frames_from_another_computer_are_refused(monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    app = FastAPI()
    app.include_router(routes.ingest_router)
    remote = TestClient(app, client=("192.168.1.20", 5000))
    token = live.hub().ingest_token("managed", "default")
    assert remote.post(f"/api/live/ingest/{token}", content=b"x").status_code == 403


def test_take_input_and_give_back(client, monkeypatch):
    calls = []

    async def handler(target, action, args):
        calls.append((target, action, args))
        return {"ok": True}

    monkeypatch.setitem(routes.input_handlers, "sandbox", handler)
    url = "/api/harness/live/sandbox/panini"
    assert client.post(f"{url}/input/click", json={"x": 1, "y": 2}).status_code == 409  # Take control first.
    assert client.post(f"{url}/control/take").json()["holder"] == "human"
    assert client.post(f"{url}/input/click", json={"x": 1, "y": 2}).json() == {"ok": True}
    assert calls == [("panini", "click", {"x": 1, "y": 2})]
    released = client.post(f"{url}/control/release").json()
    assert released["holder"] == "agent" and released["resumeSnapshotRequired"]


def test_live_view_is_off_by_default(client, monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "false")
    assert client.get("/api/harness/live").status_code == 404


def _remote_app(monkeypatch, tmp_path, verifier):
    from backend.live.remote_grants import Grants

    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    monkeypatch.setattr(live, "_grants", Grants(tmp_path / "grants.json"))
    monkeypatch.setattr(routes, "remote_verifier", verifier)
    app = FastAPI()
    app.include_router(routes.console_router)
    app.include_router(routes.remote_router)
    return TestClient(app)


def test_remote_live_view_is_closed_until_remote_access_registers_a_verifier(monkeypatch, tmp_path):
    client = _remote_app(monkeypatch, tmp_path, None)
    assert client.get("/api/remote/harness/live").status_code == 404


def test_remote_viewers_see_only_granted_hands_and_the_pc_knows_they_are_watching(monkeypatch, tmp_path):
    client = _remote_app(monkeypatch, tmp_path, lambda request: None)
    hub = live.hub()
    hub.publish(None, "chrome", "attached", b"\xff\xd8mine")
    hub.publish(None, "sandbox", "panini", b"\xff\xd8box")
    # Your Chrome and desktop are off for remote surfaces by default; the sandbox is on.
    assert [t["hand"] for t in client.get("/api/remote/harness/live").json()["targets"]] == ["sandbox"]
    refused = client.get("/api/remote/harness/live/chrome/attached/latest.jpg")
    assert refused.status_code == 403 and "Remote viewing of your Chrome is off" in refused.json()["detail"]
    assert client.post("/api/remote/harness/live/desktop/pc/control/take").status_code == 403
    assert client.get("/api/remote/harness/live/sandbox/panini/latest.jpg").content == b"\xff\xd8box"
    # The owner turns Chrome on (locally); now the phone can watch, and the PC shows it.
    assert client.patch("/api/harness/live/grants", json={"chrome": True}).json()["chrome"] is True
    assert client.get("/api/remote/harness/live/chrome/attached/latest.jpg").status_code == 200
    targets = {t["hand"]: t for t in client.get("/api/harness/live").json()["targets"]}
    assert targets["chrome"]["watchedRemotely"]  # The PC shows "Watching from your phone".
    assert "watchedRemotely" in targets["sandbox"]
    assert client.patch("/api/harness/live/grants", json={"toaster": True}).status_code == 422
