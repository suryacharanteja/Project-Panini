"""Real SSE / policy / ledger team continuation with scripted models and a local test hand."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.core_contracts import Readiness, ToolResult
from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.tests.agui.test_agui import HEADERS, TOKEN, body, events


def approve(interrupt):
    return [
        {
            "interruptId": interrupt["id"],
            "status": "resolved",
            "payload": {"approved": True},
            "metadata": {
                "nonce": interrupt["metadata"]["nonce"],
                "actionDigest": interrupt["metadata"]["actionDigest"],
            },
        }
    ]


def team_world(tmp_path, monkeypatch, mode="team"):
    for flag in ("PANINI_PERSONAS", "PANINI_V2_AGUI", "PANINI_V2_INTERRUPTS", "PANINI_V2_LEDGER"):
        monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    monkeypatch.setenv("PANINI_RUN_MAX_USD", "5")
    monkeypatch.setenv("PANINI_DAILY_MAX_USD", "5")
    # Import the process-level app before installing this test's isolated services.
    from backend.app import create_app

    h = Harness(Store(tmp_path / "h.sqlite3"), skills=Skills(user_root=tmp_path / "skills"))
    app = create_app(harness_factory=lambda: h, console_token=TOKEN)
    h.personas.create("scout")
    h.personas.create("quill")
    h.personas.set_permissions("quill", ["code"])
    calls = {"research": 0, "write": 0}

    async def ready(*args):
        return Readiness("ready")

    async def available():
        return {"state": "ready"}

    h.adapters.update({key: SimpleNamespace(availability=available) for key in ("search", "code")})

    async def research(ctx, spec, args):
        calls["research"] += 1
        return ToolResult(
            "ok",
            "Research sources",
            data={"outcome": "verified_complete", "message": "Research sources"},
            evidence=[{"id": "source:1"}],
        )

    async def write(ctx, spec, args):
        assert ctx.principal.kind == "persona" and ctx.principal.client_id == "quill"
        if not await ctx.confirm("Append a paragraph", "Append to the team document."):
            return ToolResult("blocked", "Declined")
        calls["write"] += 1
        return ToolResult(
            "ok",
            "Paragraph appended",
            data={"outcome": "verified_complete", "message": "Paragraph appended"},
            evidence=[{"id": f"paragraph:{calls['write']}"}],
        )

    async def dispatch(ctx, spec, args):
        return await (research if args["capability"] == "search" else write)(ctx, spec, args)

    h.registry.hands["use_capability"] = SimpleNamespace(readiness=ready, call=dispatch)

    def tool(capability):
        return {
            "name": "use_capability",
            "arguments": {
                "capability": capability,
                "instruction": "Append a paragraph" if capability == "code" else "Research the topic",
            },
        }

    scripts = [
        [
            {
                "calls": [
                    {
                        "name": "run_team",
                        "arguments": {
                            "goal": "Research and write",
                            "members": ["scout", "quill"],
                            "reviewer": None,
                        },
                    }
                ]
            }
        ],
        [{"calls": [tool("search")]}, {"text": "Research complete"}],
        [{"calls": [tool("code"), tool("code")]}, {"text": "Writing complete"}],
        [{"text": "The team finished"}],
        [{"text": "Writing complete"}],
        [{"text": "The team finished"}],
        [{"text": "Writing complete"}],
    ]
    if mode == "delegate":
        scripts[0] = [
            {
                "calls": [
                    {
                        "name": "delegate_to_persona",
                        "arguments": {
                            "persona_id": "quill",
                            "task": "Write two paragraphs",
                            "share_context": False,
                        },
                    }
                ]
            }
        ]
        scripts.pop(1)
    elif mode == "review":
        scripts[0][0]["calls"][0]["arguments"].update(members=["scout"], reviewer="quill")
    models = []

    def factory():
        model = ScriptedModel(scripts.pop(0))
        model.model = "gpt-4.1"
        respond = model.respond

        async def billed(*args, **kwargs):
            turn = await respond(*args, **kwargs)
            turn.usage = {"input_tokens": 10000, "output_tokens": 1000}
            return turn

        model.respond = billed
        models.append(model)
        return model

    h.model_factory = factory
    return app, h, calls, models


@pytest.mark.parametrize("mode", ["team", "delegate", "review"])
def test_team_resumes_twice_without_repeating_members_or_identical_effects(tmp_path, monkeypatch, mode):
    app, h, calls, models = team_world(tmp_path, monkeypatch, mode)
    researched = 0 if mode == "delegate" else 1
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = events(client.post("/api/agui/agents/panini/run", json=body(), headers=HEADERS))
        assert first[-1]["outcome"]["type"] == "interrupt", first
        assert calls == {"research": researched, "write": 0}
        cost = h.runs.spent_today()
        # Recreate the stream runner: continuation must come from SQLite, not its old memory.
        app.state.agui = None
        interrupt = first[-1]["outcome"]["interrupts"][0]
        second = events(
            client.post(
                "/api/agui/agents/panini/run",
                json=body(run="r2", messages=[], resume=approve(interrupt)),
                headers=HEADERS,
            )
        )
        assert second[-1]["outcome"]["type"] == "interrupt", second
        assert calls == {"research": researched, "write": 1}
        assert h.runs.spent_today() == pytest.approx(cost)
        interrupt = second[-1]["outcome"]["interrupts"][0]
        third = events(
            client.post(
                "/api/agui/agents/panini/run",
                json=body(run="r3", messages=[], resume=approve(interrupt)),
                headers=HEADERS,
            )
        )
        assert third[-1]["type"] == "RUN_FINISHED" and "result" in third[-1], third
        assert calls == {"research": researched, "write": 2}
        # Every actual model turn is charged once, across the lead and both member attempts.
        from backend.brain.budget import turn_cost

        expected = sum(len(model.requests) for model in models) * turn_cost(
            "gpt-4.1", {"input_tokens": 10000, "output_tokens": 1000}
        )
        assert h.runs.spent_today() == pytest.approx(expected)
        rows = h.runs.list()
        assert not any(row["state"] == "parked" for row in rows)
        quill = [r for r in rows if r["principal"].get("client_id") == "quill"]
        assert len(quill) == 3 and all(r["mode"] == "subagent" for r in quill)
        receipts = h.store.all("SELECT * FROM receipts WHERE tool='code_run' AND outcome='ok'")
        assert len(receipts) == 2 and all(r["persona_id"] == "quill" for r in receipts)
        again = events(
            client.post(
                "/api/agui/agents/panini/run",
                json=body(run="r4", messages=[], resume=approve(interrupt)),
                headers=HEADERS,
            )
        )
        assert again[-1]["type"] == "RUN_ERROR" and calls["write"] == 2


@pytest.mark.parametrize("completed_writes", [0, 1])
def test_declining_a_team_closes_all_waiting_members(tmp_path, monkeypatch, completed_writes):
    app, h, calls, _ = team_world(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = events(client.post("/api/agui/agents/panini/run", json=body(), headers=HEADERS))
        if completed_writes:
            first = events(
                client.post(
                    "/api/agui/agents/panini/run",
                    json=body(
                        run="approve1", messages=[], resume=approve(first[-1]["outcome"]["interrupts"][0])
                    ),
                    headers=HEADERS,
                )
            )
        decision = approve(first[-1]["outcome"]["interrupts"][0])
        decision[0]["payload"]["approved"] = False
        final = events(
            client.post(
                "/api/agui/agents/panini/run",
                json=body(run="r2", messages=[], resume=decision),
                headers=HEADERS,
            )
        )
        assert final[-1]["result"]["outcome"] == "cancelled"
        assert "Earlier completed work" in final[-1]["result"]["message"]
        assert not any(row["state"] == "parked" for row in h.runs.list())
        assert calls == {"research": 1, "write": completed_writes}


def test_over_budget_model_turn_does_not_dispatch_its_tools(tmp_path, monkeypatch):
    app, h, calls, _ = team_world(tmp_path, monkeypatch)
    monkeypatch.setenv("PANINI_RUN_MAX_USD", "0.01")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        final = events(client.post("/api/agui/agents/panini/run", json=body(), headers=HEADERS))
        assert final[-1]["result"]["outcome"] == "partial"
        assert "cost limit" in final[-1]["result"]["message"]
        assert calls == {"research": 0, "write": 0}


def test_failed_quarantined_source_does_not_complete_the_team(tmp_path, monkeypatch):
    app, h, calls, _ = team_world(tmp_path, monkeypatch)

    async def failed_source(ctx, spec, args):
        calls["research"] += 1
        return ToolResult(
            "failed", "Source request failed", data={"outcome": "partial", "message": "Source request failed"}
        )

    h.registry.hands["use_capability"].call = failed_source
    with TestClient(app, base_url="http://127.0.0.1") as client:
        final = events(client.post("/api/agui/agents/panini/run", json=body(), headers=HEADERS))
        assert final[-1]["result"]["outcome"] == "partial"
        assert calls == {"research": 1, "write": 0}


@pytest.mark.parametrize("change", ["flag_off", "archive", "revoke", "cost_limit", "time_limit"])
def test_waiting_member_cannot_bypass_changed_identity_or_budget(tmp_path, monkeypatch, change):
    app, h, calls, _ = team_world(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = events(client.post("/api/agui/agents/panini/run", json=body(), headers=HEADERS))
        interrupt = first[-1]["outcome"]["interrupts"][0]
        if change == "flag_off":
            monkeypatch.setenv("PANINI_PERSONAS", "false")
        elif change == "archive":
            h.personas.archive("quill")
        elif change == "revoke":
            h.personas.set_permissions("quill", [])
        else:
            row = next(r for r in h.runs.list() if r["principal"].get("client_id") == "quill")
            checkpoint = h.runs.load_checkpoint(row["id"])
            key, limit = (
                ("budget_spent_usd", "max_usd")
                if change == "cost_limit"
                else ("budget_spent_seconds", "max_seconds")
            )
            checkpoint[key] = checkpoint["persona_budget"][limit] + 1
            h.runs.checkpoint(row["id"], checkpoint)
        final = events(
            client.post(
                "/api/agui/agents/panini/run",
                json=body(run="r2", messages=[], resume=approve(interrupt)),
                headers=HEADERS,
            )
        )
        assert final[-1]["result"]["outcome"] in {"blocked", "partial"}, final[-1]
        assert calls == {"research": 1, "write": 0}
        assert not any(row["state"] == "parked" for row in h.runs.list())
