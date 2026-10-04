"""Fine registry tools in installed Notepad, restricted to this script's new disposable file."""

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
    from backend import ledger
    from backend.capabilities.desktop import DesktopAdapter
    from backend.hands.desktop_fine import DesktopFine
    from backend.hands.snapshot import parse
    from backend.harness.store import Store
    from backend.ledger.artifacts import Artifacts
    from backend.ledger.hook import LedgerHook
    from backend.ledger.receipts import Ledger
    from backend.panini.contracts import Fault
    from backend.policy.engine import Policy
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext, ToolView
    from backend.registry.registry import Registry

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
        PANINI_V2_POLICY="shadow",
        PANINI_POLICY_PROFILE="cautious",
    )
    broker = subprocess.Popen([str(exe("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW)
    evidence = ROOT / "artifacts/desktop-fine"
    evidence.mkdir(parents=True, exist_ok=True)
    document = evidence / ("PANINI-Fine-Notepad-" + uuid4().hex[:12] + ".txt")
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
    try:
        with tempfile.TemporaryDirectory(prefix="panini-notepad-") as directory:
            store = Store(Path(directory) / "state.sqlite3")
            h = SimpleNamespace(store=store, adapters={"desktop": DesktopAdapter(True)}, policy=Policy(store))
            h.artifacts = Artifacts(store, Path(directory) / "artifacts")
            h.ledger = ledger.install(Ledger(store), h.artifacts)
            h.desktop_fine = DesktopFine(h)
            h.registry = Registry({"harness": h}, sources=[(ROOT / "backend/registry/manifests", False)])
            assert not h.registry.errors
            h.registry.hooks = [LedgerHook(), PolicyHook(mode="shadow", policy=h.policy)]
            approvals = []
            ctx = ToolContext(view=ToolView(desktop_fine=True, ledger=True), run_id=uuid4().hex)

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
                focused = subprocess.run(
                    [str(exe("Panini.RuntimeHost")), "--focus", json.dumps(target)],
                    capture_output=True,
                    timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                assert focused.returncode == 0

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
                report = {
                    "passed": True,
                    "application": target["application"],
                    "task": "real Notepad type and save through fine registry",
                    "document": str(document),
                    "diskValueVerified": True,
                    "approvals": approvals,
                    "modelCalls": 0,
                    "coarseComparisonQualified": False,
                    "documentNavigationCharactersBlocked": True,
                    "frameworks": sorted(
                        {row["framework"] for _, row in h.desktop_fine.sessions[ctx.run_id]["nodes"].values()}
                    ),
                }
                (evidence / "notepad-evidence.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8"
                )
                await tool("desktop_press", {"key": "Ctrl+W"})
                print(json.dumps(report))
            finally:
                await h.desktop_fine.close()
                ledger._ledger = ledger._artifacts = None
                store.close()
    finally:
        broker.terminate()
        broker.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
