"""Normalize bounded Windows recording events; native IDs never become replay selectors."""

import json
import re

from ..hands.snapshot import Node, render, role
from ..harness.memory import _SECRET
from .parameterize import propose
from .schema import SENSITIVE


def target(value):
    if not isinstance(value, dict) or value.keys() - {"type", "name", "ancestors"}:
        return None
    name = value.get("name")
    ancestors = value.get("ancestors", [])
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 500
        or not isinstance(ancestors, list)
        or len(ancestors) > 8
        or not isinstance(value.get("type"), str)
    ):
        return None
    if SENSITIVE.search(json.dumps(value)) or _SECRET.search(json.dumps(value)):
        return None
    result = {"role": role(str(value.get("type", "")).removeprefix("ControlType.")), "name": name}
    result["ancestors"] = []
    for parent in ancestors:
        if not isinstance(parent, dict) or parent.keys() != {"type", "name"}:
            return None
        if (
            not isinstance(parent["name"], str)
            or len(parent["name"]) > 500
            or not isinstance(parent["type"], str)
        ):
            return None
        result["ancestors"].append(
            {"role": role(str(parent["type"]).removeprefix("ControlType.")), "name": parent["name"]}
        )
    return result


def normalize(events, application):
    if not isinstance(events, list) or len(events) > 100 or len(json.dumps(events)) > 500000:
        raise ValueError("Invalid desktop recording events.")
    steps = []
    for event in events:
        item = {
            "id": f"s{len(steps) + 1}",
            "tool": "manual",
            "note": "Review this unsupported or private interaction.",
        }
        grounded = target(event.get("target")) if isinstance(event, dict) else None
        value = event.get("value") if isinstance(event, dict) else None
        if grounded:
            kind = event.get("kind")
            args = {}
            tool = {
                "click": "desktop_click",
                "type": "desktop_type",
                "key": "desktop_press",
                "scroll_up": "desktop_scroll",
                "scroll_down": "desktop_scroll",
                "drag": "desktop_drag",
            }.get(kind)
            drop = target(event.get("drop")) if kind == "drag" else None
            if kind == "drag" and not drop:
                tool = None  # A drag that does not end on a named control is reviewed manually.
            if kind == "type":
                if not isinstance(value, str) or len(value) > 2000 or _SECRET.search(value):
                    tool = None
                else:
                    args = {"text": value, "replace": True}
            elif kind == "key":
                if (
                    not isinstance(value, str)
                    or len(value) > 80
                    or not re.fullmatch(
                        r"(?:(?:Ctrl|Shift|Alt|Win)\+)*(?:[A-Z]|Enter|Tab|Escape|Delete|Arrow(?:Left|Right|Up|Down)|Home|End|PageUp|PageDown)",
                        value,
                    )
                ):
                    tool = None
                else:
                    args = {"key": value}
            elif kind in {"scroll_up", "scroll_down"}:
                args = {"direction": kind.removeprefix("scroll_")}
            if tool:
                item = {"id": item["id"], "tool": tool, "target": grounded, "args": args}
                if drop:
                    item["drop"] = drop
                crop = event.get("crop")
                if kind == "click" and isinstance(crop, str) and 0 < len(crop) <= 110_000:
                    item["_crop"] = crop  # Stored as an artifact by the recorder, never inside the skill.
        steps.append(item)
    if not steps:
        steps = [{"id": "s1", "tool": "manual", "note": "No replayable Windows interaction was recorded."}]
    data = propose(steps)
    data.update(kind="desktop", desktop_application=application)
    return data


def snapshot(value):
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), list) or len(value["nodes"]) > 600:
        raise ValueError("Invalid recording snapshot.")
    rows = []
    privacy = []
    refs = set()
    for item in value["nodes"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid recording snapshot node.")
        if "element_id" in item or "automation_id" in item:
            raise ValueError("Native IDs cannot be saved in recording evidence.")
        ref, depth, raw_states = item.get("ref"), item.get("depth", 0), item.get("states", [])
        if (
            not isinstance(ref, str)
            or not re.fullmatch(r"w[1-9][0-9]{0,3}", ref)
            or ref in refs
            or type(depth) is not int
            or not 0 <= depth <= 30
            or depth > len(privacy)
            or not isinstance(raw_states, list)
            or any(not isinstance(s, str) for s in raw_states)
        ):
            raise ValueError("Invalid recording snapshot identity or hierarchy.")
        refs.add(ref)
        privacy = privacy[:depth]
        states = set(raw_states) & {"disabled", "password", "checked", "selected", "focused", "readonly"}
        name = str(item.get("name", ""))[:500]
        content = item.get("value")
        private = "password" in states or SENSITIVE.search(name) or _SECRET.search(name) or any(privacy)
        privacy.append(bool(private))
        if private:
            name, content = "Private field", None
            states.add("password")
        if content is not None and (not isinstance(content, str) or _SECRET.search(content)):
            content = None
        rows.append(
            Node(
                str(item["ref"]),
                role(str(item["type"]).removeprefix("ControlType.")),
                name,
                content,
                states,
                depth=depth,
            )
        )
    return render(rows)
