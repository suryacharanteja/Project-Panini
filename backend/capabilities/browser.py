"""Delegate to the working browser engines; risky actions wait for the person's approval."""

import asyncio
import json
import os
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

from .. import config
from ..panini.contracts import Fault
from ..routes.browser import get_bridge, get_extension_loop, get_loop
from .approvals import browser_action_needs_approval, managed_browser_needs_approval
from .contracts import Outcome, UnavailableAdapter

# Only an explicit request moves work out of the user's own Chrome.
MANAGED_REQUEST = re.compile(
    r"\b(separate|managed|new|clean|isolated|fresh|private|panini)\s+(chrome\s+|chromium\s+)?browser\b"
    r"|\bchromium\b",
    re.IGNORECASE,
)


def web_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise Fault("UNSUPPORTED_URL", "Only HTTP and HTTPS website addresses are supported.")


def _playwright_driver() -> bool:
    from .. import v2flags

    return v2flags.value("PANINI_BROWSER_DRIVER") == "playwright"


class BrowserAdapter(UnavailableAdapter):
    def __init__(self, app):
        super().__init__("browser", "Configure OpenAI and AI Gateway credentials for Browser.")
        self.app, self.request = app, SimpleNamespace(app=app)
        self.active, self.choices = {}, {}
        self.fallback_reason = ""

    async def availability(self):
        ready = all(config.configured(key) for key in ("OPENAI_API_KEY", "AI_GATEWAY_API_KEY"))
        return {
            "capability": self.name,
            "state": "ready" if ready else "unavailable",
            "reason": "" if ready else self.reason,
        }

    async def targets(self):
        self.choices = {
            "managed-chromium": {"mode": "chromium", "label": "PANINI managed browser", "target_id": ""}
        }
        bridge = get_bridge(self.request)
        if bridge.connected and bridge.compatible and bridge.attached_tab is not None:
            self.choices[bridge.target_id] = {
                "mode": "extension",
                "target_id": bridge.target_id,
                "label": bridge.tab_title or "Attached Chrome tab",
            }
        return [{"choice_id": key, "label": value["label"]} for key, value in self.choices.items()]

    async def _managed(self, context, instruction):
        """V2-08: the separate browser is PANINI's own Playwright-driven profile; a focused agent works it
        with the managed_* tools, so every step is policed and receipted like any other tool call."""
        from .. import v2flags
        from ..hands.browser import TOOLS

        harness = getattr(self.app.state, "harness", None)
        if harness is None or not v2flags.enabled("PANINI_V2_REGISTRY"):
            raise Fault(
                "CAPABILITY_UNAVAILABLE",
                "PANINI's Playwright browser needs the capability registry (PANINI_V2_REGISTRY).",
                503,
            )
        if self.fallback_reason:
            context.emit(
                "running",
                "Your Chrome isn't available, so PANINI is using its own browser. " + self.fallback_reason,
            )
            self.fallback_reason = ""

        async def confirm(summary, detail):
            try:
                await context.confirm(
                    {"summary": summary, "detail": detail}, "PANINI browser", summary, detail
                )
            except Fault:
                return False
            return True

        agent = harness.agent(
            delegate=None,
            emit=lambda kind, message, **detail: context.emit("running", message),
            capabilities=(),
            confirm=confirm,
            deadline_seconds=280,
            max_steps=16,
        )
        from ..hands.browser_tools import TOOLS as V2_TOOLS

        agent.only_tools = {n for n, t in V2_TOOLS.items() if t.offered and t.group == "core"} | {
            "browser_snapshot",
            "browser_media",
            "update_plan",
            "finish",
        }
        agent.only_tools |= set(TOOLS)  # The V2-08 managed_* names, when that manifest is installed.
        agent.browser_preference = "managed"
        agent.run_id = context.task.task_id
        result = await agent.run(
            "Use PANINI's own browser (the managed_* tools) for this; it is separate from the person's "
            f"Chrome.\n{instruction}"
        )
        evidence = []
        verified = result.outcome == "verified_complete" or (result.outcome == "reply" and agent.evidence)
        if verified:
            evidence.append(
                context.record_evidence(
                    "browser_managed",
                    "PANINI's browser completed the step with verified page state.",
                    {"steps": result.steps, "checks": [e for e in agent.evidence if ":" in str(e)][-10:]},
                )
            )
        outcome = (
            "verified_complete"
            if verified
            else {"reply": "partial", "clarification": "blocked"}.get(result.outcome, result.outcome)
        )
        return Outcome(outcome, result.message, [], evidence)

    async def open_in_chrome(self, url, selected=None):
        """Open a web page in the person's own Chrome through JevRev Claw: a new workspace tab
        when the workspace allows it, otherwise the attached tab. Returns None when Chrome is
        not connected, or when the person chose the separate browser."""
        web_url(url)
        if selected and selected.get("mode") == "chromium":
            return None
        bridge = get_bridge(self.request)
        if not (bridge.connected and bridge.compatible and bridge.attached_tab is not None):
            return None
        with bridge.bind_target(bridge.target_id):
            if bridge.workspace and bridge.workspace.get("allow_new_tabs"):
                await bridge.open_tab(url)
            else:
                await bridge.navigate(url)
        return {"opened": url, "where": "your Chrome (JevRev Claw)"}

    async def direct(self, action, url="", element="", text="", submit=False, confirm=None):
        """Instant actions in the person's Chrome without the multi-step planner: read the
        page, scroll, navigate, click or type. Each returns what is now visible, so the agent
        can walk through a site at conversational speed. Risky clicks still ask first."""
        from ..extension_planner import action_line

        bridge = get_bridge(self.request)
        if not (bridge.connected and bridge.compatible and bridge.attached_tab is not None):
            raise Fault(
                "BROWSER_NOT_CONNECTED",
                "Chrome is not connected. Attach a tab in JevRev Claw, or use use_capability browser.",
            )

        async def read(settle=0.0):
            if settle:
                await asyncio.sleep(settle)
            page = await bridge.snapshot()
            try:
                full = await bridge.text()
            except Exception:  # noqa: BLE001 -- The visible text still answers most questions.
                full = {}
            return {
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "visible_text": " ".join(str(page.get("text", "")).split())[:3000],
                "more_text_below": " ".join(str(full.get("text", "")).split())[:3000],
                "controls": [action_line(a) for a in page.get("actions", [])[:80]],
                # The page's real media state (playing, ad showing, skippable): report only this.
                "media": page.get("media"),
            }, page

        with bridge.bind_target(bridge.target_id):
            if action == "read":
                return (await read())[0]
            if action == "navigate":
                web_url(url)
                await bridge.navigate(url)
                return (await read(0.8))[0]
            _, page = await read()
            marker = page.get("marker", "")
            if action in {"media_pause", "media_play", "skip_ad"}:
                result = await bridge.act(
                    "media_skip_ad" if action == "skip_ad" else action, "CLICK", marker=marker
                )
                if isinstance(result, dict) and result.get("error"):
                    # Not done: say so, with what the page actually shows.
                    raise Fault("NOT_DONE", f"{result['error']} Media now: {result.get('media')}")
                return (await read(0.3))[0]
            if action in {"scroll_down", "scroll_up"}:
                await bridge.act(action, "CLICK", marker=marker)
                return (await read(0.4))[0]
            target = next((a for a in page.get("actions", []) if a.get("id") == element), None)
            if target is None:
                raise Fault("ELEMENT_CHANGED", "That control is not on the page now. Read the page again.")
            probe = {**target, "text": text}
            if browser_action_needs_approval(probe) and (confirm is None or not await confirm(probe)):
                raise Fault("CONFIRMATION_REJECTED", "The person did not approve that action.")
            if action == "type":
                await bridge.act(element, "TYPE_TEXT", marker=marker, text=text)
                if submit:
                    _, page = await read()
                    await bridge.act(element, "PRESS_ENTER", marker=page.get("marker", ""))
                return (await read(0.8 if submit else 0.2))[0]
            if action == "click":
                await bridge.act(element, "CLICK", marker=marker)
                return (await read(0.8))[0]
        raise Fault("UNSUPPORTED_ACTION", f"Unknown browser action {action}.")

    async def select(self, choice_id):
        await self.targets()
        if choice_id not in self.choices:
            raise Fault("TARGET_CHANGED", "The attached browser target changed. Select it again.")
        return dict(self.choices[choice_id])

    async def choose(self, query, selected=None):
        """Default to the user's own Chrome; a managed browser needs an explicit request."""
        await self.targets()
        self.fallback_reason = ""
        if query and MANAGED_REQUEST.search(query):
            return "managed-chromium"
        if not query and selected and selected.get("mode") == "chromium":
            return "managed-chromium"  # The user already chose it in this conversation.
        extension = next((k for k, v in self.choices.items() if v["mode"] == "extension"), None)
        if extension:
            return extension
        bridge = get_bridge(self.request)
        if not self.app.state.pairing.devices():
            reason = "Pair JevRev Claw with PANINI first (Pair browser extension), then attach a tab."
        elif not bridge.connected:
            reason = "Open Chrome with JevRev Claw so PANINI can use your browser."
        elif not bridge.compatible:
            reason = "Update JevRev Claw to 3.0 or later in chrome://extensions, then reconnect it."
        else:
            reason = "In JevRev Claw, attach the tab PANINI should use."
        # Only when Chrome is not connected at all: a connected Chrome is never bypassed.
        if not bridge.connected and os.getenv("PANINI_BROWSER_FALLBACK", "separate").strip().lower() != "ask":
            # Act instead of asking: use the separate browser and say why once.
            self.fallback_reason = reason
            return "managed-chromium"
        raise Fault(
            "BROWSER_NOT_CONNECTED",
            reason + " Or ask PANINI to use a separate browser.",
        )

    async def run(self, context, instruction, target):
        from ..extension_loop import describe
        from ..voice_service import VoiceBrowser

        if target["mode"] != "extension" and _playwright_driver():
            return await self._managed(context, instruction)

        if target["mode"] != "extension" and self.fallback_reason:
            context.emit(
                "running",
                "Your Chrome isn't available, so PANINI is using a separate browser. " + self.fallback_reason,
            )
        self.fallback_reason = ""
        browser = VoiceBrowser(self.app, {key: target[key] for key in ("mode", "target_id")}, owner="PANINI")
        loop = get_extension_loop(self.request) if target["mode"] == "extension" else get_loop(self.request)
        self.active[context.task.task_id] = loop
        previous_tools = None
        if target["mode"] == "extension":

            async def admit(action, marker):
                context.check()
                if action.get("url"):
                    web_url(action["url"])
                if browser_action_needs_approval(action):
                    await context.confirm(
                        {"action": action, "marker": marker, "target": target["target_id"]},
                        target["label"],
                        describe(action),
                        str(action.get("text", "")),
                    )
                context.check()
                # Extension act verifies its original marker and tab grant at dispatch.
                context.emit("running", describe(action))
                context.dispatch()

            loop.admission = admit
        else:
            from browser_use.tools.service import Tools

            class GuardedTools(Tools):
                async def act(self, action, browser_session, **kwargs):
                    context.check()
                    selected = [
                        (k, v) for k, v in action.model_dump(exclude_unset=True).items() if v is not None
                    ]
                    if len(selected) != 1:
                        raise Fault("INVALID_ACTION", "Only one browser action can be admitted at a time.")
                    name, params = selected[0]
                    allowed = {
                        "done",
                        "search",
                        "navigate",
                        "go_back",
                        "wait",
                        "click",
                        "input",
                        "switch",
                        "extract",
                        "search_page",
                        "find_elements",
                        "scroll",
                        "send_keys",
                        "find_text",
                        "screenshot",
                        "dropdown_options",
                        "select_dropdown",
                        "read_long_content",
                    }
                    if name not in allowed:
                        raise Fault("UNSUPPORTED_ACTION", "This browser action requires user takeover.")
                    if name == "send_keys" and str(params.get("keys", "")).upper() not in {
                        "ENTER",
                        "ESCAPE",
                        "TAB",
                        "ARROWDOWN",
                        "ARROWUP",
                    }:
                        raise Fault("UNSUPPORTED_ACTION", "This key combination requires user takeover.")
                    if params.get("url"):
                        web_url(params["url"])
                    index = params.get("index")
                    node = (
                        await browser_session.get_dom_element_by_index(index) if index is not None else None
                    )
                    if index is not None and node is None:
                        raise Fault("ELEMENT_CHANGED", "The browser control is no longer available.")
                    if name == "click" and index is None:
                        raise Fault(
                            "UNSUPPORTED_ACTION", "Coordinate clicks are not enabled in this profile."
                        )
                    if node and node.attributes.get("type", "").lower() == "password":
                        raise Fault("PASSWORD_FIELD", "Enter passwords directly in the website.")
                    state = await browser_session.get_browser_state_summary()
                    identity = (node.backend_node_id, dict(node.attributes)) if node else None
                    label = node.get_meaningful_text_for_llm()[:300] if node else ""
                    if name not in {
                        "done",
                        "wait",
                        "extract",
                        "search_page",
                        "find_elements",
                        "scroll",
                        "find_text",
                        "screenshot",
                        "dropdown_options",
                        "read_long_content",
                    } and managed_browser_needs_approval(name, params, label):
                        detail = {"action": name, "parameters": params, "element": identity, "url": state.url}
                        await context.confirm(
                            detail,
                            state.title or target["label"],
                            f"{name}: {label or json.dumps(params, ensure_ascii=False)[:500]}\nWebsite: {state.url[:300]}",
                            str(params.get("text", "")),
                        )
                        fresh = await browser_session.get_browser_state_summary()
                        current = (
                            await browser_session.get_dom_element_by_index(index)
                            if index is not None
                            else None
                        )
                        if fresh.url != state.url or (
                            identity is not None
                            and (
                                current is None
                                or (current.backend_node_id, dict(current.attributes)) != identity
                            )
                        ):
                            raise Fault(
                                "ELEMENT_CHANGED", "The page changed while the action awaited confirmation."
                            )
                    context.check()
                    context.emit("running", f"Browser action: {name}.")
                    context.dispatch()
                    return await super().act(action, browser_session, **kwargs)

            previous_tools, loop.tools = loop.tools, GuardedTools()
        terminal = {}
        try:
            async for event in browser.run(instruction):
                context.check()
                emit_activity = getattr(context, "computer_activity", None)
                if emit_activity:
                    emit_activity(
                        {
                            "surface": "browser",
                            "phase": event.get("event", "update"),
                            "message": str(event.get("message") or event.get("error") or "")[:500],
                            "action": event.get("action", ""),
                            "url": event.get("url", ""),
                            "step": event.get("step"),
                            "frame_b64": event.get("screenshot_b64", ""),
                        }
                    )
                if event.get("event") in {"complete", "incomplete", "error", "timeout"}:
                    terminal = event
                elif event.get("message"):
                    context.emit("running", str(event["message"])[:500])
            if terminal.get("event") != "complete":
                if terminal.get("event") == "error":
                    raise Fault(
                        "BROWSER_EXECUTOR_FAILED",
                        "Browser executor failed: "
                        + str(loop.last_error or "unknown error").split(":", 1)[0][:100]
                        + ". Check the browser target and retry.",
                    )
                return Outcome(
                    "partial",
                    terminal.get("message") or "Browser stopped before the requested result was verified.",
                )
            page = await browser.observation()
            context.check()
            if target["mode"] == "extension":
                verified = bool(page.get("completion_verified"))
            else:
                from ..extension_planner import completion_evidence, plan_task, propose

                plan = await asyncio.to_thread(plan_task, instruction)
                proposal = await asyncio.to_thread(propose, instruction, page, [], plan, {})
                verified = completion_evidence(plan, proposal, page, {})
            if not verified:
                return Outcome(
                    "partial",
                    "Browser actions finished, but the requested result could not be verified against the page.",
                )
            return Outcome(
                "verified_complete",
                terminal.get("message", "Verified the browser result."),
                [
                    {
                        "title": page.get("title", target["label"]),
                        "url": page.get("url", ""),
                        "detail": terminal.get("message", "")[:1500],
                    }
                ],
                [
                    context.record_evidence(
                        "browser_postcondition",
                        "Verified the browser result against the observed page.",
                        {
                            "url": page.get("url", ""),
                            "title": page.get("title", ""),
                            "text": page.get("text", "")[:3000],
                            "completion": terminal.get("message", "")[:1500],
                        },
                    )
                ],
            )
        finally:
            loop.admission = None
            if previous_tools:
                loop.tools = previous_tools

    async def stop(self, task_id):
        loop = self.active.get(task_id)
        if loop:
            loop.cancel()
            if loop._runner:
                await asyncio.wait_for(asyncio.gather(loop._runner, return_exceptions=True), 3)

    async def release(self, task_id):
        self.active.pop(task_id, None)

    async def close(self):
        for loop in (self.app.state.browser_loop, self.app.state.extension_loop):
            if loop:
                await loop.shutdown()
