"""PANINI uses the user's own Chrome by default; a managed browser only on explicit request."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.app import create_app
from backend.capabilities.browser import BrowserAdapter
from backend.panini.contracts import Fault
from backend.panini.events import EventHub
from backend.panini.targets import resolve_target


def attached(app):
    bridge = app.state.extension_bridge
    asyncio.run(bridge.attach(object(), "d" * 32))
    bridge.on_message({"type": "hello", "tab": 7, "protocol": 4, "version": "3.0.0", "tab_title": "Inbox"})
    return bridge


def adapter_with(app):
    from backend.extension_bridge import ExtensionBridge

    app.state.extension_bridge = ExtensionBridge()
    return BrowserAdapter(app)


def choose(adapter, query="", selected=None):
    return asyncio.run(adapter.choose(query, selected))


def test_unconnected_chrome_falls_back_to_a_separate_browser_and_says_why():
    adapter = adapter_with(create_app())
    assert choose(adapter) == "managed-chromium"
    assert "Pair JevRev Claw" in adapter.fallback_reason


def test_unconnected_chrome_explains_the_next_step_when_set_to_ask(monkeypatch):
    monkeypatch.setenv("PANINI_BROWSER_FALLBACK", "ask")
    app = create_app()
    adapter = adapter_with(app)
    with pytest.raises(Fault) as error:
        choose(adapter)
    assert error.value.code == "BROWSER_NOT_CONNECTED" and "Pair JevRev Claw" in error.value.message
    app.state.pairing.redeem(app.state.pairing.create_code()["code"])
    with pytest.raises(Fault) as error:
        choose(adapter)
    assert "Open Chrome with JevRev Claw" in error.value.message
    bridge = app.state.extension_bridge
    asyncio.run(bridge.attach(object(), "d" * 32))
    bridge.on_message({"type": "hello", "tab": None, "protocol": 4, "version": "3.0.0"})
    with pytest.raises(Fault) as error:
        choose(adapter)
    assert "attach the tab" in error.value.message


def test_attached_chrome_is_the_default_and_managed_needs_an_explicit_request():
    app = create_app()
    adapter = adapter_with(app)
    bridge = attached(app)
    assert choose(adapter) == bridge.target_id
    assert choose(adapter, "Chrome") == bridge.target_id
    assert choose(adapter, "use a separate browser") == "managed-chromium"
    # An earlier explicit choice holds for follow-up steps with no target named.
    assert choose(adapter, "", {"mode": "chromium"}) == "managed-chromium"


def test_resolve_target_selects_the_attached_tab_automatically():
    app = create_app()
    adapter = adapter_with(app)
    bridge = attached(app)
    session = SimpleNamespace(selected={}, hub=EventHub("instance", "session"))
    ctx = SimpleNamespace(session=session, task=SimpleNamespace(capability="browser"), check=lambda: None)
    target = asyncio.run(resolve_target(ctx, adapter, ""))
    assert target["mode"] == "extension" and target["target_id"] == bridge.target_id
    assert target["label"] == "Inbox"
    # The same selection is reused while the attachment is unchanged.
    assert asyncio.run(resolve_target(ctx, adapter, ""))["target_ref"] == target["target_ref"]
