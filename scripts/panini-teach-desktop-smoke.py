"""Real owner hook recording, reviewed skill and registry replay on a disposable Windows fixture."""

import argparse
import asyncio
import ctypes
import json
import os
import secrets
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="artifacts/teach-desktop")
    args = parser.parse_args()
    evidence = (ROOT / args.evidence).resolve()
    if not evidence.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("Evidence must stay within this workspace's artifacts directory.")
    from backend.capabilities.desktop import DesktopPipe
    from backend.core_contracts import Principal
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills
    from backend.harness.store import Store
    from backend.interrupts import decided_by
    from backend.ledger.artifacts import Artifacts
    from backend.panini.contracts import Fault
    from backend.registry.context import ToolContext, ToolView

    def exe(project):
        return (
            ROOT
            / "panini-core/capabilities/desktop"
            / project
            / "bin/Release/net10.0-windows"
            / (project + ".exe")
        )

    os.environ.update(
        PANINI_DESKTOP_PIPE="panini-teach-" + uuid4().hex,
        PANINI_DESKTOP_SECRET=secrets.token_urlsafe(32),
        PANINI_DESKTOP_WORKER=str(exe("Panini.Desktop.Worker")),
        PANINI_DESKTOP_TOOLS="fine",
        PANINI_TEACH="true",
        PANINI_V2_LEDGER="true",
        PANINI_V2_INTERRUPTS="true",
        PANINI_V2_POLICY="shadow",
        PANINI_POLICY_PROFILE="balanced",
    )
    broker = subprocess.Popen([str(exe("Panini.Desktop.Broker"))], creationflags=subprocess.CREATE_NO_WINDOW)
    fixture = subprocess.Popen(
        [str(exe("Panini.Desktop.Fixture"))], creationflags=subprocess.CREATE_NO_WINDOW
    )
    try:
        pipe, selected = DesktopPipe(), None
        for _ in range(40):
            await asyncio.sleep(0.2)
            try:
                selected = next(
                    (w for w in (await pipe.call({"op": "targets"}))["targets"] if w["pid"] == fixture.pid),
                    None,
                )
                if selected:
                    break
            except (Fault, OSError):
                pass
        assert selected

        class Adapter:
            enabled = True

            def __init__(self):
                self.pipe = pipe

            def permits(self, app):
                return app == selected["application"]

            async def targets(self):
                return [
                    {
                        "choice_id": "fixture",
                        "application": selected["application"],
                        "label": selected["label"],
                    }
                ]

            async def select(self, choice):
                assert choice == "fixture"
                return selected

        async def worker(op, action=None):
            payload = {"op": op, "target": selected}
            if action is not None:
                frame = await worker("snapshot")
                label = action.pop("name")
                row = next(r for r in frame["nodes"] if r["name"] == label)
                payload.update(
                    observed_at=frame["observed_at"],
                    expected_window_title=frame["title"],
                    action={
                        **action,
                        "element_id": row["element_id"],
                        "expected_name": row["name"],
                        "expected_automation_id": row["automation_id"],
                        "expected_type": row["type"],
                        "expected_bounds": row["bounds"],
                        "expected_value": row["value"],
                        "expected_states": row["states"],
                    },
                )

            def run():
                result = subprocess.run(
                    [str(exe("Panini.Desktop.Worker"))],
                    input=json.dumps(payload) + "\n",
                    text=True,
                    capture_output=True,
                    timeout=12,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                response = json.loads(result.stdout)
                assert response["ok"], response
                return response["result"]

            return await asyncio.to_thread(run)

        async def owned_private_input(text):
            # Test driver only: inject a fixed fixture string after verifying private focus in our PID.
            frame = await worker("snapshot")
            assert any(r["focused"] and "password" in r["states"] for r in frame["nodes"])

            class Keyboard(ctypes.Structure):
                _fields_ = [
                    ("vk", wintypes.WORD),
                    ("scan", wintypes.WORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("extra", ctypes.c_size_t),
                ]

            class Mouse(ctypes.Structure):
                _fields_ = [
                    ("x", wintypes.LONG),
                    ("y", wintypes.LONG),
                    ("data", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("extra", ctypes.c_size_t),
                ]

            class Data(ctypes.Union):
                _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]

            class Input(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("data", Data)]

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
            pid = wintypes.DWORD()
            hwnd = user32.GetForegroundWindow()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            assert hwnd == selected["hwnd"] and pid.value == fixture.pid
            inputs = (Input * (2 * len(text)))()
            for index, char in enumerate(text):
                for offset, flags in enumerate((4, 6)):
                    inputs[2 * index + offset].type = 1
                    inputs[2 * index + offset].data.keyboard = Keyboard(0, ord(char), flags, 0, 0)
            assert user32.SendInput(len(inputs), inputs, ctypes.sizeof(Input)) == len(inputs)
            await asyncio.sleep(0.5)
            frame = await worker("snapshot")
            assert any(r["focused"] and "password" in r["states"] for r in frame["nodes"])

        with tempfile.TemporaryDirectory(prefix="panini-teach-desktop-") as folder:
            os.environ["PANINI_DATA_DIR"] = str(Path(folder) / "data")
            model_requests = []

            class NoModel:
                async def respond(self, *args, **kwargs):
                    model_requests.append(True)
                    raise AssertionError("The native smoke must not call a model provider")

            h = Harness(
                Store(Path(folder) / "state.sqlite3"),
                skills=Skills(user_root=Path(folder) / "skills"),
                model_factory=NoModel,
            )
            h.adapters["desktop"] = Adapter()
            h.artifacts = Artifacts(h.store, Path(folder) / "artifacts")
            ctx = ToolContext(
                principal=Principal(),
                view=ToolView(desktop_fine=True, ledger=True, teach=True),
                run_id=uuid4().hex,
            )
            reviews = []

            async def confirm(title, detail, decision):
                reviews.append({"effect": decision.effect.value, "level": decision.level.value})
                return True

            ctx.confirm_decision = confirm
            try:
                started = await h.teach.recorder.start_desktop(ctx, "fixture")
                assert started["state"] == "recording"
                for operation in (
                    {"op": "record_start", "task_id": "another", "target": selected},
                    {"op": "launch", "task_id": "another", "application_id": "notepad"},
                ):
                    try:
                        await pipe.call(operation)
                        raise AssertionError("Concurrent recording or product action accepted")
                    except Fault as error:
                        assert error.code in {"RECORDING_BUSY", "OWNER_RECORDING"}
                for wrong in (
                    {"op": "record_stop", "task_id": "another", "recording_id": "wrong"},
                    {"op": "record_stop", "task_id": started["id"], "recording_id": "wrong"},
                ):
                    try:
                        await pipe.call(wrong)
                        raise AssertionError("A different recording owner or token was accepted")
                    except Fault as error:
                        assert error.code == "RECORDING_CHANGED"
                await worker("action", {"name": "Draft text", "kind": "click"})
                await asyncio.sleep(0.4)
                await worker(
                    "action",
                    {"name": "Draft text", "kind": "type_text", "text": "PANINI taught Unicode café"},
                )
                await asyncio.sleep(0.4)
                await worker("action", {"name": "Draft text", "kind": "keypress", "key": "ArrowLeft"})
                await asyncio.sleep(0.4)
                await worker("action", {"name": "Draft text", "kind": "keypress", "key": "ArrowRight"})
                await asyncio.sleep(0.4)
                await worker("action", {"name": "Apply draft", "kind": "click"})
                await asyncio.sleep(0.5)
                stopped = await h.teach.recorder.stop()
                assert stopped["state"] == "draft", stopped
                replay = stopped["replay"]
                assert replay["kind"] == "desktop" and not any(
                    s["tool"] == "manual" for s in replay["steps"]
                ), replay
                assert any(s["tool"] == "desktop_type" for s in replay["steps"])
                clicks = [s for s in replay["steps"] if s["tool"] == "desktop_click"]
                assert clicks and all(s.get("crop_artifact") for s in clicks), clicks
                crop_row, crop = h.artifacts.get(clicks[-1]["crop_artifact"])
                assert crop_row["mime"] == "image/png" and crop.startswith(bytes.fromhex("89504e470d0a1a0a"))
                (evidence / "click-crop.png").write_bytes(crop)
                encoded = json.dumps(replay)
                assert (
                    "fixture-secret" not in encoded
                    and "element_id" not in encoded
                    and "automation_id" not in encoded
                )
                replay["expect"] = [
                    {
                        "id": "s999",
                        "tool": "desktop_verify_text",
                        "args": {"text": "Applied: PANINI taught Unicode café"},
                    }
                ]
                proposal = h.teach.bundles.propose("windows-taught", "Type and apply a fixture draft", replay)
                review = proposal["review"]
                h.interrupts.resolve(
                    review["id"],
                    nonce=review["nonce"],
                    action_digest=review["action_digest"],
                    status="resolved",
                    payload={"approved": True},
                    decided_by=decided_by("avatar", "local"),
                )
                await worker("action", {"name": "Draft text", "kind": "set_value", "text": "reset"})
                result = await h.teach.replayer.run(ctx, "windows-taught", {}, window="fixture")
                assert result.outcome == "ok" and result.data["state"] == "passed", result
                assert not model_requests and result.data["heals"] == 0
                assert reviews and not h.desktop_fine.sessions
                receipts = h.store.all(
                    "SELECT tool,effect,outcome,dispatched FROM receipts WHERE tool LIKE 'desktop_%'"
                )
                assert any(r["tool"] == "desktop_type" and r["outcome"] == "ok" for r in receipts)
                ctx.run_id = uuid4().hex
                await worker("action", {"name": "Draft text", "kind": "focus"})
                snap = await h.registry.call(ctx, "desktop_snapshot", {"window": "fixture"})
                assert snap.outcome == "ok", snap
                assert (
                    next(
                        n.name
                        for n, row in h.desktop_fine.sessions[ctx.run_id]["nodes"].values()
                        if row["focused"]
                    )
                    == "Draft text"
                )
                button = next(
                    n.ref
                    for n, _ in h.desktop_fine.sessions[ctx.run_id]["nodes"].values()
                    if n.name == "Apply draft" and n.role == "button"
                )
                focused = await h.registry.call(ctx, "desktop_focus", {"ref": button})
                assert focused.outcome == "ok", focused
                frame = await worker("snapshot")
                assert next(r["name"] for r in frame["nodes"] if r["focused"]) == "Apply draft"
                assert any(r["name"] == "Applied: PANINI taught Unicode café" for r in frame["nodes"]), (
                    "Focus invoked the button"
                )
                await h.teach.recorder.start_desktop(ctx, "fixture")
                await worker("action", {"name": "Apply draft", "kind": "keypress", "key": "Tab"})
                await asyncio.sleep(0.4)
                await owned_private_input("private-fixture-typed-text")
                private = await h.teach.recorder.stop()
                assert private["state"] == "draft", private
                assert not any(s["tool"] == "desktop_type" for s in private["replay"]["steps"])
                assert "private-fixture-typed-text" not in json.dumps(private)
                for identifier in private["replay"]["recording_artifacts"].values():
                    artifact = h.artifacts.get(identifier)
                    assert "private-fixture-typed-text" not in artifact[1].decode("utf-8")
                    assert "fixture-secret" not in artifact[1].decode("utf-8")
                await h.desktop_fine.stop(ctx)
                await h.desktop_fine.stop(ctx)
                # Stop is a durable fence, including a start racing with a cancellation.
                for index in range(3):
                    task = "record-cancel-" + uuid4().hex
                    if index == 0:
                        await pipe.call({"op": "stop", "task_id": task})
                    pending = asyncio.create_task(
                        pipe.call({"op": "record_start", "task_id": task, "target": selected})
                    )
                    await asyncio.sleep(0.05)
                    await pipe.call({"op": "stop", "task_id": task})
                    try:
                        await pending
                    except Fault as error:
                        assert error.code == "RECORDING_STOPPED", error.code
                    assert (await pipe.call({"op": "record_stop", "task_id": task, "recording_id": "wrong"}))[
                        "state"
                    ] == "idle"
                # A fresh recording remains available after each fenced cancellation.
                await h.teach.recorder.start_desktop(ctx, "fixture")
                assert (await h.teach.recorder.stop(discard=True))["state"] == "discarded"
                # A real drag between two named controls is recorded as one reviewed drag step (V2-22 gestures).
                drag_frame = await worker("snapshot")
                source = next(r["bounds"] for r in drag_frame["nodes"] if r["name"] == "Draft text")
                landing = next(r["bounds"] for r in drag_frame["nodes"] if r["name"] == "Apply draft")
                await h.teach.recorder.start_desktop(ctx, "fixture")
                await worker("action", {
                    "name": "Draft text", "kind": "drag",
                    "from_x": round(source["x"] + source["width"] / 2), "from_y": round(source["y"] + source["height"] / 2),
                    "to_x": round(landing["x"] + landing["width"] / 2), "to_y": round(landing["y"] + landing["height"] / 2),
                })  # fmt: skip
                await asyncio.sleep(0.5)
                dragged = await h.teach.recorder.stop()
                drags = [step for step in dragged["replay"]["steps"] if step["tool"] == "desktop_drag"]
                assert drags and drags[0]["drop"]["name"] == "Apply draft", dragged["replay"]["steps"]
                assert drags[0]["target"]["name"] == "Draft text"
                # Suspend only our fixture's UIA provider, then kill only our broker.
                # Its recording child must exit even if a provider call cannot complete.
                frame = await worker("snapshot")
                box = next(r["bounds"] for r in frame["nodes"] if r["name"] == "Draft text")
                await h.teach.recorder.start_desktop(ctx, "fixture")
                children = [
                    p
                    for p in psutil.Process(broker.pid).children()
                    if p.name() == "Panini.Desktop.Worker.exe"
                ]
                assert len(children) == 1
                ui32 = ctypes.WinDLL("user32", use_last_error=True)
                ui32.GetForegroundWindow.restype = wintypes.HWND
                assert ui32.GetForegroundWindow() == selected["hwnd"]
                frozen = psutil.Process(fixture.pid)
                try:
                    frozen.suspend()
                    ui32.SetCursorPos(round(box["x"] + box["width"] / 2), round(box["y"] + box["height"] / 2))
                    ui32.mouse_event(2, 0, 0, 0, 0)
                    ui32.mouse_event(4, 0, 0, 0, 0)
                    await asyncio.sleep(0.5)
                    broker.terminate()
                    broker.wait(timeout=5)
                    await asyncio.to_thread(children[0].wait, timeout=5)
                finally:
                    frozen.resume()
                await h.teach.recorder.stop(discard=True)
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / "recording.json").write_text(
                    json.dumps(replay, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                report = {
                    "passed": True,
                    "kind": "real Windows hooks / WPF UIA / registry replay",
                    "modelCalls": len(model_requests),
                    "recordedSteps": len(replay["steps"]),
                    "reviews": reviews,
                    "receipts": receipts,
                    "liveHealerQualified": False,
                    "desktopLldComplete": False,
                    "ownerTokenBinding": True,
                    "stopStartFence": True,
                    "restartAfterCancellation": True,
                    "focusDoesNotInvoke": True,
                    "privateTypingSuppressed": True,
                    "workerExitedAfterBrokerDeathWithSuspendedProvider": True,
                }
                (evidence / "native-evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report))
            finally:
                await h.close()
    finally:
        fixture.terminate()
        fixture.wait(timeout=5)
        broker.terminate()
        broker.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
