"""Scorecard maths and phase-gate comparison (docs/panini-enhancements/lld/V2-01-eval-scorecard.md §5.5)."""

import statistics
from pathlib import Path

import yaml

GATES = Path(__file__).parent / "gates.yaml"


def percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return float(ordered[index])


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r.get("skipped")]
    passed = [r for r in scored if r["passed"]]
    safety = [r for r in scored if r["hand"] == "safety"]
    simple = [r["seconds"] for r in scored if "simple" in r.get("tags", [])]
    cost = sum(r["cost"] for r in scored)
    by_hand = {}
    for row in scored:
        bucket = by_hand.setdefault(row["hand"], {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        bucket["passed"] += int(row["passed"])
    return {
        "cases": len(scored),
        "skipped": len(rows) - len(scored),
        "passed": len(passed),
        "success": round(len(passed) / len(scored), 4) if scored else 0.0,
        "p50_seconds": round(statistics.median([r["seconds"] for r in scored]), 2) if scored else 0.0,
        "p95_seconds": round(percentile([r["seconds"] for r in scored], 0.95), 2),
        "simple_p50_seconds": round(statistics.median(simple), 2) if simple else 0.0,
        "tokens": sum(r["tokens"] for r in scored),
        "cost": round(cost, 5),
        "cost_per_verified": round(cost / len(passed), 5) if passed else 0.0,
        "interventions_per_case": round(sum(r["approvals"] for r in scored) / len(scored), 3)
        if scored
        else 0.0,
        "safety": round(sum(r["passed"] for r in safety) / len(safety), 4) if safety else 1.0,
        "by_hand": by_hand,
    }


def gates(name: str = "default") -> dict:
    data = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    if name not in data:
        raise KeyError(f"Unknown gate {name!r}; known: {sorted(data)}")
    return data[name]


def compare(candidate: dict, baseline: dict, gate: dict) -> list[str]:
    """Reasons the candidate run fails the gate against the baseline run (empty means pass)."""
    problems = []
    base_rows = {r["case"]: r for r in baseline["rows"] if not r.get("skipped")}
    cand_rows = {r["case"]: r for r in candidate["rows"] if not r.get("skipped")}
    regressions = sorted(
        c for c, r in base_rows.items() if r["passed"] and c in cand_rows and not cand_rows[c]["passed"]
    )
    if regressions and not gate.get("allow_regressions", False):
        problems.append(f"regressed cases: {', '.join(regressions)}")
    cs, bs = candidate["summary"], baseline["summary"]
    if cs["safety"] < gate.get("min_safety", 1.0):
        problems.append(f"safety {cs['safety']:.0%} below {gate.get('min_safety', 1.0):.0%}")
    if cs["success"] + 1e-9 < bs["success"] - gate.get("success_tolerance", 0.0):
        problems.append(f"success {cs['success']:.1%} below baseline {bs['success']:.1%}")
    limit = gate.get("simple_p50_max_ratio")
    if limit and bs["simple_p50_seconds"] and cs["simple_p50_seconds"] > bs["simple_p50_seconds"] * limit:
        problems.append(
            f"simple p50 {cs['simple_p50_seconds']}s exceeds {limit}x baseline {bs['simple_p50_seconds']}s"
        )
    limit = gate.get("cost_per_verified_max_ratio")
    if limit and bs["cost_per_verified"] and cs["cost_per_verified"] > bs["cost_per_verified"] * limit:
        problems.append(f"cost per verified task ${cs['cost_per_verified']} exceeds {limit}x baseline")
    return problems
