import pytest
from fastapi.testclient import TestClient

from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.app import create_app

    monkeypatch.setenv("PANINI_SPACES", "true")
    h = Harness(Store(tmp_path / "h.db"), skills=Skills(user_root=tmp_path / "skills"))
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    yield TestClient(app, base_url="http://localhost"), h
    h.store.close()


HEADERS = {"x-panini-console": "t" * 32}
BASE = "/api/harness/spaces"


def test_owner_boundary_flag_cas_and_export(client, monkeypatch):
    c, h = client
    assert c.get(BASE).status_code == 401
    s = c.post(BASE, headers=HEADERS, json={"name": "Work"}).json()
    p = c.post(
        BASE + "/pages", headers=HEADERS, json={"space_id": s["id"], "title": "Plan", "content": "# A plan"}
    ).json()
    path = BASE + "/pages/" + p["id"]
    assert c.patch(path, headers=HEADERS, json={"expected_revision": 1, "content": "new"}).status_code == 200
    conflict = c.patch(path, headers=HEADERS, json={"expected_revision": 1, "content": "lost"})
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "PAGE_CHANGED"
    exported = c.get(path + "/markdown", headers=HEADERS)
    assert exported.text == "new" and exported.headers["x-content-type-options"] == "nosniff"
    assert (
        c.post(
            BASE + "/access/" + s["id"], headers=HEADERS, json={"persona_id": "missing", "allowed": True}
        ).status_code
        == 403
    )
    monkeypatch.setenv("PANINI_SPACES", "false")
    assert c.get(BASE, headers=HEADERS).status_code == 404
    assert (
        c.patch(path, headers=HEADERS, json={"expected_revision": 2, "content": "disabled"}).status_code
        == 404
    )
    assert h.spaces.read(p["id"])["content"] == "new"
