"""The browser hand for vocabulary v2 (V2-10): Playwright's own tool names on whichever browser the run uses.

Each run has an active browser: your Chrome when JevRev Claw has full control of an attached tab, otherwise
PANINI's managed profile; `browser_select` switches (also to an isolated, in-memory browser for untrusted
links). Before a call reaches Playwright the hand applies the per-browser availability table, the stale-ref
guard, the element-name check, the password probe, upload and dialog rules; after it, it inlines the page
state, refreshes the ref index, records evidence (an observation for actions, an assertion for verify tools),
truncates long snapshots into an artifact and strips sensitive headers from network output.
"""

import json
import os
import re
import time
from pathlib import Path

from ..core_contracts import Readiness, ToolResult
from .browser import (
    OUTPUT_CHARS,
    PASSWORD,
    ChromeBrowserHand,
    ManagedBrowserHand,
    _words,
    element_names,
    inline_snapshot,
)
from .browser_tools import (
    CREDENTIAL_AUTOCOMPLETE,
    PANINI_TOOLS,
    PRIVILEGED_PROBES,
    SENSITIVE_HEADERS,
    TOOLS,
)
from .playwright_mcp import BrowserDriverError, SessionCrashed, isolated_preset, text_of

REF = re.compile(r"^(f\d+)?e\d+$")
PAGE_URL = re.compile(r"Page URL: (\S+)")
MODAL = re.compile(r"### Modal state\s*\n(.+?)(?:\n###|\Z)", re.S)
HEADER_LINE = re.compile(r"^(\s*-?\s*)(" + "|".join(SENSITIVE_HEADERS) + r")(\s*:\s*).+$", re.I | re.M)
DESTROY_WORDS = re.compile(
    r"\b(delete|remove|erase|discard|permanently|cancel (my|your) (account|order|subscription))\b", re.I
)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class BrowserHand:
    lenient_required = False

    def __init__(self, manifest, runtime):
        self.manifest, self.runtime = manifest, runtime
        self.managed = ManagedBrowserHand(manifest, runtime)
        self.chrome = ChromeBrowserHand(manifest, runtime)
        self.active: dict[str, str] = {}  # run → browser
        self.names: dict[str, dict] = {}  # browser key → {ref: "role \"name\""} from the latest page state
        self.page_url: dict[str, str] = {}
        self.modal: dict[str, str] = {}
        self.tracing: dict[str, str] = {}  # run → browser with a trace running
        from ..routes import live as live_routes

        live_routes.input_handlers["managed"] = self.owner_input  # V2-12: remote input to PANINI's browser.

    @staticmethod
    def live_target(browser) -> tuple[str, str] | None:
        """The live-view hand and target for a browser (the isolated per-run browser has none)."""
        return {"chrome": ("chrome", "attached"), "managed": ("managed", "default")}.get(browser)

    async def owner_input(self, target: str, action: str, args: dict) -> dict:
        """The owner's click, typing, keys and wheel in PANINI's browser while they hold it (V2-12 §5.4).
        Coordinate tools are offered to the owner only, never to the agent."""
        session = self.managed.session()
        if action == "click":
            x, y = int(args.get("x", -1)), int(args.get("y", -1))
            if not (0 <= x <= 16000 and 0 <= y <= 16000):
                raise ValueError("x and y must be page coordinates.")
            call = ("browser_mouse_click_xy", {"x": x, "y": y, "element": "owner"})
        elif action == "type":
            text = str(args.get("text", ""))[:2000]
            for char in text:
                await session.call("browser_press_key", {"key": char})
            return {"typed": len(text)}
        elif action == "key":
            call = ("browser_press_key", {"key": str(args.get("key", ""))[:100]})
        else:
            delta = max(-10000, min(10000, int(args.get("deltaY", 0))))
            call = ("browser_mouse_wheel", {"deltaX": 0, "deltaY": delta})
        result = await session.call(*call)
        return {"ok": not (result or {}).get("isError")}

    # Browsers ------------------------------------------------------------------
    @staticmethod
    def run_key(ctx) -> str:
        return str(getattr(ctx, "run_id", None) or "default")

    def key(self, browser, ctx) -> str:
        return f"isolated:{self.run_key(ctx)}" if browser == "isolated" else browser

    async def ready(self) -> dict:
        chrome = await self.chrome.readiness()
        managed = await self.managed.readiness()
        return {"chrome": chrome, "managed": managed, "isolated": managed}

    async def choose(self, ctx) -> str | None:
        run = self.run_key(ctx)
        if run in self.active:
            return self.active[run]
        preference = getattr(getattr(ctx, "agent", None), "browser_preference", None)
        ready = await self.ready()
        if preference in ready and ready[preference].state == "ready":
            browser = preference
        elif ready["chrome"].state == "ready":
            browser = "chrome"
        elif ready["managed"].state == "ready":
            browser = "managed"
        else:
            return None
        self.active[run] = browser
        return browser

    async def session(self, browser, ctx):
        if browser == "chrome":
            return await self.chrome.ensure_session()
        if browser == "managed":
            return self.managed.session()
        from ..extension_pairing import data_directory

        return self.managed.pool.session(isolated_preset(self.run_key(ctx), data_directory() / "artifacts"))

    async def readiness(self, capability=None):
        ready = await self.ready()
        if ready["chrome"].state == "ready" or ready["managed"].state == "ready":
            return Readiness("ready")
        return Readiness("unavailable", ready["managed"].reason or ready["chrome"].reason)

    # Calls ------------------------------------------------------------------------
    async def call(self, ctx, spec, args):
        name = spec.name
        if name == "browser_status":
            return await self._status(ctx)
        if name == "browser_select":
            return await self._select(ctx, args)
        browser = await self.choose(ctx)
        if browser is None:
            return ToolResult(
                "blocked",
                "No browser is available: attach a tab in JevRev Claw (v4) or set PANINI_BROWSER_DRIVER=playwright.",
            )
        if name == "browser_media":
            return await self._media(ctx, browser, args)
        tool = TOOLS.get(name)
        if tool is None:
            return ToolResult("blocked", f"{name} is not available.")
        availability = tool.availability(browser)
        if availability == "never":
            where = {
                "chrome": "your Chrome",
                "managed": "PANINI's browser",
                "isolated": "the isolated browser",
            }[browser]
            return ToolResult("blocked", f"{name} is not allowed in {where}.")
        key = self.key(browser, ctx)
        target = self.live_target(browser)
        if target is not None:
            from .. import live

            try:
                live.takeover().gate(*target, observe=tool.effect == "observe")
            except live.ControlBlocked as exc:
                return ToolResult("blocked", str(exc))
        refusal = self._untrusted(ctx, browser, name)
        if refusal:
            return ToolResult("blocked", refusal)
        try:
            await self._check(ctx, browser, key, tool, args)
        except ValueError as exc:
            return ToolResult("blocked", str(exc))
        if availability == "tap" and not getattr(ctx, "preapproved", False):
            if not await self._approve(ctx, browser, key, tool, args):
                return ToolResult("blocked", "The person did not approve this browser step.")
        started = time.monotonic()
        try:
            session = await self.session(browser, ctx)
            await self._trace(ctx, browser, session)
            if tool.typing:
                await self._probe_typing(session, key, tool, args)
            # Never a model-chosen file path: outputs stay in PANINI's artifacts.
            payload = {k: v for k, v in args.items() if k != "filename"}
            result = await session.call(name, payload, timeout_s=spec.timeout_ms / 1000)
        except ValueError as exc:
            return ToolResult("blocked", str(exc))
        except SessionCrashed as exc:
            return ToolResult("unknown_effect", f"{exc} Check the page before trying again.", dispatched=True)
        except BrowserDriverError as exc:
            if browser == "chrome":
                return self.chrome.driver_failure(exc)
            return ToolResult("failed", str(exc)[:500])
        return self._result(ctx, browser, key, tool, args, result, started)

    def _result(self, ctx, browser, key, tool, args, result, started):
        text = inline_snapshot(text_of(result), self.managed.output_root())
        if tool.group == "browser_devtools":
            text = HEADER_LINE.sub(r"\1\2\3[redacted]", text)
        if (result or {}).get("isError"):
            return ToolResult("failed", text[:2000] or "The browser action failed.")
        names = element_names(text)
        if names:
            self.names[key] = names  # A new page state: refs from older views are stale now.
        url = PAGE_URL.search(text)
        if url:
            self.page_url[key] = url.group(1)
        modal = MODAL.search(text)
        self.modal[key] = modal.group(1).strip() if modal else ""
        evidence, artifact_ids = [], []
        if tool.evidence == "assertion":
            evidence.append(
                {
                    "kind": "assertion",
                    "summary": f"{tool.title}: {json.dumps(args, ensure_ascii=False)[:200]}",
                }
            )
        elif tool.evidence == "page" or "### Page" in text:
            evidence.append(
                {"kind": "page", "summary": f"Page after {tool.name}: {self.page_url.get(key, '')}"[:300]}
            )
        content = text
        if len(text) > OUTPUT_CHARS:
            artifact = self._artifact("snapshot", text.encode(), "text/yaml")
            if artifact:
                artifact_ids.append(artifact)
            content = text[:OUTPUT_CHARS] + "\n… Snapshot truncated. Use browser_find to search the page."
        data = {
            "browser": browser,
            "page": content,
            "settled_ms": int((time.monotonic() - started) * 1000),
        }
        return ToolResult("ok", content, data=data, evidence=evidence, artifact_ids=artifact_ids)

    @staticmethod
    def _untrusted(ctx, browser, name):
        """After reading an email, a channel message, another agent's or an MCP result, links open in the
        sandbox computer when PANINI_UNTRUSTED_BROWSING=sandbox; the person can still ask for their Chrome."""
        from .. import v2flags

        if name not in ("browser_navigate", "browser_tabs") or browser == "isolated":
            return None
        if os.getenv("PANINI_UNTRUSTED_BROWSING", "isolated").strip().lower() != "sandbox":
            return None
        untrusted = set(getattr(ctx, "taint", ()) or ()) & {
            "email",
            "channel",
            "call",
            "mcp",
            "a2a",
            "shared",
        }
        if not untrusted or not v2flags.enabled("PANINI_SANDBOX"):
            return None
        return (
            "This link came from untrusted content, so it opens in PANINI's sandbox computer: use "
            "sandbox_navigate. Ask the person first if it must open in their own browser."
        )

    # Checks -----------------------------------------------------------------------
    async def _check(self, ctx, browser, key, tool, args):
        from ..capabilities.browser import web_url
        from ..panini.contracts import Fault

        name = tool.name
        if name == "browser_navigate" or (
            name == "browser_tabs" and args.get("action") == "new" and args.get("url")
        ):
            try:
                web_url(str(args.get("url", "")))
            except Fault as exc:
                raise ValueError(exc.message) from None
            if browser == "isolated":
                from .net_guard import BlockedUrl, check_url

                try:
                    check_url(str(args["url"]))
                except BlockedUrl as exc:
                    raise ValueError(str(exc)) from None
        if name == "browser_tabs" and browser == "chrome" and args.get("action") in ("new", "close"):
            raise ValueError(
                "PANINI works in the tab you attached; it does not open or close your Chrome tabs."
            )
        refs = []
        if tool.ref:
            refs.append((str(args.get("target", "")), str(args.get("element", ""))))
        if name == "browser_fill_form":
            refs += [
                (str(f.get("target", "")), str(f.get("element", "") or f.get("name", "")))
                for f in args.get("fields") or []
            ]
        for ref, element in refs:
            self._check_ref(key, ref, element, tool.effect != "observe")
        if name == "browser_file_upload":
            self._check_upload(args)
        if (
            name == "browser_handle_dialog"
            and args.get("accept")
            and DESTROY_WORDS.search(self.modal.get(key, ""))
        ):
            if not getattr(ctx, "preapproved", False):
                approved = ctx.confirm is not None and await ctx.confirm(
                    "Accept this dialog?", f"The page asks: {self.modal.get(key, '')[:500]}"
                )
                if not approved:
                    raise ValueError("The person did not approve answering this dialog.")

    def _check_ref(self, key, ref, element, acting):
        if not REF.match(ref):
            if acting:
                raise ValueError(
                    "Act on an element ref from the latest snapshot (for example e12), not a selector."
                )
            return
        names = self.names.get(key)
        if names is None:
            raise ValueError("Read the page with browser_snapshot first, then act on its element refs.")
        actual = names.get(ref)
        if actual is None:
            raise ValueError(
                "That element reference is from an older view. Take browser_snapshot again before acting."
            )
        if acting:
            if not element.strip():
                raise ValueError("Describe the element as the snapshot names it.")
            meaningful = _words(actual) - {"button", "link", "textbox", "checkbox", "combobox", "generic"}
            if meaningful and not (_words(actual) & _words(element)):
                raise ValueError(
                    f"Ref {ref} is {actual}, not '{element}'. Describe the element as it appears in the snapshot."
                )

    async def _probe_typing(self, session, key, tool, args):
        """I5: never type into a password, one-time-code or card field, whatever the snapshot calls it."""
        targets = []
        if tool.name == "browser_type":
            targets.append((str(args.get("target", "")), str(args.get("element", ""))))
        elif tool.name == "browser_fill_form":
            targets += [
                (str(f.get("target", "")), str(f.get("element", "") or f.get("name", "")))
                for f in args.get("fields") or []
                if f.get("type", "textbox") == "textbox"
            ]
        for ref, element in targets:
            label = self.names.get(key, {}).get(ref, "")
            if PASSWORD.search(label) or PASSWORD.search(element):
                raise ValueError(
                    "PANINI never types passwords or security codes. Ask the person to enter it."
                )
            probe = await session.call(
                "browser_evaluate",
                {"function": PRIVILEGED_PROBES["field_info"], "target": ref, "element": element or "field"},
                timeout_s=15,
            )
            info = _json_in(text_of(probe))
            autocomplete = str(info.get("autocomplete", ""))
            if (
                info.get("type") == "password"
                or any(autocomplete.startswith(a) or f" {a}" in autocomplete for a in CREDENTIAL_AUTOCOMPLETE)
                or PASSWORD.search(f"{info.get('name', '')} {info.get('id', '')}")
            ):
                raise ValueError(
                    "PANINI never types passwords or security codes. Ask the person to enter it."
                )

    def _check_upload(self, args):
        from ..harness.computer import SECRET

        paths = [Path(str(p)) for p in args.get("paths") or []]
        if not paths:
            return
        total = 0
        for path in paths:
            if not path.is_absolute() or not path.is_file():
                raise ValueError(f"{path} is not a file on this PC.")
            if SECRET.search(str(path)):
                raise ValueError(f"{path.name} looks like a secret or key file; PANINI does not upload it.")
            total += path.stat().st_size
        if total > MAX_UPLOAD_BYTES:
            raise ValueError("Those files are larger than 100 MB together.")

    async def _approve(self, ctx, browser, key, tool, args) -> bool:
        if ctx.confirm is None:
            return False
        detail = await self.preview(ctx, tool, args, browser=browser, key=key)
        return bool(await ctx.confirm(f"{tool.title} in {self._where(browser)}", detail))

    async def preview(self, ctx, spec_or_tool, args, *, browser=None, key=None) -> str:
        """What the person sees before approving: the page, the element, files with sizes, and a highlighted
        screenshot stored as an artifact (V2-10 §5.7)."""
        browser = browser or await self.choose(ctx) or "managed"
        key = key or self.key(browser, ctx)
        lines = [f"Page: {self.page_url.get(key, 'unknown')}"]
        ref = str(args.get("target", ""))
        if ref:
            lines.append(f"Element: {self.names.get(key, {}).get(ref, ref)}")
        for path in args.get("paths") or []:
            try:
                lines.append(f"File: {Path(path).name} ({Path(path).stat().st_size:,} bytes)")
            except OSError:
                lines.append(f"File: {path}")
        if args.get("fields"):
            lines += [
                f"Field {f.get('name') or f.get('element')}: {str(f.get('value', ''))[:200]}"
                for f in args["fields"]
            ]
        if ref and REF.match(ref):
            artifact = await self._highlight(ctx, browser, ref, args.get("element", ""))
            if artifact:
                lines.append(f"Screenshot: artifact {artifact}")
        return "\n".join(lines)[:3000]

    async def _highlight(self, ctx, browser, ref, element):
        try:
            session = await self.session(browser, ctx)
            await session.call(
                "browser_highlight", {"target": ref, "element": element or "element"}, timeout_s=10
            )
            shot = await session.call(
                "browser_take_screenshot", {"scale": "css", "type": "jpeg"}, timeout_s=15
            )
            await session.call("browser_hide_highlight", {}, timeout_s=10)
        except BrowserDriverError:
            return None
        match = re.search(r"\]\(([^)]+\.(?:jpe?g|png))\)", text_of(shot))
        if not match:
            return None
        path = Path(match.group(1))
        try:
            if path.resolve().is_relative_to(self.managed.output_root().resolve()):
                return self._artifact("screenshot", path.read_bytes(), "image/jpeg")
        except OSError:
            return None
        return None

    # PANINI tools -------------------------------------------------------------------
    async def _status(self, ctx):
        ready = await self.ready()
        active = self.active.get(self.run_key(ctx))
        data = {
            "active": active,
            "browsers": {k: {"state": v.state, "reason": v.reason} for k, v in ready.items()},
        }
        return ToolResult("ok", json.dumps(data), data=data)

    async def _select(self, ctx, args):
        browser = str(args.get("browser", ""))
        if browser not in ("chrome", "managed", "isolated"):
            return ToolResult("blocked", "Choose chrome, managed or isolated.")
        ready = await self.ready()
        if ready[browser].state != "ready":
            return ToolResult("blocked", ready[browser].reason or f"{browser} is not ready.")
        self.active[self.run_key(ctx)] = browser
        return ToolResult("ok", f"Now using {self._where(browser)}.", data={"active": browser})

    async def _media(self, ctx, browser, args):
        action = str(args.get("action", "state"))
        if action not in ("state", "play", "pause", "skip_ad"):
            return ToolResult("blocked", "action must be state, play, pause or skip_ad.")
        try:
            session = await self.session(browser, ctx)
            if action != "state":
                await session.call(
                    "browser_evaluate", {"function": PRIVILEGED_PROBES[f"media_{action}"]}, timeout_s=15
                )
            probe = await session.call(
                "browser_evaluate", {"function": PRIVILEGED_PROBES["media_state"]}, timeout_s=15
            )
        except BrowserDriverError as exc:
            return ToolResult("failed", str(exc)[:500])
        state = _json_in(text_of(probe))
        evidence = [{"kind": "assertion", "summary": f"Media state: {json.dumps(state)}"}] if state else []
        return ToolResult("ok", json.dumps(state), data=state, evidence=evidence)

    # Traces ---------------------------------------------------------------------------
    async def _trace(self, ctx, browser, session):
        if browser == "chrome" and os.getenv("PANINI_CHROME_TRACING", "off").lower() != "on":
            return  # Traces hold DOM snapshots of your pages: off in your Chrome unless asked for.
        if os.getenv("PANINI_BROWSER_TRACING", "on").lower() == "off":
            return
        run = self.run_key(ctx)
        if run in self.tracing or run == "default":
            return
        try:
            await session.call("browser_start_tracing", {}, timeout_s=15)
            self.tracing[run] = browser
        except BrowserDriverError:
            pass

    async def run_finished(self, ctx):
        """Stop the run's trace and keep it as an artifact the receipt links to."""
        run = self.run_key(ctx)
        browser = self.tracing.pop(run, None)
        self.active.pop(run, None)
        if browser is None:
            return None
        try:
            session = await self.session(browser, ctx)
            result = await session.call("browser_stop_tracing", {}, timeout_s=60)
        except BrowserDriverError:
            return None
        data = self._trace_zip(text_of(result))
        if data is None:
            return None
        artifact = self._artifact("trace", data, "application/zip")
        if artifact:
            from ..ledger import mirror_evidence

            mirror_evidence(
                run,
                "browser",
                "trace",
                f"Playwright trace of the {browser} browser",
                {"artifact_id": artifact},
            )
        return artifact

    def _trace_zip(self, text: str) -> bytes | None:
        """Playwright 1.63 leaves a trace as loose files; the trace viewer opens them as one zip
        (trace.trace, trace.network, resources/). Only files inside PANINI's driver output are read."""
        import io
        import zipfile

        match = re.search(r"\[Trace\]\(([^)]+\.trace)\)", text)
        if not match:
            return None
        trace = Path(match.group(1)).resolve()
        root = self.managed.output_root().resolve()
        if not trace.is_relative_to(root) or not trace.exists():
            return None
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(trace, "trace.trace")
            network = trace.with_suffix(".network")
            if network.exists():
                archive.write(network, "trace.network")
            resources = trace.parent / "resources"
            if resources.is_dir():
                for item in resources.iterdir():
                    if item.is_file():
                        archive.write(item, f"resources/{item.name}")
        return buffer.getvalue()

    # Helpers ----------------------------------------------------------------------------
    def _artifact(self, kind, data: bytes, mime: str):
        harness = self.runtime.get("harness")
        artifacts = getattr(harness, "artifacts", None)
        if artifacts is None:
            return None
        try:
            return artifacts.put(kind, data, mime)
        except Exception:  # noqa: BLE001 -- An artifact is evidence, not a reason to fail the step.
            return None

    @staticmethod
    def _where(browser):
        return {"chrome": "your Chrome", "managed": "PANINI's browser", "isolated": "the isolated browser"}[
            browser
        ]


def _json_in(text: str) -> dict:
    """The JSON object inside a Playwright tool's text result."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


__all__ = ["PANINI_TOOLS", "BrowserHand"]
