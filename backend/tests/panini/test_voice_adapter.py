from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.panini.contracts import Fault
from backend.panini.voice import INSTRUCTIONS, NeutralConnection, VoiceMedia


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
