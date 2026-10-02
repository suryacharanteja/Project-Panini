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
