"""The capability registry: manifests → tool specs, model tools, prompt sections, readiness, and the one
call pipeline from the brain to every hand (docs/panini-enhancements/lld/V2-02-capability-registry.md)."""

import asyncio
import importlib
import json
import logging
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from ..core_contracts import EffectClass, ToolResult, ToolSpec
from .context import ToolContext, ToolView
from .manifest import BUILTIN, load_all
from .mcp import connector_specs

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
OUTPUT_CHARS = 8000


def capability_sources():
    """Built-in manifests, then capability-owned manifests, then the person's declarative manifests."""
    from ..extension_pairing import data_directory

    sources = [(BUILTIN, False)]
    sources += [(p, False) for p in sorted((ROOT / "panini-core/capabilities").glob("*/manifests"))]
    sources.append((data_directory() / "capabilities", True))
    return sources


class Hooks:
    """Pipeline stages filled in by later items: policy (V2-03), ledger (V2-04), interrupts (V2-05)."""

    async def before(self, ctx, spec, args):
        """Return a ToolResult to stop the call, or None to continue."""
        return None

    async def after(self, ctx, spec, args, result):
        return None

    async def around(self, ctx, spec, args, call):
        """Wrap the hand call (policy uses this to cancel in-flight work when permissions change)."""
        return await call()


class Registry:
    def __init__(self, runtime=None, *, sources=None, state=None):
        self.runtime = runtime or {}
        self.sources = sources if sources is not None else capability_sources()
        self.state = state  # Optional Store for capability_state (enablement)
        self.hooks: list[Hooks] = []
        self.manifests, self.errors, self.specs, self.hands = [], [], {}, {}
        self.control, self._validators, self._slots = set(), {}, {}
        self.load()

    # Loading ---------------------------------------------------------------
    def load(self):
        manifests, errors = load_all(self.sources)
        self.manifests, self.errors, self.specs, self.hands, self.control = manifests, errors, {}, {}, set()
        self.order, self.offered, self.adapters = {}, {}, {}
        for manifest in manifests:
            hand = None
            if manifest.entry:
                try:
                    module, _, name = manifest.entry.partition(":")
                    hand = getattr(importlib.import_module(module), name)(manifest, self.runtime)
                except Exception as exc:  # noqa: BLE001 -- One broken manifest must not break the rest.
                    self.errors.append(f"{manifest.id}: cannot load {manifest.entry}: {exc}")
                    continue
            for tool in manifest.tools:
                spec = tool.spec(manifest.id)
                self.specs[spec.name] = spec
                self.order[spec.name] = tool.order
                self.offered[spec.name] = tool.offered and not tool.owner_only
                if tool.adapter:
                    self.adapters[spec.name] = tool.adapter
                if tool.control:
                    self.control.add(spec.name)
                if tool.adapter:
                    from .hands import LegacyAdapterHand

                    self.hands[spec.name] = LegacyAdapterHand(manifest, self.runtime)
                elif hand is not None:
                    self.hands[spec.name] = hand
        for spec in connector_specs(self.runtime.get("connectors")):
            self.specs.setdefault(spec.name, spec)
            self.offered.setdefault(spec.name, False)
        return self.errors

    # Views -----------------------------------------------------------------
    def enabled(self, capability: str) -> bool:
        if not self.state:
            return True
        row = self.state.one("SELECT enabled FROM capability_state WHERE id=?", (capability,))
        return row is None or bool(row["enabled"])

    def tool(self, name: str) -> ToolSpec:
        return self.specs[name]

    def tools(self, view: ToolView) -> list[ToolSpec]:
        chosen = [
            spec
            for name, spec in self.specs.items()
            if self.offered.get(name) and view.satisfies(spec.requires) and self.enabled(spec.capability)
        ]
        return sorted(chosen, key=lambda s: (self.order.get(s.name, 1000), s.name))

    def model_tools(self, view: ToolView, *, capabilities=None) -> list[dict]:
        tools = []
        for spec in self.tools(view):
            tool = json.loads(json.dumps(spec.model_tool()))
            if spec.name == "use_capability" and capabilities is not None:
                tool["parameters"]["properties"]["capability"]["enum"] = list(capabilities)
            tools.append(tool)
        return tools

    def prompt_sections(self, offered: set[str]) -> list[str]:
        sections = []
        for manifest in self.manifests:
            if any(t.name in offered for t in manifest.tools) and manifest.principles():
                sections.append("\n".join(f"- {p}" for p in manifest.principles()))
        return sections

    def planner_summary(self, ready: set[str]) -> str:
        """One sentence per ready coarse capability, for the planner's rules."""
        parts = []
        for name, adapter in self.adapters.items():
            if adapter in ready:
                parts.append(f"{adapter} {self.specs[name].description.rstrip('.').lower()}")
        return "; ".join(parts)

    async def inventory(self) -> dict:
        result = {}
        for manifest in self.manifests:
            entry = {"title": manifest.title, "summary": manifest.summary, "tools": [], "state": "ready"}
            for tool in manifest.tools:
                spec = self.specs.get(tool.name)
                hand = self.hands.get(tool.name)
                state = "ready"
                if tool.adapter and hand is not None:
                    try:
                        async with asyncio.timeout(10):
                            state = (await hand.readiness(tool.adapter)).state
                    except (TimeoutError, Exception):  # noqa: BLE001
                        state = "unavailable"
                entry["tools"].append(
                    {
                        "name": tool.name,
                        "effect": str(spec.effect) if spec else tool.effect,
                        "group": tool.group,
                        "offered": self.offered.get(tool.name, False),
                        "state": state,
                    }
                )
            entry["enabled"] = self.enabled(manifest.id)
            result[manifest.id] = entry
        return result

    # Calling ---------------------------------------------------------------
    def resolve(self, name: str, args: dict) -> ToolSpec:
        """A meta tool's concrete target, which policy and receipts act on."""
        spec = self.specs[name]
        if name == "use_capability":
            return self.specs.get(f"{args.get('capability')}_run", spec)
        if name == "use_connector":
            from .mcp import connector_tool_name

            return self.specs.get(connector_tool_name(args.get("server", ""), args.get("tool", "")), spec)
        return spec

    def validate(self, spec: ToolSpec, args) -> str | None:
        if not isinstance(args, dict):
            return "Arguments must be a JSON object."
        validator = self._validators.get(spec.name)
        if validator is None:
            validator = self._validators[spec.name] = Draft202012Validator(spec.input_schema)
        lenient = getattr(self.hands.get(spec.name), "lenient_required", False)
        errors = sorted(
            (e for e in validator.iter_errors(args) if not (lenient and e.validator == "required")),
            key=lambda e: list(e.path),
        )
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.path) or "arguments"
            return f"Invalid {where}: {first.message}"[:300]
        return None

    def slot(self, key: str | None):
        if not key:
            return _NullSlot()
        return self._slots.setdefault(key, asyncio.Lock())

    async def call(self, ctx: ToolContext, name: str, args: dict) -> ToolResult:
        """The only path from the brain to a hand (invariant I1)."""
        if name not in self.specs or name not in self.hands:
            return _refused("This tool is unavailable in this task.")
        spec = self.specs[name]
        if spec.owner_only and ctx.principal.kind != "owner":
            return _refused("This tool is reserved for the owner.")
        problem = self.validate(spec, args)
        if problem:
            return _refused(problem)
        target = self.resolve(name, args)
        ctx.calls[target.name] += 1
        if (
            target.budget_per_run
            and ctx.calls[target.name] > target.budget_per_run
            and not _v1_budgeted(target)
        ):
            return _refused(
                f"{target.name} was already used {target.budget_per_run} times. Answer now with what you have."
            )
        ran = []
        for hook in self.hooks:
            stopped = await hook.before(ctx, target, args)
            ran.append(hook)
            if stopped is not None:
                for done in ran:  # Hooks that already began (the ledger) see the refusal too.
                    await done.after(ctx, target, args, stopped)
                return stopped
        ctx.check()
        started = time.monotonic()

        async def run():
            async with self.slot(target.concurrency if name not in self.control else None):
                return await self.hands[name].call(ctx, spec, args)

        call = run
        for hook in reversed(self.hooks):
            call = _wrap(hook, ctx, target, args, call)
        try:
            result = await call()
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.exception("Tool %s failed", name)
            message = str(getattr(exc, "message", "")) or "The tool failed."
            result = _refused(message[:300], outcome="failed")
        result_ms = int((time.monotonic() - started) * 1000)
        for tool_taint in target.taints:
            ctx.taint.add(tool_taint)
        for hook in self.hooks:
            await hook.after(ctx, target, args, result)
        logger.debug("tool %s → %s in %d ms", name, result.outcome, result_ms)
        return result


def _wrap(hook, ctx, spec, args, inner):
    async def wrapped():
        return await hook.around(ctx, spec, args, inner)

    return wrapped


def _v1_budgeted(spec):
    """v1 already enforces CALL_BUDGET inside the agent's use_capability handler."""
    return spec.name.endswith("_run")


def _refused(message, outcome="blocked"):
    return ToolResult(outcome, message, data={"error": message})


class _NullSlot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


EFFECTS = {e.value for e in EffectClass}
