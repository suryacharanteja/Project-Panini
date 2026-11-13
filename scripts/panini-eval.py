"""PANINI golden-task scorecard (docs/panini-enhancements/lld/V2-01-eval-scorecard.md).

    .venv\\Scripts\\python.exe scripts/panini-eval.py run --mode offline            # scripted model, no network
    .venv\\Scripts\\python.exe scripts/panini-eval.py run --mode live --engine v1   # real providers (costs money)
    .venv\\Scripts\\python.exe scripts/panini-eval.py compare artifacts/evals/<run>.json --baseline artifacts/evals/baseline-v1.json --gate phase1
    .venv\\Scripts\\python.exe scripts/panini-eval.py list

Each case runs in its own temporary PANINI data folder, so your memories, skills and pairings are untouched.
Approvals are decided per case (decline by default): risky actions are counted, not performed.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT))

from evals import cases, runner, scorecard  # noqa: E402

OUT = ROOT / "artifacts" / "evals"


def cmd_run(args):
    only = {x.strip() for x in args.only.split(",")} if args.only else None
    record = runner.run_sync(args.engine, args.mode, only=only, report=lambda line: print(line, flush=True))
    OUT.mkdir(parents=True, exist_ok=True)
    path = Path(args.output) if args.output else OUT / f"{record['id']}-{args.engine}-{args.mode}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    s = record["summary"]
    print(
        f"\n{s['passed']}/{s['cases']} passed ({s['skipped']} skipped); success {s['success']:.1%}; "
        f"p50 {s['p50_seconds']}s p95 {s['p95_seconds']}s; ${s['cost']:.4f}; safety {s['safety']:.0%}\n→ {path}"
    )
    if args.fail_under is not None and s["success"] < args.fail_under:
        return 1
    return 0 if s["safety"] == 1.0 else 1


def cmd_compare(args):
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    problems = scorecard.compare(candidate, baseline, scorecard.gates(args.gate))
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        s = run["summary"]
        print(
            f"{label:<9} {run['engine']}/{run['mode']} {s['passed']}/{s['cases']} success {s['success']:.1%} "
            f"p50 {s['p50_seconds']}s simple-p50 {s['simple_p50_seconds']}s cost/verified ${s['cost_per_verified']}"
        )
    for problem in problems:
        print("GATE FAIL:", problem)
    print("GATE PASS" if not problems else "")
    return 1 if problems else 0


def cmd_list(_args):
    for case in cases.load():
        print(f"{case.id:<40} {','.join(case.modes):<14} {case.title}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--engine", default="v1", choices=sorted(runner.ENGINES))
    run.add_argument("--mode", default="offline", choices=["offline", "live", "local"])
    run.add_argument("--only", help="Comma-separated case ids")
    run.add_argument("--output")
    run.add_argument("--fail-under", type=float, help="Exit 1 when success is below this fraction")
    run.set_defaults(func=cmd_run)
    compare = sub.add_parser("compare")
    compare.add_argument("candidate")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--gate", default="default")
    compare.set_defaults(func=cmd_compare)
    sub.add_parser("list").set_defaults(func=cmd_list)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
