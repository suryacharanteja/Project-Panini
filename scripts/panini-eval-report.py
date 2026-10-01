"""Compare two PANINI capability eval runs without making provider calls.

Usage: python scripts/panini-eval-report.py candidate.json --baseline baseline.json
Exit status 1 means a previously passed case regressed or the case set changed.
"""

import argparse
import json
import statistics
from pathlib import Path


def load(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: expected a nonempty list of cases")
    by_name = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case"), str):
            raise ValueError(f"{path}: each case needs a name")
        name = row["case"]
        if name in by_name:
            raise ValueError(f"{path}: duplicate case {name!r}")
        if type(row.get("passed")) is not bool:
            raise ValueError(f"{path}: {name!r} needs a boolean passed value")
        for field in ("seconds", "tokens", "cost"):
            value = row.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{path}: {name!r} needs nonnegative {field}")
        by_name[name] = row
    return by_name


def summary(rows):
    values = list(rows.values())
    return {
        "cases": len(values),
        "solved": sum(row["passed"] for row in values),
        "median_seconds": round(statistics.median(row["seconds"] for row in values), 2),
        "total_tokens": sum(row["tokens"] for row in values),
        "total_cost": round(sum(row["cost"] for row in values), 5),
        "approval_requests": sum(
            row.get("approval_requests", len(row.get("declined", []))) for row in values
        ),
        "evidence_backed_solves": sum(row["passed"] and row.get("evidence_count", 0) > 0 for row in values),
    }


def compare(candidate, baseline):
    missing = sorted(baseline.keys() - candidate.keys())
    added = sorted(candidate.keys() - baseline.keys())
    regressions = sorted(
        name
        for name in candidate.keys() & baseline.keys()
        if baseline[name]["passed"] and not candidate[name]["passed"]
    )
    return {
        "missing": missing,
        "added": added,
        "regressions": regressions,
        "gate_passed": not (missing or added or regressions),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, help="Write machine-readable report")
    args = parser.parse_args()
    candidate = load(args.candidate)
    report = {"candidate": summary(candidate)}
    if args.baseline:
        baseline = load(args.baseline)
        report["baseline"] = summary(baseline)
        report["comparison"] = compare(candidate, baseline)
    result = json.dumps(report, indent=2)
    print(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    return 0 if report.get("comparison", {}).get("gate_passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
