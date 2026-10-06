import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.panini.contracts import Fault
from backend.panini.voice import INSTRUCTIONS, NeutralConnection, VoiceMedia


@pytest.mark.anyio
async def test_live_close_bounds_a_handler_that_ignores_cancellation(monkeypatch):
    from panini import live

    monkeypatch.setattr(live, "HANDLER_CLOSE_TIMEOUT_S", 0.01)
    connection = NeutralConnection("test", AsyncMock(), AsyncMock())
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_handler():
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    task = connection._track(slow_handler())
    try:
        await started.wait()
        await asyncio.wait_for(connection.close(), 0.5)
        assert connection.closing and not task.done()
    finally:
        release.set()
        await task


@pytest.mark.anyio
async def test_context_acknowledgment_matches_the_sent_command():
    import json

    events = AsyncMock()
    connection = NeutralConnection("test", events, AsyncMock())
    connection.socket = SimpleNamespace(send=AsyncMock())
    identifier = await connection.speak("Backend result is ready.")
    command = json.loads(connection.socket.send.call_args.args[0])
    assert identifier == command["event_id"] and command["delegation_id"] is None
    await connection.handle_event({"type": "session.commentary.appended", "client_event_id": identifier})
    events.assert_awaited_once_with({"type": "context.accepted", "client_event_id": identifier})


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_media_offer_preserves_sdp_line_terminators():
    from uuid import uuid4

    from backend.panini.contracts import MediaOffer

    sdp = "v=0\r\no=- 1234 2 IN IP4 127.0.0.1\r\n"
    assert MediaOffer(command_id=uuid4(), media_generation=1, sdp=sdp).sdp == sdp


def test_neutral_voice_keeps_legacy_prompt_separate():
    from panini.live import LiveConnection

    legacy = LiveConnection("test", None, None)
    neutral = NeutralConnection("test", None, None)
    assert neutral.configuration()["instructions"].endswith(INSTRUCTIONS)
    assert "nDimensions.AI" in neutral.configuration()["instructions"]
    assert legacy.configuration()["instructions"] != INSTRUCTIONS
    assert neutral.configuration()["delegation"] == {"type": "client"}
    assert neutral.configuration()["store"] is False


@pytest.mark.anyio
async def test_voice_uses_same_arbiter_and_stable_delegation_ids():
    owner = SimpleNamespace(authorize=lambda *args: None, submit=AsyncMock())
    session = SimpleNamespace(session_id="session", state="ready")
    media = VoiceMedia(owner, session, object(), 3)
    await media.request("Find the UIA documentation", "delegation-1")
    await media.request("Find the UIA documentation", "delegation-1")
    first, second = owner.submit.call_args_list
    assert first.args[3].command_id == second.args[3].command_id
    assert first.kwargs["voice"] is True
    assert first.args[2] == 3


@pytest.mark.anyio
async def test_voice_cannot_bypass_expired_lease():
    def expired(*args):
        raise Fault("LEASE_EXPIRED", "Reconnect PANINI.")

    owner = SimpleNamespace(authorize=expired, submit=AsyncMock())
    media = VoiceMedia(owner, SimpleNamespace(session_id="s"), object(), 1)
    media.connection.speak = AsyncMock()
    await media.request("Click Send", "delegation-2")
    owner.submit.assert_not_called()
    media.connection.speak.assert_awaited_once()


@pytest.mark.anyio
async def test_lost_voice_stops_task_then_closes_media_without_ending_session():
    owner = SimpleNamespace(stop=AsyncMock(), close_media=AsyncMock())
    session = SimpleNamespace(session_id="s", state="ready")
    media = VoiceMedia(owner, session, object(), 1)
    await media.disconnected()
    owner.stop.assert_awaited_once_with(session)
    owner.close_media.assert_awaited_once_with(session)
    assert session.state == "ready"


@pytest.mark.anyio
async def test_persona_voice_routes_to_specialist_and_revalidates_archive(tmp_path, monkeypatch):
    from backend.harness.store import Store
    from backend.personas import Personas
    from backend.policy.engine import Policy

    monkeypatch.setenv("PANINI_PERSONAS", "true")
    store = Store(tmp_path / "voice.sqlite3")
    personas = Personas(
        store, Policy(store), identity_read=lambda: "Owner identity", identity_write=lambda text: None
    )
    personas.create("scout", name="Scout", instructions="Research only", color="cyan", voice="cedar")
    owner = SimpleNamespace(
        authorize=lambda *args: None,
        submit=AsyncMock(),
        adapters={"agent": SimpleNamespace(harness=SimpleNamespace(personas=personas))},
    )
    session = SimpleNamespace(session_id="voice-test", voice_persona_id="scout", state="ready")
    media = VoiceMedia(owner, session, object(), 1)
    config = media.connection.configuration()
    assert config["audio"]["output"]["voice"] == "cedar"
    assert config["instructions"].startswith("Research only")
    assert "Owner identity" not in config["instructions"]
    await media.request("Find papers", "one")
    assert owner.submit.call_args.args[3].text == "@scout Find papers"
    await media.request("stop", "two")
    assert owner.submit.call_args.args[3].text == "stop"
    media.connection.speak = AsyncMock()
    personas.archive("scout")
    await media.request("Find more papers", "three")
    assert owner.submit.await_count == 2
    assert "unavailable" in media.connection.speak.call_args.args[0]
    monkeypatch.setenv("PANINI_PERSONAS", "false")
    from backend.panini.voice import voice_profile

    with pytest.raises(Fault, match="disabled"):
        voice_profile(owner, "scout")
    store.close()


@pytest.mark.anyio
async def test_voice_reconnect_history_is_bounded_and_scoped(monkeypatch):
    monkeypatch.setenv("PANINI_PERSONAS", "false")
    owner = SimpleNamespace()
    session = SimpleNamespace(
        session_id="s", state="ready", voice_history={}, hub=SimpleNamespace(publish=lambda *args: None)
    )
    first = VoiceMedia(owner, session, None, 1)
    await first.event({"type": "transcript", "role": "user", "delta": "Hello"})
    await first.event({"type": "transcript", "role": "assistant", "delta": "Hi"})
    second = VoiceMedia(owner, session, None, 1)
    assert second.connection.configuration()["input"][0]["content"][0]["text"] == "Hello"
    assert second.connection.configuration()["input"][1]["content"][0]["type"] == "output_text"
    session.voice_history["other"] = [{"role": "user", "text": "Other persona private text"}]
    assert "Other persona private text" not in str(second.connection.configuration())
    for _ in range(80):
        await first.event({"type": "transcript", "role": "user", "delta": "u" * 4000})
        await first.event({"type": "transcript", "role": "assistant", "delta": "a" * 4000})
    assert sum(len(row["text"]) for row in session.voice_history["panini"]) <= 6000


@pytest.mark.anyio
async def test_spoken_handoff_reconnects_in_the_next_personas_voice_with_only_the_owners_words(
    tmp_path, monkeypatch
):
    from backend.harness.store import Store
    from backend.personas import Personas
    from backend.personas.routing import handoff_target
    from backend.policy.engine import Policy

    monkeypatch.setenv("PANINI_PERSONAS", "true")
    store = Store(tmp_path / "handoff.sqlite3")
    personas = Personas(
        store, Policy(store), identity_read=lambda: "Owner identity", identity_write=lambda text: None
    )
    personas.create("scout", name="Scout", instructions="Research only", color="cyan", voice="cedar")
    personas.create("writer", name="Writer", instructions="Write only", color="violet", voice="marin")
    for text, expected in (
        ("Hand me to Scout", "scout"),
        ("let me talk to the writer please", "writer"),
        ("Okay, back to PANINI.", "panini"),
        ("Could you switch me over to Scout?", "scout"),
        ("switch to dark mode", None),
        ("Tell Scout to hand me the report", None),
        ("let me talk to Bob", None),
    ):
        assert handoff_target(personas, text) == expected, text
    published = []
    owner = SimpleNamespace(
        authorize=lambda *args: None,
        submit=AsyncMock(),
        adapters={"agent": SimpleNamespace(harness=SimpleNamespace(personas=personas))},
    )
    session = SimpleNamespace(
        session_id="handoff",
        voice_persona_id="scout",
        state="ready",
        voice_history={
            "scout": [
                {"role": "user", "text": "Find three sources on tidal power"},
                {"role": "assistant", "text": "Scout private notes"},
            ]
        },
        hub=SimpleNamespace(publish=lambda kind, data: published.append((kind, data))),
    )
    media = VoiceMedia(owner, session, object(), 1)
    media.connection.speak = AsyncMock()
    await media.request("Hand me to the writer", "one")
    owner.submit.assert_not_awaited()  # A handoff is not a task.
    assert media.connection.speak.call_args.args[0] == "Handing you to Writer."
    assert published == [
        ("voice.handoff", {"from": "scout", "persona": {"id": "writer", "name": "Writer", "color": "violet"}})
    ]
    carried = session.voice_history["writer"][-1]["text"]
    assert "tidal power" in carried and "Scout private notes" not in carried
    following = VoiceMedia(
        owner, SimpleNamespace(**{**vars(session), "voice_persona_id": "writer"}), object(), 1
    )
    assert following.connection.configuration()["audio"]["output"]["voice"] == "marin"
    assert "tidal power" in str(following.connection.configuration()["input"])
    await media.request("let me talk to Scout", "two")
    assert "already talking to Scout" in media.connection.speak.call_args.args[0]
    personas.archive("writer")
    await media.request("hand me to Writer", "three")
    assert (
        len(published) == 1 and owner.submit.await_count == 1
    )  # Not a persona any more: an ordinary request.
    store.close()
