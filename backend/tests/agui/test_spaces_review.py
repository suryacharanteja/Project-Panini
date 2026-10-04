from fastapi.testclient import TestClient
from panini_cli.agent.model import ScriptedModel

from backend.harness.runtime import Harness
from backend.harness.skills import Skills
from backend.harness.store import Store
from backend.tests.agui.test_agui import HEADERS, TOKEN, body, events


def test_real_sse_page_review_resume_restores_runner(tmp_path, monkeypatch):
    for flag in ("PANINI_SPACES", "PANINI_V2_AGUI", "PANINI_V2_INTERRUPTS"):
        monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    from backend.app import create_app

    model = ScriptedModel([])
    h = Harness(
        Store(tmp_path / "h.db"), model_factory=lambda: model, skills=Skills(user_root=tmp_path / "skills")
    )
    app = create_app(harness_factory=lambda: h, console_token=TOKEN)
    space = h.spaces.create_space("Notebook")
    model.steps = [
        {
            "calls": [
                {
                    "name": "pages_create",
                    "arguments": {
                        "space_id": space["id"],
                        "title": "Research",
                        "content": "Reviewed findings",
                    },
                }
            ]
        },
        {
            "calls": [
                {
                    "name": "finish",
                    "arguments": {"outcome": "verified_complete", "message": "Saved the reviewed page."},
                }
            ]
        },
    ]
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            first = events(client.post("/api/agui/agents/panini/run", headers=HEADERS, json=body()))
            interrupt = first[-1]["outcome"]["interrupts"][0]
            assert interrupt["metadata"]["kind"] == "review", first
            assert h.spaces.pages(space["id"]) == []
            app.state.agui = None
            resume = [
                {
                    "interruptId": interrupt["id"],
                    "status": "resolved",
                    "payload": {"approved": True, "content": "Owner approved and edited"},
                    "metadata": {
                        "nonce": interrupt["metadata"]["nonce"],
                        "actionDigest": interrupt["metadata"]["actionDigest"],
                    },
                }
            ]
            # The decision commits, but its response never reaches the client. The parked
            # checkpoint must resume using the same proof and decision, after a restart.
            from backend import interrupts

            h.interrupts.resolve(
                interrupt["id"],
                nonce=interrupt["metadata"]["nonce"],
                action_digest=interrupt["metadata"]["actionDigest"],
                status="resolved",
                payload=resume[0]["payload"],
                decided_by=interrupts.decided_by("agui", "tap"),
            )
            bad = [{**resume[0], "metadata": {**resume[0]["metadata"], "nonce": "wrong"}}]
            rejected = events(
                client.post(
                    "/api/agui/agents/panini/run",
                    headers=HEADERS,
                    json=body(run="bad", messages=[], resume=bad),
                )
            )
            assert rejected[-1]["code"] == "INTERRUPT_CHANGED"
            assert h.spaces.pages(space["id"]) == []
            second = events(
                client.post(
                    "/api/agui/agents/panini/run",
                    headers=HEADERS,
                    json=body(run="r2", messages=[], resume=resume),
                )
            )
            assert second[-1]["result"]["outcome"] == "verified_complete", second
            pages = h.spaces.pages(space["id"])
            assert len(pages) == 1
            assert h.spaces.read(pages[0]["id"])["content"] == "Owner approved and edited"
            again = events(
                client.post(
                    "/api/agui/agents/panini/run",
                    headers=HEADERS,
                    json=body(run="r3", messages=[], resume=resume),
                )
            )
            assert again[-1]["code"] == "INTERRUPT_UNKNOWN"
            assert len(h.spaces.pages(space["id"])) == 1
    finally:
        h.store.close()
