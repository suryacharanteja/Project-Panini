"""Validated task definitions shared by both comparison providers."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["boolean"] = "boolean"
    instructions: str = Field(min_length=1)


class Task(BaseModel):
    id: str
    name: str
    description: str
    category: str = ""
    questions: dict[str, Question] = Field(min_length=1)
    examples: list[str] = Field(default_factory=list)


def load_tasks(path: Path) -> dict[str, Task]:
    tasks = [
        Task.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))["experiments"]
    ]
    result = {task.id: task for task in tasks}
    if not tasks or len(result) != len(tasks):
        raise ValueError("Task IDs must be unique and the catalog must not be empty")
    return result


TASKS = load_tasks(Path(__file__).with_name("tasks.json"))
