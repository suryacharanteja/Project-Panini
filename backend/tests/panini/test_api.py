from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.panini.settings import Settings

CONSOLE = "c" * 43


@pytest.fixture
def runtime():
    settings = Settings(enabled=True, bootstrap_token="b" * 40)
    app = create_app(panini_settings=settings, console_token=CONSOLE)
    client = TestClient(app, base_url="http://localhost")
    body = client.post("/api/panini/clients", headers={"authorization": "Bearer " + "b" * 40}, json={}).json()
    headers = {"authorization": "Bearer " + body["token"], "x-panini-instance": settings.instance_id}
    s = client.post("/api/panini/sessions", headers=headers, json={"command_id": str(uuid4())}).json()
    sid = s["session_id"]
    lease = client.post(
        f"/api/panini/sessions/{sid}/lease/acquire", headers=headers, json={"command_id": str(uuid4())}
    ).json()
    headers["x-panini-lease-epoch"] = str(lease["lease_epoch"])
    return client, app.state.panini, sid, headers


def test_disabled_profile_does_not_enable_panini():
    client = TestClient(create_app(panini_settings=Settings()), base_url="http://localhost")
    assert client.get("/api/panini/health").status_code == 404
    assert client.post("/api/browser/stop").status_code == 200


def test_end_receipt_is_idempotent_after_lease_is_closed(runtime):
    client, _, sid, controller = runtime
    body = {"command_id": str(uuid4())}
    first = client.post(f"/api/panini/sessions/{sid}/end", headers=controller, json=body)
    second = client.post(f"/api/panini/sessions/{sid}/end", headers=controller, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"status": "closed"}


def test_lease_renew_retry_does_not_advance_liveness_twice(runtime):
    client, coordinator, sid, controller = runtime
    body = {"command_id": str(uuid4()), "renderer_revision": 7}
    first = client.post(f"/api/panini/sessions/{sid}/lease/renew", headers=controller, json=body)
    expiry = coordinator.get(sid).expires
    second = client.post(f"/api/panini/sessions/{sid}/lease/renew", headers=controller, json=body)
    assert first.status_code == second.status_code == 200
    assert coordinator.get(sid).expires == expiry
    changed = client.post(
        f"/api/panini/sessions/{sid}/lease/renew", headers=controller, json={**body, "renderer_revision": 8}
    )
    assert changed.status_code == 409


@pytest.mark.parametrize(
    "path",
    [
        "/api/browser/run-stream",
        "/api/browser/stop",
        "/api/browser/reset",
        "/api/voice/session",
        "/api/voice/console",
        "/api/voice/turn",
        "/api/voice/end",
        "/api/compare",
        "/api/stt/transcribe",
        "/api/browser/extension/status",
    ],
)
def test_console_routes_require_the_gateway_signed_in_token(runtime, path):
    client, _, _, headers = runtime
    # PANINI controller credentials do not grant console access, and nothing
    # reaches a console route directly without the gateway's per-launch token.
    assert client.post(path, headers=headers, json={}).status_code == 401
    assert client.get(path).status_code == 401
    assert client.post(path, headers={"x-panini-console": "wrong"}, json={}).status_code == 401
    assert client.post(path, headers={"x-panini-console": CONSOLE}, json={}).status_code != 401


def test_observer_ticket_cannot_mutate_or_read_confirmation(runtime):
    client, _, sid, controller = runtime
    ticket = client.post(
        f"/api/panini/sessions/{sid}/observers", headers=controller, json={"command_id": str(uuid4())}
    ).json()["ticket"]
    response = client.post(
        "/api/panini/observer/exchange", headers={"origin": "http://localhost"}, json={"ticket": ticket}
    )
    assert response.status_code == 200 and "HttpOnly" in response.headers["set-cookie"]
    assert client.get(f"/api/panini/sessions/{sid}").status_code == 200
    for endpoint in ["stop", "end", "commands", "lease/renew", "targets", "observers"]:
        assert (
            client.post(
                f"/api/panini/sessions/{sid}/{endpoint}",
                json={
                    "command_id": str(uuid4()),
                    **({"text": "test"} if endpoint == "commands" else {}),
                    **({"renderer_revision": 1} if endpoint == "lease/renew" else {}),
                    **({"capability": "desktop", "choice_id": "fake"} if endpoint == "targets" else {}),
                },
            ).status_code
            == 403
        )
    assert client.get(f"/api/panini/sessions/{sid}/confirmations/missing").status_code == 403
    assert (
        client.post(
            "/api/panini/observer/exchange", headers={"origin": "http://localhost"}, json={"ticket": ticket}
        ).status_code
        == 401
    )


def test_credentials_do_not_appear_in_public_health(runtime):
    client, _, _, controller = runtime
    body = client.get("/api/panini/health").text
    assert controller["authorization"][7:] not in body and "b" * 40 not in body
    assert client.post("/api/panini/clients", json={}).status_code == 401
