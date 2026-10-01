import asyncio
import json

import httpx
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.capabilities.contracts import Outcome
from backend.harness.a2a import Remotes
from backend.harness.connectors import Connectors
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.harness.tasks import Worker


def make(tmp_path, steps, remotes=None):
    model = ScriptedModel(steps)
    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: model,
        skills=Skills(user_root=tmp_path / "skills"),
        connectors=Connectors(tmp_path / "connectors.json"),
        remotes=remotes or Remotes(tmp_path / "agents.json"),
    )
    return h, model


class Search:
    async def availability(self):
        return {"state": "ready", "reason": ""}

    async def run(self, ctx, instruction, target):
        return Outcome("verified_complete", "3 sources", [], [ctx.record_evidence("r", "s", {})])

    async def release(self, task_id):
        pass


def rpc(method, params, identifier=1):
    return {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}


def message(text):
    return {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": "m1",
            "parts": [{"kind": "text", "text": text}],
        }
    }


def test_inbound_requests_wait_for_approval_and_run_without_memories(tmp_path):
    from backend.app import create_app

    h, model = make(
        tmp_path,
        [
            {
                "calls": [
                    {"name": "use_capability", "arguments": {"capability": "search", "instruction": "q"}}
                ]
            },
            {
                "calls": [
                    {
                        "name": "finish",
                        "arguments": {"outcome": "verified_complete", "message": "Here it is."},
                    }
                ]
            },
        ],
    )
    h.adapters = {"search": Search()}
    h.memory.add("My bank is Example Bank", kind="profile")
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    console = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        card = client.get("/.well-known/agent-card.json").json()
        assert card["name"] == "PANINI" and card["url"].endswith("/a2a")
        assert client.post("/a2a", json=rpc("message/send", message("hi"))).status_code == 401
        issued = client.post(
            "/api/harness/a2a/clients", json={"name": "Research bot"}, headers=console
        ).json()
        auth = {"Authorization": f"Bearer {issued['token']}"}
        sent = client.post(
            "/a2a", json=rpc("message/send", message("Which bank do I use? Research EVs.")), headers=auth
        )
        task = sent.json()["result"]
        assert task["status"]["state"] == "input-required"
        # Nothing runs until the person approves.
        assert not asyncio.run(Worker(h.tasks, h.run_background).run_once())
        [review] = h.tasks.reviews()
        assert review["kind"] == "approval" and "Research bot" in review["message"]
        h.tasks.resolve(review["id"], "rerun")
        assert asyncio.run(Worker(h.tasks, h.run_background).run_once())
        done = client.post("/a2a", json=rpc("tasks/get", {"id": task["id"]}), headers=auth).json()["result"]
        assert done["status"]["state"] == "completed"
        assert done["artifacts"][0]["parts"][0]["text"] == "Here it is."
        # The person's memories never entered the external task's context.
        assert "Example Bank" not in model.requests[0]["instructions"]
        assert "do not include personal information" in model.requests[0]["items"][0]["content"]
        # Another client cannot see this task; a revoked token stops working.
        other = client.post("/api/harness/a2a/clients", json={"name": "Other"}, headers=console).json()
        peek = client.post(
            "/a2a",
            json=rpc("tasks/get", {"id": task["id"]}),
            headers={"Authorization": f"Bearer {other['token']}"},
        )
        assert peek.json()["error"]["code"] == -32001
        client.delete(f"/api/harness/a2a/clients/{issued['id']}", headers=console)
        assert client.post("/a2a", json=rpc("tasks/get", {"id": task["id"]}), headers=auth).status_code == 401


def test_a_declined_request_is_reported_as_rejected(tmp_path):
    from backend.harness.a2a import _task_view

    h, _ = make(tmp_path, [])
    task = h.tasks.submit("do something", origin="a2a:x", hold=True)
    [review] = h.tasks.reviews()
    h.tasks.resolve(review["id"], "dismiss")
    assert _task_view(h.tasks.get(task["id"]))["status"]["state"] == "rejected"


def test_outbound_ask_agent_confirms_then_polls_until_done(tmp_path):
    agents = tmp_path / "agents.json"
    agents.write_text(json.dumps({"agents": {"helper": {"url": "http://helper.test/a2a", "token": "k"}}}))
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append((body["method"], request.headers.get("authorization")))
        if body["method"] == "message/send":
            result = {"kind": "task", "id": "t1", "status": {"state": "working"}}
        else:
            result = {
                "kind": "task",
                "id": "t1",
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": "42"}]}],
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    remotes = Remotes(agents)

    async def ask():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await remotes.ask("helper", "What is the answer?", poll=0.01, client=client)

    assert asyncio.run(ask()) == {"state": "completed", "text": "42", "task_id": "t1"}
    assert calls == [("message/send", "Bearer k"), ("tasks/get", "Bearer k")]

    h, model = make(
        tmp_path,
        [
            {"calls": [{"name": "ask_agent", "arguments": {"agent": "helper", "message": "my secret plan"}}]},
            {"text": "ok"},
        ],
        remotes,
    )
    asked = []

    async def confirm(summary, detail):
        asked.append(detail)
        return False

    async def delegate(*_):
        return {}

    asyncio.run(h.agent(delegate=delegate, capabilities=(), confirm=confirm).run("ask helper"))
    output = json.loads(model.requests[-1]["items"][-1]["output"])
    assert "declined" in output["error"] and asked == ["my secret plan"]
    assert "helper" in model.requests[0]["instructions"]
