import asyncio
from types import SimpleNamespace

import httpx
import pytest
from panini.conversation import Conversation
from panini.live import LiveConnection
from panini.models import Report, ResultItem, Turn

from backend.app import create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Browser:
    def __init__(self, *, hold=False, terminal=None):
        self.calls = []
        self.cancelled = 0
        self.hold = hold
        self.entered = asyncio.Event()
        self.terminal = terminal or {"event": "complete", "message": "Results loaded"}

    async def run(self, instruction):
        self.calls.append(instruction)
        self.entered.set()
        if self.hold:
            await asyncio.Event().wait()
        yield self.terminal

    def cancel(self):
        self.cancelled += 1

    async def observation(self):
        return {
            "text": "AI Manager at Acme in Dubai. Application asks for notice period.",
            "url": "https://example.test/jobs?q=AI+Manager",
            "actions": [{"href": "https://example.test/jobs/123"}],
        }


def fixture(browser=None):
    events, inputs, turns, reports = [], [], [], []

    async def emit(event):
        events.append(event)

    async def parse(rules, payload, schema):
        inputs.append(payload)
        return turns.pop(0) if schema is Turn else reports.pop(0)

    c = Conversation(browser or Browser(), parse, emit)
    return c, events, inputs, turns, reports


def run_turn(instruction="Find five AI manager jobs", item_id="", collect_results=True):
    return Turn(
        intent="run",
        instruction=instruction,
        reply="Looking now.",
        item_id=item_id,
        collect_results=collect_results,
    )


def report(question="", items=None):
    return Report(message="Here is the observed result.", question=question, items=items or [])


@pytest.mark.anyio
async def test_invalid_result_quotes_are_repaired_without_repeating_browser_work():
    c, events, inputs, turns, reports = fixture()
    turns.append(run_turn())
    reports.extend(
        [
            report(
                items=[ResultItem(title="AI Manager", detail="Acme", url="", evidence="AI Manager Dubai")]
            ),
            report(
                items=[ResultItem(title="AI Manager", detail="Acme", url="", evidence="AI Manager at Acme")]
            ),
        ]
    )
    await c.handle("Find jobs")
    await c.task
    assert len(c.browser.calls) == 1
    assert len(c.items) == 1
    assert "validation_error" in inputs[-1]
    assert events[-1]["feedback"] and events[-1]["items"][0]["title"] == "AI Manager"


@pytest.mark.anyio
async def test_failed_quote_repair_remains_unverified():
    c, events, inputs, turns, reports = fixture()
    turns.append(run_turn())
    reports.extend(
        [
            report(items=[ResultItem(title="Fake", detail="Fake", url="", evidence="Invented result")]),
            report(items=[ResultItem(title="Fake", detail="Fake", url="", evidence="Still invented")]),
        ]
    )
    await c.handle("Find jobs")
    await c.task
    assert not c.items
    assert len(c.browser.calls) == 1
    assert "could not verify" in events[-1]["message"]


@pytest.mark.anyio
async def test_followup_keeps_verified_result_identity_and_missing_question():
    c, events, inputs, turns, reports = fixture()
    turns.append(run_turn())
    reports.append(
        report(
            items=[
                ResultItem(
                    title="AI Manager",
                    detail="Acme, Dubai",
                    url="https://example.test/jobs/123",
                    evidence="AI Manager at Acme in Dubai.",
                )
            ]
        )
    )
    await c.handle("Find jobs", "one")
    await c.task
    item = c.items[0]
    turns.append(run_turn("Open the first application", item["id"], collect_results=False))
    reports.append(report("What is your notice period?"))
    await c.handle("Apply to the first one", "two")
    await c.task
    assert item["id"] in c.browser.calls[-1]
    assert "https://example.test/jobs/123" in c.browser.calls[-1]
    assert c.state == "needs_information"
    turns.append(run_turn("Continue the application with notice period 30 days", collect_results=False))
    reports.append(report())
    await c.handle("30 days", "three")
    await c.task
    assert any(p.get("question") == "What is your notice period?" for p in inputs)
    assert "30 days" in c.browser.calls[-1]
    assert len(c.browser.calls) == 3
    await c.close()


@pytest.mark.anyio
async def test_stop_during_work_leaves_voice_listening_and_prevents_stale_result():
    c, events, inputs, turns, reports = fixture(Browser(hold=True))
    turns.append(run_turn())
    await c.handle("Find jobs")
    await c.browser.entered.wait()
    await c.handle("stop the task")
    assert c.browser.cancelled == 1
    assert c.state == "listening"
    assert not any(e.get("type") == "results" and e.get("items") for e in events)
    await c.close()


@pytest.mark.anyio
async def test_stop_invalidates_a_pending_planner_before_any_browser_action():
    c, events, inputs, turns, reports = fixture()
    entered, release = asyncio.Event(), asyncio.Event()

    async def parse(*args):
        entered.set()
        await release.wait()
        return run_turn()

    c.parse = parse
    request = asyncio.create_task(c.handle("Find jobs"))
    await entered.wait()
    await c.handle("stop")
    release.set()
    await request
    assert c.browser.calls == []
    await c.close()


@pytest.mark.anyio
async def test_duplicate_turn_and_smalltalk_never_replay_browser_work():
    c, events, inputs, turns, reports = fixture()
    turns.append(run_turn())
    reports.append(report())
    await c.handle("Find jobs", "event1")
    await c.task
    await c.handle("Find jobs", "event1")
    turns.append(Turn(intent="reply", instruction="", reply="You're welcome.", item_id=""))
    await c.handle("thanks")
    assert len(c.browser.calls) == 1
    await c.close()


@pytest.mark.anyio
async def test_unobserved_results_and_links_are_not_accepted():
    c, events, inputs, turns, reports = fixture()
    turns.append(run_turn())
    reports.append(
        report(
            items=[
                ResultItem(title="Fake", detail="Fake", url="https://evil.test", evidence="Invented job"),
                ResultItem(
                    title="AI Manager",
                    detail="Acme",
                    url="javascript:alert(1)",
                    evidence="AI Manager at Acme in Dubai.",
                ),
            ]
        )
    )
    await c.handle("Find jobs")
    await c.task
    assert len(c.items) == 1
    assert c.items[0]["url"] == ""
    await c.close()


@pytest.mark.anyio
async def test_missing_result_reference_asks_instead_of_clicking_different_job():
    c, events, inputs, turns, reports = fixture()
    turns.append(run_turn(item_id="nonexistent"))
    await c.handle("apply first")
    assert not c.browser.calls
    assert "Which result" in events[-1]["message"]
    await c.close()


@pytest.mark.anyio
async def test_live_delegation_uses_user_context_once_and_ignores_assistant_audio():
    events, requests = [], []

    async def emit(event):
        events.append(event)

    async def request(text, identifier):
        requests.append((text, identifier))

    live = LiveConnection("test", emit, request)
    await live.handle_event(
        {"type": "session.input_transcript.delta", "delta": "Find AI ", "start_ms": 0, "end_ms": 700}
    )
    await live.handle_event(
        {"type": "session.input_transcript.delta", "delta": "manager jobs", "start_ms": 700, "end_ms": 1500}
    )
    await live.handle_event(
        {"type": "session.output_transcript.delta", "delta": "Certainly", "start_ms": 1500, "end_ms": 1700}
    )
    event = {
        "type": "session.delegation.created",
        "offset_ms": 1700,
        "delegation": {"id": "d1", "target": "client"},
    }
    await live.handle_event(event)
    await asyncio.gather(*live.handlers)
    await live.handle_event(event)
    assert requests == [("Find AI manager jobs", "d1")]
    await live.handle_event(
        {"type": "session.input_transcript.delta", "delta": "Only in Dubai", "start_ms": 2000, "end_ms": 2400}
    )
    await live.handle_event({**event, "offset_ms": 2500, "delegation": {"id": "d2", "target": "client"}})
    await asyncio.gather(*live.handlers)
    assert requests[-1][0] == "Only in Dubai"
    await live.close()


@pytest.mark.anyio
async def test_voice_session_capability_and_typed_browser_exclusion():
    app = create_app()
    app.state.voice_session = SimpleNamespace(
        token="a" * 32, closed=False, conversation=SimpleNamespace(state="listening")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (await client.post("/api/voice/end", json={"session_id": "b" * 32})).status_code == 404
        response = await client.post(
            "/api/browser/run-stream", json={"mode": "chromium", "instruction": "search"}
        )
        assert response.status_code == 409
        assert "Live conversation" in response.json()["detail"]


@pytest.mark.anyio
async def test_followup_does_not_replace_result_list_or_overwrite_application_receipt():
    c, events, inputs, turns, reports = fixture()
    saved = {"id": "saved", "title": "AI Manager", "detail": "Acme", "url": "https://example.test/jobs/123"}
    c.items = [saved]
    turns.append(run_turn("Submit Acme application with 30 days notice", "saved", collect_results=False))
    reports.append(
        Report(
            message="The page confirms application submitted: TEST123.",
            question="",
            items=[ResultItem(title="Old job", detail="stale", url="", evidence="No longer visible")],
        )
    )
    await c.handle("30 days, submit")
    await c.task
    assert c.items == [saved]
    assert "TEST123" in events[-1]["message"]
    await c.close()


@pytest.mark.anyio
async def test_live_comments_keep_delegation_id():
    sent = []

    async def unused(*args):
        pass

    async def send(kind, **values):
        sent.append({"type": kind, **values})

    connection = LiveConnection("test", unused, unused)
    connection.send = send
    await connection.speak("Verified job result", delegation_id="task1")
    assert sent == [
        {"type": "session.commentary.append", "delegation_id": "task1", "content": "Verified job result"}
    ]
    await connection.close()


@pytest.mark.anyio
async def test_failed_voice_creation_releases_reservation(monkeypatch):
    import backend.routes.voice as routes
    from backend import voice_service

    app = create_app()
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(routes, "prepare_run", lambda *args, **kwargs: None)

    async def fail(self, sdp):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(voice_service.VoiceSession, "start", fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.post("/api/voice/session", json={"sdp": "offer" * 10, "mode": "chromium"})
    assert response.status_code == 502
    assert app.state.voice_session is None


@pytest.mark.anyio
async def test_normal_sideband_disconnect_notifies_owner():
    events = []

    async def emit(event):
        events.append(event)

    async def unused(*args):
        pass

    async def socket():
        if False:
            yield ""

    connection = LiveConnection("test", emit, unused)
    connection.socket = socket()
    await connection._read()
    assert events[-1]["type"] == "disconnected"


@pytest.mark.anyio
async def test_correction_arriving_during_planning_is_not_dropped():
    c, events, inputs, turns, reports = fixture(Browser(hold=True))
    entered, release = asyncio.Event(), asyncio.Event()

    async def parse(rules, payload, schema):
        if payload["user"] == "Find jobs":
            entered.set()
            await release.wait()
            return run_turn("Find jobs")
        return run_turn("Find jobs only in Dubai")

    c.parse = parse
    first = asyncio.create_task(c.handle("Find jobs"))
    await entered.wait()
    second = asyncio.create_task(c.handle("Only in Dubai"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert c.objective == "Find jobs only in Dubai"
    await c.close()


@pytest.mark.anyio
async def test_stop_discards_corrections_already_queued_behind_planner():
    c, events, inputs, turns, reports = fixture(Browser(hold=True))
    entered, release = asyncio.Event(), asyncio.Event()

    async def parse(*args):
        entered.set()
        await release.wait()
        return run_turn()

    c.parse = parse
    first = asyncio.create_task(c.handle("Find jobs"))
    await entered.wait()
    second = asyncio.create_task(c.handle("Only in Dubai"))
    await asyncio.sleep(0)
    await c.handle("stop")
    release.set()
    await asyncio.gather(first, second)
    assert c.browser.calls == []
    await c.close()


@pytest.mark.anyio
async def test_spoken_end_is_enforced_without_model_delegation():
    requests = []

    async def emit(event):
        pass

    async def request(text, identifier):
        requests.append((text, identifier))

    live = LiveConnection("test", emit, request)
    await live.handle_event(
        {
            "type": "session.input_transcript.delta",
            "delta": "End the conversation. Goodbye.",
            "start_ms": 0,
            "end_ms": 1300,
        }
    )
    await live.control_timer
    assert requests == [("End the conversation. Goodbye.", None)]
    await live.handle_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 1400,
            "delegation": {"id": "late", "target": "client"},
        }
    )
    assert len(requests) == 1
    await live.close()


@pytest.mark.parametrize(
    "text",
    [
        "Don't stop",
        "Stop talking",
        "Tell me how to end the conversation",
        "Close the application form",
        "End the conversation button is broken",
    ],
)
def test_control_phrases_do_not_match_negation_or_other_actions(text):
    from panini.conversation import control_intent

    assert control_intent(text) is None


async def drain(live):
    while live.handlers:
        await asyncio.gather(*live.handlers)
        await asyncio.sleep(0)  # Let done callbacks remove eagerly completed tasks.


@pytest.mark.anyio
async def test_delegation_before_transcript_does_not_discard_spoken_instruction():
    requests = []

    async def emit(event):
        pass

    async def request(text, identifier):
        requests.append((text, identifier))

    live = LiveConnection("test", emit, request)
    await live.handle_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 1800,
            "delegation": {"id": "early", "target": "client"},
        }
    )
    await asyncio.sleep(0)
    await live.handle_event(
        {
            "type": "session.input_transcript.delta",
            "delta": "Find flights from Dubai",
            "start_ms": 100,
            "end_ms": 1500,
        }
    )
    await drain(live)
    assert requests == [("Find flights from Dubai", "early")]
    await live.close()


@pytest.mark.anyio
async def test_missing_delegation_fallback_and_late_delegation_execute_once():
    requests = []

    async def emit(event):
        pass

    async def request(text, identifier):
        requests.append(text)

    live = LiveConnection("test", emit, request)
    await live.handle_event(
        {
            "type": "session.input_transcript.delta",
            "delta": "Search for AI manager jobs",
            "start_ms": 100,
            "end_ms": 1500,
        }
    )
    await live.turn_timer
    await drain(live)
    assert requests == ["Search for AI manager jobs"]
    await live.handle_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 1800,
            "delegation": {"id": "late", "target": "client"},
        }
    )
    # The duplicate is never dispatched, including while it waits for delayed input.
    await asyncio.sleep(0.05)
    assert requests == ["Search for AI manager jobs"]
    await live.close()


@pytest.mark.anyio
async def test_early_delegation_includes_the_remainder_of_a_spoken_request():
    requests = []

    async def emit(event):
        pass

    async def request(text, identifier):
        requests.append(text)

    live = LiveConnection("test", emit, request)
    await live.handle_event(
        {"type": "session.input_transcript.delta", "delta": "Find flights ", "start_ms": 0, "end_ms": 500}
    )
    await live.handle_event(
        {
            "type": "session.delegation.created",
            "offset_ms": 600,
            "delegation": {"id": "early", "target": "client"},
        }
    )
    await asyncio.sleep(0)
    await live.handle_event(
        {
            "type": "session.input_transcript.delta",
            "delta": "from Dubai to Bengaluru tomorrow",
            "start_ms": 700,
            "end_ms": 2100,
        }
    )
    await drain(live)
    assert requests == ["Find flights from Dubai to Bengaluru tomorrow"]
    await live.close()


@pytest.mark.anyio
async def test_actionable_browser_failure_is_spoken_and_reported():
    from panini.errors import BrowserUnavailable

    class Unavailable(Browser):
        async def run(self, instruction):
            raise BrowserUnavailable("target_changed", "The attached tab changed. Start a new conversation.")
            yield

    c, events, inputs, turns, reports = fixture(Unavailable())
    turns.append(run_turn())
    await c.handle("Find jobs")
    await c.task
    assert events[-1]["message"] == "The attached tab changed. Start a new conversation."
    assert events[-2]["error_code"] == "target_changed"
    assert not reports and len(inputs) == 1  # Never generate a fabricated report after no actions.
    await c.close()


@pytest.mark.anyio
async def test_native_clarification_context_is_supplied_to_the_task_planner(monkeypatch):
    from panini.console_connection import ConsoleConnection

    from backend.voice_service import VoiceSession

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    session = VoiceSession(create_app(), {"mode": "chromium"}, connection_factory=ConsoleConnection)
    captured = []

    async def parse(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=run_turn()))])

    monkeypatch.setattr(session.client.chat.completions, "parse", parse)
    await session.provider_event({"type": "transcript", "role": "assistant", "delta": "Which date?"})
    await session.provider_event({"type": "transcript", "role": "user", "delta": "Tomorrow"})
    page_text = "Rôle • HR · UAE – دبي"
    await session.parse("rules", {"user": "Tomorrow", "observation": {"text": page_text}}, Turn)
    import json

    payload = json.loads(captured[0]["messages"][1]["content"])
    # Escaped Unicode in model input caused malformed evidence quotes in UAT8.
    assert page_text in captured[0]["messages"][1]["content"]
    assert payload["native_conversation"] == [
        {"role": "assistant", "text": "Which date?"},
        {"role": "user", "text": "Tomorrow"},
    ]
    await session.close()


@pytest.mark.anyio
async def test_attachment_change_ends_voice_instead_of_hiding_repeated_failed_tasks(monkeypatch):
    from panini.console_connection import ConsoleConnection

    from backend import voice_service

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    app = create_app()
    session = voice_service.VoiceSession(
        app, {"mode": "extension", "target_id": "original"}, connection_factory=ConsoleConnection
    )
    app.state.voice_session = session
    session.stream_connected = True
    monkeypatch.setattr(
        voice_service, "get_bridge", lambda request: SimpleNamespace(connected=True, target_id="new")
    )
    await asyncio.wait_for(session._guard(), 3)
    assert session.closed
    assert "tab changed" in session.events[-1]["message"]
    assert app.state.voice_session is None
    assert app.state.voice_last_diagnostics["error_code"] == "session_target_or_connection_lost"


@pytest.mark.parametrize(
    "text", ["Leave it", "Okay, just leave it, leave it.", "Never mind", "Please forget it"]
)
def test_natural_abandon_commands_cancel(text):
    from panini.conversation import control_intent

    assert control_intent(text) == "stop"


@pytest.mark.anyio
async def test_uat8_backchannels_do_not_cancel_or_restart_work_or_lose_feedback():
    release = asyncio.Event()

    class SlowBrowser(Browser):
        async def run(self, instruction):
            self.calls.append(instruction)
            self.entered.set()
            yield {
                "event": "observation",
                "url": "https://example.test/jobs?q=AI+Manager",
                "title": "Results",
                "text": "AI Manager at Acme in Dubai.",
            }
            await release.wait()
            yield self.terminal

    c, events, inputs, turns, reports = fixture(SlowBrowser())
    turns.append(run_turn())
    reports.append(
        report(
            items=[
                ResultItem(
                    title="AI Manager",
                    detail="Acme in Dubai",
                    url="",
                    evidence="AI Manager at Acme in Dubai.",
                )
            ]
        )
    )
    await c.handle("Find AI manager jobs")
    await c.browser.entered.wait()
    task = c.task
    for text in ["Mm, okay", "Okay, okay", "Yeah, okay", "Okay, that's fine", "That's great", "It's good"]:
        await c.handle(text)
    assert c.task is task and not task.done()
    assert c.browser.cancelled == 0
    assert len(inputs) == 1
    assert c.context()["observed_page"]["title"] == "Results"
    release.set()
    await task
    assert len(c.browser.calls) == 1
    assert events[-1]["items"][0]["title"] == "AI Manager"
    assert c.state == "listening"
    await c.handle("Thanks")
    assert len(c.browser.calls) == 1
    await c.close()


@pytest.mark.anyio
async def test_same_objective_while_running_does_not_restart_even_if_planner_says_run():
    c, events, inputs, turns, reports = fixture(Browser(hold=True))
    turns.extend([run_turn("Search for HR UAE"), run_turn("Search for HR UAE")])
    await c.handle("Search for HR UAE")
    await c.browser.entered.wait()
    task = c.task
    await c.handle("Keep going")
    assert c.task is task and c.browser.cancelled == 0
    await c.close()


@pytest.mark.anyio
async def test_acknowledgement_to_a_pending_question_is_not_discarded():
    c, events, inputs, turns, reports = fixture()
    c.objective = "Search recruiters in UAE"
    c.question = "Should I include recruitment agencies?"
    c.state = "needs_information"
    turns.append(run_turn("Search recruiters and recruitment agencies in UAE"))
    reports.append(report())
    await c.handle("Yes")
    await c.task
    assert len(c.browser.calls) == 1
    await c.close()


@pytest.mark.anyio
async def test_voice_report_preserves_verified_results_and_reads_beyond_viewport():
    from contextlib import nullcontext

    from backend.voice_service import VoiceBrowser

    verified = {
        "url": "https://example.test/search",
        "title": "HR UAE results",
        "text": "Recruiter Alice in Dubai",
        "actions": [{"href": "https://example.test/alice"}],
    }

    async def read():
        return {
            "url": verified["url"],
            "title": verified["title"],
            "text": "Home Search Recruiter Alice in Dubai Recruiter Bob in Abu Dhabi",
        }

    browser = VoiceBrowser(None, {"mode": "extension", "target_id": "pinned"})
    browser.loop = SimpleNamespace(
        bridge=SimpleNamespace(bind_target=lambda t: nullcontext(), text=read),
        last_run={"observation": verified},
    )
    browser.completed_page = verified
    page = await browser.observation()
    assert "Recruiter Bob" in page["text"]
    assert page["completion_verified"]
    assert page["actions"] == verified["actions"]

    async def navigated():
        return {"url": "https://example.test/feed", "title": "Home", "text": "Home Search"}

    browser.loop.bridge.text = navigated
    page = await browser.observation()
    assert page["text"] == verified["text"]
    assert page["current_page"]["title"] == "Home"


@pytest.mark.parametrize(
    "text", ["Okay search Dubai", "Yes stop the task", "That's fine, change it to HR", "No, try again"]
)
def test_actual_commands_are_not_swallowed_as_backchannels(text):
    from panini.conversation import is_backchannel

    assert not is_backchannel(text)
