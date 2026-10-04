"""Android hand (V2-21): explicit device selection, fresh targets and policy-bound effects."""

import asyncio
import base64
import io
import json
import re
import time
from dataclasses import replace
from urllib.parse import urlsplit

from PIL import Image

from .. import v2flags
from ..core_contracts import EffectClass, Readiness, ToolResult, raise_effect
from ..policy.effects import classify_words
from . import adb
from .android_driver import AndroidDriver
from .android_tree import tree

SENSITIVE = re.compile(
    r"bank|wallet|payment|paypal|authenticator|password|bitwarden|1password|keepass|"
    r"com\.android\.(settings|vending)|com\.google\.android\.(gsf|finsky)",
    re.I,
)
PACKAGE = re.compile(r"^[a-zA-Z]\w*(\.[a-zA-Z]\w*)+$")
SYSTEM_UI = {
    "com.android.systemui",
    "com.android.launcher",
    "com.android.launcher3",
    "com.google.android.apps.nexuslauncher",
    "com.sec.android.app.launcher",
    "com.google.android.inputmethod.latin",
    "com.samsung.android.honeyboard",
}


class Android:
    def __init__(self, harness, *, driver=None):
        self.h = harness
        self.driver = driver or AndroidDriver()
        self.settings = harness.store.path.parent / "android.json"
        try:
            self.config = json.loads(self.settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.config = {"serial": "", "driver_consent": False, "allowed_apps": []}
        self.nodes, self.packages = {}, set()
        self.visible_refs = set()
        self.last_action = 0.0
        self.generation = 0
        self.lock = asyncio.Lock()

    def save(self, serial, driver_consent, allowed_apps):
        if not isinstance(serial, str) or not 1 <= len(serial) <= 200 or any(ord(c) < 32 for c in serial):
            raise ValueError("Select a connected phone.")
        if type(driver_consent) is not bool or not isinstance(allowed_apps, list) or len(allowed_apps) > 40:
            raise ValueError("Check driver consent and the app list.")
        if any(not isinstance(p, str) or not PACKAGE.fullmatch(p) for p in allowed_apps):
            raise ValueError("Use Android package identifiers in the app allow list.")
        self.config = {"serial": serial, "driver_consent": driver_consent, "allowed_apps": allowed_apps}
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.settings.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config), encoding="utf-8")
        temporary.replace(self.settings)
        self.nodes.clear()
        self.visible_refs.clear()
        # Changing the selected device or app scope cancels in-flight policy-bound actions.
        self.h.policy.bump()
        return self.status()

    def status(self):
        return {
            **self.config,
            "enabled": v2flags.enabled("PANINI_ANDROID"),
            "adb": adb.executable(),
            "qualification": "Physical Android testing is pending.",
        }

    def ready(self):
        if not v2flags.enabled("PANINI_ANDROID"):
            raise ValueError("Enable Android hands in Settings first.")
        if not v2flags.enabled("PANINI_V2_LEDGER"):
            raise ValueError(
                "Enable PANINI_V2_LEDGER before using Android hands; every call requires a receipt."
            )
        if not self.config.get("serial") or self.config.get("driver_consent") is not True:
            raise ValueError(
                "Select a phone and acknowledge Playwright driver installation in Android setup."
            )

    def activity(self):
        from ..live import takeover

        serial = self.config.get("serial", "")
        return {
            "target": serial,
            "active": bool(
                v2flags.enabled("PANINI_ANDROID")
                and serial
                and time.time() - self.last_action < 60
                and takeover().state("android", serial).holder == "agent"
            ),
        }

    async def request(self, op, **args):
        self.ready()
        return await self.driver.call(op, serial=self.config["serial"], consent=True, **args)

    def check_apps(self, packages):
        # Package-name patterns cannot identify every bank or password manager. Unknown apps therefore
        # require the same explicit owner allowance; only fixed launcher/keyboard UI is implicit.
        blocked = {
            p
            for p in packages
            if p not in self.config["allowed_apps"] and (p not in SYSTEM_UI or SENSITIVE.search(p))
        }
        if blocked:
            raise ValueError(
                "This app needs an explicit owner allowance in Android setup: "
                + ", ".join(sorted(blocked))[:300]
            )

    async def snapshot(self, depth=20, *, publish=True):
        if publish:
            self.nodes.clear()  # Even a failed refresh invalidates old refs.
            self.visible_refs.clear()
            self.generation += 1
        for attempt in range(2):
            try:
                found, packages, text = tree(await self.request("snapshot"), self.generation, depth)
                self.check_apps(packages)
                if publish:
                    self.nodes, self.packages = found, packages
                    self.visible_refs = {r for r, n in found.items() if n.node.depth <= depth}
                return text if publish else found
            except ValueError:
                if attempt:
                    raise
                await asyncio.sleep(0.5)

    async def frame(self, target):
        if target != self.config.get("serial") or not v2flags.enabled("PANINI_ANDROID"):
            return None
        async with self.lock:
            # A screenshot must not silently expose a sensitive app after the phone changes.
            found = await self.snapshot(publish=False)
            if any("password" in n.node.states for n in found.values()):
                raise ValueError("Live frames are hidden while a credential field is visible.")
            raw = base64.b64decode(await self.request("screenshot"), validate=True)
            if len(raw) > 3_000_000:
                raise ValueError("Phone screenshot too large.")
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError("Phone screenshot too large.")
                image.thumbnail((1280, 1280))
                output = io.BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=75)
            return output.getvalue(), {"title": "Android phone", "width": image.width, "height": image.height}

    async def authorize(self, ctx, spec, args, target, reviewed=None):
        effect = spec.effect
        label = target.node.name if target else ""
        if target and "password" in target.node.states:
            raise ValueError(
                "PANINI never acts on password or security-code fields. Enter these on the phone."
            )
        if target and "disabled" in target.node.states:
            raise ValueError("The target is disabled.")
        if word := classify_words(label):
            effect = raise_effect(effect, word)
        if spec.name == "android_press" and args["key"] == "ENTER":
            effect = raise_effect(effect, EffectClass.SUBMIT)
        if spec.name in {"android_tap", "android_longtap"} and re.search(r"\bcall\b", label, re.I):
            effect = raise_effect(effect, EffectClass.COMMUNICATE)
        if effect == EffectClass.CREDENTIAL:
            raise ValueError("Credential entry is reserved for the person.")
        real = {**args, "element": label, "role": target.node.role if target else ""}
        concrete = replace(spec, effect=effect)
        decision = self.h.policy.decide(
            ctx.principal, concrete, real, taint=ctx.taint, background=ctx.view.background
        )
        previous = ctx.decision
        ctx.android_policy = (concrete, real)
        ctx.decision = decision
        if decision.action == "deny":
            raise ValueError("Android action denied: " + "; ".join(decision.reasons))
        if decision.action != "allow":
            if reviewed is not None:
                if (
                    reviewed.action == "allow"
                    or reviewed.effect != decision.effect
                    or reviewed.level != decision.level
                ):
                    raise ValueError("Android permissions changed. Review the action again.")
                return decision
            if not (ctx.preapproved and previous and previous.effect == decision.effect):
                detail = json.dumps({**real, "device": self.config["serial"]}, ensure_ascii=False)
                if ctx.confirm is None or not await ctx.confirm(
                    f"{spec.title}: {decision.effect.value}", detail[:3000]
                ):
                    raise ValueError("The owner did not approve this Android action.")
        ctx.check()
        if self.h.policy.paused():
            raise ValueError("PANINI is paused.")
        return decision

    async def call(self, ctx, spec, args):
        from ..live import takeover

        async with self.lock:
            attempted = False
            try:
                self.ready()
                selected = json.dumps(self.config, sort_keys=True)
                observe = spec.effect == EffectClass.OBSERVE
                takeover().gate("android", self.config["serial"], observe=spec.name == "android_snapshot")
                if spec.name == "android_devices":
                    return ToolResult(data={"devices": await self.request("devices")})
                if spec.name == "android_snapshot":
                    text = await self.snapshot(args.get("depth", 20))
                    artifact = self.h.artifacts.put("snapshot", text.encode(), "text/yaml", run_id=ctx.run_id)
                    return ToolResult(
                        content=text,
                        data={"snapshot": text, "generation": self.generation},
                        artifact_ids=[artifact],
                    )
                if spec.name == "android_verify_text":
                    await self.snapshot()
                    visible = any(
                        args["text"] in n.node.name or args["text"] in (n.node.value or "")
                        for n in self.nodes.values()
                        if "password" not in n.node.states
                    )
                    return ToolResult(
                        "ok" if visible else "failed",
                        data={"verified": visible},
                        evidence=[{"kind": "assertion", "passed": visible}],
                    )
                if spec.name == "android_wait_for":
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + args["timeout_seconds"]
                    while True:
                        await self.snapshot()
                        if any(
                            args["text"] in n.node.name
                            for n in self.nodes.values()
                            if "password" not in n.node.states
                        ):
                            return ToolResult(data={"visible": True})
                        if loop.time() >= deadline:
                            return ToolResult(
                                "failed", "The expected text did not appear within the wait window."
                            )
                        if self.h.policy.paused():
                            raise ValueError("PANINI is paused.")
                        await asyncio.sleep(0.5)
                if spec.name == "android_screenshot":
                    await self.snapshot()
                    if any("password" in n.node.states for n in self.nodes.values()):
                        raise ValueError("Screenshot refused while a credential field is visible.")
                    raw = base64.b64decode(await self.request("screenshot"), validate=True)
                    if len(raw) > 3_000_000:
                        raise ValueError("Phone screenshot too large.")
                    artifact = self.h.artifacts.put("screenshot", raw, "image/png", run_id=ctx.run_id)
                    return ToolResult(data={"artifact_id": artifact}, artifact_ids=[artifact])
                if observe:
                    raise ValueError("Unknown Android observation.")
                target = self.nodes.get(args.get("ref"))
                if "ref" in args and (target is None or args["ref"] not in self.visible_refs):
                    raise ValueError("Stale Android ref. Read android_snapshot again.")
                before = target.identity() if target else None
                await self.snapshot()
                if before:
                    matches = [n for n in self.nodes.values() if n.identity() == before]
                    if len(matches) != 1:
                        raise ValueError("The phone changed or the target is ambiguous. Take a new snapshot.")
                    target = matches[0]
                    if sum(n.selector() == target.selector() for n in self.nodes.values()) != 1:
                        raise ValueError("The target selector is ambiguous; no action was sent.")
                if spec.name == "android_press" and args["key"] not in {"BACK", "HOME", "ESCAPE"}:
                    focused = [n for n in self.nodes.values() if n.focused]
                    if len(focused) != 1:
                        raise ValueError("A unique focused field is required before pressing a key.")
                    target = focused[0]
                reviewed = await self.authorize(ctx, spec, args, target)
                # An approval can take minutes. Validate the complete current view again immediately before dispatch.
                identity = target.identity() if target else None
                await self.snapshot()
                if identity and sum(n.identity() == identity for n in self.nodes.values()) != 1:
                    raise ValueError("The phone changed during approval. Take a new snapshot.")
                if target and sum(n.selector() == target.selector() for n in self.nodes.values()) != 1:
                    raise ValueError("The target became ambiguous during approval.")
                if selected != json.dumps(self.config, sort_keys=True):
                    raise ValueError("Android setup changed during this action.")
                # Grants and pause may change while the approval UI is open.
                await self.authorize(ctx, spec, args, target, reviewed=reviewed)
                takeover().gate("android", self.config["serial"], observe=False)
                op = spec.name.removeprefix("android_")
                payload = {k: v for k, v in args.items() if k != "ref"}
                if target and op != "press":
                    payload["selector"] = target.selector()
                if op == "launch":
                    self.check_apps({args["package"]})
                if op == "open_url":
                    parts = urlsplit(args["url"])
                    if (
                        parts.scheme not in {"http", "https"}
                        or not parts.hostname
                        or parts.username
                        or parts.password
                    ):
                        raise ValueError("Only http(s) URLs without credentials are allowed.")
                    op = "url"
                attempted = True
                self.last_action = time.time()
                await self.request(op, **payload)
                self.nodes.clear()
                return ToolResult(data={"dispatched": True, "verified": False}, dispatched=True)
            except (ValueError, RuntimeError) as exc:
                self.nodes.clear()
                return ToolResult(
                    "unknown_effect" if attempted else "blocked",
                    str(exc),
                    dispatched=attempted,
                    error_code="ANDROID",
                )
            except (TimeoutError, OSError):
                self.nodes.clear()
                return ToolResult(
                    "unknown_effect" if attempted else "failed",
                    "Android transport interrupted. Inspect the phone before continuing.",
                    dispatched=attempted,
                )


class AndroidHand:
    def __init__(self, manifest, runtime):
        self.runtime = runtime

    @property
    def service(self):
        return self.runtime["harness"].android

    async def readiness(self, capability=None):
        try:
            self.service.ready()
            return Readiness("ready" if adb.executable() else "needs_setup", "ADB must be installed.")
        except ValueError as exc:
            return Readiness("needs_setup", str(exc))

    async def call(self, ctx, spec, args):
        return await self.service.call(ctx, spec, args)

    async def run_finished(self, ctx):
        self.service.last_action = 0.0
