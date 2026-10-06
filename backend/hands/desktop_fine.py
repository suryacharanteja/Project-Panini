"""Observation-scoped Windows tools. Native IDs remain in the HMAC broker."""

import asyncio
import base64
import io
import json
import re
import unicodedata
from dataclasses import replace
from uuid import uuid4

from PIL import Image

from .. import v2flags
from ..core_contracts import LEVEL_ORDER, EffectClass, Readiness, ToolResult, raise_effect
from ..live import takeover
from ..panini.contracts import Fault
from ..policy.effects import classify_words
from .snapshot import Node, render, role

KEY = re.compile(
    r"^(?:(?:Ctrl|Shift|Alt|Win)\+){0,4}(?:[A-Z0-9]|F(?:[1-9]|1[0-2])|Enter|Return|Tab|Escape|Space|Backspace|Delete|(?:Arrow)?(?:Up|Down|Left|Right)|Home|End|PageUp|PageDown|Insert)$",
    re.I,
)


def tree(page):
    rows = page.get("nodes")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 600:
        raise ValueError("The desktop returned an invalid tree.")
    found = {}
    for row in rows:
        if "element_id" in row or "automation_id" in row:
            raise ValueError("The companion needs the opaque-ref snapshot protocol.")
        reference = row.get("ref", "")
        if not re.fullmatch(r"w\d+", reference) or reference in found:
            raise ValueError("Invalid desktop ref.")
        states = set(row["states"])
        if not re.fullmatch(r"[A-F0-9]{64}", row.get("identity", "")):
            raise ValueError("Invalid desktop target identity.")
        if not re.fullmatch(r"[A-F0-9]{64}", row.get("anchor", "")):
            raise ValueError("Invalid desktop target anchor.")
        if "entity" in row and not re.fullmatch(r"[A-F0-9]{64}", row["entity"]):
            raise ValueError("Invalid desktop entity identity.")
        if row["type"] == "ControlType.TabItem" and "selected" in states and "entity" not in row:
            raise ValueError("Update the companion to bind the selected document tab.")
        box = tuple(round(row["bounds"][k]) for k in ("x", "y", "width", "height"))
        n = Node(
            reference,
            role(row["type"].removeprefix("ControlType.")),
            "Password field" if "password" in states else row["name"],
            None if "password" in states else row.get("value"),
            states,
            box,
            row["depth"],
        )
        found[reference] = (n, row)
    text = render([n for n, _ in found.values()])
    return found, text


class DesktopFine:
    def __init__(self, harness):
        self.h = harness
        self.sessions = {}
        self.lock = asyncio.Lock()

    @property
    def adapter(self):
        adapter = self.h.adapters.get("desktop")
        if adapter is None or not adapter.enabled:
            raise ValueError("The Windows companion is unavailable.")
        return adapter

    def ready(self, ctx):
        if v2flags.value("PANINI_DESKTOP_TOOLS") != "fine" or not v2flags.enabled("PANINI_V2_LEDGER"):
            raise ValueError("Enable fine desktop tools and the action ledger in Settings.")
        if (
            not ctx.view.private
            or ctx.view.background
            or not ctx.principal.verified
            or ctx.principal.kind not in {"owner", "persona"}
        ):
            raise ValueError("Windows tools require a private foreground owner or permitted persona run.")
        if (
            not ctx.view.desktop_fine
            or ctx.view.capabilities is not None
            and "desktop" not in ctx.view.capabilities
        ):
            raise ValueError("Fine desktop tools are unavailable in this run.")
        if self.h.policy.paused():
            raise ValueError("PANINI is paused.")
        if not ctx.run_id:
            ctx.run_id = uuid4().hex
        if len(self.sessions) >= 128 and ctx.run_id not in self.sessions:
            raise ValueError("Too many desktop runs are active.")
        session = self.sessions.setdefault(
            ctx.run_id, {"principal": ctx.principal, "target": None, "nodes": {}, "choice": ""}
        )
        if session["principal"] != ctx.principal:
            raise ValueError("This desktop run belongs to a different principal.")
        return session

    async def stop(self, ctx):
        session = self.sessions.pop(ctx.run_id, None)
        if session:
            await self.adapter.pipe.call({"op": "stop", "task_id": ctx.run_id})

    async def close(self):
        runs = list(self.sessions)
        self.sessions.clear()
        for identifier in runs:
            try:
                await self.adapter.pipe.call({"op": "stop", "task_id": identifier})
            except (Fault, OSError, ValueError):
                pass

    async def snapshot(self, ctx, session, depth=20, *, publish=True):
        if publish:
            session["nodes"] = {}
        if not session["target"]:
            raise ValueError("List desktop_windows and select a window in desktop_snapshot first.")
        if not self.adapter.permits(session["target"]["application"]):
            raise ValueError("This application is no longer allowed.")
        page = await self.adapter.pipe.call(
            {
                "op": "snapshot",
                "task_id": ctx.run_id,
                "target": session["target"],
                "depth": depth,
                "max_nodes": 600,
            }
        )
        found, text = tree(page)
        if publish:
            session["nodes"], session["page"] = found, page
        return page, found, text

    def target(self, session, args, name):
        if name == "desktop_activate":
            roots = [(n, row) for n, row in session["nodes"].values() if n.depth == 0 and n.role == "window"]
            if len(roots) != 1:
                raise ValueError("A unique reviewed window root is required for activation.")
            return roots[0]
        if name == "desktop_press":
            focused = [(n, row) for n, row in session["nodes"].values() if row.get("focused")]
            if len(focused) != 1:
                raise ValueError("A unique focused control is required before pressing keys.")
            return focused[0]
        target = session["nodes"].get(args.get("ref"))
        if target is None:
            raise ValueError("Stale desktop ref. Take desktop_snapshot again.")
        if name == "desktop_select" and args.get("option"):
            parent, _ = target
            children = []
            inside = False
            for n, row in session["nodes"].values():
                if n.ref == parent.ref:
                    inside = True
                    continue
                if inside and n.depth <= parent.depth:
                    break
                if (
                    inside
                    and n.name == args["option"]
                    and "SelectionItemPatternIdentifiers.Pattern" in row["patterns"]
                ):
                    children.append((n, row))
            if len(children) != 1:
                raise ValueError(
                    "Open the list and take a new snapshot; the option must be uniquely visible."
                )
            target = children[0]
        return target

    async def authorize(self, ctx, spec, args, session, target, reviewed=None):
        n, row = target
        if "password" in n.states or "disabled" in n.states:
            raise ValueError("PANINI never acts on credential or disabled controls.")
        effect = spec.effect
        terminal = session["target"]["application"].lower() in {
            "cmd",
            "conhost",
            "powershell",
            "pwsh",
            "windowsterminal",
        } or bool(re.search(r"\b(terminal|console)\b", n.name, re.I))
        if terminal and spec.name in {"desktop_type", "desktop_press"}:
            effect = raise_effect(effect, EffectClass.LOCAL_EXEC)
        if session["target"]["application"].lower() == "systemsettings" and spec.name == "desktop_toggle":
            effect = raise_effect(effect, EffectClass.ACCOUNT)
        if word := classify_words(n.name):
            effect = raise_effect(effect, word)
        if spec.name == "desktop_drag":
            drop = session["nodes"].get(args.get("to_ref"))
            if drop and (word := classify_words(drop[0].name)):
                effect = raise_effect(effect, word)
        if spec.name == "desktop_type" and args.get("submit"):
            # Type-and-submit is one reviewed step with the submit effect (V2-26 combined action).
            effect = raise_effect(effect, EffectClass.SUBMIT)
        if spec.name == "desktop_type" and n.role not in {"textbox", "document"}:
            raise ValueError("Typing requires an editable text control.")
        if (
            spec.name == "desktop_type"
            and "ValuePatternIdentifiers.Pattern" not in row["patterns"]
            and any(unicodedata.category(c) == "Cc" for c in args["text"])
        ):
            raise ValueError(
                "Document typing requires plain text; send Enter or Tab as separately reviewed key steps."
            )
        if spec.name == "desktop_press":
            key = args["key"].lower()
            if not KEY.fullmatch(args["key"]) or len(set(key.split("+")[:-1])) != len(key.split("+")[:-1]):
                raise ValueError("Unsupported key chord.")
            if key in {"enter", "return"}:
                effect = raise_effect(effect, EffectClass.SUBMIT)
            elif key in {"ctrl+s", "ctrl+shift+s"}:
                effect = raise_effect(effect, EffectClass.LOCAL_WRITE)
            elif key in {"delete", "shift+delete", "alt+f4", "ctrl+w"}:
                effect = raise_effect(effect, EffectClass.DESTROY)
            elif key == "ctrl+enter":
                effect = raise_effect(effect, EffectClass.COMMUNICATE)
            elif "win+" in key or key == "ctrl+shift+enter":
                effect = raise_effect(effect, EffectClass.LOCAL_EXEC)
        if effect == EffectClass.CREDENTIAL:
            raise ValueError("Credential entry belongs to the person.")
        concrete = replace(spec, effect=effect)
        real = {
            **args,
            "element": n.name,
            "role": n.role,
            "box": n.box,
            "window": session["page"].get("title", session["target"]["label"]),
            "application": session["target"]["application"],
        }
        if spec.name == "desktop_drag" and session["nodes"].get(args.get("to_ref")):
            real["drop_element"] = session["nodes"][args["to_ref"]][0].name
        if terminal and spec.name == "desktop_type":
            real["command"] = args["text"]
        decision = self.h.policy.decide(
            ctx.principal, concrete, real, taint=ctx.taint, background=ctx.view.background
        )
        ctx.desktop_policy, ctx.decision = (concrete, real), decision
        if decision.action == "deny":
            raise ValueError("Desktop action denied: " + "; ".join(decision.reasons))
        if reviewed is not None:
            if decision.action != "allow" and (
                reviewed.action == "allow"
                or decision.effect != reviewed.effect
                or decision.level != reviewed.level
            ):
                raise ValueError("Desktop permissions changed. Review the action again.")
        elif decision.action != "allow":
            detail = json.dumps(real, ensure_ascii=False)
            preview = await self.preview(ctx, session, n)
            richer = getattr(ctx, "confirm_decision", None)
            if not richer and decision.level.value != "tap":
                raise ValueError("This caller cannot present the required stronger approval.")
            approved = (
                await richer(spec.title, preview + detail, decision)
                if richer
                else ctx.confirm is not None and await ctx.confirm(spec.title, preview + detail)
            )
            if not approved:
                raise ValueError("The owner did not approve this desktop action.")
        self.ready(ctx)
        return decision

    async def preview(self, ctx, session, node):
        # Never capture a page with a credential field. The text preview is always available.
        if any("password" in n.states for n, _ in session["nodes"].values()):
            return ""
        try:
            raw = await self.adapter.pipe.call(
                {"op": "capture", "task_id": ctx.run_id, "target": session["target"]}
            )
            data = base64.b64decode(raw["image_base64"], validate=True)
            if len(data) > 3_000_000:
                return ""
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 20_000_000:
                    return ""
                x, y, w, h = node.box
                scale = raw["scale"]
                left, top = (x - raw["source_x"]) * scale, (y - raw["source_y"]) * scale
                cropped = image.crop(
                    (
                        max(0, round(left - 16)),
                        max(0, round(top - 16)),
                        min(image.width, round(left + w * scale + 16)),
                        min(image.height, round(top + h * scale + 16)),
                    )
                )
                cropped.thumbnail((800, 600))
                stream = io.BytesIO()
                cropped.save(stream, format="PNG")
            identifier = self.h.artifacts.put("screenshot", stream.getvalue(), "image/png", run_id=ctx.run_id)
            return f"Target preview: /api/harness/artifacts/{identifier}\n"
        except (Fault, ValueError, OSError, KeyError):
            return ""

    async def open_application(self, ctx, spec, args):
        """Launch only a fixed native catalog entry and prove a new allowed window exists."""
        session = self.ready(ctx)
        requested = args["application_id"]
        catalog = await self.adapter.applications()
        matches = [app for app in catalog if app["application_id"] == requested]
        if len(matches) != 1:
            raise ValueError("This installed application is unavailable or not allowed.")
        app = matches[0]
        concrete = {
            "application_id": requested,
            "application": app["application"],
            "window": app["label"],
        }
        decision = self.h.policy.decide(
            ctx.principal, spec, concrete, taint=ctx.taint, background=ctx.view.background
        )
        ctx.desktop_policy, ctx.decision = (spec, concrete), decision
        if decision.action == "deny":
            raise ValueError("Application launch denied: " + "; ".join(decision.reasons))
        # A native process launch always gets a fresh owner decision, including in balanced mode.
        detail = json.dumps(concrete, ensure_ascii=False)
        richer = getattr(ctx, "confirm_decision", None)
        if not richer and decision.level.value != "tap":
            raise ValueError("This caller cannot present the required stronger approval.")
        approved = (
            await richer(spec.title, detail, decision)
            if richer
            else ctx.confirm is not None and await ctx.confirm(spec.title, detail)
        )
        if not approved:
            raise ValueError("The owner did not approve this application launch.")
        self.ready(ctx)
        current = [row for row in await self.adapter.applications() if row["application_id"] == requested]
        if current != [app]:
            raise ValueError("The installed application changed during review.")
        again = self.h.policy.decide(
            ctx.principal, spec, concrete, taint=ctx.taint, background=ctx.view.background
        )
        if (
            again.action == "deny"
            or again.effect != decision.effect
            or (
                again.action != "allow"
                and (
                    decision.action == "allow"
                    or again.effect != decision.effect
                    or again.level != decision.level
                )
            )
            or LEVEL_ORDER.index(again.level) > LEVEL_ORDER.index(decision.level)
        ):
            raise ValueError("Application permissions changed during review.")
        ctx.check()
        before = {
            row["choice_id"]
            for row in await self.adapter.targets()
            if row["application"].casefold() == app["application"].casefold()
        }
        self.ready(ctx)
        return session, app, before

    async def draw(self, ctx, session, args):
        """The drawing macro (V2-26 §5.4): the visual planner draws in the selected window; it plans its own
        strokes and asks before acting where policy requires. Fine refs are not used for canvases."""
        if not session or not session.get("target"):
            return ToolResult("blocked", "Select the canvas window with desktop_snapshot first.")
        if ctx.delegate is None:
            return ToolResult("blocked", "Drawing needs the desktop capability in this run.")
        subject = " ".join(str(args.get("subject", "")).split())[:300]
        label = (
            session["target"].get("label") or session["target"].get("application") or "the selected window"
        )
        session["nodes"] = {}  # The drawing changes the canvas: earlier refs are stale.
        outcome = await ctx.delegate("desktop", f"In {label}, draw {subject}.")
        state = outcome.get("outcome", "failed")
        result = (
            "ok"
            if state in {"verified_complete", "partial"}
            else ("blocked" if state == "blocked" else "failed")
        )
        return ToolResult(
            result,
            str(outcome.get("message", ""))[:600],
            data={"outcome": state, "subject": subject, "next": "Take a snapshot to see the result."},
            evidence=[{"kind": "drawing", "summary": f"Drew {subject}"}] if result == "ok" else [],
            dispatched=state != "blocked",
        )

    async def call(self, ctx, spec, args):
        async with self.lock:
            attempted = False
            session = None
            try:
                session = self.ready(ctx)
                if spec.name == "desktop_windows":
                    return ToolResult(data={"windows": await self.adapter.targets()})
                if spec.name == "desktop_draw":
                    return await self.draw(ctx, session, args)
                if spec.name == "desktop_applications":
                    return ToolResult(data={"applications": await self.adapter.applications()})
                if spec.name == "desktop_open_application":
                    session, app, known = await self.open_application(ctx, spec, args)
                    attempted = True
                    await self.adapter.pipe.call(
                        {"op": "launch", "task_id": ctx.run_id, "application_id": app["application_id"]}
                    )
                    for _ in range(40):
                        self.ready(ctx)
                        ctx.check()
                        try:
                            observed = await self.adapter.targets()
                        except (Fault, OSError):
                            # GUI providers can be temporarily unavailable during window creation.
                            # Repeat observation only; never dispatch a second launch.
                            await asyncio.sleep(0.25)
                            continue
                        candidates = [
                            row
                            for row in observed
                            if row["application"].casefold() == app["application"].casefold()
                            and row["choice_id"] not in known
                        ]
                        if len(candidates) == 1:
                            target, verified = await self.adapter.window_identity(
                                candidates[0]["choice_id"], ctx.run_id
                            )
                            if verified["application"].casefold() == app["application"].casefold() and all(
                                verified[key] == target[key] for key in ("hwnd", "pid", "created")
                            ):
                                return ToolResult(
                                    data={
                                        "application_id": app["application_id"],
                                        "application": app["application"],
                                        "window": candidates[0]["choice_id"],
                                        "title": verified["title"],
                                        "verified": True,
                                    },
                                    evidence=[{"kind": "assertion", "passed": True}],
                                    dispatched=True,
                                )
                            raise ValueError("The launched window identity changed during verification.")
                        if len(candidates) > 1:
                            raise ValueError("Several new windows appeared; the launch result is ambiguous.")
                        await asyncio.sleep(0.25)
                    raise ValueError("The launch was dispatched, but no new window could be verified.")
                if spec.name == "desktop_snapshot":
                    if args.get("window"):
                        target = await self.adapter.select(args["window"])
                        session.update(target=target, choice=args["window"], nodes={})
                    takeover().gate("desktop", session["choice"], observe=True)
                    _, _, text = await self.snapshot(ctx, session, args.get("depth", 20))
                    artifact = self.h.artifacts.put("snapshot", text.encode(), "text/yaml", run_id=ctx.run_id)
                    return ToolResult(
                        content=text,
                        data={
                            "snapshot": text,
                            "window": session["choice"],
                            "title": session["page"].get("title", ""),
                            "selection": sorted(
                                row["entity"]
                                for n, row in session["nodes"].values()
                                if n.role == "tab" and "selected" in n.states and "entity" in row
                            ),
                            "truncated": bool(session["page"].get("truncated")),
                            "focused_ref": next(
                                (
                                    n.ref
                                    for n, row in session["nodes"].values()
                                    if row.get("focused") and "password" not in n.states
                                ),
                                None,
                            ),
                        },
                        artifact_ids=[artifact],
                    )
                if spec.name == "desktop_activate":
                    target = await self.adapter.select(args["window"])
                    session.update(target=target, choice=args["window"], nodes={})
                    await self.snapshot(ctx, session)
                takeover().gate("desktop", session["choice"], observe=False)
                if spec.name in {"desktop_verify_text", "desktop_verify_element", "desktop_verify_value"}:
                    old = session["nodes"].get(args.get("ref"))
                    _, found, _ = await self.snapshot(ctx, session)
                    visible = [n for n, _ in found.values() if "password" not in n.states]
                    if spec.name == "desktop_verify_text":
                        passed = any(
                            args["text"] in n.name or args["text"] in (n.value or "") for n in visible
                        )
                    elif spec.name == "desktop_verify_element":
                        if args.get("ref"):
                            if old is None:
                                raise ValueError(
                                    "Stale desktop ref. Take a snapshot before verifying an element."
                                )
                            matching = [
                                node
                                for node, row in found.values()
                                if row["anchor"] == old[1]["anchor"] and "password" not in node.states
                            ]
                            passed = (
                                len(matching) == 1
                                and matching[0].role == args["role"]
                                and matching[0].name == args["name"]
                            )
                        else:
                            passed = any(n.role == args["role"] and n.name == args["name"] for n in visible)
                    else:
                        if old is None:
                            raise ValueError("Stale desktop ref. Take a snapshot before verifying a value.")
                        _, previous = old
                        matching = [
                            node
                            for node, row in found.values()
                            if row["anchor"] == previous["anchor"] and "password" not in node.states
                        ]
                        passed = len(matching) == 1 and matching[0].value == args["value"]
                    return ToolResult(
                        "ok" if passed else "failed",
                        data={"verified": passed},
                        evidence=[{"kind": "assertion", "passed": passed}],
                    )
                target = self.target(session, args, spec.name)
                identity = target[1]["identity"]
                drop_identity = None
                if spec.name == "desktop_drag":
                    drop = session["nodes"].get(args.get("to_ref"))
                    if drop is None or args.get("to_ref") == args.get("ref"):
                        raise ValueError("Stale or identical drop ref. Take desktop_snapshot again.")
                    if "password" in drop[0].states or "disabled" in drop[0].states:
                        raise ValueError("PANINI never drags onto credential or disabled controls.")
                    drop_identity = drop[1]["identity"]
                page, found, _ = await self.snapshot(ctx, session, publish=False)
                title = session["page"].get("title")
                matching = [(n, row) for n, row in found.values() if row["identity"] == identity]
                if (
                    len(matching) != 1
                    or (spec.name != "desktop_activate" and not page["foreground"])
                    or page.get("title") != title
                ):
                    raise ValueError("The desktop changed or lost focus. Take a new snapshot.")
                target = matching[0]
                reviewed = await self.authorize(ctx, spec, args, session, target)
                page, found, _ = await self.snapshot(ctx, session, publish=False)
                matching = [(n, row) for n, row in found.values() if row["identity"] == identity]
                if (
                    len(matching) != 1
                    or (spec.name != "desktop_activate" and not page["foreground"])
                    or page.get("title") != title
                ):
                    raise ValueError("The target changed or lost focus during review. Take a new snapshot.")
                target = matching[0]
                drop_node = None
                if drop_identity is not None:
                    drops = [n2 for n2, r2 in found.values() if r2["identity"] == drop_identity]
                    if len(drops) != 1:
                        raise ValueError("The drop target changed during review. Take a new snapshot.")
                    drop_node = drops[0]
                await self.authorize(ctx, spec, args, session, target, reviewed=reviewed)
                takeover().gate("desktop", session["choice"], observe=False)
                n, row = target
                patterns = set(row["patterns"])
                if spec.name == "desktop_activate":
                    action = {"kind": "activate"}
                elif spec.name == "desktop_focus":
                    action = {"kind": "focus"}
                elif spec.name == "desktop_click":
                    action = {
                        "kind": "double_click"
                        if args.get("double")
                        else "invoke"
                        if "InvokePatternIdentifiers.Pattern" in patterns
                        else "click"
                    }
                elif spec.name == "desktop_type":
                    action = {
                        "kind": "set_value" if "ValuePatternIdentifiers.Pattern" in patterns else "fine_type",
                        "text": args["text"],
                    }
                    if action["kind"] == "fine_type":
                        action["replace"] = args.get("replace", False)
                elif spec.name == "desktop_select":
                    if "SelectionItemPatternIdentifiers.Pattern" not in patterns:
                        raise ValueError("This control has no selection pattern.")
                    action = {"kind": "select"}
                elif spec.name == "desktop_toggle":
                    if "TogglePatternIdentifiers.Pattern" not in patterns:
                        raise ValueError("This control has no toggle pattern.")
                    action = {"kind": "toggle"}
                elif spec.name == "desktop_scroll":
                    if "ScrollPatternIdentifiers.Pattern" not in patterns:
                        raise ValueError("This control has no scroll pattern.")
                    action = {"kind": "scroll", "direction": args["direction"]}
                elif spec.name == "desktop_press":
                    action = {"kind": "keypress", "key": args["key"]}
                elif spec.name == "desktop_drag":
                    fx, fy, fw, fh = n.box
                    tx, ty, tw, th = drop_node.box
                    action = {"kind": "drag", "from_x": fx + fw // 2, "from_y": fy + fh // 2,
                              "to_x": tx + tw // 2, "to_y": ty + th // 2}  # fmt: skip
                else:
                    raise ValueError("Unknown fine desktop operation.")
                attempted = True
                receipt = await self.adapter.pipe.call(
                    {
                        "op": "fine_action",
                        "task_id": ctx.run_id,
                        "target": session["target"],
                        "snapshot_id": page["snapshot_id"],
                        "ref": n.ref,
                        "action": action,
                    }
                )
                submitted = None
                if spec.name == "desktop_type" and args.get("submit") and receipt.get("dispatched"):
                    # Enter goes to the same control only: re-find it by identity in a fresh snapshot.
                    page, found, _ = await self.snapshot(ctx, session, publish=False)
                    # The typed value changes the control's identity; its anchor (position in the tree) does not.
                    again = [
                        (n2, r2)
                        for n2, r2 in found.values()
                        if r2["anchor"] == row["anchor"] and "password" not in n2.states
                    ]
                    if len(again) != 1 or not page["foreground"] or page.get("title") != title:
                        raise ValueError(
                            "Typed, but the field changed before submitting. Take a new snapshot."
                        )
                    takeover().gate("desktop", session["choice"], observe=False)
                    submitted = await self.adapter.pipe.call(
                        {
                            "op": "fine_action",
                            "task_id": ctx.run_id,
                            "target": session["target"],
                            "snapshot_id": page["snapshot_id"],
                            "ref": again[0][0].ref,
                            "action": {"kind": "keypress", "key": "Enter"},
                        }
                    )
                return ToolResult(
                    data={
                        "dispatched": bool(receipt["dispatched"]),
                        "verified": bool(receipt["verified"]),
                        **({"submitted": bool(submitted["dispatched"])} if submitted is not None else {}),
                        "next": "Take a snapshot and assert the expected result.",
                    },
                    dispatched=True,
                )
            except asyncio.CancelledError:
                await self.stop(ctx)
                raise
            except (ValueError, RuntimeError, Fault, OSError) as exc:
                return ToolResult(
                    "unknown_effect" if attempted else "blocked",
                    getattr(exc, "message", str(exc)),
                    dispatched=attempted,
                    error_code="DESKTOP_FINE",
                )
            finally:
                if session is not None and spec.effect != EffectClass.OBSERVE:
                    session["nodes"] = {}


class DesktopFineHand:
    def __init__(self, manifest, runtime):
        self.runtime = runtime

    @property
    def service(self):
        return self.runtime["harness"].desktop_fine

    async def readiness(self, capability=None):
        return Readiness(
            "ready" if v2flags.value("PANINI_DESKTOP_TOOLS") == "fine" else "needs_setup",
            "Fine desktop tools are opt-in.",
        )

    async def call(self, ctx, spec, args):
        return await self.service.call(ctx, spec, args)

    async def run_finished(self, ctx):
        if ctx.run_id in self.service.sessions:
            await self.service.stop(ctx)
