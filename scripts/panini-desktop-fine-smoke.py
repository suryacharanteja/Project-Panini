"""Real HMAC broker / UIA tree / opaque-ref actions against a disposable WPF fixture."""

import asyncio
import ctypes
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
    from backend.capabilities.desktop import DesktopPipe
    from backend.hands.snapshot import Node, render, role
    from backend.panini.contracts import Fault

    def exe(project):
        return (
            ROOT
            / "panini-core/capabilities/desktop"
            / project
            / "bin/Release/net10.0-windows"
            / (project + ".exe")
        )

    os.environ["PANINI_DESKTOP_PIPE"] = "panini-fine-" + uuid4().hex
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    os.environ["PANINI_DESKTOP_WORKER"] = str(exe("Panini.Desktop.Worker"))
    broker = subprocess.Popen([str(exe("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW)
    win32 = "--win32" in sys.argv
    fixture = subprocess.Popen(
        [str(exe("Panini.Desktop.Fixture")), *(["--win32"] if win32 else [])],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    checks = []
    try:
        pipe, target = DesktopPipe(), None
        for _ in range(30):
            await asyncio.sleep(0.2)
            try:
                targets = await pipe.call({"op": "targets"})
                target = next((row for row in targets["targets"] if row["pid"] == fixture.pid), None)
                if target:
                    break
            except (Fault, OSError):
                pass
        assert target, "No fixture window in the interactive session."
        task = uuid4().hex

        async def snapshot(**kwargs):
            return await pipe.call({"op": "snapshot", "target": target, "task_id": task, **kwargs})

        page = await snapshot()
        assert page["nodes"] and page["nodes"][0]["depth"] == 0
        assert any(row["depth"] > 0 for row in page["nodes"])
        assert "element_id" not in json.dumps(page) and "automation_id" not in json.dumps(page)
        assert "fixture-secret" not in json.dumps(page)
        assert any("password" in row["states"] and row["value"] is None for row in page["nodes"])
        checks.append("hierarchy, broker-only UIA IDs and password suppression")
        clipped = await snapshot(depth=1, max_nodes=10)
        assert len(clipped["nodes"]) <= 10 and max(n["depth"] for n in clipped["nodes"]) <= 1
        checks.append("depth and node limits")
        focused = subprocess.run(
            [str(exe("Panini.RuntimeHost")), "--focus", json.dumps(target)],
            capture_output=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert focused.returncode == 0
        page = await snapshot()
        assert page["foreground"]
        draft = next(
            row for row in page["nodes"] if row["name"] == "Draft text" and row["type"] == "ControlType.Edit"
        )
        password = next(row for row in page["nodes"] if "password" in row["states"])
        try:
            await pipe.call(
                {
                    "op": "fine_action",
                    "target": target,
                    "task_id": task,
                    "snapshot_id": page["snapshot_id"],
                    "ref": password["ref"],
                    "action": {"kind": "set_value", "text": "NEVER"},
                }
            )
            raise AssertionError("Password action accepted")
        except Fault as exc:
            assert exc.code == "ELEMENT_UNAVAILABLE"
        checks.append("password action refused")
        page = await snapshot()
        draft = next(
            row for row in page["nodes"] if row["name"] == "Draft text" and row["type"] == "ControlType.Edit"
        )
        request = {
            "op": "fine_action",
            "target": target,
            "task_id": task,
            "snapshot_id": page["snapshot_id"],
            "ref": draft["ref"],
            "action": {"kind": "set_value", "text": "Fine desktop café 漢字 🪷"},
        }
        result = await pipe.call(request)
        assert result["dispatched"] and result["verified"]
        updated = await snapshot()
        assert (
            next(
                row
                for row in updated["nodes"]
                if row["name"] == "Draft text" and row["type"] == "ControlType.Edit"
            )["value"]
            == "Fine desktop café 漢字 🪷"
        )
        checks.append("opaque-ref native Unicode value action and assertion")
        for changed in ({}, {"task_id": "another-run"}, {"target": {**target, "created": "0"}}):
            bad = request
            if changed:
                fresh = await snapshot()
                field = next(
                    row
                    for row in fresh["nodes"]
                    if row["name"] == "Draft text" and row["type"] == "ControlType.Edit"
                )
                bad = {**request, "snapshot_id": fresh["snapshot_id"], "ref": field["ref"]}
            try:
                await pipe.call({**bad, **changed})
                raise AssertionError("Stale or rebound snapshot accepted")
            except Fault as exc:
                assert exc.code == "SNAPSHOT_EXPIRED"
        checks.append("consumed, cross-run and cross-target refs refused")
        stale = await snapshot()
        await snapshot()
        try:
            await pipe.call({**request, "snapshot_id": stale["snapshot_id"], "ref": stale["nodes"][0]["ref"]})
            raise AssertionError("A new snapshot did not invalidate old refs")
        except Fault as exc:
            assert exc.code == "SNAPSHOT_EXPIRED"
        checks.append("new snapshot invalidates prior refs")
        page = await snapshot()
        await pipe.call({"op": "stop", "task_id": task})
        try:
            await pipe.call({**request, "snapshot_id": page["snapshot_id"], "ref": page["nodes"][0]["ref"]})
            raise AssertionError("Stopped action accepted")
        except Fault as exc:
            assert exc.code == "SNAPSHOT_EXPIRED"
        checks.append("Stop retracts native refs")
        # Exercise the actual Python registry, ledger and concrete policy hand on the same native app.
        from backend import ledger
        from backend.capabilities.desktop import DesktopAdapter
        from backend.hands.desktop_fine import DesktopFine
        from backend.hands.snapshot import parse
        from backend.harness.store import Store
        from backend.ledger.artifacts import Artifacts
        from backend.ledger.hook import LedgerHook
        from backend.ledger.receipts import Ledger
        from backend.policy.engine import Policy
        from backend.policy.hook import PolicyHook
        from backend.registry.context import ToolContext, ToolView
        from backend.registry.registry import Registry

        os.environ.update(
            PANINI_DESKTOP_TOOLS="fine",
            PANINI_V2_LEDGER="true",
            PANINI_V2_POLICY="shadow",
            PANINI_POLICY_PROFILE="balanced",
        )
        with tempfile.TemporaryDirectory(prefix="panini-fine-") as directory:
            store = Store(Path(directory) / "state.sqlite3")
            h = SimpleNamespace(store=store, adapters={"desktop": DesktopAdapter(True)}, policy=Policy(store))
            h.artifacts = Artifacts(store, Path(directory) / "artifacts")
            h.ledger = ledger.install(Ledger(store), h.artifacts)
            h.desktop_fine = DesktopFine(h)
            h.registry = Registry({"harness": h}, sources=[(ROOT / "backend/registry/manifests", False)])
            assert not h.registry.errors
            h.registry.hooks = [LedgerHook(), PolicyHook(mode="shadow", policy=h.policy)]
            approvals = []

            async def confirm(title, detail, decision):
                approvals.append(decision.effect.value)
                return True

            ctx = ToolContext(view=ToolView(desktop_fine=True, ledger=True), run_id=uuid4().hex)
            ctx.confirm_decision = confirm
            try:
                listed = await h.registry.call(ctx, "desktop_windows", {})
                choice = next(
                    row["choice_id"] for row in listed.data["windows"] if row["label"] == target["label"]
                )

                async def read():
                    result = await h.registry.call(ctx, "desktop_snapshot", {"window": choice})
                    assert result.outcome == "ok", result.content
                    return parse(result.data["snapshot"])

                async def act(name, label, **kwargs):
                    current = await read()
                    reference = next(
                        n.ref
                        for n in current
                        if n.name == label and (label != "Draft text" or n.role == "textbox")
                    )
                    result = await h.registry.call(ctx, name, {"ref": reference, **kwargs})
                    assert result.outcome == "ok", result.content
                    return result

                await act("desktop_click", "Draft text")
                await act("desktop_type", "Draft text", text="Registry replay café 漢字 🪷")
                current = await read()
                reference = next(n.ref for n in current if n.name == "Draft text" and n.role == "textbox")
                verified = await h.registry.call(
                    ctx, "desktop_verify_value", {"ref": reference, "value": "Registry replay café 漢字 🪷"}
                )
                assert verified.outcome == "ok" and verified.evidence[0]["passed"]
                await act("desktop_click", "Apply draft")
                assert approvals == ["communicate"]
                verified = await h.registry.call(
                    ctx, "desktop_verify_text", {"text": "Applied: Registry replay café 漢字 🪷"}
                )
                assert verified.outcome == "ok" and verified.evidence[0]["passed"]
                await act("desktop_toggle", "Enable fixture option")
                current = await read()
                assert "checked" in next(n.states for n in current if n.name == "Enable fixture option")
                if not win32:
                    await act("desktop_select", "Beta")
                    current = await read()
                    assert "selected" in next(
                        n.states for n in current if n.name == "Beta" and n.role == "listitem"
                    )
                    await act("desktop_scroll", "Fixture rows", direction="down")
                checks.append(
                    "real registry click/type/assert/toggle"
                    + ("/select/scroll" if not win32 else "")
                    + ", concrete owner review and ledger receipts"
                )
                installed = await h.registry.call(ctx, "desktop_applications", {})
                assert installed.outcome == "ok" and any(
                    app["application_id"] == "explorer" for app in installed.data["applications"]
                )
                launch_ops = []
                original_call = h.adapters["desktop"].pipe.call

                async def trace_launch(payload):
                    try:
                        result = await original_call(payload)
                        launch_ops.append((payload["op"], "ok"))
                        return result
                    except Exception as exc:
                        launch_ops.append((payload["op"], type(exc).__name__, str(exc)))
                        raise

                h.adapters["desktop"].pipe.call = trace_launch
                opened = await h.registry.call(
                    ctx, "desktop_open_application", {"application_id": "explorer"}
                )
                assert opened.outcome == "ok" and opened.data["verified"], (opened, launch_ops)
                launched = h.adapters["desktop"].choices[opened.data["window"]]
                assert launched["application"].casefold() == "explorer"
                identity = await h.adapters["desktop"].pipe.call(
                    {"op": "window", "task_id": ctx.run_id, "target": launched}
                )
                assert identity["hwnd"] == launched["hwnd"]
                assert approvals[-1] == "local_exec"
                # Close only this newly observed HWND after the broker revalidates its native identity.
                assert ctypes.windll.user32.PostMessageW(ctypes.c_void_p(launched["hwnd"]), 0x10, 0, 0)
                checks.append("reviewed fixed-catalog Explorer launch verified as a new native window")
            finally:
                await h.desktop_fine.close()
                ledger._ledger = ledger._artifacts = None
                store.close()
        nodes = [
            Node(
                row["ref"],
                role(row["type"].removeprefix("ControlType.")),
                row["name"],
                row["value"],
                set(row["states"]),
                tuple(round(row["bounds"][key]) for key in ("x", "y", "width", "height")),
                row["depth"],
            )
            for row in updated["nodes"]
        ]
        evidence = ROOT / "artifacts/desktop-fine"
        evidence.mkdir(parents=True, exist_ok=True)
        platform = "win32" if win32 else "wpf"
        frameworks = sorted({row["framework"] for row in updated["nodes"]})
        assert ("Win32" if win32 else "WPF") in frameworks
        (evidence / f"native-{platform}-snapshot.txt").write_text(render(nodes), encoding="utf-8")
        (evidence / f"native-{platform}-evidence.json").write_text(
            json.dumps(
                {"passed": True, "platform": platform, "frameworks": frameworks, "checks": checks}, indent=2
            ),
            encoding="utf-8",
        )
        print(json.dumps({"passed": True, "checks": checks}))
    finally:
        fixture.terminate()
        fixture.wait(timeout=5)
        broker.terminate()
        broker.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
