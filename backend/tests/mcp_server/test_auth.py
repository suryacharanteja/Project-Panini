import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Route

from backend.harness.store import Store
from backend.mcp_server.clients import Clients, token_hash
from backend.routes.mcp import Endpoint


def test_hash_only_grants_revoke_and_protected_file(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    clients = Clients(store, directory=tmp_path / "tokens")
    client = clients.create("Codex")
    row = store.one("SELECT * FROM mcp_clients")
    assert row["token_hash"] == token_hash(client["token"])
    assert client["token"] not in json.dumps(row)
    assert "token" not in json.dumps(clients.list())
    assert clients.authenticate("Bearer " + client["token"])["id"] == client["id"]
    from pathlib import Path

    path = Path(client["token_file"])
    assert path.read_text() == client["token"]
    assert clients.revoke(client["id"])
    assert not path.exists()
    assert clients.authenticate("Bearer " + client["token"]) is None
    assert not clients.revoke(client["id"])
    store.close()


@pytest.mark.parametrize("grants", [["shell"], ["chrome"], "desktop", ["memory"], ["sandbox"]])
def test_unknown_or_unsafe_scopes_refused(tmp_path, grants):
    store = Store(tmp_path / "h.sqlite3")
    with pytest.raises(ValueError):
        Clients(store).create("Agent", grants, save_token=False)
    assert store.one("SELECT COUNT(*) AS n FROM mcp_clients")["n"] == 0
    store.close()


@pytest.mark.parametrize(
    "authorization", ["", "Bearer wrong", "bearer abc", "Basic abc", "Bearer " + "x" * 300]
)
def test_credentials_fail_closed(tmp_path, authorization):
    store = Store(tmp_path / "h.sqlite3")
    clients = Clients(store)
    assert clients.authenticate(authorization) is None
    store.close()


@pytest.mark.parametrize(
    "headers,status",
    [
        ({}, 401),
        ({"Origin": "http://127.0.0.1"}, 403),
        ({"Origin": ""}, 403),
        ({"Host": "evil.test"}, 403),
        ({"Host": "127.0.0.1.evil.test"}, 403),
    ],
)
def test_http_independent_boundary(tmp_path, monkeypatch, headers, status):
    monkeypatch.setenv("PANINI_MCP_SERVER", "true")
    store = Store(tmp_path / "h.sqlite3")
    app = FastAPI()
    app.state.mcp = SimpleNamespace(clients=Clients(store), available=lambda: True)
    app.router.routes.append(Route("/mcp", Endpoint(app), methods=["POST"]))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 10)) as client:
        assert client.post("/mcp", headers=headers, json={}).status_code == status
    store.close()


def test_flag_off_and_remote_peer(tmp_path, monkeypatch):
    app = FastAPI()
    app.router.routes.append(Route("/mcp", Endpoint(app), methods=["POST"]))
    monkeypatch.setenv("PANINI_MCP_SERVER", "false")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/mcp", json={}).status_code == 404
    monkeypatch.setenv("PANINI_MCP_SERVER", "true")
    with TestClient(app, base_url="http://127.0.0.1", client=("192.168.0.2", 10)) as client:
        assert client.post("/mcp", json={}).status_code == 403
