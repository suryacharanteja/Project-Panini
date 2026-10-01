import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "panini-longrun-eval.py"
SPEC = importlib.util.spec_from_file_location("panini_longrun_eval", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_delayed_daily_sample_does_not_catch_up_with_back_to_back_runs():
    state = {"started": 0, "interval_hours": 24, "samples": [{"started": 200000}]}
    assert evaluation.next_due(state) == 286400


def test_empty_failed_duplicate_and_malformed_results_fail_the_gate():
    good = {"case": "one", "passed": True}
    assert evaluation.check_rows([good], 1)
    assert not evaluation.check_rows([], 1)
    assert not evaluation.check_rows([good, good], 2)
    assert not evaluation.check_rows([{**good, "passed": False}], 1)
    assert not evaluation.check_rows([{**good, "case": {}}], 1)


def test_process_success_with_failed_cases_is_reported_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        evaluation.sys,
        "argv",
        [
            str(SCRIPT),
            "--runs",
            "1",
            "--interval-hours",
            "0",
            "--only",
            "1",
            "--state-dir",
            str(tmp_path),
            "--once",
        ],
    )
    monkeypatch.setattr(evaluation, "source_revision", lambda: {"commit": "a" * 40, "dirty": False})

    def run(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps([{"case": "one", "passed": False}]), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(evaluation.subprocess, "run", run)
    assert evaluation.main() == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["samples"][0]["case_gate_passed"] is False
    assert state["samples"][0]["source"]["commit"] == "a" * 40
