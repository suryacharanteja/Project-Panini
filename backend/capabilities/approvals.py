"""Which actions stop for the person's approval.

`PANINI_APPROVALS=risky` (default) asks only before actions that are irreversible or reach
other people: paying, sending, posting, deleting, signing in, applying, installing and
similar. Reading, navigating, scrolling, typing into fields, searching and in-workspace
file edits run without interruption. `PANINI_APPROVALS=all` asks before every effect.
The check is deterministic (no model call), so it adds no latency.
"""

import os

# The patterns live in the v2 policy engine; v1 callers keep these names (V2-03).
from ..policy.effects import RISKY_COMMAND, RISKY_WORDS  # noqa: E402,F401


def _shadow(name, effect, dynamic, args, v1_ask):
    """V2-03 shadow mode: decide the same action with the policy engine and record disagreements."""
    from .. import v2flags

    if v2flags.value("PANINI_V2_POLICY") != "shadow":
        return v1_ask
    try:
        from ..core_contracts import Principal, ToolSpec
        from ..policy import current

        spec = ToolSpec(
            name,
            "v1",
            "v1",
            name,
            name,
            {"type": "object", "additionalProperties": False},
            effect,
            dynamic_effect=dynamic,
        )
        current().record_shadow(name, current().decide(Principal(), spec, args), v1_ask)
    except Exception:  # noqa: BLE001 -- Shadow mode only observes; it never changes v1 behaviour.
        pass
    return v1_ask


def ask_every_time():
    return os.getenv("PANINI_APPROVALS", "risky").strip().lower() == "all"


def browser_action_needs_approval(action):
    return _shadow("browser_action", "submit", True, _browser_args(action), _browser_v1(action))


def _browser_args(action):
    args = {k: str(action.get(k) or "") for k in ("label", "text", "value", "href") if action.get(k)}
    if action.get("kind") in {"wait", "scroll", "select_tab", "navigate", "open_tab"}:
        args["action"] = "navigate"
    return args


def _browser_v1(action):
    if ask_every_time():
        return action.get("kind", "click") not in {"wait", "scroll", "select_tab"}
    if action.get("kind") in {"wait", "scroll", "select_tab", "navigate", "open_tab"}:
        return False
    text = " ".join(
        str(action.get(key) or "") for key in ("label", "role", "text", "value", "current_value", "href")
    )
    return bool(RISKY_WORDS.search(text)) or "password" in str(action.get("type", "")).lower()


def managed_browser_needs_approval(name, params, label):
    if ask_every_time():
        return True
    if name in {"navigate", "go_to_url", "search", "search_google", "go_back", "switch_tab", "wait"}:
        return False
    return bool(RISKY_WORDS.search(f"{label} {params.get('text', '')}"))


def desktop_action_needs_approval(kind, element_name, text=""):
    v1 = ask_every_time() or bool(RISKY_WORDS.search(f"{element_name} {text}"))
    return _shadow("desktop_action", "submit", True, {"label": element_name, "text": text}, v1)


def code_action_needs_approval(item):
    v1 = _code_v1(item)
    if item.get("type") == "commandExecution":
        return _shadow("run_command", "local_exec", True, {"command": str(item.get("command", ""))}, v1)
    return v1


def _code_v1(item):
    if ask_every_time():
        return True
    if item.get("type") == "commandExecution":
        return bool(RISKY_COMMAND.search(str(item.get("command", ""))))
    # File changes are limited to the configured workspace and are shown in the activity.
    return False
