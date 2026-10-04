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
from .schema import SENSITIVE, Target, input_values, substitute

NODE = re.compile(r'^\s*-\s+([\w-]+)(?:\s+"((?:[^"\\]|\\.)*)")?.*?\[ref=((?:f\d+)?e\d+)\](.*)$')


def nodes(snapshot):
    result = []
    for line in snapshot.splitlines():
        match = NODE.match(line)
        if not match:
            continue
        name = match[2] or ""
        try:
            name = json.loads('"' + name + '"')
        except json.JSONDecodeError:
            pass
        if not SENSITIVE.search(name):
            result.append(
                {
                    "role": match[1],
                    "name": name,
                    "ref": match[3],
                    "checked": "[checked]" in line,
                    "disabled": "[disabled]" in line,
                }
            )
    return result[:600]


def resolve(target, candidates):
    if target.frame:
        raise ValueError("Frame-scoped replay requires owner correction; it cannot match another frame.")
    main_frame = candidates[0]["ref"].split("e")[0] if candidates else ""
    chosen = [
        n
        for n in candidates
        if not n["disabled"]
        and n["ref"].split("e")[0] == main_frame
        and (not target.role or n["role"] == target.role)
    ]
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
        result = await self.h.registry.call(ctx, tool, args)
        if result.outcome != "ok":
            raise ReplayFailure(result)
        return result

    async def run(self, ctx, name, supplied, browser="managed"):
        if self.lock.locked() or self.h.teach.recorder.current or self.h.teach.recorder.starting:
            return ToolResult("blocked", "Another demonstration is replaying.")
        async with self.lock:
            identifier = uuid4().hex
            row, replay = self.bundles.current(name)
            values = input_values(replay, supplied)
            patch = copy.deepcopy(replay.model_dump())
            self.active = asyncio.current_task()
            self.active_name = name
            previous_binding = getattr(ctx, "teach_binding", None)
            ctx.teach_binding = (name, row["version"])
            state, failure, heals, evidence = "failed", "", 0, []
            self.h.store.execute(
                "INSERT INTO skill_runs(id,name,version,state,inputs_digest,started) VALUES (?,?,?,'running',?,?)",
                (identifier, name, row["version"], digest(values), time.time()),
            )
            try:
                await self.call(ctx, "browser_select", {"browser": browser})
                for index, step in enumerate(replay.steps):
                    if not self.h.teach.allowed() or not self.h.teach.valid_binding(ctx):
                        raise ValueError("Teach replay was disabled or PANINI was paused.")
                    if step.tool == "manual":
                        raise ValueError("A manual step requires the owner.")
                    args = substitute(step.args, values)
                    if step.target:
                        target = Target.model_validate(substitute(step.target.model_dump(), values))
                        fresh = await self.call(ctx, "browser_snapshot", {})
                        candidates = nodes(fresh.content)
                        node = resolve(target, candidates)
                        if node is None:
                            # No step has been dispatched. Never retry an effect with an unknown result.
                            if heals >= 2 or target.frame:
                                raise ValueError("The reviewed target is missing or ambiguous.")
                            main_frame = candidates[0]["ref"].split("e")[0] if candidates else ""
                            safe = [
                                n
                                for n in candidates
                                if not n["disabled"]
                                and n["ref"].split("e")[0] == main_frame
                                and (not target.role or n["role"] == target.role)
                            ]
                            node = await healer.propose(self.h.model_factory, step, safe)
                            if node is None:
                                raise ValueError("The healer could not identify a unique safe target.")
                            if (
                                sum(n["role"] == node["role"] and n["name"] == node["name"] for n in safe)
                                != 1
                            ):
                                raise ValueError("The repaired target is ambiguous.")
                            heals += 1
                            patch["steps"][index]["target"] = {"role": node["role"], "name": node["name"]}
                        if step.checked is not None and node["checked"] == step.checked:
                            continue
                        grounded = {"target": node["ref"], "element": f"{node['role']} {node['name']}"}
                        if step.tool in {"browser_press_key", "browser_file_upload"}:
                            await self.call(ctx, "browser_click", grounded)
                        else:
                            args.update(grounded)
                    result = await self.call(ctx, step.tool, args)
                    evidence.extend(result.evidence)
                for expectation in replay.expect:
                    args = substitute(expectation.args, values)
                    if expectation.target:
                        target = Target.model_validate(substitute(expectation.target.model_dump(), values))
                        fresh = await self.call(ctx, "browser_snapshot", {})
                        node = resolve(target, nodes(fresh.content))
                        if node is None:
                            raise ValueError("An expected target is missing or ambiguous.")
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
                    if exc.result.outcome == "unknown_effect" or exc.result.dispatched
                    else "failed"
                )
            except asyncio.CancelledError:
                state, failure = (
                    "interrupted",
                    "Replay stopped. Inspect the page before starting another run.",
                )
                raise
            except Exception:  # noqa: BLE001 -- Provider/validation failures never replay an effect.
                failure = (
                    "Replay stopped: the target, inputs, permission or expected result could not be verified."
                )
            finally:
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
