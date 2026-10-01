import asyncio
from types import SimpleNamespace

import anyio
import pytest

from backend import chat_jev, jev_browser_loop


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(
        jev_browser_loop,
        "Tools",
        lambda: SimpleNamespace(registry=SimpleNamespace(registry=SimpleNamespace(actions={}))),
    )
    monkeypatch.setattr(
        jev_browser_loop, "ChatJev", lambda **kwargs: SimpleNamespace(total_cost=lambda: 0, decisions=[])
    )
    return jev_browser_loop.JevBrowserLoop()


def test_startup_failure_releases_browser_slot(loop, monkeypatch):
    async def fail():
        raise RuntimeError("no browser")

    monkeypatch.setattr(loop, "startup", fail)

    async def run():
        events = [event async for event in loop.run_instruction("test")]
        assert events[-1]["event"] == "error"
        assert not loop.loop_active
        assert loop.instruction_history[-1]["outcome"] == "error"

    asyncio.run(run())


def test_concurrent_startup_is_rejected_and_stop_cleans_up(loop, monkeypatch):
    async def run():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def startup():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        monkeypatch.setattr(loop, "startup", startup)
        first = loop.run_instruction("first")
        assert (await anext(first))["event"] == "initialized"
        await entered.wait()
        second = [event async for event in loop.run_instruction("second")]
        assert second[0]["event"] == "error"
        loop.cancel()
        assert (await anext(first))["event"] == "cancelled"
        await first.aclose()
        assert cleaned.is_set()
        assert not loop.loop_active

    asyncio.run(run())


def test_disconnected_consumer_cancels_active_agent(loop, monkeypatch):
    async def run():
        async def startup():
            await asyncio.Event().wait()

        monkeypatch.setattr(loop, "startup", startup)
        stream = loop.run_instruction("test")
        await anext(stream)
        await stream.aclose()
        assert not loop.loop_active
        assert loop._runner is None

    asyncio.run(run())


def test_stop_before_agent_is_scheduled_emits_terminal_event(loop):
    async def run():
        stream = loop.run_instruction("test")
        assert (await anext(stream))["event"] == "initialized"
        loop.cancel()
        assert (await asyncio.wait_for(anext(stream), timeout=0.5))["event"] == "cancelled"
        await stream.aclose()
        assert not loop.loop_active

    asyncio.run(run())


def test_http_disconnect_scope_cannot_interrupt_cleanup(loop, monkeypatch):
    async def run():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def startup():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

        monkeypatch.setattr(loop, "startup", startup)
        with anyio.CancelScope() as scope:
            stream = loop.run_instruction("test")
            await anext(stream)
            await entered.wait()
            scope.cancel()
            await stream.aclose()
        if loop._cleanup:
            await asyncio.wait_for(loop._cleanup, timeout=1)
        assert cleaned.is_set()
        assert not loop.loop_active

    asyncio.run(run())


def test_step_limit_is_not_reported_as_success(loop, monkeypatch):
    async def startup():
        return None

    class Agent:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            return SimpleNamespace(
                is_done=lambda: False, is_successful=lambda: False, final_result=lambda: None
            )

    monkeypatch.setattr(loop, "startup", startup)
    monkeypatch.setattr(jev_browser_loop, "Agent", Agent)

    async def run():
        events = [event async for event in loop.run_instruction("test")]
        assert events[-1]["event"] == "incomplete"
        assert events[-1]["success"] is False

    asyncio.run(run())


def test_judge_receives_task_and_dom_and_rejects_unknown_options(monkeypatch):
    monkeypatch.setattr(chat_jev, "ChatOpenAI", lambda **kwargs: None)
    model = chat_jev.ChatJev()
    candidates = [
        chat_jev.ActionCandidate(action_name="click", params_json='{"index":2}', rationale="Press greeting")
    ]
    captured = []

    def ask(state, questions):
        captured.append(state)
        return {"answers": {"option_0": {"probability": 0.8}, "option_999": {"probability": 1}}, "usage": {}}

    monkeypatch.setattr(chat_jev.jev_client, "ask", ask)
    messages = [SimpleNamespace(role="user", content="Task: enter Jev; DOM: button index 2")]
    winner, decision = asyncio.run(model._decide(candidates, messages))
    assert captured[0]["context"][0]["text"] == messages[0].content
    assert winner == candidates[0]
    assert decision["confidence"] == 0.8
    assert (
        model._validate(chat_jev.ActionCandidate(action_name="click", params_json="[]", rationale="bad"))
        is not None
    )


@pytest.mark.parametrize("cancel_after", [None, 12])
def test_managed_continuous_retains_agent_and_cleans_up_after_completion_or_stop(
    loop, monkeypatch, cancel_after
):
    agents = []
    closed = []

    async def nothing(**kwargs):
        pass

    class Agent:
        def __init__(self, **kwargs):
            self.calls = 0
            self.state = SimpleNamespace(stopped=False, consecutive_failures=0, last_result=[])
            self.settings = SimpleNamespace(max_failures=3, step_timeout=1)
            self.history = SimpleNamespace(
                history=[],
                is_done=lambda: self.calls >= 15,
                is_successful=lambda: True,
                final_result=lambda: "All stages completed",
            )
            self.eventbus = SimpleNamespace(stop=nothing)
            self.browser_session = SimpleNamespace(get_current_page_url=nothing, start=nothing)
            agents.append(self)

        async def take_step(self):
            self.calls += 1
            if cancel_after and self.calls > cancel_after:
                await asyncio.Event().wait()
            return self.calls >= 15, True

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(loop, "startup", nothing)
    monkeypatch.setattr(jev_browser_loop, "Agent", Agent)

    async def run():
        events = []
        async for e in loop.run_instruction("Complete the workflow", max_steps=None, timeout_seconds=None):
            events.append(e)
            if cancel_after and e.get("step") == cancel_after:
                loop.cancel()
        return events

    events = asyncio.run(run())
    assert len(agents) == 1
    assert len([e for e in events if e["event"] == "step"]) == (cancel_after or 15)
    assert events[-1]["event"] == ("cancelled" if cancel_after else "complete")
    assert closed == [True] and not loop.loop_active


def test_managed_judge_never_receives_tools_excluded_by_current_sdk_schema(monkeypatch):
    from pydantic import BaseModel, ConfigDict

    class Click(BaseModel):
        model_config = ConfigDict(extra="forbid")
        click: dict

    class Output(BaseModel):
        evaluation_previous_goal: str
        memory: str
        next_goal: str
        action: list[Click]

    monkeypatch.setattr(chat_jev, "ChatOpenAI", lambda **kwargs: None)
    model = chat_jev.ChatJev()
    proposal = chat_jev.CandidateSet(
        evaluation_previous_goal="",
        memory="",
        candidates=[
            chat_jev.ActionCandidate(
                action_name="screenshot", params_json='{"file_name":"test.png"}', rationale="Observe"
            ),
            chat_jev.ActionCandidate(
                action_name="click", params_json='{"index":2}', rationale="Show greeting"
            ),
        ],
    )

    async def propose(messages):
        return proposal

    async def decide(candidates, messages):
        assert [c.action_name for c in candidates] == ["click"]
        return candidates[0], {"cost": 0, "jev_input_tokens": 0}

    monkeypatch.setattr(model, "_propose", propose)
    monkeypatch.setattr(model, "_decide", decide)
    response = asyncio.run(model.ainvoke([], Output))
    assert response.completion.action[0].click == {"index": 2}
    assert model.last_decision["rejected"][0]["action"] == "screenshot"
