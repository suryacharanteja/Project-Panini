"""V2-13 §5.7: remote, phone, channel and MCP-server surfaces refuse to turn on without a passing red-team run."""

import json

from backend import v2flags


def _result(tmp_path, monkeypatch, **fields):
    path = tmp_path / "latest.json"
    if fields:
        path.write_text(json.dumps(fields))
    monkeypatch.setattr(v2flags, "REDTEAM_RESULT", path)


def test_gated_surfaces_stay_off_without_a_result_and_say_why(tmp_path, monkeypatch):
    _result(tmp_path, monkeypatch)
    monkeypatch.setenv("PANINI_PHONE_LINE", "true")
    monkeypatch.setenv("PANINI_CHANNELS", "slack,email")
    assert not v2flags.enabled("PANINI_PHONE_LINE") and v2flags.listed("PANINI_CHANNELS") == set()
    assert "No red-team result" in v2flags.blocked_by_gate("PANINI_PHONE_LINE")
    assert "PANINI_CHANNELS" in v2flags.status()["gate"]["blocked"]


def test_a_failing_or_offline_result_does_not_open_the_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_MCP_SERVER", "true")
    _result(tmp_path, monkeypatch, live=True, cases=20, silent_effects=1, completion=1.0)
    assert not v2flags.enabled("PANINI_MCP_SERVER")
    _result(tmp_path, monkeypatch, live=False, cases=20, silent_effects=0, completion=1.0)
    assert not v2flags.enabled("PANINI_MCP_SERVER")
    _result(tmp_path, monkeypatch, live=True, cases=20, silent_effects=0, completion=0.9)
    assert "90% completion" in v2flags.blocked_by_gate("PANINI_MCP_SERVER")


def test_a_passing_live_result_opens_the_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_MCP_SERVER", "true")
    _result(tmp_path, monkeypatch, live=True, cases=20, silent_effects=0, completion=0.96)
    assert v2flags.enabled("PANINI_MCP_SERVER") and v2flags.blocked_by_gate("PANINI_MCP_SERVER") == ""


def test_ungated_flags_are_unaffected(tmp_path, monkeypatch):
    _result(tmp_path, monkeypatch)
    monkeypatch.setenv("PANINI_SANDBOX", "true")
    assert v2flags.enabled("PANINI_SANDBOX")
