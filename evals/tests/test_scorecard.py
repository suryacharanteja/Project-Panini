from evals.scorecard import compare, gates, summarize


def row(case, passed, seconds=1.0, cost=0.01, hand="chat", tags=("simple",), approvals=0):
    return {
        "case": case,
        "passed": passed,
        "seconds": seconds,
        "cost": cost,
        "tokens": 10,
        "hand": hand,
        "tags": list(tags),
        "approvals": approvals,
        "skipped": "",
    }


def run(rows):
    return {"engine": "v1", "mode": "offline", "rows": rows, "summary": summarize(rows)}


def test_summary_maths_and_skips():
    rows = [
        row("a", True, 1),
        row("b", False, 3),
        row("s", True, hand="safety"),
        {**row("x", False), "skipped": "needs desktop"},
    ]
    s = summarize(rows)
    assert (s["cases"], s["passed"], s["skipped"]) == (3, 2, 1)
    assert s["safety"] == 1.0 and s["p50_seconds"] == 1.0
    assert s["cost_per_verified"] == 0.015


def test_identical_runs_pass_and_regressions_fail():
    base = run([row("a", True), row("b", True)])
    assert compare(base, base, gates("phase1")) == []
    worse = run([row("a", True), row("b", False)])
    problems = compare(worse, base, gates("phase1"))
    assert any("regressed cases: b" in p for p in problems)


def test_latency_safety_and_cost_gates():
    base = run([row("a", True, seconds=1.0, cost=0.01)])
    slow = run([row("a", True, seconds=1.5, cost=0.02), row("s", False, hand="safety")])
    problems = " ".join(compare(slow, base, gates("phase1")))
    assert "simple p50" in problems and "safety" in problems and "cost per verified" in problems
