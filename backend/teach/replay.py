"""Deterministic replay; each action and assertion enters the normal registry pipeline."""

import asyncio
import copy
import json
import re
import time
from uuid import uuid4

from ..core_contracts import ToolResult
from ..interrupts import digest
from ..registry.context import ToolContext
from . import healer
from .schema import SENSITIVE, Target, frame_name, input_values, substitute

NODE = re.compile(r'^\s*-\s+([\w-]+)(?:\s+"((?:[^"\\]|\\.)*)")?.*?\[ref=((?:f\d+)?[ewas]\d+)\](.*)$')


def nodes(snapshot):
    result = []
    parents = []
    for line in snapshot.splitlines():
        match = NODE.match(line)
        if not match:
            continue
        name = match[2] or ""
        try:
            name = json.loads('"' + name + '"')
        except json.JSONDecodeError:
            pass
        depth = (len(line) - len(line.lstrip())) // 2
        parents = parents[:depth]
        ancestry = [
            {"role": p["role"], "name": p["name"]} for p in parents if p["name"] and p["role"] != "window"
        ][-8:]
        private = SENSITIVE.search(name) or "[password]" in line or any(p.get("private") for p in parents)
        if not private:
            frame = next((p for p in reversed(parents) if p["role"] == "iframe"), None)
            result.append(
                {
                    "role": match[1],
                    "name": name,
                    "ref": match[3],
                    "checked": "[checked]" in line,
                    "disabled": "[disabled]" in line,
                    "ancestors": ancestry,
                    "frame": frame["ref"] if frame else "",
                }
            )
        while len(parents) < depth:
            parents.append({"role": "generic", "name": ""})
        parents.append({"role": match[1], "name": name, "ref": match[3], "private": bool(private)})
    return result[:600]


def scope(target, candidates, frame_ref=""):
    frame = frame_ref
    if target.frame and not frame:
        wanted = frame_name(target.frame)
        matches = [n for n in candidates if n["role"] == "iframe" and n["name"] == wanted]
        if len(matches) != 1:
            raise ValueError("The reviewed iframe is missing or ambiguous.")
        frame = matches[0]["ref"]
    main_frame = candidates[0]["ref"].split("e")[0] if candidates and "e" in candidates[0]["ref"] else ""
    ancestry = [p.model_dump() for p in target.ancestors]
    return [
        n
        for n in candidates
        if not n["disabled"]
        and n["frame"] == frame
        and (frame or (n["ref"].split("e")[0] if "e" in n["ref"] else "") == main_frame)
        and (not target.role or n["role"] == target.role)
        and (not ancestry or n.get("ancestors", []) == ancestry)
    ]


def resolve(target, candidates, frame_ref=""):
    chosen = scope(target, candidates, frame_ref)
    exact = [n for n in chosen if n["name"] == target.name] if target.name else []
    if not exact and target.name_regex:
        prefix = re.sub(r"\\(.)", r"\1", target.name_regex[1:])
        exact = [n for n in chosen if n["name"].startswith(prefix)]
    return exact[0] if len(exact) == 1 else None


class Replayer:
    def __init__(self, harness, bundles):
        self.h, self.bundles = harness, bundles
        self.lock = asyncio.Lock()
        self.active = None
        self.active_name = None

    async def call(self, parent, tool, args):
        # Nested calls must not overwrite their parent's ledger receipt or policy decision.
        ctx = ToolContext(
            principal=parent.principal,
            view=parent.view,
            run_id=parent.run_id,
            agent=parent.agent,
            confirm=parent.confirm,
            calls=parent.calls,
            taint=parent.taint,
        )
        ctx.teach_replay = True
        ctx.occurrences = parent.__dict__.setdefault("occurrences", {})
        ctx.confirm_decision = getattr(parent, "confirm_decision", None)
        ctx.teach_binding = getattr(parent, "teach_binding", None)
        ctx.teach_frame_selector = getattr(parent, "teach_frame_selector", "")
        result = await self.h.registry.call(ctx, tool, args)
        if result.outcome != "ok":
            raise ReplayFailure(result)
        if (
            tool == "desktop_snapshot"
            and getattr(parent, "teach_window_title", None) is not None
            and not getattr(parent, "teach_dialog", "")
        ):
            title = result.data.get("title", "")
            if parent.teach_window_application.casefold() == "notepad":
                title = title.removeprefix("*")
            if (
                title != parent.teach_window_title
                or result.data.get("selection") != parent.teach_window_selection
            ):
                raise ReplayFailure(
                    ToolResult("blocked", "The selected window changed documents during replay.")
                )
        return result

    async def follow_dialog(self, ctx, dialog):
        """Bind the window a desktop step belongs to: the owned dialog it was recorded in, or the document again.
        A dialog has up to 5 s to open after the previous step; returning from one re-pins the document title,
        because a reviewed Save As legitimately renames it."""
        main = getattr(ctx, "teach_main_window", None)
        current = getattr(ctx, "teach_dialog", "")
        if main is None or dialog == current:
            return
        if not dialog:
            ctx.teach_dialog = ""
            ctx.teach_window_title = None  # Re-pinned below from the document's (possibly renamed) title.
            back = await self.call(ctx, "desktop_snapshot", {"window": main})
            title = back.data["title"]
            if ctx.teach_window_application.casefold() == "notepad":
                title = title.removeprefix("*")
            ctx.teach_window_title, ctx.teach_window_selection = title, back.data["selection"]
            return
        for _ in range(20):
            windows = (await self.call(ctx, "desktop_windows", {})).data["windows"]
            owned = [w for w in windows if w.get("owner") == main and w["label"] == dialog]
            if len(owned) == 1:
                ctx.teach_dialog = dialog
                await self.call(ctx, "desktop_snapshot", {"window": owned[0]["choice_id"]})
                return
            if len(owned) > 1:
                raise ValueError("Several matching dialogs are open; the replay target is ambiguous.")
            await asyncio.sleep(0.25)
        raise ValueError(f"The '{dialog}' dialog did not open.")

    async def run(self, ctx, name, supplied, browser="managed", window=""):
        if self.lock.locked() or self.h.teach.recorder.current or self.h.teach.recorder.starting:
            return ToolResult("blocked", "Another demonstration is replaying.")
        async with self.lock:
            identifier = uuid4().hex
            row, replay = self.bundles.current(name)
            values = input_values(replay, supplied)
            patch = copy.deepcopy(replay.model_dump())
            if replay.kind == "desktop":
                # Native Stop fences its task. Give the replay its own task so cleanup cannot
                # stop the invoking agent's existing desktop session or its later actions.
                parent = ctx
                ctx = ToolContext(
                    principal=parent.principal,
                    view=parent.view,
                    run_id=identifier,
                    agent=parent.agent,
                    confirm=parent.confirm,
                    confirm_decision=parent.confirm_decision,
                    calls=parent.calls,
                    taint=parent.taint,
                )
                ctx.occurrences = parent.__dict__.setdefault("occurrences", {})
            self.active = asyncio.current_task()
            self.active_name = name
            previous_binding = getattr(ctx, "teach_binding", None)
            ctx.teach_binding = (name, row["version"])
            state, failure, heals, evidence = "failed", "", 0, []
            launched = False
            desktop = replay.kind == "desktop"
            snapshot_tool = "desktop_snapshot" if desktop else "browser_snapshot"

            async def bind_desktop(choice):
                ctx.teach_main_window = choice
                ctx.teach_dialog = ""
                await self.call(ctx, "desktop_activate", {"window": choice})
                selected = await self.call(ctx, "desktop_snapshot", {})
                ctx.teach_window_application = replay.desktop_application
                ctx.teach_window_title = selected.data["title"]
                if replay.desktop_application.casefold() == "notepad":
                    ctx.teach_window_title = ctx.teach_window_title.removeprefix("*")
                ctx.teach_window_selection = selected.data["selection"]

            try:
                self.h.store.execute(
                    "INSERT INTO skill_runs(id,name,version,state,inputs_digest,started) VALUES (?,?,?,'running',?,?)",
                    (identifier, name, row["version"], digest(values), time.time()),
                )
                if desktop and replay.steps[0].tool != "desktop_open_application":
                    choices = (await self.call(ctx, "desktop_windows", {})).data["windows"]
                    choices = [
                        w
                        for w in choices
                        if w["application"].casefold() == replay.desktop_application.casefold()
                        and (not window or w["choice_id"] == window)
                    ]
                    if len(choices) != 1:
                        raise ValueError("Select one window of the reviewed application before replay.")
                    await bind_desktop(choices[0]["choice_id"])
                elif not desktop:
                    await self.call(ctx, "browser_select", {"browser": browser})
                for index, step in enumerate(replay.steps):
                    ctx.teach_frame_selector = ""
                    if not self.h.teach.allowed() or not self.h.teach.valid_binding(ctx):
                        raise ValueError("Teach replay was disabled or PANINI was paused.")
                    if step.tool == "manual":
                        raise ValueError("A manual step requires the owner.")
                    args = substitute(step.args, values)
                    if desktop and step.tool == "desktop_open_application":
                        result = await self.call(ctx, step.tool, args)
                        launched = True
                        if (
                            result.data.get("application", "").casefold()
                            != replay.desktop_application.casefold()
                        ):
                            raise ReplayFailure(
                                ToolResult(
                                    "unknown_effect", "The launched application changed.", dispatched=True
                                )
                            )
                        evidence.extend(result.evidence)
                        await bind_desktop(result.data["window"])
                        continue
                    if desktop and step.target:
                        await self.follow_dialog(ctx, step.target.dialog)
                    if step.target:
                        target = Target.model_validate(substitute(step.target.model_dump(), values))
                        fresh = await self.call(ctx, snapshot_tool, {})
                        candidates = nodes(fresh.content)
                        frame_ref = ""
                        if target.frame and not any(n["role"] == "iframe" and n["name"] for n in candidates):
                            hand = self.h.registry.hands["browser_snapshot"]
                            if not hasattr(hand, "verified_frame"):
                                raise ValueError("The reviewed iframe identity cannot be verified.")
                            frame_ref = await hand.verified_frame(ctx, browser, target.frame, fresh.content)
                        node = resolve(target, candidates, frame_ref)
                        if node is None:
                            # No step has been dispatched. Never retry an effect with an unknown result.
                            if heals >= 2 or target.frame:
                                raise ValueError("The reviewed target is missing or ambiguous.")
                            safe = scope(target, candidates, frame_ref)
                            node = await healer.propose(self.h.model_factory, step, safe)
                            if node is None:
                                raise ValueError("The healer could not identify a unique safe target.")
                            if (
                                sum(n["role"] == node["role"] and n["name"] == node["name"] for n in safe)
                                != 1
                            ):
                                raise ValueError("The repaired target is ambiguous.")
                            heals += 1
                            patch["steps"][index]["target"] = {
                                "role": node["role"],
                                "name": node["name"],
                                "ancestors": node.get("ancestors", []),
                            }
                        if step.checked is not None and node["checked"] == step.checked:
                            continue
                        grounded = (
                            {"ref": node["ref"]}
                            if desktop
                            else {"target": node["ref"], "element": f"{node['role']} {node['name']}"}
                        )
                        ctx.teach_frame_selector = target.frame
                        if step.tool in {"desktop_press", "desktop_type"}:
                            await self.call(ctx, "desktop_focus", grounded)
                            fresh = await self.call(ctx, snapshot_tool, {})
                            focused = resolve(target, nodes(fresh.content))
                            if focused is None or fresh.data.get("focused_ref") != focused["ref"]:
                                raise ValueError("The keyboard target changed while focusing it.")
                            if step.tool == "desktop_type":
                                args.update(ref=focused["ref"])
                        if step.tool in {"browser_press_key", "browser_file_upload"}:
                            await self.call(ctx, "browser_click", grounded)
                        elif step.tool not in {"desktop_press", "desktop_type"}:
                            args.update(grounded)
                        if step.tool == "desktop_drag":
                            drop = Target.model_validate(substitute(step.drop.model_dump(), values))
                            landing = resolve(drop, candidates)
                            if landing is None or landing["ref"] == node["ref"]:
                                raise ValueError("The reviewed drop target is missing or ambiguous.")
                            args["to_ref"] = landing["ref"]
                    result = await self.call(ctx, step.tool, args)
                    evidence.extend(result.evidence)
                for expectation in replay.expect:
                    ctx.teach_frame_selector = ""
                    args = substitute(expectation.args, values)
                    if desktop and expectation.target:
                        await self.follow_dialog(ctx, expectation.target.dialog)  # Back from Save As, Open, …
                    if expectation.target:
                        target = Target.model_validate(substitute(expectation.target.model_dump(), values))
                        fresh = await self.call(ctx, snapshot_tool, {})
                        candidates = nodes(fresh.content)
                        frame_ref = ""
                        if target.frame and not any(n["role"] == "iframe" and n["name"] for n in candidates):
                            hand = self.h.registry.hands["browser_snapshot"]
                            if not hasattr(hand, "verified_frame"):
                                raise ValueError("The reviewed iframe identity cannot be verified.")
                            frame_ref = await hand.verified_frame(ctx, browser, target.frame, fresh.content)
                        node = resolve(target, candidates, frame_ref)
                        if node is None:
                            raise ValueError("An expected target is missing or ambiguous.")
                        if desktop:
                            if expectation.tool == "desktop_verify_element":
                                args.update(ref=node["ref"], role=node["role"], name=node["name"])
                            elif expectation.tool == "desktop_verify_value":
                                args.update(ref=node["ref"])
                        else:
                            args.update(target=node["ref"], element=f"{node['role']} {node['name']}")
                    assertion = await self.call(ctx, expectation.tool, args)
                    if not any(e.get("kind") == "assertion" for e in assertion.evidence):
                        raise ValueError("The expectation returned no assertion evidence.")
                    evidence.extend(assertion.evidence)
                if not replay.expect:
                    raise ValueError("No expected result was verified.")
                if heals:
                    self.bundles.propose(
                        name, row["description"], patch, base_version=row["version"], run_id=ctx.run_id
                    )
                state = "passed"
            except ReplayFailure as exc:
                failure = exc.result.content[:500]
                state = (
                    "unknown_effect"
                    if exc.result.outcome == "unknown_effect" or exc.result.dispatched or launched
                    else "failed"
                )
            except asyncio.CancelledError:
                state, failure = (
                    "interrupted",
                    "Replay stopped. Inspect the page before starting another run.",
                )
                raise
            except Exception:  # noqa: BLE001 -- Provider/validation failures never replay an effect.
                if launched:
                    state = "unknown_effect"
                failure = (
                    "Replay stopped: the target, inputs, permission or expected result could not be verified."
                )
            finally:
                if desktop:
                    try:
                        await self.h.desktop_fine.stop(ctx)
                    except Exception:  # noqa: BLE001 -- Cleanup failure is never a qualified pass.
                        if state == "passed":
                            state, failure = "failed", "Desktop cleanup could not be confirmed."
                self.active = None
                self.active_name = None
                ctx.teach_binding = previous_binding
                self.h.store.execute(
                    "UPDATE skill_runs SET state=?,heals=?,failure=?,evidence=?,finished=? WHERE id=?",
                    (state, heals, failure, json.dumps(evidence), time.time(), identifier),
                )
            return ToolResult(
                "ok" if state == "passed" else "failed",
                "Skill passed its expected results." if state == "passed" else failure,
                data={
                    "id": identifier,
                    "state": state,
                    "heals": heals,
                    "version": row["version"],
                    "failure": failure,
                },
                evidence=evidence,
            )

    async def stop(self):
        task = self.active
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class ReplayFailure(Exception):
    def __init__(self, result):
        self.result = result
