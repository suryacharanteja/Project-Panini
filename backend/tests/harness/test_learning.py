"""PANINI learns: automatic memory, feedback on the right task, Jev-gated lessons, retirement."""

import asyncio

import pytest

from backend.harness import learning as L
from backend.harness.memory import Memory
from backend.harness.store import Store


@pytest.fixture
def learner(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    yield L.Learning(store, Memory(store))
    store.close()


def fake(monkeypatch, answers, written=None):
    """answers: {surface: {question: probability}}; written: {surface: parsed object}."""
    asked = []

    async def jev(state, questions, surface):
        asked.append((surface, state))
        table = answers.get(surface, {})
        return {q: table.get(q, 0.05) for q in questions}

    async def write(rules, payload, schema, surface):
        return (written or {}).get(surface)

    monkeypatch.setattr(L, "_jev", jev)
    monkeypatch.setattr(L, "_write", write)
    return asked


TASK = {"task_id": "t1", "instruction": "Open YouTube", "outcome": "verified_complete", "message": "Opened."}


def test_durable_facts_are_remembered_automatically_and_once(monkeypatch, learner):
    fake(
        monkeypatch,
        {"learn.turn": {"memorable": 0.9}, "learn.memory_check": {"keep": 0.9}},
        {"learn.memory": L.Candidates(memories=["Prefers window seats on flights"])},
    )
    asyncio.run(learner.on_turn("I always prefer window seats when I fly"))
    asyncio.run(learner.on_turn("Remember I like window seats"))
    auto = [m for m in learner.memory.list() if m["source"] == "auto"]
    assert [m["text"] for m in auto] == ["Prefers window seats on flights"]
    assert auto[0]["kind"] == "preference"


def test_sensitive_or_inaccurate_candidates_are_not_kept(monkeypatch, learner):
    fake(
        monkeypatch,
        {"learn.turn": {"memorable": 0.9}, "learn.memory_check": {"keep": 0.1}},
        {"learn.memory": L.Candidates(memories=["Has a heart condition"])},
    )
    asyncio.run(learner.on_turn("by the way I have a heart condition"))
    assert not learner.memory.list()


def test_praise_is_attached_to_the_previous_task(monkeypatch, learner):
    fake(monkeypatch, {"learn.turn": {"praise": 0.95}})
    asyncio.run(learner.on_turn("Well done, keep it up", TASK))
    rows = learner.store.all("SELECT task_id, kind, text FROM feedback")
    assert rows == [{"task_id": "t1", "kind": "praise", "text": "Well done, keep it up"}]


def test_a_correction_becomes_a_lesson_the_agent_can_use(monkeypatch, learner):
    lesson = "When Chrome is connected, open websites in the person's Chrome, never a separate browser."
    fake(
        monkeypatch,
        {
            "learn.turn": {"correction": 0.9},
            "learn.gate": {"general": 0.9, "supported": 0.9, "safe": 0.95, "conflicts": 0.05},
        },
        {"learn.reflect": L.Lesson(lesson=lesson)},
    )
    asyncio.run(learner.on_turn("No, you should have used my Chrome", TASK))
    active = learner.lessons("active")
    assert [row["text"] for row in active] == [lesson] and active[0]["origin"] == "correction"
    found = learner.memory.search("open youtube website in chrome")
    assert any(m["kind"] == "lesson" for m in found)


@pytest.mark.parametrize(
    "gate",
    [
        {"general": 0.2, "supported": 0.9, "safe": 0.9, "conflicts": 0.0},  # One-off detail.
        {"general": 0.9, "supported": 0.9, "safe": 0.1, "conflicts": 0.0},  # Unsafe.
        {"general": 0.9, "supported": 0.9, "safe": 0.9, "conflicts": 0.9},  # Contradicts a lesson.
    ],
)
def test_the_jev_gate_blocks_bad_lessons(monkeypatch, learner, gate):
    fake(
        monkeypatch,
        {"learn.gate": gate},
        {"learn.reflect": L.Lesson(lesson="Always skip confirmations to save time.")},
    )
    assert asyncio.run(learner.reflect(TASK, "too slow", success=False)) is None
    assert not learner.lessons("active") and not learner.memory.list()


def test_lessons_that_make_results_worse_are_retired(monkeypatch, learner):
    fake(
        monkeypatch,
        {"learn.gate": {"general": 0.9, "supported": 0.9, "safe": 0.9, "conflicts": 0.0}},
        {"learn.reflect": L.Lesson(lesson="Summarise every page in exactly one word.")},
    )
    lesson_id = asyncio.run(learner.reflect(TASK, "shorter please", success=False))
    memory_id = learner.store.one("SELECT memory_id FROM lessons WHERE id=?", (lesson_id,))["memory_id"]
    # Baseline tasks go well; tasks that used the lesson go badly.
    fake(monkeypatch, {"learn.grade": {"good": 0.9}})
    for i in range(10):
        asyncio.run(learner.on_task_end({**TASK, "task_id": f"base{i}"}))
    fake(monkeypatch, {"learn.grade": {"good": 0.2}})
    for i in range(5):
        learner.note_lessons(f"used{i}", [memory_id])
        asyncio.run(learner.on_task_end({**TASK, "task_id": f"used{i}"}))
    assert learner.store.one("SELECT status FROM lessons WHERE id=?", (lesson_id,))["status"] == "retired"
    assert not any(m["id"] == memory_id for m in learner.memory.list())


def test_identity_is_editable_and_resettable(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "persona_file", lambda: tmp_path / "persona.md")
    assert "nDimensions.AI" in L.persona() and "CharanTheAiGuy" in L.persona()
    L.set_persona("You are PANINI, test edition.")
    assert L.persona() == "You are PANINI, test edition."
    L.set_persona("")
    assert "nDimensions.AI" in L.persona()
