"""Repeat live PANINI capability checks over elapsed time, resuming after interruption.

Example: python scripts/panini-longrun-eval.py --runs 3 --interval-hours 24 --only 1,4,10
The same state directory can be passed again after a restart. Provider calls cost money.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def source_revision():
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return {"commit": commit.stdout.strip(), "dirty": bool(status.stdout.strip())}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def check_rows(rows, expected_count):
    return (
        isinstance(rows, list)
        and len(rows) == expected_count
        and all(
            isinstance(row, dict)
            and row.get("passed") is True
            and isinstance(row.get("case"), str)
            and row["case"]
            for row in rows
        )
        and len({row.get("case") for row in rows}) == expected_count
    )


def next_due(state):
    # Delayed runs must not compress several daily samples into one session.
    previous = state["samples"][-1]["started"] if state["samples"] else state["started"]
    return previous + (state["interval_hours"] * 3600 if state["samples"] else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--interval-hours", type=float, default=24)
    parser.add_argument("--only", default="1,4,10")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "artifacts" / "longrun-eval")
    parser.add_argument("--once", action="store_true", help="Run at most one due sample, then exit")
    args = parser.parse_args()
    if not 1 <= args.runs <= 30 or not 0 <= args.interval_hours <= 168:
        parser.error("Use 1-30 runs and an interval of 0-168 hours.")
    try:
        selected = {int(value) for value in args.only.split(",")}
    except ValueError:
        parser.error("Cases must be comma-separated positive integers.")
    if not selected or min(selected) < 1:
        parser.error("Cases must be comma-separated positive integers.")
    state_dir = args.state_dir.resolve()
    state_path = state_dir / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["only"] != args.only or state["interval_hours"] != args.interval_hours:
            parser.error("The existing state directory uses different cases or interval.")
    else:
        state = {
            "started": time.time(),
            "only": args.only,
            "interval_hours": args.interval_hours,
            "samples": [],
        }
        save(state_path, state)
    if args.runs < len(state["samples"]):
        parser.error("Requested fewer runs than this state directory already contains.")
    for index in range(len(state["samples"]), args.runs):
        due = next_due(state)
        if args.once and due > time.time():
            print(json.dumps({"next_due": due, "completed": len(state["samples"])}))
            break
        while (remaining := due - time.time()) > 0:
            time.sleep(min(remaining, 60))
        output = state_dir / f"sample-{index + 1:02d}.json"
        command = [
            sys.executable,
            str(ROOT / "scripts" / "panini-capability-eval.py"),
            "--only",
            args.only,
            "--output",
            str(output),
        ]
        started = time.time()
        revision = source_revision()
        try:
            result = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, timeout=3600, check=False
            )
            error = result.stderr[-1000:] if result.returncode else ""
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            error, exit_code = "The live eval exceeded one hour.", 124
        sample = {
            "index": index + 1,
            "started": started,
            "finished": time.time(),
            "exit_code": exit_code,
            "output": str(output),
            "error": error,
            "source": revision,
        }
        if output.exists():
            try:
                rows = json.loads(output.read_text(encoding="utf-8"))
                sample["cases"] = len(rows) if isinstance(rows, list) else 0
                sample["passed"] = (
                    sum(row.get("passed") is True for row in rows if isinstance(row, dict))
                    if isinstance(rows, list)
                    else 0
                )
                sample["case_gate_passed"] = check_rows(rows, len(selected))
            except (OSError, ValueError):
                sample["case_gate_passed"] = False
        else:
            sample["case_gate_passed"] = False
        if not sample["case_gate_passed"]:
            sample["error"] = (
                sample["error"] or "The result is missing, malformed, incomplete, or contains failed cases."
            )
        if exit_code == 0 and sample["case_gate_passed"]:
            if index and (baseline := state_dir / "sample-01.json").exists():
                comparison = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "panini-eval-report.py"),
                        str(output),
                        "--baseline",
                        str(baseline),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                sample["baseline_gate_passed"] = comparison.returncode == 0
                sample["baseline_comparison"] = comparison.stdout[-4000:]
        state["samples"].append(sample)
        save(state_path, state)
        print(json.dumps(sample), flush=True)
        if args.once:
            break
    return (
        0
        if all(
            s["exit_code"] == 0
            and s.get("passed", 0) == len(selected)
            and s.get("cases", 0) == len(selected)
            and s.get("case_gate_passed", True)
            and s.get("baseline_gate_passed", True)
            for s in state["samples"][: args.runs]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
