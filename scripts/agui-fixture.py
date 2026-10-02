"""Record the AG-UI event streams PANINI produces for the conformance test (V2-07).

Runs a scripted turn with a tool call, an approval interrupt and its resume through the real endpoint, and
writes the SSE bodies to panini-core/apps/admin/test/fixtures/agui-*.sse. The admin test validates every
event against the official @ag-ui/core schemas.

    .venv\\Scripts\\python.exe scripts/agui-fixture.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "panini-core/apps/admin/test/fixtures"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="panini-agui-"))
    os.environ.update(
        PANINI_DATA_DIR=str(tmp / "data"),
        PANINI_V2_AGUI="true",
        PANINI_V2_INTERRUPTS="true",
        PANINI_V2_LEDGER="true",
        PANINI_V2_REGISTRY="true",
        PANINI_JEV_GATES="false",
        PANINI_APPROVALS="all",
        # As in the backend tests: no capability depends on what this machine has turned on.
        PANINI_CLI_ENABLED="false",
        JEV_REV_DESKTOP_ENABLED="false",
        PANINI_CAPABILITY_ROUTER_ENABLED="false",
    )
    from fastapi.testclient import TestClient
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store

    command = "Set-Content -Path note.txt -Value 'hello'; Get-Content note.txt"
    plan = {"steps": [{"step": "Write the note", "status": "in_progress"}]}
    model = ScriptedModel(
        [
            {"text": "I'll write the note.", "calls": [{"name": "update_plan", "arguments": plan}]},
            {"calls": [{"name": "run_command", "arguments": {"command": command, "cwd": str(tmp)}}]},
            {
                "calls": [
                    {"name": "finish", "arguments": {"outcome": "verified_complete", "message": "Saved."}}
                ]
            },
        ]
    )
    h = Harness(
        Store(tmp / "h.sqlite3"), model_factory=lambda: model, skills=Skills(user_root=tmp / "skills")
    )
    token = "f" * 32
    headers = {"x-panini-console": token, "accept": "text/event-stream"}
    app = create_app(harness_factory=lambda: h, console_token=token)
    base = {"threadId": "fixture-thread", "tools": [], "context": [], "state": {}, "forwardedProps": {}}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.post(
            "/api/agui/agents/panini/run",
            json={
                **base,
                "runId": "run-1",
                "messages": [{"id": "u1", "role": "user", "content": "Save a note"}],
            },
            headers=headers,
        ).text
        finished = [json.loads(line[6:]) for line in first.splitlines() if line.startswith("data: ")][-1]
        interrupt = finished["outcome"]["interrupts"][0]
        resume = [
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
        second = client.post(
            "/api/agui/agents/panini/run",
            json={**base, "runId": "run-2", "messages": [], "resume": resume},
            headers=headers,
        ).text
        connect = client.post(
            "/api/agui/agents/panini/connect", json={"threadId": "fixture-thread"}, headers=headers
        ).text
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in (("run-interrupt", first), ("run-resume", second), ("connect", connect)):
        # Secrets and machine paths never go into the repository.
        text = text.replace(interrupt["metadata"]["nonce"], "NONCE")
        for escapes in (8, 4, 2, 1):  # The working folder, at every JSON escaping depth.
            text = text.replace(str(tmp).replace("\\", "\\" * escapes), "TMP")
        assert tmp.name not in text, "a machine path would leak into the fixture"
        (OUT / f"agui-{name}.sse").write_text(text, encoding="utf-8")
    print("wrote", sorted(p.name for p in OUT.glob("agui-*.sse")))


if __name__ == "__main__":
    main()
