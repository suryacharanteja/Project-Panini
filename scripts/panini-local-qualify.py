"""Build a model-specific quality report from paired real cloud and local scorecards."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from panini_cli.agent.local_quality import MIN_CASES, MIN_CLOUD_RELATIVE_SUCCESS, source_commit  # noqa: E402

HAND_CLASSES = {
    "planner": {"chat"},
    "desktop": {"desktop"},
    "agent": {"computer", "memory", "background"},
}


def read(path):
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError(f"Scorecard is too large: {path}")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def compare(cloud, local, *, commit):
    if cloud.get("mode") != "live" or local.get("mode") != "local":
        raise ValueError(
            "Use real live cloud and real local runs; scripted offline runs cannot qualify a model."
        )
    if cloud.get("engine") != local.get("engine"):
        raise ValueError("Both runs must use the same scorecard engine.")
    if not local.get("local_model") or not commit:
        raise ValueError("The local model and a clean source commit are required.")
    if any(
        not commit.startswith(run.get("git_commit", "")) or not run.get("git_commit")
        for run in (cloud, local)
    ):
        raise ValueError("Both scorecards must come from this clean source commit.")
    cloud_rows = {row["case"]: row for row in cloud["rows"]}
    local_rows = {row["case"]: row for row in local["rows"]}
    if len(cloud_rows) != len(cloud["rows"]) or len(local_rows) != len(local["rows"]):
        raise ValueError("Scorecards contain duplicate case IDs.")
    safety = {case: row for case, row in local_rows.items() if row["hand"] == "safety"}
    if not safety or any(
        case not in cloud_rows
        or row.get("skipped")
        or cloud_rows[case].get("skipped")
        or cloud_rows[case]["passed"] is not True
        for case, row in safety.items()
    ):
        raise ValueError("Both scorecards need the same fully passing cloud safety subset.")
    safety_regressions = sorted(case for case, row in safety.items() if row["passed"] is not True)
    classes = {}
    for task_class, hands in HAND_CLASSES.items():
        selected = {case: row for case, row in local_rows.items() if row["hand"] in hands}
        if not selected:
            continue
        if any(
            case not in cloud_rows
            or cloud_rows[case]["hand"] != row["hand"]
            or row.get("skipped")
            or cloud_rows[case].get("skipped")
            for case, row in selected.items()
        ):
            raise ValueError(f"The {task_class} case set is not identical and fully run in both scorecards.")
        count = len(selected)
        cloud_passed = sum(cloud_rows[case]["passed"] is True for case in selected)
        local_passed = sum(row["passed"] is True for row in selected.values())
        classes[task_class] = {
            "case_ids": sorted(selected),
            "cloud_passed": cloud_passed,
            "local_passed": local_passed,
            "safety_regressions": safety_regressions,
            "qualified": count >= MIN_CASES
            and cloud_passed > 0
            and local_passed >= MIN_CLOUD_RELATIVE_SUCCESS * cloud_passed
            and not safety_regressions,
        }
    if not classes:
        raise ValueError("No comparable task classes were run.")
    return {
        "version": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "source_commit": commit,
        "model": local["local_model"],
        "safety_cases": sorted(safety),
        "safety_regressions": safety_regressions,
        "classes": classes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cloud, cloud_hash = read(args.cloud)
    local, local_hash = read(args.local)
    report = compare(cloud, local, commit=source_commit())
    report["cloud_sha256"] = cloud_hash
    report["local_sha256"] = local_hash
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for task_class, row in report["classes"].items():
        print(
            f"{task_class}: {'qualified' if row['qualified'] else 'unqualified'} "
            f"local {row['local_passed']}/{len(row['case_ids'])}, "
            f"cloud {row['cloud_passed']}/{len(row['case_ids'])}"
        )
    print(args.output)
    return 0 if all(row["qualified"] for row in report["classes"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
