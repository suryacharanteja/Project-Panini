"""Resolve only discovered targets. Model text never becomes an executable or path."""

import asyncio
import re
import secrets

from .contracts import Fault


def words(value):
    return re.findall(r"\w+", value.casefold())


def grounded_query(query, instruction):
    """A model may paraphrase an app name, but may not choose an unrequested workspace/window."""

    def normalize(value):
        value = " ".join(words(value))
        for alias in ("visual studio code", "vs code", "vscode"):
            value = value.replace(alias, "code")
        return value

    return f" {normalize(query)} " in f" {normalize(instruction)} "


async def inventory(adapters):
    async def read(name, adapter):
        try:
            async with asyncio.timeout(8):
                state = await adapter.availability()
                result = {**state, "targets": [], "applications": []}
                if state["state"] == "ready":
                    result["targets"] = (await adapter.targets())[:100]
                    if hasattr(adapter, "applications"):
                        result["applications"] = await adapter.applications()
                return name, result
        except (Exception, TimeoutError):
            return name, {
                "state": "unavailable",
                "reason": "Discovery failed. Refresh capability readiness.",
                "targets": [],
                "applications": [],
            }

    return dict(await asyncio.gather(*(read(k, v) for k, v in adapters.items())))


# Words that name an application in ordinary speech, keyed by the window's process name.
APPLICATION_WORDS = {
    "mspaint": ("paint", "ms paint"),
    "calculatorapp": ("calculator", "calc"),
    "notepad": ("notepad",),
    "windowsterminal": (
        "terminal",
        "powershell",
        "command prompt",
        "command line",
        "cmd",
        "shell",
        "panini cli",
        "cli",
    ),
    "code": ("vs code", "vscode", "visual studio code"),
    "explorer": ("file explorer", "explorer", "folder"),
    "chrome": ("chrome", "google chrome"),
    "msedge": ("edge", "microsoft edge"),
    "firefox": ("firefox",),
    "winword": ("word",),
    "excel": ("excel",),
    "powerpnt": ("powerpoint",),
    "spotify": ("spotify",),
    "slack": ("slack",),
    "discord": ("discord",),
    "claude": ("claude",),
}
# Label words too common to identify a window.
GENERIC_WORDS = {
    "untitled",
    "window",
    "microsoft",
    "google",
    "windows",
    "program",
    "manager",
    "this",
    "that",
    "with",
    "your",
}
# Windows that are part of Windows itself and never what a person means.
NOISE_LABELS = {
    "program manager",
    "nvidia geforce overlay",
    "microsoft text input application",
    "windows input experience",
}


def pick_from_instruction(instruction, candidates):
    """Choose the one window an instruction is plainly about ("In Paint, draw..."), or None.

    Applications are matched by their process name, by ordinary words for them (Paint, terminal, VS Code)
    and by distinctive words in the window title. A tie or no signal picks nothing, so the person is asked
    rather than PANINI guessing.
    """
    text = f" {' '.join(words(instruction))} "

    def mentions(phrase):
        return f" {' '.join(words(phrase))} " in text

    scored = []
    for candidate in candidates:
        application = str(candidate.get("application", "")).casefold()
        score = 0
        if any(mentions(phrase) for phrase in APPLICATION_WORDS.get(application, ())):
            score += 4
        if len(application) > 2 and mentions(application):
            score += 4
        for word in set(words(str(candidate.get("label", "")))):
            if len(word) >= 4 and word not in GENERIC_WORDS and mentions(word):
                score += 1
        scored.append((score, candidate))
    scored.sort(key=lambda row: row[0], reverse=True)
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def matches(query, target):
    query = " ".join(words(query))
    aliases = {"vs code": "code", "visual studio code": "code", "windows notepad": "notepad"}
    query = aliases.get(query, query)
    return bool(query) and any(
        query == " ".join(words(str(target.get(key, ""))))
        or f" {query} " in f" {' '.join(words(str(target.get(key, ''))))} "
        for key in ("label", "application")
    )


async def resolve_target(ctx, adapter, query, instruction=""):
    s, task = ctx.session, ctx.task
    if task.capability in ("search", "agent"):
        return None  # Search has no target; the agent resolves targets per delegated step.
    selected = s.selected.get(task.capability)
    if hasattr(adapter, "choose"):
        # The capability owns its default (e.g. Browser prefers the user's own Chrome).
        choice_id = await adapter.choose(query, selected)
        ctx.check()
        if selected and selected.get("choice_id") == choice_id:
            return dict(selected)
        target = await adapter.select(choice_id)
        ctx.check()
        target = {**target, "choice_id": choice_id, "target_ref": secrets.token_urlsafe(24)}
        s.selected[task.capability] = target
        s.hub.publish(
            "target.selected", {"capability": task.capability, "label": target["label"], "automatic": True}
        )
        return dict(target)
    if selected and (not query or matches(query, selected)):
        # Adapters revalidate identity immediately before any action.
        return dict(selected)
    choices = await adapter.targets()
    ctx.check()
    candidates = [t for t in choices if matches(query, t)] if query else choices
    if len(candidates) > 1:
        # Every window on this PC is a valid target, so the instruction itself says which one it means.
        candidates = [
            t for t in candidates if str(t.get("label", "")).casefold() not in NOISE_LABELS
        ] or candidates
        if instruction and (chosen := pick_from_instruction(instruction, candidates)):
            candidates = [chosen]
    if len(candidates) != 1:
        task.choices = [
            {"choice_id": t["choice_id"], "label": t["label"], "capability": task.capability}
            for t in candidates[:30]
        ]
        if not candidates:
            reason = (
                f'No available {task.capability} target matches "{query}".'
                if query
                else f"No {task.capability} target is available."
            )
            raise Fault(
                "TARGET_UNAVAILABLE", reason + " Open the application or configure the workspace, then retry."
            )
        labels = "; ".join(f"{i + 1}. {t['label']}" for i, t in enumerate(candidates[:8]))
        raise Fault("TARGET_AMBIGUOUS", f"Which {task.capability} target should I use? {labels}")
    target = await adapter.select(candidates[0]["choice_id"])
    ctx.check()
    target = {**target, "target_ref": secrets.token_urlsafe(24)}
    s.selected[task.capability] = target
    s.hub.publish(
        "target.selected", {"capability": task.capability, "label": target["label"], "automatic": True}
    )
    return dict(target)
