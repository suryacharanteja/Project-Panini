"""Effect classification (docs/panini-enhancements/lld/V2-03-policy-engine.md §5.2).

The v1 word and command patterns are kept exactly (moved here from capabilities/approvals.py), so the
v2 classes are a strict refinement of v1's ask/don't-ask rule: anything v1 called risky is at least
SUBMIT here, and the category regexes say which class it is.
"""

import re

from ..core_contracts import EffectClass, raise_effect

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

# Word categories, most severe first. Every RISKY_WORDS term belongs to one of them.
WORD_CLASSES = [
    (EffectClass.CREDENTIAL, re.compile(r"\b(password|passcode|credit card|card number|cvv)\b", re.I)),
    (
        EffectClass.TRANSACT,
        re.compile(
            r"\b(pay(ment)?|purchase|buy|checkout|check out|place (the )?order|order now|subscribe|donate|"
            r"transfer|book|reserve)\b",
            re.I,
        ),
    ),
    (
        EffectClass.DESTROY,
        re.compile(r"\b(delete|remove|erase|unsubscribe|cancel (my )?(subscription|order))\b", re.I),
    ),
    (
        EffectClass.ACCOUNT,
        re.compile(
            r"\b(sign ?in|log ?in|sign ?up|register|authori[sz]e|cancel (my )?account|accept|agree)\b", re.I
        ),
    ),
    (EffectClass.INSTALL, re.compile(r"\binstall\b", re.I)),
    (
        EffectClass.COMMUNICATE,
        re.compile(
            r"\b(send|post|publish|tweet|reply|comment|share|invite|connect|message|follow|apply|easy apply|"
            r"submit application)\b",
            re.I,
        ),
    ),
    (EffectClass.SHARE_OUT, re.compile(r"\bupload\b", re.I)),
    (EffectClass.LOCAL_WRITE, re.compile(r"\bdownload\b", re.I)),
    (EffectClass.SUBMIT, re.compile(r"\b(save|confirm)\b", re.I)),
]
COMMAND_CLASSES = [
    (
        EffectClass.DESTROY,
        re.compile(
            r"(\brm\b|\bdel\b|\brmdir\b|\brd\b|remove-item|\bformat(\.com)?\s+[a-z]:|git\s+(reset\s+--hard|clean))",
            re.I,
        ),
    ),
    (
        EffectClass.INSTALL,
        re.compile(r"(pip\s+install|npm\s+(install|i)\b|pnpm\s+add|yarn\s+add|winget|choco)", re.I),
    ),
    (EffectClass.SHARE_OUT, re.compile(r"(git\s+push|npm\s+publish|\bssh\b|\bscp\b)", re.I)),
    (
        EffectClass.ACCOUNT,
        re.compile(
            r"(\breg\b|\bsudo\b|set-executionpolicy|icacls|takeown|mklink|shutdown|restart-computer)", re.I
        ),
    ),
]
NETWORK_COMMAND = re.compile(r"(\bcurl\b|\bwget\b|invoke-webrequest|invoke-restmethod|\biwr\b|\birm\b)", re.I)
PASSWORD_FIELD = re.compile(r"(password|one-time-code|cc-number|cc-csc|current-password|new-password)", re.I)


def classify_words(text: str) -> EffectClass | None:
    """The class of a risky label/text, or None when v1 would not have asked."""
    if not text or not RISKY_WORDS.search(text):
        return None
    for effect, pattern in WORD_CLASSES:
        if pattern.search(text):
            return effect
    return EffectClass.SUBMIT


def classify_command(command: str) -> tuple[EffectClass, set[str]]:
    taints = {"network"} if NETWORK_COMMAND.search(command or "") else set()
    for effect, pattern in COMMAND_CLASSES:
        if pattern.search(command or ""):
            return effect, taints
    if RISKY_COMMAND.search(command or ""):
        return EffectClass.SUBMIT, taints  # Risky in v1 but uncategorized: still asks.
    return EffectClass.LOCAL_EXEC, taints


def classify(spec, args: dict, observation: dict | None = None) -> tuple[EffectClass, list[str], set[str]]:
    """(effect, reasons, taints) for one call. Never below the manifest's static effect."""
    effect, reasons, taints = EffectClass(spec.effect), [], set()
    observation = observation or {}
    if not spec.dynamic_effect and not observation:
        return effect, reasons, taints
    name = spec.name
    if name.endswith("_run"):
        # Coarse capabilities plan their own actions and ask per action inside the adapter (v1 rule).
        return effect, ["approvals happen per action inside the capability"], set(spec.taints)
    if name == "run_command" or "command" in args:
        found, taints = classify_command(str(args.get("command") or ""))
        if found is not effect:
            reasons.append(f"command looks like {found.value.replace('_', ' ')}")
        effect = raise_effect(effect, found) if found is not EffectClass.LOCAL_EXEC else effect
    elif name == "open":
        target = str(args.get("target", ""))
        effect = EffectClass.NAVIGATE if re.match(r"^https?://", target, re.I) else EffectClass.LOCAL_EXEC
        return effect, reasons, taints
    elif name == "read_file":
        return EffectClass.OBSERVE, reasons, taints
    elif name == "browser":
        action = str(args.get("action", "read"))
        if action in {"read", "scroll_down", "scroll_up"}:
            return EffectClass.OBSERVE, reasons, {"web"}
        if action == "navigate":
            return EffectClass.NAVIGATE, reasons, {"web"}
        effect = (
            EffectClass.INPUT
            if action in {"type", "media_pause", "media_play", "skip_ad"}
            else EffectClass.SUBMIT
        )
        if action == "type" and args.get("submit"):
            effect = EffectClass.SUBMIT
        taints = {"web"}
    label = " ".join(
        str(observation.get(k) or args.get(k) or "")
        for k in ("label", "role", "name", "text", "element", "value", "href", "instruction")
    )
    word = classify_words(label)
    if word:
        reasons.append(f"'{_short(label)}' looks like {word.value.replace('_', ' ')}")
        effect = raise_effect(effect, word)
    if PASSWORD_FIELD.search(
        str(observation.get("type", "")) + " " + str(observation.get("autocomplete", ""))
    ):
        effect = EffectClass.CREDENTIAL
        reasons.append("password or payment field")
    return effect, reasons, taints


def _short(text):
    text = " ".join(text.split())
    return text[:60] + ("…" if len(text) > 60 else "")
