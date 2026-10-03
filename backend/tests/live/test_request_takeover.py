"""V2-12 request_takeover: PANINI asks you to take over a hand and waits until you hand it back."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import interrupts as module
from backend import live
from backend.hands.live_hand import LiveHand
from backend.harness.store import Store
from backend.interrupts import Interrupts, decided_by
from backend.registry.manifest import BUILTIN
from backend.registry.registry import Registry

ROOT = Path(__file__).resolve().parents[3]
CTX = SimpleNamespace(run_id="run-1", principal=None)
SPEC = SimpleNamespace(name="request_takeover")


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    monkeypatch.setenv("PANINI_V2_INTERRUPTS", "true")
    store = Store(tmp_path / "h.sqlite3")
    yield module.install(Interrupts(store))
    store.close()


def _ask(hand="managed", reason="Please sign in to the shop."):
    return asyncio.ensure_future(
        LiveHand(None, {}, poll_s=0.02).call(CTX, SPEC, {"hand": hand, "reason": reason})
    )


def test_the_manifest_loads():
    r = Registry({}, sources=[(BUILTIN, False), (ROOT / "panini-core/capabilities/live/manifests", False)])
    assert r.errors == [] and "request_takeover" in r.specs


def test_answering_the_card_hands_control_back_and_requires_a_fresh_look(service):
    async def scenario():
        asking = _ask()
        await asyncio.sleep(0.1)
        assert live.takeover().state("managed", "default").requested
        card = service.list()[0]
        assert card["kind"] == "takeover" and "sign in" in card["message"]
        full = service.get(card["id"], include_secret=True)
        service.resolve(card["id"], nonce=full["nonce"], action_digest=None, status="resolved", payload={},
                        decided_by=decided_by("console", "tap"))  # fmt: skip
        return await asyncio.wait_for(asking, 3)

    result = asyncio.run(scenario())
    assert result.outcome == "ok" and "snapshot" in result.content
    assert live.takeover().state("managed", "default").resume_snapshot_required


def test_taking_over_in_a_live_view_and_giving_back_also_ends_the_wait(service):
    async def scenario():
        asking = _ask("sandbox", "Solve the CAPTCHA, please.")
        await asyncio.sleep(0.1)
        live.takeover().take("sandbox", "panini")
        await asyncio.sleep(0.1)
        assert not asking.done()
        live.takeover().release("sandbox", "panini")
        result = await asyncio.wait_for(asking, 3)
        return result, service.list(status=None)[0]["status"]

    result, card = asyncio.run(scenario())
    assert result.outcome == "ok" and card == "cancelled"  # The card closes itself.


def test_declining_blocks_the_step(service):
    async def scenario():
        asking = _ask("desktop", "Enter your PIN.")
        await asyncio.sleep(0.1)
        service.cancel(service.list()[0]["id"], "Not now.")
        return await asyncio.wait_for(asking, 3)

    result = asyncio.run(scenario())
    assert result.outcome == "blocked" and "Do not do this step yourself" in result.content
