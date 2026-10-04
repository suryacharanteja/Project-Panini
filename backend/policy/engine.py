"""One deterministic decision per tool call: allow, ask (at a level), park (background) or deny
(docs/panini-enhancements/lld/V2-03-policy-engine.md)."""

import asyncio
import fnmatch
import json
import os
import time
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from ..core_contracts import RISK, ApprovalLevel, EffectClass, Principal, stricter
from .effects import RISKY_WORDS, classify

E, L = EffectClass, ApprovalLevel
TIERS = ("local", "remote", "channel", "external", "background")
UNTRUSTED_TAINTS = {"web", "email", "channel", "call", "mcp", "a2a", "shared", "client_context", "page"}

# balanced (= PANINI_APPROVALS=risky): effect → level per tier (local, remote, channel, external, background)
BALANCED = {
    E.OBSERVE: (L.NONE, L.NONE, L.NONE, L.NONE, L.NONE),
    E.NAVIGATE: (L.NONE, L.NONE, L.NONE, L.NONE, L.NONE),
    E.INPUT: (L.NONE, L.NONE, L.NONE, L.TAP, L.NONE),
    E.LOCAL_WRITE: (L.NONE, L.TAP, L.TAP, L.TAP, L.TAP),
    E.LOCAL_EXEC: (L.NONE, L.TAP, L.TAP, L.TAP, L.TAP),
    E.SUBMIT: (L.NONE, L.TAP, L.TAP, L.TAP, L.TAP),
    E.SHARE_OUT: (L.TAP, L.TAP, L.TAP, L.TAP, L.TAP),
    E.COMMUNICATE: (L.TAP, L.TAP, L.TAP, L.TAP, L.TAP),
    E.TRANSACT: (L.TAP, L.VERIFY, L.VERIFY, L.DENY, L.TAP),
    E.ACCOUNT: (L.TAP, L.VERIFY, L.VERIFY, L.DENY, L.TAP),
    E.DESTROY: (L.TAP, L.VERIFY, L.VERIFY, L.DENY, L.TAP),
    E.INSTALL: (L.TAP, L.VERIFY, L.DENY, L.DENY, L.TAP),
    E.CREDENTIAL: (L.DENY,) * 5,
}
CAUTIOUS = {
    effect: tuple(stricter(level, L.TAP) if RISK[effect] >= RISK[E.NAVIGATE] else level for level in row)
    for effect, row in BALANCED.items()
}
TRUSTED = {
    **BALANCED,
    E.LOCAL_WRITE: (L.NONE, L.NONE, L.TAP, L.TAP, L.TAP),
    E.LOCAL_EXEC: (L.NONE, L.NONE, L.TAP, L.TAP, L.TAP),
}
PROFILES = {"balanced": BALANCED, "cautious": CAUTIOUS, "trusted": TRUSTED}
HIGH = {E.TRANSACT, E.ACCOUNT, E.DESTROY, E.INSTALL}


def profile_name() -> str:
    explicit = os.getenv("PANINI_POLICY_PROFILE", "").strip().lower()
    if explicit in PROFILES:
        return explicit
    return "cautious" if os.getenv("PANINI_APPROVALS", "risky").strip().lower() == "all" else "balanced"


def tier(principal: Principal, background: bool = False) -> str:
    if principal.kind in {"external_agent", "mcp_client", "channel_user"} or not principal.verified:
        return "external"
    if principal.surface in {"slack", "telegram", "whatsapp", "email"}:
        return "channel"
    if background or principal.surface in {"background", "trigger", "schedule"}:
        return "background"
    if principal.surface in {"phone_app", "phone_line", "agui_remote"}:
        return "remote"
    return "local"


@dataclass(frozen=True)
class Grant:
    id: str
    subject: str = "owner"  # owner | surface:<name> | persona:<id> | mcp_client:<id> | a2a:<id>
    capability: str = "*"
    tools: tuple[str, ...] = ("*",)
    groups: tuple[str, ...] = ("*",)
    levels: dict = field(default_factory=dict)  # effect → level
    conditions: dict = field(default_factory=dict)  # surfaces, background
    expires_at: float | None = None
    note: str = ""

    def specificity(self, spec) -> int:
        if any(t != "*" and fnmatch.fnmatch(spec.name, t) for t in self.tools):
            return 3
        if any(g != "*" and g == spec.group for g in self.groups):
            return 2
        return 1 if self.capability != "*" else 0

    def applies(self, subjects: set[str], spec, principal, now) -> bool:
        if self.expires_at and self.expires_at <= now:
            return False
        if self.subject not in subjects:
            return False
        if self.capability not in {"*", spec.capability}:
            return False
        if not any(fnmatch.fnmatch(spec.name, t) for t in self.tools):
            return False
        if not any(g in {"*", spec.group} for g in self.groups):
            return False
        surfaces = self.conditions.get("surfaces")
        return not surfaces or principal.surface in surfaces


@dataclass(frozen=True)
class Decision:
    action: str  # allow | ask | park | deny
    level: ApprovalLevel
    effect: EffectClass
    reasons: tuple[str, ...] = ()
    preview: dict = field(default_factory=dict)
    redactions: tuple[str, ...] = ()
    grant_ids: tuple[str, ...] = ()
    taints: tuple[str, ...] = ()
    tier: str = "local"

    def view(self) -> dict:
        data = asdict(self)
        data["level"], data["effect"] = self.level.value, self.effect.value
        return data


REDACT = {
    "text": "text",
    "contents": "contents",
    "command": "command",
    "value": "value",
    "arguments": "arguments",
}


class Policy:
    def __init__(self, store=None, clock=time.time):
        self.store, self.clock = store, clock
        self._changed = asyncio.Event() if _loop_ready() else None
        self._memory_epoch, self._memory_paused, self._memory_grants = 0, False, {}

    # Grant store -----------------------------------------------------------
    def grants(self, subject: str | None = None) -> list[Grant]:
        if not self.store:
            rows = list(self._memory_grants.values())
        else:
            rows = [
                _grant(json.loads(r["data"]))
                for r in self.store.all("SELECT data FROM grants ORDER BY created")
            ]
        return [g for g in rows if subject is None or g.subject == subject]

    def put_grant(self, grant: Grant, by: str = "owner") -> Grant:
        if self.store:
            self.store.execute(
                "INSERT INTO grants(id, data, created, created_by) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (grant.id, json.dumps(asdict(grant)), self.clock(), by),
            )
        else:
            self._memory_grants[grant.id] = grant
        self.bump()
        return grant

    def revoke(self, grant_id: str, by: str = "owner") -> bool:
        if self.store:
            removed = self.store.execute("DELETE FROM grants WHERE id=?", (grant_id,)).rowcount > 0
        else:
            removed = self._memory_grants.pop(grant_id, None) is not None
        self.bump()
        return removed

    # Epochs and pause ------------------------------------------------------
    def epoch(self) -> int:
        if not self.store:
            return self._memory_epoch
        row = self.store.one("SELECT epoch FROM grant_epochs WHERE id=1")
        return row["epoch"] if row else 0

    def paused(self) -> bool:
        if not self.store:
            return self._memory_paused
        row = self.store.one("SELECT paused FROM grant_epochs WHERE id=1")
        return bool(row and row["paused"])

    def bump(self):
        if self.store:
            self.store.execute(
                "INSERT INTO grant_epochs(id, epoch, paused) VALUES (1, 1, 0) "
                "ON CONFLICT(id) DO UPDATE SET epoch = epoch + 1"
            )
        else:
            self._memory_epoch += 1
        self._notify()

    def pause(self, paused: bool, by: str = "owner"):
        if self.store:
            self.store.execute(
                "INSERT INTO grant_epochs(id, epoch, paused) VALUES (1, 1, ?) "
                "ON CONFLICT(id) DO UPDATE SET paused=excluded.paused, epoch = epoch + 1",
                (int(paused),),
            )
        else:
            self._memory_paused = paused
            self._memory_epoch += 1
        self._notify()

    def _notify(self):
        event, self._changed = self._changed, (asyncio.Event() if _loop_ready() else None)
        if event is not None:
            event.set()

    async def wait_change(self):
        if self._changed is None:
            self._changed = asyncio.Event()
        await self._changed.wait()

    # Decisions -------------------------------------------------------------
    def decide(
        self,
        principal: Principal,
        spec,
        args: dict,
        *,
        observation=None,
        taint=(),
        background=False,
        subjects: set[str] | None = None,
    ) -> Decision:
        effect, reasons, new_taints = classify(spec, args or {}, observation)
        where = tier(principal, background)
        if self.paused():
            return Decision("deny", L.DENY, effect, ("PANINI is paused",), tier=where)
        name = profile_name()
        level = PROFILES[name][effect][TIERS.index(where)]
        # v1: commands are judged by the command patterns only; labels and typed text by the word patterns.
        words = "" if "command" in (args or {}) else _label(args, observation)
        risky = bool(RISKY_WORDS.search(words)) or any("command looks like" in r for r in reasons)
        if level is L.NONE and where == "local" and effect in {E.SUBMIT, E.LOCAL_EXEC} and risky:
            level, reasons = L.TAP, [*reasons, "v1 risky rule"]
        if spec.name.endswith("_run") and effect is not E.CREDENTIAL:
            level = L.NONE  # The capability asks per action itself.
        subjects = subjects or _subjects(principal)
        if principal.kind == "persona":
            # Specialists never inherit owner or surface grants. An absent grant is a denial,
            # including read-only tools; approval cannot supply a missing permission.
            subjects = {f"persona:{principal.client_id}"}
            if not any(g.applies(subjects, spec, principal, self.clock()) for g in self.grants()):
                return Decision("deny", L.DENY, effect, ("Persona lacks this permission",), tier=where)
        grant_ids = []
        now = self.clock()
        for grant in sorted(self.grants(), key=lambda g: g.specificity(spec)):
            if grant.applies(subjects, spec, principal, now) and effect.value in grant.levels:
                level = ApprovalLevel(grant.levels[effect.value])
                grant_ids.append(grant.id)
        # Hard ceilings that no grant can lower.
        if effect is E.CREDENTIAL:
            level = L.DENY
        if where == "external" and RISK[effect] >= RISK[E.SUBMIT]:
            level = stricter(level, L.TAP)
        if where in {"remote", "channel"} and effect in HIGH:
            exact = [
                g
                for g in self.grants()
                if g.id in grant_ids and spec.name in g.tools and g.expires_at and g.expires_at - now <= 86400
            ]
            if not exact:
                level = stricter(level, L.VERIFY)
        # Taint escalation: acting after reading untrusted content. Only earlier reads count (not this
        # call's own output), and web taint does not escalate ordinary actions inside a browser (browsing a
        # site means clicking on what it shows; risky clicks already ask through the word rules).
        all_taint = set(taint)
        # Web pages and search results are judged by the guardian (V2-13), which asks whether an action was
        # induced by the content; gating every host action after any web read would make PANINI unusable.
        # Direct-message channels (email, chat, calls, connectors, other agents) escalate here.
        untrusted = (all_taint & UNTRUSTED_TAINTS) - {"web"}
        if level is L.NONE and RISK[effect] >= RISK[E.SUBMIT] and untrusted:
            level = L.TAP
            reasons = [*reasons, "acting after reading untrusted content"]
        if "sensitive_local" in all_taint and effect in {E.SHARE_OUT, E.COMMUNICATE}:
            level = stricter(level, L.TAP)
        if "download" in all_taint and effect is E.LOCAL_EXEC:
            level = stricter(level, L.TAP)
        action = (
            "allow"
            if level is L.NONE
            else "deny"
            if level is L.DENY
            else "park"
            if where == "background"
            else "ask"
        )
        redactions = tuple(key for key in REDACT if key in (args or {}))
        preview = {"effect": effect.value, "tool": spec.name, "target": _label(args, observation)[:200]}
        return Decision(
            action,
            level,
            effect,
            tuple(reasons),
            preview,
            redactions,
            tuple(grant_ids),
            tuple(sorted(new_taints)),
            where,
        )

    # Shadow mode -----------------------------------------------------------
    def record_shadow(self, tool: str, decision: Decision, v1_ask: bool):
        v2_ask = decision.action != "allow"
        if v2_ask == v1_ask or not self.store:
            return
        self.store.execute(
            "INSERT INTO policy_shadow(ts, tool, effect, v1_ask, v2_action, v2_level, reasons) VALUES (?,?,?,?,?,?,?)",
            (
                self.clock(),
                tool,
                decision.effect.value,
                int(v1_ask),
                decision.action,
                decision.level.value,
                json.dumps(decision.reasons),
            ),
        )

    def shadow_stats(self) -> dict:
        if not self.store:
            return {"more_permissive": 0, "stricter": 0}
        rows = self.store.all("SELECT v1_ask, COUNT(*) AS n FROM policy_shadow GROUP BY v1_ask")
        counts = {r["v1_ask"]: r["n"] for r in rows}
        return {"more_permissive": counts.get(1, 0), "stricter": counts.get(0, 0)}


def _is_browser(spec) -> bool:
    return (
        spec.name == "browser"
        or spec.name.startswith(("browser_", "managed_", "sandbox_"))
        or spec.capability in {"chrome_direct", "browser"}
    )


def _label(args, observation):
    values = list((observation or {}).values()) + [
        (args or {}).get(k) for k in ("label", "text", "element", "value", "href", "command", "target", "url")
    ]
    return " ".join(str(v) for v in values if isinstance(v, (str, int, float)) and v)


def _subjects(principal: Principal) -> set[str]:
    subjects = {f"surface:{principal.surface}"}
    if principal.kind == "owner":
        subjects.add("owner")
    if principal.client_id:
        prefix = {"mcp_client": "mcp_client", "external_agent": "a2a", "persona": "persona"}.get(
            principal.kind
        )
        if prefix:
            subjects.add(f"{prefix}:{principal.client_id}")
    return subjects


def _grant(data: dict) -> Grant:
    data["tools"], data["groups"] = tuple(data.get("tools", ("*",))), tuple(data.get("groups", ("*",)))
    return Grant(**data)


def new_grant(**fields) -> Grant:
    return Grant(id=uuid4().hex[:12], **fields)


def _loop_ready() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False
