import asyncio

import pytest

from backend.panini import cleanup


def jev(p):
    return {"answers": {"leave_open": {"probability": p}}, "usage": {}}


@pytest.fixture
def screen(monkeypatch):
    closed = []
    monkeypatch.setattr(cleanup, "opened_since", lambda before: {7: ("Untitled - Notepad", "notepad.exe")})
    monkeypatch.setattr(cleanup, "close", closed.append)
    monkeypatch.setenv("PANINI_JEV_GATES", "true")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-gateway-key")
    return closed


@pytest.mark.parametrize("leave_open,expected", [(0.1, [7]), (0.9, [])])
def test_windows_used_only_to_do_the_work_are_closed(monkeypatch, screen, leave_open, expected):
    from backend import jev_client

    monkeypatch.setattr(jev_client, "ask", lambda state, questions: jev(leave_open))
    asyncio.run(cleanup.tidy("Write buy milk in Notepad and save it", {1: ("x", "y")}, lambda text: None))
    assert screen == expected


def test_cleanup_can_be_turned_off_and_is_safe_without_jev(monkeypatch, screen):
    monkeypatch.setenv("PANINI_CLEANUP", "never")
    asyncio.run(cleanup.tidy("save it", {1: ("x", "y")}, lambda text: None))
    monkeypatch.setenv("PANINI_CLEANUP", "auto")
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    asyncio.run(cleanup.tidy("save it", {1: ("x", "y")}, lambda text: None))
    assert screen == []  # Unsure means the person's screen is left alone.


def test_web_pages_open_in_the_connected_chrome(monkeypatch):
    from backend.app import create_app
    from backend.capabilities import browser

    class Bridge:
        connected = compatible = True
        attached_tab, target_id, workspace = 5, "t", {"allow_new_tabs": True}
        opened = []

        def bind_target(self, target_id):
            import contextlib

            return contextlib.nullcontext()

        async def open_tab(self, url):
            self.opened.append(url)

    bridge = Bridge()
    monkeypatch.setattr(browser, "get_bridge", lambda request: bridge)
    adapter = browser.BrowserAdapter(create_app())
    assert asyncio.run(adapter.open_in_chrome("https://youtube.com"))["where"].startswith("your Chrome")
    assert bridge.opened == ["https://youtube.com"]
    assert asyncio.run(adapter.open_in_chrome("https://youtube.com", {"mode": "chromium"})) is None
    bridge.connected = False
    assert asyncio.run(adapter.open_in_chrome("https://youtube.com")) is None


def test_direct_browser_actions_scroll_read_and_gate_risky_clicks(monkeypatch):
    import contextlib

    from backend.app import create_app
    from backend.capabilities import browser
    from backend.panini.contracts import Fault

    class Bridge:
        connected = compatible = True
        attached_tab, target_id, workspace = 5, "t", None
        acts = []
        position = 0

        def bind_target(self, target_id):
            return contextlib.nullcontext()

        async def snapshot(self):
            return {
                "url": "https://acme.test",
                "title": "Acme",
                "text": f"Section {self.position}",
                "marker": f"m{self.position}",
                "actions": [
                    {"id": "e1_click", "kind": "click", "label": "About us"},
                    {"id": "e2_click", "kind": "click", "label": "Buy now"},
                ],
            }

        async def text(self):
            return {"text": "Everything about Acme"}

        async def act(self, element, op, marker="", text=""):
            self.acts.append((element, op, marker))
            if element == "scroll_down":
                self.position += 1

    bridge = Bridge()
    monkeypatch.setattr(browser, "get_bridge", lambda request: bridge)
    adapter = browser.BrowserAdapter(create_app())
    monkeypatch.setenv("PANINI_APPROVALS", "risky")
    page = asyncio.run(adapter.direct("scroll_down"))
    assert page["visible_text"] == "Section 1" and bridge.acts[-1] == ("scroll_down", "CLICK", "m0")
    assert "e1_click | click | About us" in page["controls"]
    asyncio.run(adapter.direct("click", element="e1_click"))  # Ordinary: no approval needed.

    async def deny(action):
        return False

    with pytest.raises(Fault, match="did not approve"):
        asyncio.run(adapter.direct("click", element="e2_click", confirm=deny))
    assert ("e2_click", "CLICK", "m1") not in bridge.acts
