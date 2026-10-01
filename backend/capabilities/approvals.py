"""Which actions stop for the person's approval.

`PANINI_APPROVALS=risky` (default) asks only before actions that are irreversible or reach
other people: paying, sending, posting, deleting, signing in, applying, installing and
similar. Reading, navigating, scrolling, typing into fields, searching and in-workspace
file edits run without interruption. `PANINI_APPROVALS=all` asks before every effect.
The check is deterministic (no model call), so it adds no latency.
"""

import os
import re

RISKY_WORDS = re.compile(
    r"\b(pay(ment)?|purchase|buy|checkout|check out|place (the )?order|order now|subscribe|donate|"
    r"transfer|send|post|publish|tweet|reply|comment|share|apply|easy apply|delete|remove|erase|"
    r"unsubscribe|cancel (my )?(subscription|order|account)|sign ?in|log ?in|sign ?up|register|"
    r"password|passcode|credit card|card number|cvv|confirm|accept|agree|authori[sz]e|install|"
    r"download|upload|book|reserve|invite|connect|message|follow|save|submit application)\b",
    re.I,
)
RISKY_COMMAND = re.compile(
    r"(\brm\b|\bdel\b|\brmdir\b|\brd\b|remove-item|\bformat(\.com)?\s+[a-z]:|git\s+(push|reset\s+--hard|clean)|"
    r"\bcurl\b|\bwget\b|invoke-webrequest|invoke-restmethod|\biwr\b|\birm\b|pip\s+install|"
    r"npm\s+(install|i|publish)|pnpm\s+add|yarn\s+add|winget|choco|shutdown|restart-computer|"
    r"\breg\b|\bsudo\b|set-executionpolicy|\bssh\b|\bscp\b|mklink|takeown|icacls)",
    re.I,
)


def ask_every_time():
    return os.getenv("PANINI_APPROVALS", "risky").strip().lower() == "all"


def browser_action_needs_approval(action):
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
    if ask_every_time():
        return True
    return bool(RISKY_WORDS.search(f"{element_name} {text}"))


def code_action_needs_approval(item):
    if ask_every_time():
        return True
    if item.get("type") == "commandExecution":
        return bool(RISKY_COMMAND.search(str(item.get("command", ""))))
    # File changes are limited to the configured workspace and are shown in the activity.
    return False
