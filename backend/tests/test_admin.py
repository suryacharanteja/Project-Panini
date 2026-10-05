import json

import pytest
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend import config
from backend.admin import envfile
from backend.admin.service import Admin
from backend.harness.a2a import Remotes
from backend.harness.connectors import Connectors
from backend.harness.runtime import Harness
from backend.harness.secrets_store import SecretStore
from backend.harness.skills import Skills
from backend.harness.store import Store

HEADERS = {"x-panini-console": "t" * 32}


def test_persona_admin_boundary_crud_and_test_chat(client, monkeypatch):
    root = "/api/admin/v1/personas"
    assert client.post(root, json={"id": "scout"}).status_code in {401, 403}
    response = client.post(root, headers=HEADERS, json={"id": "scout", "values": {"name": "Scout"}})
    assert response.status_code == 200
    assert response.json()["permissions"] == ["research", "spaces_read"]
    assert client.patch(root + "/scout", headers=HEADERS, json={"archived": 1}).status_code == 422
    assert client.put(root + "/scout/permissions", headers=HEADERS, json={"permissions": ["desktop"]}).json()[
        "permissions"
    ] == ["desktop"]
    assert client.post(root + "/scout/test", headers=HEADERS, json={"text": "hi"}).status_code == 409
    monkeypatch.setenv("PANINI_PERSONAS", "true")
    tested = client.post(root + "/scout/test", headers=HEADERS, json={"text": "@panini hi"})
    assert tested.status_code == 200 and tested.json()["persona_id"] == "scout"
    assert client.delete(root + "/panini", headers=HEADERS).status_code == 422
    assert client.delete(root + "/scout", headers=HEADERS).json()["archived"]
    assert not client.h.policy.grants("persona:scout")


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".env").write_text(
        "# PANINI\nOPENAI_API_KEY=sk-old-key-000000\nVOICE_NAME=marin\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "ROOT", root)
    import os

    saved = dict(os.environ)
    for key in ("VOICE_NAME", "PANINI_CLI_ENABLED", "PANINI_CLI_WORKSPACES", "BROWSER_HEADLESS"):
        os.environ.pop(key, None)
    os.environ["OPENAI_API_KEY"] = "sk-old-key-000000"
    yield root / ".env"
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def client(tmp_path, env):
    from backend.app import create_app

    secrets = SecretStore(tmp_path / "s.bin")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "skills"),
        connectors=Connectors(tmp_path / "connectors.json", secrets=secrets),
        remotes=Remotes(tmp_path / "agents.json", secrets=secrets),
    )
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.h = h
        yield c


def test_envfile_keeps_comments_and_quotes_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep me\nA=1\nB=two\n", encoding="utf-8")
    envfile.update(path, {"A": "3", "C": '["D:/x y"]', "B": None})
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# keep me\nA=3\n") and "B=" not in text
    assert envfile.read(path)["C"] == '["D:/x y"]'
    # .env parsers treat backslashes as escapes, so they are refused rather than corrupted.
    with pytest.raises(ValueError):
        envfile.update(path, {"D": "C:\\bad path"})


def test_settings_are_listed_without_secret_values(client):
    body = client.get("/api/admin/v1/settings", headers=HEADERS).json()
    rows = {r["key"]: r for r in body["settings"]}
    assert rows["OPENAI_API_KEY"]["configured"] and "value" not in rows["OPENAI_API_KEY"]
    assert rows["OPENAI_API_KEY"]["masked"].endswith("0000") and "sk-old" not in json.dumps(body)
    assert rows["VOICE_NAME"]["value"] == "marin" and rows["VOICE_NAME"]["source"] == "file"
    assert set(body["groups"]) >= {"keys", "code", "voice"}


def test_update_validates_writes_applies_and_audits(client, env, tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bad = client.patch(
        "/api/admin/v1/settings", json={"changes": {"VOICE_SESSION_SECONDS": "5"}}, headers=HEADERS
    )
    assert bad.status_code == 422 and "at least 60" in bad.json()["detail"]
    missing = client.patch(
        "/api/admin/v1/settings", json={"changes": {"PANINI_CLI_WORKSPACES": ["Z:/nope"]}}, headers=HEADERS
    )
    assert missing.status_code == 422
    ok = client.patch(
        "/api/admin/v1/settings",
        json={
            "changes": {
                "VOICE_NAME": "cedar",
                "PANINI_CLI_ENABLED": True,
                "PANINI_CLI_WORKSPACES": [str(workspace)],
            }
        },
        headers=HEADERS,
    ).json()
    assert "PANINI_CLI_ENABLED" in ok["restart_needed"]
    stored = envfile.read(env)
    assert stored["VOICE_NAME"] == "cedar" and stored["PANINI_CLI_ENABLED"] == "true"
    assert json.loads(stored["PANINI_CLI_WORKSPACES"]) == [workspace.resolve().as_posix()]
    import os

    assert os.environ["VOICE_NAME"] == "cedar"  # Applied at once.
    [entry] = client.get("/api/admin/v1/audit", headers=HEADERS).json()["entries"]
    assert entry["diff"]["VOICE_NAME"] == {"old": "marin", "new": "cedar"}
    restored = client.post(f"/api/admin/v1/audit/{entry['id']}/restore", headers=HEADERS)
    assert restored.status_code == 200 and envfile.read(env)["VOICE_NAME"] == "marin"
    assert (
        client.patch("/api/admin/v1/settings", json={"changes": {"NOPE": "1"}}, headers=HEADERS).status_code
        == 422
    )


def test_rejected_keys_are_never_saved(client, env, monkeypatch):
    async def reject(self, which, value=None):
        return False, "the provider rejected this key"

    monkeypatch.setattr(Admin, "test_key", reject)
    r = client.patch(
        "/api/admin/v1/settings", json={"changes": {"OPENAI_API_KEY": "sk-new-bad-111111"}}, headers=HEADERS
    )
    assert r.status_code == 422 and "rejected" in r.json()["detail"]
    assert envfile.read(env)["OPENAI_API_KEY"] == "sk-old-key-000000"

    async def accept(self, which, value=None):
        return True, "accepted"

    monkeypatch.setattr(Admin, "test_key", accept)
    client.patch(
        "/api/admin/v1/settings", json={"changes": {"OPENAI_API_KEY": "sk-new-good-222222"}}, headers=HEADERS
    )
    assert envfile.read(env)["OPENAI_API_KEY"] == "sk-new-good-222222"
    entries = client.get("/api/admin/v1/audit", headers=HEADERS).json()["entries"]
    assert entries[0]["diff"]["OPENAI_API_KEY"] == {"secret": True}
    assert "sk-new" not in json.dumps(entries)


def test_local_model_settings_are_loopback_only_and_probe_is_authenticated(client, env, monkeypatch):
    root = "/api/admin/v1/local-model/test"
    assert client.post(root).status_code in {401, 403}
    bad = client.patch(
        "/api/admin/v1/settings",
        json={"changes": {"PANINI_LOCAL_BASE_URL": "http://remote.example:11434/v1"}},
        headers=HEADERS,
    )
    assert bad.status_code == 422 and "loopback" in bad.json()["detail"]
    assert "PANINI_LOCAL_BASE_URL" not in envfile.read(env)
    saved = client.patch(
        "/api/admin/v1/settings",
        json={
            "changes": {
                "PANINI_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
                "PANINI_LOCAL_MODEL": "small-local",
            }
        },
        headers=HEADERS,
    )
    assert saved.status_code == 200

    class FakeLocal:
        def __init__(self, timeout):
            assert timeout == 10.0

        async def probe(self):
            return {"model": "small-local", "tool_calls": True}

        async def close(self):
            return None

    from panini_cli.agent import local_model

    monkeypatch.setattr(local_model, "LocalModel", FakeLocal)
    assert client.post(root, headers=HEADERS).json() == {
        "ok": True,
        "detail": "small-local answered the tool-call test.",
    }


def test_private_local_model_probe_rejects_unsupported_evidence(client, monkeypatch):
    from types import SimpleNamespace

    from panini_cli.agent import local_model

    monkeypatch.setenv("PANINI_LOCAL_MODE", "private")
    response = {"achieved": False}

    class FakeLocal:
        def __init__(self, timeout):
            assert timeout == 10.0

        async def probe(self):
            return {"model": "small-local", "tool_calls": True}

        async def respond(self, instructions, items, tools, *, output_schema, max_output_tokens):
            assert output_schema["properties"]["achieved"]["type"] == "boolean"
            assert "delegated_results" in items[0]["content"]
            return SimpleNamespace(text=__import__("json").dumps(response), calls=[])

        async def close(self):
            return None

    monkeypatch.setattr(local_model, "LocalModel", FakeLocal)
    url = "/api/admin/v1/local-model/test"
    assert client.post(url, headers=HEADERS).json() == {
        "ok": True,
        "detail": "small-local passed tool-call and evidence-schema tests.",
    }
    response["achieved"] = True
    assert "unsupported claim" in client.post(url, headers=HEADERS).json()["detail"]


def test_connectors_agents_skills_and_hooks_without_files(client, tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    fake = str(Path(__file__).parent / "harness" / "fake_mcp.py")
    saved = client.put(
        "/api/admin/v1/connectors/notes",
        json={
            "kind": "local",
            "command": sys.executable,
            "args": [fake],
            "env": {"NOTES_TOKEN": "tok-123456789"},
        },
        headers=HEADERS,
    ).json()
    assert saved["state"] == "ready" and {t["name"] for t in saved["tools"]} == {"lookup", "create_note"}
    listed = client.get("/api/admin/v1/connectors", headers=HEADERS).json()
    assert listed["connectors"][0]["spec"]["secret_keys"] == ["NOTES_TOKEN"]
    assert "tok-123" not in json.dumps(listed) and "tok-123" not in (tmp_path / "connectors.json").read_text()
    tried = client.post(
        "/api/admin/v1/connectors/notes/tools/lookup/try", json={"arguments": {"q": "x"}}, headers=HEADERS
    )
    assert tried.json()["text"] == 'lookup -> {"q": "x"}'
    refused = client.post("/api/admin/v1/connectors/notes/tools/create_note/try", json={}, headers=HEADERS)
    assert refused.status_code == 409
    grant = client.post(
        "/api/admin/v1/connectors/notes/background-grant",
        json={"tool": "lookup", "allowed": True},
        headers=HEADERS,
    )
    assert grant.json()["granted"] == ["lookup"]
    assert client.get("/api/admin/v1/connectors", headers=HEADERS).json()["connectors"][0]["spec"][
        "background_read_only"
    ] == ["lookup"]
    assert (
        client.post(
            "/api/admin/v1/connectors/notes/background-grant",
            json={"tool": "create_note", "allowed": True},
            headers=HEADERS,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/v1/connectors/notes/background-grant",
            json={"tool": "lookup", "allowed": False},
            headers=HEADERS,
        ).json()["granted"]
        == []
    )
    assert client.delete("/api/admin/v1/connectors/notes", headers=HEADERS).json() == {"ok": True}
    assert (
        client.put(
            "/api/admin/v1/connectors/bad",
            json={"kind": "remote", "url": "http://evil.example"},
            headers=HEADERS,
        ).status_code
        == 422
    )

    assert (
        client.put(
            "/api/admin/v1/agents/helper",
            json={"url": "https://helper.example/a2a", "token": "abc12345"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    agents = client.get("/api/admin/v1/agents", headers=HEADERS).json()["agents"]
    assert agents == [
        {"name": "helper", "url": "https://helper.example/a2a", "description": "", "has_token": True}
    ]
    assert "abc12345" not in (tmp_path / "agents.json").read_text()

    skill = client.put(
        "/api/admin/v1/skills/weekly-report",
        json={"description": "Build the weekly report", "instructions": "1. Gather"},
        headers=HEADERS,
    )
    assert skill.json()["origin"] == "user"
    assert (
        client.put(
            "/api/admin/v1/skills/research-brief",
            json={"description": "x", "instructions": "y"},
            headers=HEADERS,
        ).status_code
        == 409
    )  # Built-ins are read-only.
    assert client.delete("/api/admin/v1/skills/weekly-report", headers=HEADERS).json() == {"ok": True}

    monkeypatch.setattr("backend.routes.admin._hooks_file", lambda: tmp_path / "hooks.json")
    hooks = client.put(
        "/api/admin/v1/hooks",
        json={"hooks": {"PreToolUse": [{"matcher": "run_command", "command": "echo ok"}]}},
        headers=HEADERS,
    ).json()
    assert hooks["hooks"]["PreToolUse"][0]["timeout"] == 30
    assert client.put("/api/admin/v1/hooks", json={"hooks": {"Nope": []}}, headers=HEADERS).status_code == 422
    kinds = [e["kind"] for e in client.get("/api/admin/v1/audit", headers=HEADERS).json()["entries"]]
    assert {"connector", "agent", "skill", "hooks"} <= set(kinds)


def test_health_reports_each_dependency(client, monkeypatch):
    async def fake(self, which, value=None):
        return which == "openai", "accepted" if which == "openai" else "not configured"

    monkeypatch.setattr(Admin, "test_key", fake)
    body = client.get("/api/admin/v1/health?refresh=true", headers=HEADERS).json()
    ids = {c["id"]: c for c in body["checks"]}
    assert ids["openai"]["state"] == "ok" and ids["assemblyai"]["state"] == "warning"
    assert {"search", "extension", "docker", "harness"} <= set(ids)
    assert client.get("/api/admin/v1/health").status_code == 401  # Console sign-in required.


def test_every_setting_has_a_known_type():
    from backend.admin.schema import SETTINGS

    assert {s.type for s in SETTINGS} <= {"text", "secret", "bool", "int", "enum", "paths"}
    assert all(s.help for s in SETTINGS if s.key.endswith("_MODEL"))
