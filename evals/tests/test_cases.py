from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import cases
from evals.checks import evaluate, read_text


def test_repository_cases_are_valid_and_unique():
    loaded = cases.load()
    assert len(loaded) >= 36
    assert len({c.id for c in loaded}) == len(loaded)
    assert len(cases.load(mode="offline")) >= 16
    assert all(c.script for c in cases.load(mode="offline"))


def test_real_local_comparison_requires_completed_answers_and_reviewed_effects():
    paired = cases.load(mode="local")
    chat = [case for case in paired if case.hand == "chat"]
    safety = [case for case in paired if case.hand == "safety"]
    assert len(chat) >= 8 and safety
    assert all(case.expect.get("outcome_in") for case in chat)
    assert all(
        set(case.expect["outcome_in"]) <= {"verified_complete", "reply"}
        for case in chat
        if "clarify" not in case.id
    )
    assert all(case.expect.get("approvals_requested_min", 0) >= 1 for case in safety)
    assert all(case.expect.get("file_exists") for case in safety)


def write(tmp_path, hand, body):
    folder = tmp_path / hand
    folder.mkdir(exist_ok=True)
    path = folder / "case.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_validation_errors(tmp_path):
    with pytest.raises(cases.CaseError, match="unknown hand"):
        cases.parse(write(tmp_path, "nowhere", "id: x\n"))
    with pytest.raises(cases.CaseError, match="must start with"):
        cases.parse(write(tmp_path, "chat", "id: x.y\ntitle: t\nturn: t\nmodes: [live]\nexpect: {}\n"))
    with pytest.raises(cases.CaseError, match="scripted model"):
        cases.parse(write(tmp_path, "chat", "id: chat.y\ntitle: t\nturn: t\nmodes: [offline]\nexpect: {}\n"))
    with pytest.raises(cases.CaseError, match="unknown expectations"):
        cases.parse(
            write(tmp_path, "chat", "id: chat.y\ntitle: t\nturn: t\nmodes: [live]\nexpect: {wish: 1}\n")
        )


def test_placeholders_expand_in_keys_and_values(tmp_path):
    assert cases.expand({"{work}/a": ["{work}/b"]}, Path("W")) == {"W/a": ["W/b"]}


def test_checks(tmp_path):
    (tmp_path / "u16.txt").write_bytes("buy milk".encode("utf-16"))
    assert read_text(tmp_path / "u16.txt") == "buy milk"
    result = SimpleNamespace(
        outcome="verified_complete",
        message="Total: 6,950",
        seconds=2.0,
        approvals_requested=0,
        evidence_count=1,
        task_status="completed",
    )
    assert (
        evaluate({"outcome_in": ["verified_complete"], "message_contains_any": ["6950"]}, result, tmp_path)
        == []
    )
    failures = evaluate(
        {"file_exists": ["{work}/missing"], "approvals_requested_min": 1, "max_seconds": 1}, result, tmp_path
    )
    assert len(failures) == 3


def test_persona_cases_declare_specialists_and_their_runs(tmp_path):
    from evals.engines import TurnResult

    folder = tmp_path / "personas"
    folder.mkdir()
    bad = folder / "bad.yaml"
    bad.write_text(
        "\n".join(
            [
                "id: personas.bad",
                "title: t",
                "modes: [offline]",
                "turn: x",
                "script: [{text: hi}]",
                "personas: [{id: scout, permissions: [computer]}]",
                "expect: {}",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(cases.CaseError, match="personas need"):
        cases.parse(bad)  # Grants are never widened by a case file.
    loaded = [c for c in cases.load() if c.hand == "personas"]
    assert {c.id for c in loaded} >= {"personas.offline-delegation", "personas.offline-isolation"}
    assert all(c.personas for c in loaded)
    ran = TurnResult(personas_ran=["panini", "scout"])
    assert evaluate({"personas_ran": ["scout"], "personas_not_ran": ["writer"]}, ran, tmp_path) == []
    assert evaluate({"personas_ran": ["writer"]}, ran, tmp_path) == ["personas ['writer'] did not run"]
