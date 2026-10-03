"""V2-12: moving the mouse during a desktop task takes control (pause) instead of stopping it."""

import asyncio
from types import SimpleNamespace

import pytest

from backend import live
from backend.capabilities import desktop_visual
from backend.panini.contracts import Fault


def _context(stopped=lambda: False):
    def check():
        if stopped():
            raise Fault("STOPPED", "Stopped.")

    return SimpleNamespace(check=check, task=SimpleNamespace(task_id="t1"))


def test_the_task_waits_while_you_hold_the_desktop_and_resumes_when_you_give_it_back(monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    phases = []

    async def scenario():
        waiting = asyncio.ensure_future(
            desktop_visual._held_by_person(_context(), lambda phase, *a, **k: phases.append(phase), {})
        )
        await asyncio.sleep(0.2)
        assert live.takeover().state("desktop", "pc").holder == "human" and not waiting.done()
        live.takeover().release("desktop", "pc")
        return await asyncio.wait_for(waiting, 3)

    assert asyncio.run(scenario()) is True
    assert phases == ["paused", "resumed"]
    assert not live.takeover().state("desktop", "pc").resume_snapshot_required


def test_stop_still_works_while_you_hold_control(monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    stop = {"now": False}

    async def scenario():
        waiting = asyncio.ensure_future(
            desktop_visual._held_by_person(_context(lambda: stop["now"]), lambda *a, **k: None, {})
        )
        await asyncio.sleep(0.2)
        stop["now"] = True
        await asyncio.wait_for(waiting, 3)

    with pytest.raises(Fault, match="Stopped"):
        asyncio.run(scenario())


def test_without_live_view_moving_the_mouse_still_stops_the_task(monkeypatch):
    monkeypatch.setenv("PANINI_LIVE_VIEW", "false")
    assert asyncio.run(desktop_visual._held_by_person(_context(), lambda *a, **k: None, {})) is False
