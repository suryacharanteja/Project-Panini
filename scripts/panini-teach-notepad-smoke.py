"""Real teach/replay in Notepad, using only a new disposable document and an owned WPF window."""

import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    from backend import interrupts, ledger
    from backend.capabilities.desktop import DesktopAdapter
    from backend.hands.desktop_fine import DesktopFine
    from backend.hands.snapshot import parse
    from backend.harness.skills import Skills
    from backend.harness.store import Store
    from backend.interrupts import Interrupts, decided_by
    from backend.ledger.artifacts import Artifacts
    from backend.ledger.hook import LedgerHook
    from backend.ledger.receipts import Ledger
    from backend.panini.contracts import Fault
    from backend.policy.engine import Policy
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext, ToolView
    from backend.registry.registry import Registry
    from backend.teach.service import Teach

    def exe(project):
        return (
            ROOT
            / "panini-core/capabilities/desktop"
            / project
            / "bin/Release/net10.0-windows"
            / (project + ".exe")
        )

    os.environ.update(
        PANINI_DESKTOP_PIPE="panini-notepad-" + uuid4().hex,
        PANINI_DESKTOP_SECRET=secrets.token_urlsafe(32),
        PANINI_DESKTOP_WORKER=str(exe("Panini.Desktop.Worker")),
        PANINI_DESKTOP_TOOLS="fine",
        PANINI_V2_LEDGER="true",
        PANINI_TEACH="true",
        PANINI_V2_INTERRUPTS="true",
        PANINI_V2_POLICY="shadow",
        PANINI_POLICY_PROFILE="cautious",
    )
    broker = subprocess.Popen([str(exe("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW)
    evidence = ROOT / "artifacts/teach-notepad"
    evidence.mkdir(parents=True, exist_ok=True)
    document = evidence / ("PANINI-Teach-Notepad-" + uuid4().hex[:12] + ".txt")
    baseline = "PANINI disposable Notepad baseline"
    expected = "PANINI fine controls — café 漢字 🪷"
    document.write_text(baseline, encoding="utf-8")
    environment = {
        k: v
        for k, v in os.environ.items()
        if not (k.startswith("PANINI_") or k.endswith("_KEY") or "TOKEN" in k or "SECRET" in k)
    }
    subprocess.Popen(
        [str(Path(os.environ["WINDIR"]) / "System32/notepad.exe"), str(document)],
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    fixture = None
    try:
        with tempfile.TemporaryDirectory(prefix="panini-notepad-") as directory:
            store = Store(Path(directory) / "state.sqlite3")
            h = SimpleNamespace(store=store, adapters={"desktop": DesktopAdapter(True)}, policy=Policy(store))
            h.artifacts = Artifacts(store, Path(directory) / "artifacts")
            h.ledger = ledger.install(Ledger(store), h.artifacts)
            h.desktop_fine = DesktopFine(h)
            h.skills = Skills(user_root=Path(directory) / "skills")
            h.interrupts = interrupts.install(Interrupts(store))
            h.teach = Teach(h)
            model_requests = []

            class NoModel:
                async def respond(self, *args, **kwargs):
                    model_requests.append(True)
                    raise AssertionError("Native Notepad replay must not call a model provider")

            h.model_factory = NoModel
            h.registry = Registry({"harness": h}, sources=[(ROOT / "backend/registry/manifests", False)])
            assert not h.registry.errors
            h.registry.hooks = [LedgerHook(), PolicyHook(mode="shadow", policy=h.policy)]
            approvals = []
            ctx = ToolContext(view=ToolView(desktop_fine=True, ledger=True, teach=True), run_id=uuid4().hex)

            async def confirm(title, detail, decision):
                assert document.stem in detail, "A review left the disposable document."
                approvals.append({"effect": decision.effect.value, "level": decision.level.value})
                return True

            ctx.confirm_decision = confirm
            choice = None
            try:
                for _ in range(40):
                    listed = await h.registry.call(ctx, "desktop_windows", {})
                    if listed.outcome == "ok":
                        choice = next(
                            (r["choice_id"] for r in listed.data["windows"] if document.stem in r["label"]),
                            None,
                        )
                        if choice:
                            break
                    await asyncio.sleep(0.25)
                assert choice, "The new disposable document did not open in Notepad."
                target = h.adapters["desktop"].choices[choice]
                observed_titles = []
                original = h.adapters["desktop"].pipe.call

                async def monitored(payload):
                    result = await original(payload)
                    if payload["op"] == "snapshot" and payload.get("target") == target:
                        observed_titles.append(result["title"])
                        (evidence / "notepad-titles.json").write_text(
                            json.dumps(observed_titles), encoding="utf-8"
                        )
                    return result

                h.adapters["desktop"].pipe.call = monitored
                activation = await h.registry.call(ctx, "desktop_activate", {"window": choice})
                assert activation.outcome == "ok" and activation.data["verified"], activation

                async def worker(action):
                    # Fetch fine-only states using a direct disposable worker for the owned test document.
                    payload = {"op": "snapshot", "target": target}

                    def invoke(payload):
                        result = subprocess.run(
                            [str(exe("Panini.Desktop.Worker"))],
                            input=json.dumps(payload) + "\n",
                            text=True,
                            capture_output=True,
                            timeout=12,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        response = json.loads(result.stdout)
                        assert response["ok"], (response, result.stderr)
                        return response["result"]

                    frame = await asyncio.to_thread(invoke, payload)
                    row = next(r for r in frame["nodes"] if r["type"] == "ControlType.Document")
                    assert document.stem in frame["title"]
                    request = {
                        "op": "action",
                        "target": target,
                        "observed_at": frame["observed_at"],
                        "expected_window_title": frame["title"],
                        "action": {
                            **action,
                            "element_id": row["element_id"],
                            "expected_name": row["name"],
                            "expected_automation_id": row["automation_id"],
                            "expected_type": row["type"],
                            "expected_bounds": row["bounds"],
                            "expected_states": row["states"],
                            "expected_value": row["value"],
                        },
                    }
                    return await asyncio.to_thread(invoke, request)

                async def read():
                    result = await h.registry.call(ctx, "desktop_snapshot", {"window": choice})
                    assert result.outcome == "ok", result.content
                    nodes = parse(result.data["snapshot"])
                    assert document.stem in nodes[0].name
                    return result.data["snapshot"], nodes

                async def tool(name, args):
                    result = await h.registry.call(ctx, name, args)
                    assert result.outcome == "ok", result.content
                    return result

                text, nodes = await read()
                (evidence / "notepad-before.txt").write_text(text, encoding="utf-8")
                editor = next(
                    n for n in nodes if n.role in {"textbox", "document"} and baseline in (n.value or "")
                )
                session = h.desktop_fine.sessions[ctx.run_id]
                try:
                    await h.adapters["desktop"].pipe.call(
                        {
                            "op": "fine_action",
                            "task_id": ctx.run_id,
                            "target": target,
                            "snapshot_id": session["page"]["snapshot_id"],
                            "ref": editor.ref,
                            "action": {"kind": "fine_type", "text": "BLOCKED\tINPUT"},
                        }
                    )
                    raise AssertionError("Native document navigation characters were accepted")
                except Fault as error:
                    assert error.code == "USE_REVIEWED_KEY_STEPS", error.message
                _, nodes = await read()
                editor = next(
                    n for n in nodes if n.role in {"textbox", "document"} and baseline in (n.value or "")
                )
                await tool("desktop_click", {"ref": editor.ref})
                await read()
                await tool("desktop_press", {"key": "Ctrl+A"})
                _, nodes = await read()
                editor = next(
                    n for n in nodes if n.role in {"textbox", "document"} and baseline in (n.value or "")
                )
                await tool("desktop_type", {"ref": editor.ref, "text": expected})
                text, _ = await read()
                (evidence / "notepad-after.txt").write_text(text, encoding="utf-8")
                verified = await tool("desktop_verify_text", {"text": expected})
                assert verified.evidence[0]["passed"]
                await read()
                await tool("desktop_press", {"key": "Ctrl+S"})
                for _ in range(30):
                    if document.read_text(encoding="utf-8-sig").strip() == expected:
                        break
                    await asyncio.sleep(0.1)
                assert document.read_text(encoding="utf-8-sig").strip() == expected
                await read()
                # Real recorder hooks; input below is a test driver acting only on our disposable document.
                await worker({"kind": "focus"})
                await worker({"kind": "fine_type", "text": "", "replace": True})
                started = await h.teach.recorder.start_desktop(ctx, choice)
                assert started["state"] == "recording"
                demonstrated = "PANINI demonstrated — café 漢字 🪷"
                await worker({"kind": "fine_type", "text": demonstrated})
                await asyncio.sleep(0.5)
                await worker({"kind": "keypress", "key": "Ctrl+S"})
                await asyncio.sleep(0.5)
                stopped = await h.teach.recorder.stop()
                assert stopped["state"] == "draft", stopped
                replay = stopped["replay"]
                (evidence / "notepad-recording.json").write_text(
                    json.dumps(replay, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                assert not any(s["tool"] == "manual" for s in replay["steps"]), replay
                typed = next(s for s in replay["steps"] if s["tool"] == "desktop_type")
                assert typed["args"]["replace"] is True
                assert any(
                    s["tool"] == "desktop_press" and s["args"]["key"] == "Ctrl+S" for s in replay["steps"]
                )
                replay["inputs"] = {"draft": {"default": demonstrated}}
                typed["args"]["text"] = "{{draft}}"
                replay["expect"] = [
                    {
                        "id": "s999",
                        "tool": "desktop_verify_value",
                        "target": typed["target"],
                        "args": {"value": "{{draft}}"},
                    }
                ]
                review = h.teach.bundles.propose(
                    "notepad-taught", "Write and save the selected disposable document", replay
                )["review"]
                h.interrupts.resolve(
                    review["id"],
                    nonce=review["nonce"],
                    action_digest=review["action_digest"],
                    status="resolved",
                    payload={"approved": True},
                    decided_by=decided_by("avatar", "local"),
                )
                await worker(
                    {"kind": "fine_type", "text": "reset text that must be replaced", "replace": True}
                )
                fixture = subprocess.Popen(
                    [str(exe("Panini.Desktop.Fixture"))],
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for _ in range(30):
                    await asyncio.sleep(0.15)
                    windows = await h.adapters["desktop"].pipe.call({"op": "targets", "task_id": ctx.run_id})
                    owned = next((w for w in windows["targets"] if w["pid"] == fixture.pid), None)
                    if owned:
                        focused = await asyncio.to_thread(
                            subprocess.run,
                            [str(exe("Panini.RuntimeHost")), "--focus", json.dumps(owned)],
                            capture_output=True,
                            timeout=3,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        assert focused.returncode == 0, "The explicitly owned fixture could not be focused."
                    frame = await h.adapters["desktop"].pipe.call(
                        {"op": "snapshot", "task_id": ctx.run_id, "target": target}
                    )
                    if not frame["foreground"]:
                        break
                assert not frame["foreground"], "The separate owned fixture did not take foreground."
                expected = "PANINI replay replaced and saved — résumé 漢字 🪷"
                result = await h.teach.replayer.run(ctx, "notepad-taught", {"draft": expected}, window=choice)
                assert result.outcome == "ok" and result.data["state"] == "passed", result
                assert not model_requests and result.data["heals"] == 0
                for _ in range(30):
                    if document.read_text(encoding="utf-8-sig") == expected:
                        break
                    await asyncio.sleep(0.1)
                assert document.read_text(encoding="utf-8-sig") == expected
                receipts = store.all(
                    "SELECT tool,effect,outcome,dispatched FROM receipts WHERE tool LIKE 'desktop_%'"
                )
                assert any(
                    r["tool"] == "desktop_activate" and r["outcome"] == "ok" and r["dispatched"]
                    for r in receipts
                )
                assert any(
                    r["tool"] == "desktop_press" and r["effect"] == "local_write" and r["outcome"] == "ok"
                    for r in receipts
                )
                report = {
                    "passed": True,
                    "task": "real Notepad record / approve / parameterized replay / save",
                    "application": target["application"],
                    "document": str(document),
                    "recordedSteps": len(replay["steps"]),
                    "diskValueVerified": True,
                    "differentReplayInput": True,
                    "documentReplaced": True,
                    "emptyReplacementVerified": True,
                    "activatedFromAnotherOwnedWindow": True,
                    "modelCalls": len(model_requests),
                    "approvals": approvals,
                    "receipts": receipts,
                    "liveHealerQualified": False,
                    "desktopLldComplete": False,
                    "saveAsDialogQualified": False,
                    "physicalAndroidQualified": False,
                    "coarseComparisonQualified": False,
                }
                (evidence / "notepad-evidence.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8"
                )
                await tool("desktop_activate", {"window": choice})
                await read()
                await tool("desktop_press", {"key": "Ctrl+W"})
                print(json.dumps(report))
            finally:
                await h.teach.close()
                await h.desktop_fine.close()
                interrupts._service = None
                ledger._ledger = ledger._artifacts = None
                store.close()
    finally:
        if fixture and fixture.poll() is None:
            fixture.terminate()
            fixture.wait(timeout=5)
        broker.terminate()
        broker.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
