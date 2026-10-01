"""Regression checks for the offline capability benchmark report."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "panini-eval-report.py"
SPEC = importlib.util.spec_from_file_location("panini_eval_report", SCRIPT)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def row(name, passed=True):
    return {
        "case": name,
        "passed": passed,
        "seconds": 2,
        "tokens": 10,
        "cost": 0.01,
        "approval_requests": 1,
        "evidence_count": 1,
    }


def test_report_detects_regressed_verified_solve_and_case_drift(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps([row("a"), row("b", False)]), encoding="utf-8")
    candidate_path.write_text(json.dumps([row("a", False), row("c")]), encoding="utf-8")
    baseline, candidate = report.load(baseline_path), report.load(candidate_path)
    assert report.compare(candidate, baseline) == {
        "missing": ["b"],
        "added": ["c"],
        "regressions": ["a"],
        "gate_passed": False,
    }
    assert report.summary(candidate)["evidence_backed_solves"] == 1


def test_report_rejects_duplicate_or_malformed_results(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([row("a"), row("a")]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        report.load(path)
    path.write_text(json.dumps([{**row("a"), "passed": "yes"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="boolean"):
        report.load(path)
