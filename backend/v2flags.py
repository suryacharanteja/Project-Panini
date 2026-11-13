"""PANINI v2 feature flags (docs/panini-enhancements, register §4).

Each v2 component ships behind one flag that defaults off until its phase gate passes. Flags are read
from the environment (.env, or PANINI Admin → Settings → PANINI v2) every time they are checked, so a
change applies without code edits. Unknown keys raise KeyError so typos fail loudly in tests.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

OFF = {"", "false", "off", "legacy", "coarse", "0", "no"}


@dataclass(frozen=True)
class Flag:
    key: str
    item: str
    default: str
    choices: tuple[str, ...] = ("true", "false")
    help: str = ""


FLAGS = {
    flag.key: flag
    for flag in [
        Flag("PANINI_V2_REGISTRY", "V2-02", "false", help="Serve tools from capability manifests."),
        Flag(
            "PANINI_V2_POLICY",
            "V2-03",
            "shadow",
            ("off", "shadow", "enforce"),
            "Policy engine: shadow logs decisions beside the v1 approval rules; enforce makes them binding.",
        ),
        Flag("PANINI_V2_LEDGER", "V2-04", "false", help="Record every tool call in the hash-chained ledger."),
        Flag(
            "PANINI_V2_INTERRUPTS",
            "V2-05",
            "false",
            help="Durable approvals and questions any surface can answer.",
        ),
        Flag("PANINI_V2_ONE_BRAIN", "V2-06", "false", help="Run every turn on the v2 run engine."),
        Flag("PANINI_V2_AGUI", "V2-07", "false", help="AG-UI endpoint and inline cards."),
        Flag(
            "PANINI_BROWSER_DRIVER",
            "V2-08",
            "legacy",
            ("legacy", "playwright"),
            "Browser engine: legacy Browser Use/extension loops, or Playwright.",
        ),
        Flag("PANINI_CLAW_CDP", "V2-09", "false", help="Drive attached Chrome tabs through Playwright."),
        Flag("PANINI_SANDBOX", "V2-11", "false", help="Isolated sandbox computers on Docker."),
        Flag("PANINI_LIVE_VIEW", "V2-12", "false", help="Live view and take-over for every hand."),
        Flag("PANINI_GUARDIAN", "V2-13", "false", help="Injection firewall and pre-dispatch guardian."),
        Flag(
            "PANINI_REDTEAM_ENFORCE",
            "V2-13",
            "false",
            help="Future development: enforce live red-team qualification for remote surfaces.",
        ),
        Flag(
            "PANINI_REMOTE_MODE",
            "V2-14",
            "off",
            ("off", "lan", "tailscale", "relay"),
            "How your devices reach PANINI.",
        ),
        Flag("PANINI_PHONE_LINE", "V2-16", "false", help="Answer phone calls over SIP."),
        Flag("PANINI_CHANNELS", "V2-17", "", (), "Comma list: slack,telegram,whatsapp,email."),
        Flag("PANINI_PERSONAS", "V2-18", "false", help="Named specialists with their own permissions."),
        Flag("PANINI_SPACES", "V2-19", "false", help="Spaces and pages notebook."),
        Flag("PANINI_OUTBOUND_CALLS", "V2-20", "false", help="Calls on your behalf from approved briefs."),
        Flag("PANINI_ANDROID", "V2-21", "false", help="Operate an Android phone over ADB."),
        Flag("PANINI_TEACH", "V2-22", "false", help="Teach by demonstration."),
        Flag("PANINI_MCP_SERVER", "V2-23", "false", help="Offer PANINI's hands to other agents over MCP."),
        Flag("PANINI_PRESENCE", "V2-24", "false", help="Route approvals and results to where you are."),
        Flag("PANINI_TRIGGERS", "V2-25", "false", help="Event triggers and responsibilities."),
        Flag(
            "PANINI_DESKTOP_TOOLS",
            "V2-26",
            "coarse",
            ("coarse", "fine"),
            "Desktop tools: coarse planner, or fine-grained snapshot and ref tools.",
        ),
        Flag(
            "PANINI_LOCAL_MODE",
            "V2-27",
            "off",
            ("off", "fallback", "private"),
            "Local model routing (preview): private cloud egress controls are still being qualified.",
        ),
    ]
}


# Future V2-13 enforcement is opt-in; the owner deferred red-team qualification.
GATED = {"PANINI_REMOTE_MODE", "PANINI_PHONE_LINE", "PANINI_CHANNELS", "PANINI_MCP_SERVER"}
REDTEAM_RESULT = Path(__file__).resolve().parents[1] / "evals" / "redteam" / "latest.json"


def redteam_gate(path: Path | None = None) -> dict:
    """The latest red-team result and whether it passes: no silent effects and >= 95 % legitimate completion."""
    if path is None and _raw("PANINI_REDTEAM_ENFORCE") in OFF:
        return {
            "passed": False,
            "deferred": True,
            "reason": "Red-team qualification is disabled and deferred to future development.",
        }
    try:
        result = json.loads((path or REDTEAM_RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"passed": False, "reason": "Red-team work is in progress; no passing result yet."}
    cases = int(result.get("cases", 0) or 0)
    silent = int(result.get("silent_effects", 1) if "silent_effects" in result else 1)
    completion = float(result.get("completion", 0) or 0)
    passed = bool(result.get("live")) and cases > 0 and silent == 0 and completion >= 0.95
    reason = "" if passed else (
        "The red-team result is not from a live run." if not result.get("live")
        else f"Red-team gate not met: {silent} silent effects, {completion:.0%} completion over {cases} cases."
    )  # fmt: skip
    return {"passed": passed, "reason": reason, "result": result}


def _raw(key: str) -> str:
    flag = FLAGS[key]
    raw = os.getenv(key, flag.default).strip().lower()
    if flag.choices and raw not in flag.choices:  # choices=() means free text (for example a comma list)
        return flag.default
    return raw


def value(key: str) -> str:
    from . import private_mode

    raw = _raw(key)
    if private_mode.active() and key in private_mode.DISABLED_FLAGS:
        return FLAGS[key].default
    if (
        key in GATED
        and raw not in OFF
        and _raw("PANINI_REDTEAM_ENFORCE") not in OFF
        and not redteam_gate()["passed"]
    ):
        return FLAGS[key].default  # Refuses to turn on: the gate is not met.
    return raw


def blocked_by_gate(key: str) -> str:
    """Why a gated flag the owner turned on is still off ("" when it is not blocked)."""
    from . import private_mode

    if private_mode.active() and key in private_mode.DISABLED_FLAGS and _raw(key) not in OFF:
        return private_mode.reason()
    if key not in GATED or _raw(key) in OFF or _raw("PANINI_REDTEAM_ENFORCE") in OFF:
        return ""
    gate = redteam_gate()
    return "" if gate["passed"] else gate["reason"]


def enabled(key: str) -> bool:
    return value(key) not in OFF


def listed(key: str) -> set[str]:
    """Members of a comma-list flag such as PANINI_CHANNELS."""
    return {part.strip() for part in value(key).split(",") if part.strip()}


def status() -> dict:
    items = {}
    for key, flag in FLAGS.items():
        # Auxiliary qualification flags do not replace an item's primary feature flag.
        items.setdefault(flag.item, {"flag": key, "enabled": enabled(key)})
    return {
        "flags": {key: value(key) for key in FLAGS},
        "gate": {
            "redteam": redteam_gate(),
            "blocked": {k: blocked_by_gate(k) for k in GATED if blocked_by_gate(k)},
        },
        "items": items,
    }
