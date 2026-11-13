"""Run cases through an engine and build a scorecard run record."""

import asyncio
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from . import cases as case_module
from .checks import evaluate
from .engines import ENGINES
from .scorecard import summarize

ROOT = Path(__file__).resolve().parents[1]


def git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _configured(name):
    from backend import config  # Loads .env the way PANINI does.

    return config.configured(name)


def missing_needs(case):
    """Live prerequisites this machine cannot provide right now; such cases are skipped, never passed."""
    missing = []
    for need in case.needs:
        if need == "openai" and not _configured("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")
        elif (
            need in {"desktop", "chrome", "docker", "phone", "android"}
            and os.getenv(f"PANINI_EVAL_{need.upper()}") != "1"
        ):
            missing.append(f"{need} (set PANINI_EVAL_{need.upper()}=1 when available)")
    return missing


async def run(engine_name: str, mode: str, *, only=None, report=print) -> dict:
    if mode == "local" and not os.getenv("PANINI_LOCAL_MODEL", "").strip():
        raise ValueError("Set PANINI_LOCAL_MODEL before a real-local scorecard run.")
    engine = ENGINES[engine_name]()
    selected = case_module.load(mode=mode, only=only)
    rows = []
    for index, case in enumerate(selected, 1):
        missing = missing_needs(case) if mode == "live" else []
        if missing:
            rows.append(_row(case, None, [], skipped=", ".join(missing)))
            report(f"SKIP {index:>2}. {case.id:<36} needs {', '.join(missing)}")
            continue
        result, work = await engine.run(case, mode)
        failures = ([result.error] if result.error else []) + evaluate(case.expect, result, work)
        rows.append(_row(case, result, failures))
        mark = "PASS" if not failures else "FAIL"
        report(
            f"{mark} {index:>2}. {case.id:<36} {result.seconds:>6.1f}s ${result.cost:.4f} "
            f"[{result.outcome}] {'; '.join(failures)[:120]}"
        )
    return {
        "id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6],
        "created": time.time(),
        "engine": engine_name,
        "mode": mode,
        "local_model": os.getenv("PANINI_LOCAL_MODEL", "").strip() if mode == "local" else None,
        "git_commit": git_commit(),
        "flags": {
            k: v for k, v in os.environ.items() if k.startswith("PANINI_V2_") or k == "PANINI_BROWSER_DRIVER"
        },
        "rows": rows,
        "summary": summarize(rows),
    }


def _row(case, result, failures, skipped=""):
    return {
        "case": case.id,
        "hand": case.hand,
        "tags": list(case.tags),
        "passed": bool(result) and not failures and not skipped,
        "skipped": skipped,
        "failures": failures,
        "seconds": result.seconds if result else 0.0,
        "tokens": result.tokens if result else 0,
        "cost": result.cost if result else 0.0,
        "approvals": result.approvals_requested if result else 0,
        "outcome": result.outcome if result else "skipped",
        "route": result.route if result else None,
        "message": (result.message if result else "")[:300],
    }


def run_sync(*args, **kwargs):
    return asyncio.run(run(*args, **kwargs))
