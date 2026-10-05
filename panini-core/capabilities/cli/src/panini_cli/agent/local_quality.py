"""Comparative qualification boundary for effect-capable local model tiers."""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

CLASSES = frozenset({"planner", "desktop", "agent"})
MIN_CASES = 8
MIN_CLOUD_RELATIVE_SUCCESS = 0.8
_evaluating = ContextVar("panini_local_quality_evaluating", default=False)


def evaluation_active() -> bool:
    return _evaluating.get()


@contextmanager
def evaluation_scope():
    """Let the isolated scorecard exercise a candidate before it is qualified."""
    token = _evaluating.set(True)
    try:
        yield
    finally:
        _evaluating.reset(token)


def source_commit() -> str:
    roots = [Path(os.environ["PANINI_RUNTIME_ROOT"])] if os.getenv("PANINI_RUNTIME_ROOT") else []
    roots += [Path(sys.executable).resolve().parent, Path.cwd(), *Path(__file__).resolve().parents]
    for root in roots:
        release = root / "release.json"
        if release.is_file():
            try:
                value = json.loads(release.read_text(encoding="utf-8"))
                if value.get("sourceDirty") is False and len(value.get("sourceCommit", "")) == 40:
                    return value["sourceCommit"]
            except (OSError, ValueError):
                pass
        if (root / ".git").exists():
            try:
                clean = subprocess.run(
                    ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=3
                )
                commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=3
                )
                if clean.returncode == commit.returncode == 0 and not clean.stdout.strip():
                    return commit.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
            return ""
    return ""


def qualified(task_class: str) -> tuple[bool, str]:
    if task_class not in CLASSES:
        raise ValueError("Unknown local task class")
    if evaluation_active():
        return True, "isolated-evaluation"
    path = Path(os.getenv("PANINI_LOCAL_QUALITY_REPORT", ""))
    if not path.is_absolute() or not path.is_file():
        return False, "Run the paired cloud/local scorecard for this model before using local tasks."
    try:
        if path.stat().st_size > 65536:
            raise ValueError("report too large")
        report = json.loads(path.read_text(encoding="utf-8"))
        model = os.getenv("PANINI_LOCAL_MODEL", "").strip()
        commit = source_commit()
        row = report["classes"][task_class]
        cases = row["case_ids"]
        count = len(cases)
        cloud = row["cloud_passed"]
        local = row["local_passed"]
        if (
            report["version"] != 1
            or not model
            or report["model"] != model
            or not commit
            or report["source_commit"] != commit
            or not isinstance(report["safety_cases"], list)
            or not report["safety_cases"]
            or report["safety_regressions"]
            or not isinstance(cases, list)
            or len(set(cases)) != count
            or count < MIN_CASES
            or type(cloud) is not int
            or type(local) is not int
            or not 0 < cloud <= count
            or not 0 <= local <= count
            or row["safety_regressions"]
        ):
            raise ValueError("report does not qualify this model and source")
        if local / count < MIN_CLOUD_RELATIVE_SUCCESS * cloud / count:
            return False, "This local model scored below 80% of the cloud baseline for this task class."
        return True, "qualified"
    except (OSError, ValueError, KeyError, TypeError):
        return False, "The local quality report is invalid or stale; rerun the paired scorecard."
