"""Bounded display projection. Activity is never proof of task completion."""

import json
import re
import time


def display_text(value, limit=1500):
    text = str(value or "")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[redacted]", text)
    text = re.sub(
        r"(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[redacted]", text
    )
    return text[-limit:]


class CodeActivity:
    def __init__(self, workspace, model):
        self.value = {
            "workspace": display_text(workspace, 500),
            "model": model,
            "started_at": time.time(),
            "updated_at": time.time(),
            "items": [],
            "trimmed": False,
        }

    def update(self, record):
        self.value["updated_at"] = time.time()
        if record.get("type") == "execution.started":
            self.value["model"] = display_text(record.get("model"), 100)
            return self.value
        identifier = display_text(record.get("item_id"), 100)
        kinds = {"commandExecution", "fileChange", "agentMessage", "dynamicToolCall"}
        if not identifier or record.get("kind") not in kinds:
            return self.value
        items = self.value["items"]
        row = next((item for item in items if item["id"] == identifier), None)
        if row is None:
            row = {"id": identifier, "kind": record.get("kind"), "status": "running", "output": ""}
            items.append(row)
        if record.get("type") == "item.delta":
            row["output"] = display_text(row["output"] + str(record.get("delta", "")))
        if record.get("command"):
            row["command"] = display_text(record["command"], 500)
        if record.get("summary"):
            row["command"] = display_text(record["summary"], 300)  # A read: what the agent inspected.
        if record.get("paths"):
            row["paths"] = [display_text(p, 160) for p in record["paths"][:5]]
        if record.get("type") == "item.completed":
            row["status"] = record.get("status") or "completed"
            if "text" in record:
                row["output"] = display_text(record["text"])
            if receipt := record.get("receipt"):
                row.update(
                    command=display_text(receipt.get("command"), 500),
                    output=display_text(receipt.get("output")),
                    exit_code=receipt.get("exit_code"),
                )
        while len(items) > 12 or len(json.dumps(self.value, ensure_ascii=False).encode()) > 48000:
            items.pop(0)
            self.value["trimmed"] = True
        return self.value
