import asyncio
import json
import threading
import time

import pytest

from backend.harness.store import Store
from backend.ledger import Artifacts, Ledger, install
from backend.ledger.chain import verify
from backend.ledger.redact import redact_args


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "h.sqlite3")
    yield s
    s.close()


@pytest.fixture
def ledger(store, monkeypatch):
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    return install(Ledger(store))


def begin(ledger, run="r1", tool="run_command", args=None, effect="local_exec"):
    return ledger.begin(
        run_id=run,
        tool=tool,
        capability="computer",
        hand="computer",
        args=args or {"command": "ls"},
        effect=effect,
        principal={"kind": "owner"},
    )


def test_lifecycle_chains_and_verifies(ledger, store):
    r = begin(ledger)
    ledger.decision(r["id"], {"action": "allow", "effect": "local_exec"})
    ledger.dispatched(r["id"])
    ledger.evidence(r["id"], "exit_code", "Exit 0", {"exit_code": 0})
    ledger.end(r["id"], outcome="ok", content="done")
    receipt = ledger.receipt(r["id"])
    assert [e["kind"] for e in receipt["entries"]] == ["begin", "decision", "dispatch", "evidence", "end"]
    assert receipt["dispatched"] == 1 and receipt["outcome"] == "ok"
    assert receipt["evidence"][0]["exit_code"] == 0
    assert verify(store)["ok"]


def test_tampering_is_detected(ledger, store):
    for _ in range(3):
        r = begin(ledger)
        ledger.end(r["id"], outcome="ok")
    store.execute("UPDATE ledger_entries SET body = replace(body, 'ok', 'failed') WHERE seq = 4")
    result = verify(store)
    assert result["ok"] is False and result["first_bad_seq"] == 4


def test_concurrent_writers_keep_one_chain(ledger, store):
    def work():
        for _ in range(25):
            r = begin(ledger, run=threading.current_thread().name)
            ledger.end(r["id"], outcome="ok")

    threads = [threading.Thread(target=work, name=f"t{i}") for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result = verify(store)
    assert result["ok"] and result["entries"] == 200


def test_operation_keys_replay_and_unknown(ledger):
    first = begin(ledger, args={"command": "echo hi"})
    ledger.end(first["id"], outcome="ok", content="hi")
    # The same run repeating the same call (same occurrence) after a restart: return the stored result.
    second = ledger.begin(
        run_id="r1",
        tool="run_command",
        capability="computer",
        hand="computer",
        args={"command": "echo hi"},
        effect="local_exec",
        principal={},
    )
    assert second["operation_key"] != first["operation_key"]  # A second occurrence in a live run is new work.
    verdict, _ = ledger.replay_decision(first["operation_key"], exclude="x")
    assert verdict == "replay"
    pending = begin(ledger, run="r2")
    ledger.dispatched(pending["id"])
    assert ledger.replay_decision(pending["operation_key"], exclude="x")[0] == "unknown"
    never = begin(ledger, run="r3")
    assert ledger.replay_decision(never["operation_key"], exclude="x")[0] == "proceed"
    assert ledger.receipt(never["id"])["outcome"] == "cancelled"


def test_recovery_after_a_crash(ledger, store):
    sent = begin(ledger, run="a")
    ledger.dispatched(sent["id"])
    begin(ledger, run="b")
    unknown = ledger.recover()
    assert [row["id"] for row in unknown] == [sent["id"]]
    outcomes = {r["run_id"]: r["outcome"] for r in ledger.query()}
    assert outcomes == {"a": "unknown_effect", "b": "cancelled"}
    assert verify(store)["ok"]


def test_redaction_never_keeps_secrets_or_typed_values():
    view = redact_args(
        {
            "text": "my diary entry",
            "contents": "file body",
            "command": "setx OPENAI_API_KEY sk-abcdefghijklmnop12345",
            "url": "https://x.example/cb?token=abc123&page=2",
            "element": "Send",
        }
    )
    assert view["text"]["chars"] == 14 and "diary" not in json.dumps(view)
    assert "sk-abcdefghijklmnop12345" not in json.dumps(view)
    assert "abc123" not in view["url"] and "page=2" in view["url"]
    assert view["element"] == "Send"


def test_artifacts_store_retention_and_containment(store, tmp_path):
    arts = Artifacts(store, tmp_path / "artifacts")
    old = arts.put("screenshot", b"x" * 10, "image/jpeg")
    pinned = arts.put("trace", b"y" * 10, "application/zip", pinned=True)
    store.execute("UPDATE artifacts SET created = created - 100 * 86400")
    fresh = arts.put("snapshot", b"z" * 10, "text/yaml")
    assert arts.enforce_retention(max_days=90, max_bytes=10**9) == 1
    assert arts.get(old) is None and arts.get(pinned) and arts.get(fresh)
    store.execute("UPDATE artifacts SET path='C:/Windows/win.ini' WHERE id=?", (fresh,))
    assert arts.get(fresh) is None  # Never serves a file outside the artifacts folder.
    with pytest.raises(ValueError):
        arts.put("malware", b"", "text/plain")


def test_every_registry_call_is_receipted_even_refusals(tmp_path, monkeypatch):
    """Invariant I1: begin and end entries for every call through the registry, including policy refusals."""
    from panini_cli.agent.model import ScriptedModel

    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills

    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    monkeypatch.setenv("PANINI_V2_POLICY", "enforce")
    monkeypatch.setenv("PANINI_APPROVALS", "risky")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    steps = [
        {"calls": [{"name": "list_files", "arguments": {"path": str(tmp_path), "pattern": "*"}}]},
        {
            "calls": [
                {
                    "name": "run_command",
                    "arguments": {"command": "rm -rf x", "cwd": str(tmp_path), "timeout_seconds": 5},
                }
            ]
        },
        {"text": "Done."},
    ]
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel(steps),
        skills=Skills(user_root=tmp_path / "skills"),
    )

    async def delegate(capability, instruction):
        return {"outcome": "verified_complete", "message": "", "results": []}

    async def no(summary, detail):
        return False

    agent = h.agent(delegate=delegate, capabilities=("search",), confirm=no)
    agent.run_id = "task-1"
    asyncio.run(agent.run("list, then delete"))
    receipts = {r["tool"]: r for r in h.ledger.query(run_id="task-1")}
    assert receipts["list_files"]["outcome"] == "ok" and receipts["list_files"]["dispatched"] == 0
    refused = receipts["run_command"]
    assert refused["outcome"] == "blocked" and refused["decision"]["action"] == "ask"
    assert refused["dispatched"] == 0  # Refused before the hand ran.
    assert verify(h.store)["ok"]
    h.store.close()


def test_coordinator_evidence_is_mirrored(ledger):
    from backend.ledger import mirror_evidence

    evidence_id = mirror_evidence("task-9", "desktop", "uia_postcondition", "Verified", {"x": 1})
    rows = ledger.query(run_id="task-9")
    assert rows[0]["tool"] == "run" and rows[0]["evidence"][0]["evidence_id"] == evidence_id


def test_api_and_activity_search(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.registry.context import ToolContext

    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "skills"),
    )
    r = begin(h.ledger, tool="list_files", effect="observe")
    h.ledger.end(r["id"], outcome="ok", content="2 files")
    art = h.artifacts.put("screenshot", b"\xff\xd8jpeg", "image/jpeg")
    result = asyncio.run(
        h.registry.call(ToolContext(), "activity_search", {"query": "list", "since_hours": 1})
    )
    assert result.data["actions"][0]["tool"] == "list_files"
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        listed = client.get("/api/harness/receipts?tool=list_files", headers=headers).json()["receipts"]
        assert len(listed) >= 1
        detail = client.get(f"/api/harness/receipts/{r['id']}", headers=headers).json()
        assert detail["entries"][0]["kind"] == "begin"
        assert client.get("/api/harness/ledger/verify", headers=headers).json()["ok"]
        image = client.get(f"/api/harness/artifacts/{art}", headers=headers)
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert client.get("/api/harness/artifacts/nope", headers=headers).status_code == 404


def test_ten_thousand_entries_verify_quickly(ledger, store):
    for i in range(5000):
        r = ledger.begin(
            run_id=f"r{i % 50}",
            tool="t",
            capability="c",
            hand="h",
            args={"i": i},
            effect="observe",
            principal={},
        )
        ledger.end(r["id"], outcome="ok")
    started = time.monotonic()
    assert verify(store)["entries"] == 10000
    assert time.monotonic() - started < 2.0
