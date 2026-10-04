"""Audit all 28 LLD acceptance lists. Incomplete work cannot pass release promotion."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(root):
    docs = root / "docs/panini-enhancements"
    register = (docs / "live_panini_version2_implementation_register.md").read_text(encoding="utf-8-sig")
    statuses = {}
    for line in register.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and re.fullmatch(r"V2-\d{2}", cells[0]):
            statuses[cells[0]] = cells[-1]
    items = []
    for index in range(28):
        identifier = f"V2-{index:02}"
        files = sorted((docs / "lld").glob(f"{identifier}-*.md"))
        item = {
            "id": identifier,
            "status": statuses.get(identifier, "Missing"),
            "checks": [],
            "passed": False,
        }
        if len(files) != 1:
            item["reason"] = "Exactly one LLD is required."
            items.append(item)
            continue
        source = files[0].read_text(encoding="utf-8-sig")
        item["document"] = files[0].relative_to(root).as_posix()
        item["sha256"] = hashlib.sha256(files[0].read_bytes()).hexdigest()
        section = re.search(r"^## 8\. Acceptance criteria\s*\n(.*?)(?=^## |\Z)", source, re.M | re.S)
        if not section:
            item["reason"] = "Acceptance criteria are missing."
        else:
            for checked, text in re.findall(r"^- \[([ xX])\] (.*(?:\n[ \t]+[^\n]+)*)", section[1], re.M):
                text = " ".join(text.split())
                deferred = "red-team" in text.lower() or "redteam" in text.lower()
                item["checks"].append(
                    {"text": text, "passed": checked.lower() == "x", "deferred_by_owner": deferred}
                )
            active = [c for c in item["checks"] if not c["deferred_by_owner"]]
            item["passed"] = bool(active) and all(c["passed"] for c in active) and item["status"] == "Done"
            if not item["passed"]:
                item["reason"] = "Implementation or acceptance evidence remains incomplete."
        items.append(item)
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", action="store_true", help="Exit nonzero unless every active LLD passes.")
    parser.add_argument("--expected-commit", help="Require the exact clean release source commit.")
    parser.add_argument("--output", default="artifacts/program/qualification.json")
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip())
    items = audit(ROOT)
    report = {
        "product": "PANINI",
        "kind": "lld-acceptance-audit",
        "at": datetime.now(UTC).isoformat(),
        "sourceCommit": commit,
        "sourceDirty": dirty,
        "productionQualified": False,
        "items": items,
        "redTeam": "disabled and deferred by owner",
        "passed": not dirty
        and all(i["passed"] for i in items)
        and (not args.expected_commit or args.expected_commit == commit),
    }
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("Write evidence under this workspace artifacts directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"LLD acceptance: {sum(i['passed'] for i in items)}/28; {'passed' if report['passed'] else 'incomplete'}."
    )
    for item in items:
        if not item["passed"]:
            active = sum(not c["passed"] and not c["deferred_by_owner"] for c in item["checks"])
            print(f"{item['id']}: {item['status']}; {active} unchecked active criteria.")
    print(f"Evidence: {output}")
    return 1 if args.gate and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
