"""Deterministic candidate inputs. The owner can edit constants and expectations in review."""

import copy
import re

from .schema import PARAM


def propose(steps):
    steps = copy.deepcopy(steps)
    inputs = {}
    for step in steps:
        args = step.get("args", {})
        field = {
            "browser_type": "text",
            "desktop_type": "text",
            "browser_select_option": "values",
            "browser_file_upload": "paths",
        }.get(step["tool"])
        if not field or field not in args:
            continue
        values = args[field] if isinstance(args[field], list) else [args[field]]
        replacement = []
        for value in values:
            if not isinstance(value, str) or PARAM.search(value):
                replacement.append(value)
                continue
            base = (
                re.sub(r"[^a-z0-9]+", "_", step.get("target", {}).get("name", "value").lower()).strip("_")[
                    :30
                ]
                or "value"
            )
            name = base
            if not name[0].isalpha():
                name = base = "input_" + name
            count = 2
            while name in inputs:
                name = f"{base}_{count}"
                count += 1
            kind = "file" if field == "paths" else "text"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                kind = "date"
            elif re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                kind = "email"
            elif re.fullmatch(r"-?\d+(\.\d+)?", value):
                kind = "number"
            inputs[name] = {"default": value, "kind": kind, "required": True}
            replacement.append("{{" + name + "}}")
        args[field] = replacement if isinstance(args[field], list) else replacement[0]
    return {"version": 1, "kind": "browser", "steps": steps, "expect": [], "inputs": inputs}
