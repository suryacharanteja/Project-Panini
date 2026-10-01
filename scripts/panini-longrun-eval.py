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
        due = state["started"] + index * args.interval_hours * 3600
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
        }
        if exit_code == 0 and output.exists():
            rows = json.loads(output.read_text(encoding="utf-8"))
            sample["cases"] = len(rows)
            sample["passed"] = sum(bool(row.get("passed")) for row in rows)
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
        state["samples"].append(sample)
        save(state_path, state)
        print(json.dumps(sample), flush=True)
        if args.once:
            break
    return (
        0
        if all(
            s["exit_code"] == 0 and s.get("baseline_gate_passed", True) for s in state["samples"][: args.runs]
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
