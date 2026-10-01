import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from backend import comparison
from backend.task_catalog import TASKS


def test_providers_start_concurrently(monkeypatch):
    barrier = threading.Barrier(2, timeout=2)

    def classify(*args):
        barrier.wait()
        return {"result": "ok"}

    monkeypatch.setattr(comparison, "direct_llm_classify", classify)
    monkeypatch.setattr(comparison, "jev_classify", classify)
    result = asyncio.run(comparison.compare("sample", "sentiment"))
    assert result["direct"]["result"] == result["jev"]["result"] == "ok"


def test_direct_uses_same_questions_and_usage_cost(monkeypatch):
    probabilities = {key: 0.7 for key in TASKS["sentiment"].questions}

    def create(**kwargs):
        assert set(json.loads(kwargs["messages"][1]["content"])["questions"]) == set(probabilities)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps({"answers": probabilities})))
            ],
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=100,
                prompt_tokens_details=SimpleNamespace(cached_tokens=200),
            ),
        )

    monkeypatch.setattr(
        comparison,
        "get_openai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    result = comparison.direct_llm_classify("great product", "sentiment")
    assert result["cost"] == pytest.approx(0.000195)
    assert result["tokens"] == 1100
    assert set(result["answers"]) == set(probabilities)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 1.1, True, "0.9"])
def test_invalid_probabilities_rejected(value):
    with pytest.raises(ValueError):
        comparison.probability(value)


def test_missing_jev_answers_report_failure(monkeypatch):
    monkeypatch.setattr(comparison.jev_client, "ask", lambda *args: {"answers": {}})
    result = comparison.jev_classify("hello", "sentiment")
    assert "error" in result
    assert result["is_real"] is False
