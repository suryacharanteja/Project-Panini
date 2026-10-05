"""Comparative local tier admission; scripted scorecards never create readiness."""

import asyncio
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from panini_cli.agent import local_quality


def report():
    cases = [f"chat.case-{index}" for index in range(8)]
    return {
        "version": 1,
        "model": "test-local",
        "source_commit": "a" * 40,
        "safety_cases": ["safety.delete"],
        "safety_regressions": [],
        "classes": {
            "planner": {
                "case_ids": cases,
                "cloud_passed": 8,
                "local_passed": 7,
                "safety_regressions": [],
            }
        },
    }


def test_quality_report_requires_model_commit_safety_and_eighty_percent(tmp_path, monkeypatch):
    path = tmp_path / "quality.json"
    record = report()
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("PANINI_LOCAL_QUALITY_REPORT", str(path))
    monkeypatch.setenv("PANINI_LOCAL_MODEL", "test-local")
    monkeypatch.setattr(local_quality, "source_commit", lambda: "a" * 40)
    assert local_quality.qualified("planner") == (True, "qualified")
    assert local_quality.qualified("desktop")[0] is False

    record["classes"]["planner"]["local_passed"] = 6
    path.write_text(json.dumps(record), encoding="utf-8")
    assert local_quality.qualified("planner")[0] is False
    record["classes"]["planner"]["local_passed"] = 7
    record["model"] = "different"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert local_quality.qualified("planner")[0] is False
    record["model"] = "test-local"
    record["safety_regressions"] = ["safety.delete"]
    path.write_text(json.dumps(record), encoding="utf-8")
    assert local_quality.qualified("planner")[0] is False


def test_candidate_evaluation_scope_is_temporary(monkeypatch):
    monkeypatch.delenv("PANINI_LOCAL_QUALITY_REPORT", raising=False)
    assert local_quality.qualified("agent")[0] is False
    with local_quality.evaluation_scope():
        assert local_quality.qualified("agent") == (True, "isolated-evaluation")
    assert local_quality.qualified("agent")[0] is False
    with pytest.raises(ValueError, match="Unknown local task class"):
        local_quality.qualified("unknown")


def test_real_local_evaluation_never_loads_saved_cloud_credentials(monkeypatch):
    from panini_cli.agent import auth, config

    monkeypatch.setenv("PANINI_LOCAL_MODE", "fallback")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(auth, "read_key", lambda: pytest.fail("Credential Manager accessed"))
    with local_quality.evaluation_scope():
        config.load_panini_env()
    assert config.configured_key() is False


def test_unqualified_fallback_stays_unavailable_before_model_or_desktop_action(monkeypatch):
    from panini_cli.agent.model import ModelError, create_model

    from backend.capabilities.agent import AgentAdapter
    from backend.capabilities.desktop import DesktopAdapter
    from backend.panini import planning
    from backend.panini.contracts import Fault

    monkeypatch.setenv("PANINI_LOCAL_MODE", "fallback")
    monkeypatch.setenv("PANINI_LOCAL_MODEL", "unqualified-local")
    monkeypatch.setenv("PANINI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "")
    monkeypatch.delenv("PANINI_LOCAL_QUALITY_REPORT", raising=False)
    assert asyncio.run(AgentAdapter(None, {}).availability())["state"] == "unavailable"

    class Pipe:
        async def call(self, request):
            if request["op"] == "health":
                return {"graphical_actions": []}
            raise AssertionError("An unqualified desktop requested a live action")

    desktop = DesktopAdapter(True)
    desktop.pipe = Pipe()
    assert asyncio.run(desktop.availability())["state"] == "unavailable"
    with pytest.raises(ModelError, match="paired cloud/local scorecard"):
        create_model(SimpleNamespace(model="cloud", reasoning_effort=None, request_timeout=5))
    with pytest.raises(Fault, match="paired cloud/local scorecard"):
        asyncio.run(planning.parse("Reply", {"request": "hello"}, planning.Plan))


def test_pair_comparison_rejects_scripted_and_underperforming_runs():
    script = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/panini-local-qualify.py"))
    compare = script["compare"]
    cases = [f"chat.case-{index}" for index in range(8)]
    rows = [{"case": case, "hand": "chat", "passed": True, "skipped": ""} for case in cases]
    rows.append({"case": "safety.delete", "hand": "safety", "passed": True, "skipped": ""})
    cloud = {"mode": "live", "engine": "v1", "git_commit": "a" * 7, "rows": rows}
    local = {
        "mode": "local",
        "engine": "v1",
        "git_commit": "a" * 7,
        "local_model": "test-local",
        "rows": [dict(row) for row in rows],
    }
    result = compare(cloud, local, commit="a" * 40)
    assert result["classes"]["planner"]["qualified"] is True
    local["rows"][0]["passed"] = False
    assert compare(cloud, local, commit="a" * 40)["classes"]["planner"]["qualified"] is True
    local["rows"][1]["passed"] = False
    assert compare(cloud, local, commit="a" * 40)["classes"]["planner"]["qualified"] is False
    local["mode"] = "offline"
    with pytest.raises(ValueError, match="scripted offline"):
        compare(cloud, local, commit="a" * 40)
