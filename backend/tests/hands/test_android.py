import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.core_contracts import Principal
from backend.hands import adb
from backend.hands.android import Android
from backend.hands.android_tree import tree
from backend.hands.snapshot import Node, parse, render, role
from backend.harness.store import Store
from backend.ledger.artifacts import Artifacts
from backend.ledger.hook import LedgerHook
from backend.ledger.receipts import Ledger
from backend.policy.engine import Policy
from backend.policy.hook import PolicyHook
from backend.registry.context import ToolContext, ToolView
from backend.registry.registry import Registry

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def xml(name="Search", *, package="com.example.notes", password=False, focused=True, x=0, duplicate=False):
    node = f'<node text="{name}" content-desc="" class="android.widget.EditText" package="{package}" resource-id="{package}:id/search" enabled="true" password="{str(password).lower()}" focused="{str(focused).lower()}" bounds="[{x},0][100,40]" />'
    return "<hierarchy>" + node + (node if duplicate else "") + "</hierarchy>"


class Driver:
    def __init__(self):
        self.xml = xml()
        self.events = []
        self.fail = None
        image = Image.new("RGB", (200, 400), "black")
        out = io.BytesIO()
        image.save(out, format="PNG")
        self.image = base64.b64encode(out.getvalue()).decode()

    async def call(self, op, **args):
        self.events.append((op, args))
        if op == "snapshot":
            return self.xml
        if op == "screenshot":
            return self.image
        if op == "devices":
            return [{"serial": "phone", "model": "Fixture"}]
        if self.fail:
            raise self.fail
        if op == "fill":
            self.xml = xml(args["text"])
        return {"dispatched": True}

    async def close(self):
        pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("PANINI_ANDROID", "true")
    monkeypatch.setenv("PANINI_V2_LEDGER", "true")
    monkeypatch.setenv("PANINI_POLICY_PROFILE", "strict")
    store = Store(tmp_path / "state.sqlite3")
    harness = SimpleNamespace(
        store=store, policy=Policy(store), artifacts=Artifacts(store, tmp_path / "artifacts")
    )
    harness.android = Android(harness, driver=Driver())
    from backend import ledger

    harness.ledger = ledger.install(Ledger(store), harness.artifacts)
    harness.android.save("phone", True, ["com.example.notes"])
    registry = Registry(
        {"harness": harness}, sources=[(ROOT / "panini-core/capabilities/android/manifests", False)]
    )
    assert registry.errors == []
    registry.hooks += [LedgerHook(), PolicyHook(mode="shadow", policy=harness.policy)]
    asked = []

    async def confirm(title, detail):
        asked.append((title, detail))
        return True

    ctx = ToolContext(view=ToolView(android=True), confirm=confirm, run_id="android-test")
    yield harness, registry, ctx, asked
    store.close()


async def snapshot(registry, ctx):
    result = await registry.call(ctx, "android_snapshot", {"depth": 20})
    assert result.outcome == "ok"
    return parse(result.data["snapshot"])[0].ref


def test_grammar_roundtrip_and_password_value_scrub():
    nodes = [
        Node("w1", "window", 'A "quote"]\nnext'),
        Node("w2", "textbox", "Password", "secret", {"password", "disabled"}, (1, 2, 30, 40), 1),
    ]
    encoded = render(nodes)
    assert "secret" not in encoded
    restored = parse(encoded)
    assert restored[0] == nodes[0]
    assert restored[1].value is None and restored[1].box == (1, 2, 30, 40)
    assert render(restored) == encoded


@pytest.mark.parametrize(
    "invalid",
    [
        "- button nope",
        '- button "A" [ref=x1]',
        '- button "A" [ref=a1] [bad]',
        '- button "A" [ref=a1]\n- button "B" [ref=a1]',
    ],
)
def test_grammar_rejects_bad_refs_and_attributes(invalid):
    with pytest.raises(ValueError):
        parse(invalid)


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("Edit", "textbox"),
        ("android.widget.EditText", "textbox"),
        ("SplitButton", "button"),
        ("MenuBar", "menubar"),
        ("DataGrid", "grid"),
        ("Pane", "group"),
        ("android.widget.RecyclerView", "list"),
        ("ImageButton", "button"),
    ],
)
def test_roles(kind, expected):
    assert role(kind) == expected


def test_android_password_xml_never_retains_value_or_name():
    nodes, packages, text = tree(xml("supersecret", password=True), 1)
    assert "supersecret" not in text and "supersecret" not in repr(nodes)
    assert packages == {"com.example.notes"}
    _, _, text = tree(
        xml("supersecret", password=True).replace('content-desc=""', 'content-desc="supersecret"'), 2
    )
    assert "supersecret" not in text


@pytest.mark.parametrize(
    "invalid",
    ["<!DOCTYPE x><hierarchy></hierarchy>", "no tree", "<hierarchy><node /></hierarchy>", "<hierarchy>"],
)
def test_malformed_android_trees_fail_closed(invalid):
    with pytest.raises(ValueError):
        tree(invalid, 1)


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8:1234",
        "127.0.0.1:22",
        "192.168.1.2:0",
        "192.168.1.2:65536",
        "-H localhost",
        "host:5555",
        "192.168.1.2:12;whoami",
    ],
)
def test_pair_address_is_local_and_not_a_shell(address):
    with pytest.raises(ValueError):
        adb.endpoint(address)


def test_pair_address_valid():
    assert adb.endpoint("192.168.1.2:5555") == "192.168.1.2:5555"


@pytest.mark.anyio
async def test_registry_fill_then_verify_with_evidence(world):
    h, r, ctx, asked = world
    ref = await snapshot(r, ctx)
    result = await r.call(ctx, "android_fill", {"ref": ref, "text": "hello"})
    assert result.outcome == "ok" and result.dispatched and result.data["verified"] is False
    stale = await r.call(ctx, "android_fill", {"ref": ref, "text": "repeat"})
    assert stale.outcome == "blocked"
    result = await r.call(ctx, "android_verify_text", {"text": "hello"})
    assert result.evidence == [{"kind": "assertion", "passed": True}]
    assert len([e for e in h.android.driver.events if e[0] == "fill"]) == 1


@pytest.mark.anyio
async def test_real_send_label_cannot_be_hidden_from_policy(world):
    h, r, ctx, asked = world
    h.android.driver.xml = xml("Send")
    ref = await snapshot(r, ctx)
    result = await r.call(ctx, "android_tap", {"ref": ref})
    assert result.outcome == "ok"
    assert len(asked) == 1 and "communicate" in asked[0][0] and '"element": "Send"' in asked[0][1]
    receipt = h.store.one("SELECT effect,decision,dispatched FROM receipts WHERE tool='android_tap'")
    assert receipt["effect"] == "communicate" and receipt["dispatched"] == 1


@pytest.mark.anyio
async def test_denied_approval_never_dispatches(world):
    h, r, ctx, asked = world
    h.android.driver.xml = xml("Send")
    ref = await snapshot(r, ctx)

    async def deny(*args):
        return False

    ctx.confirm = deny
    assert (await r.call(ctx, "android_tap", {"ref": ref})).outcome == "blocked"
    assert not any(e[0] == "tap" for e in h.android.driver.events)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change", [xml("Other"), xml(x=10), xml(duplicate=True), xml(password=True), xml(package="com.bank.app")]
)
async def test_changed_target_password_sensitive_or_ambiguous_blocks_dispatch(world, change):
    h, r, ctx, asked = world
    ref = await snapshot(r, ctx)
    h.android.driver.xml = change
    assert (await r.call(ctx, "android_fill", {"ref": ref, "text": "hello"})).outcome == "blocked"
    assert not any(e[0] == "fill" for e in h.android.driver.events)


@pytest.mark.anyio
async def test_phone_change_while_owner_reviews_requires_fresh_approval(world):
    h, r, ctx, asked = world
    h.android.driver.xml = xml("Pay")
    ref = await snapshot(r, ctx)

    async def change(*args):
        h.android.driver.xml = xml("Pay", x=20)
        return True

    ctx.confirm = change
    assert (await r.call(ctx, "android_tap", {"ref": ref})).outcome == "blocked"
    assert not any(e[0] == "tap" for e in h.android.driver.events)


@pytest.mark.anyio
async def test_setup_change_and_pause_while_reviewing_stop_dispatch(world):
    h, r, ctx, asked = world
    h.android.driver.xml = xml("Send")
    ref = await snapshot(r, ctx)

    async def change(*args):
        h.android.save("another-phone", True, [])
        h.policy.pause(True)
        return True

    ctx.confirm = change
    assert (await r.call(ctx, "android_tap", {"ref": ref})).outcome == "blocked"
    assert not any(e[0] == "tap" for e in h.android.driver.events)


@pytest.mark.anyio
async def test_no_consent_no_driver_calls(world):
    h, r, ctx, asked = world
    h.android.config["driver_consent"] = False
    assert (await r.call(ctx, "android_snapshot", {"depth": 20})).outcome == "blocked"
    assert h.android.driver.events == []


@pytest.mark.anyio
async def test_live_frame_does_not_expire_refs_and_hides_credentials(world):
    h, r, ctx, asked = world
    ref = await snapshot(r, ctx)
    found = await h.android.frame("phone")
    assert found[0][:2] == b"\xff\xd8" and ref in h.android.nodes
    h.android.driver.xml = xml("secret", password=True)
    with pytest.raises(ValueError, match="credential"):
        await h.android.frame("phone")


@pytest.mark.anyio
async def test_transport_error_after_effect_is_unknown_without_retry(world):
    h, r, ctx, asked = world
    ref = await snapshot(r, ctx)
    h.android.driver.fail = ValueError("Driver disconnected")
    result = await r.call(ctx, "android_fill", {"ref": ref, "text": "hello"})
    assert result.outcome == "unknown_effect" and result.dispatched
    assert len([e for e in h.android.driver.events if e[0] == "fill"]) == 1


@pytest.mark.anyio
async def test_takeover_stops_agent_and_release_requires_snapshot(world):
    from backend.live import takeover

    h, r, ctx, asked = world
    ref = await snapshot(r, ctx)
    takeover().take("android", "phone")
    assert (await r.call(ctx, "android_fill", {"ref": ref, "text": "hello"})).outcome == "blocked"
    takeover().release("android", "phone")
    assert (await r.call(ctx, "android_fill", {"ref": ref, "text": "hello"})).outcome == "blocked"
    ref = await snapshot(r, ctx)
    assert (await r.call(ctx, "android_fill", {"ref": ref, "text": "hello"})).outcome == "ok"


@pytest.mark.anyio
async def test_external_identity_has_no_owner_approval(world):
    h, r, ctx, asked = world
    h.android.driver.xml = xml("Send")
    ctx.principal = Principal(kind="mcp_client", surface="mcp", client_id="test")
    ctx.confirm = None
    ref = await snapshot(r, ctx)
    assert (await r.call(ctx, "android_tap", {"ref": ref})).outcome == "blocked"


def test_manifest_flag_hides_tools_and_strict_schemas(world):
    h, r, ctx, asked = world
    assert r.tools(ToolView()) == []
    assert len(r.tools(ToolView(android=True))) == 13
    for tool in r.model_tools(ToolView(android=True)):
        schema = tool["parameters"]
        assert set(schema["required"]) == set(schema["properties"])
        assert schema["additionalProperties"] is False


@pytest.mark.anyio
async def test_missing_ledger_prevents_every_driver_call(world, monkeypatch):
    h, r, ctx, asked = world
    monkeypatch.setenv("PANINI_V2_LEDGER", "false")
    assert (await r.call(ctx, "android_snapshot", {"depth": 20})).outcome == "blocked"
    assert h.android.driver.events == []


@pytest.mark.anyio
async def test_pause_cancels_inflight_even_with_shadow_policy(world):
    import asyncio

    h, r, ctx, asked = world
    ref = await snapshot(r, ctx)
    original = h.android.driver.call
    entered = asyncio.Event()

    async def slow(op, **args):
        if op == "fill":
            entered.set()
            await asyncio.sleep(10)
        return await original(op, **args)

    h.android.driver.call = slow
    task = asyncio.create_task(r.call(ctx, "android_fill", {"ref": ref, "text": "hello"}))
    await asyncio.wait_for(entered.wait(), 2)
    h.policy.pause(True)
    result = await asyncio.wait_for(task, 2)
    assert result.outcome == "cancelled" and result.dispatched
    receipt = h.store.one("SELECT outcome FROM receipts WHERE tool='android_fill'")
    assert receipt["outcome"] == "cancelled"


@pytest.mark.anyio
async def test_bounded_wait_and_sensitive_app_owner_grant(world):
    h, r, ctx, asked = world
    assert (
        await r.call(ctx, "android_wait_for", {"text": "missing", "timeout_seconds": 0})
    ).outcome == "failed"
    h.android.driver.xml = xml("Search", package="com.bank.app")
    assert (await r.call(ctx, "android_snapshot", {"depth": 20})).outcome == "blocked"
    h.android.save("phone", True, ["com.bank.app"])
    assert (await r.call(ctx, "android_snapshot", {"depth": 20})).outcome == "ok"


@pytest.mark.anyio
async def test_pair_code_uses_stdin_and_never_arguments(monkeypatch):
    seen = []

    async def fixed(args, **kwargs):
        seen.append((args, kwargs))
        return "Successfully paired"

    monkeypatch.setattr(adb, "run", fixed)
    assert await adb.pair("192.168.1.2:12345", "123456") == {"paired": True}
    assert seen == [(["pair", "192.168.1.2:12345"], {"stdin": b"123456\n"})]


@pytest.mark.anyio
@pytest.mark.parametrize("label", ["OTP", "Verification code", "Credit card number", "CVC"])
async def test_security_fields_without_password_attribute_are_refused(world, label):
    h, r, ctx, asked = world
    h.android.driver.xml = xml(label)
    ref = await snapshot(r, ctx)
    assert (await r.call(ctx, "android_fill", {"ref": ref, "text": "123456"})).outcome == "blocked"
    assert not any(e[0] == "fill" for e in h.android.driver.events)


@pytest.mark.anyio
async def test_home_works_without_focused_field_but_enter_requires_one(world):
    h, r, ctx, asked = world
    h.android.driver.xml = xml(focused=False)
    assert (await r.call(ctx, "android_press", {"key": "HOME"})).outcome == "ok"
    assert (await r.call(ctx, "android_press", {"key": "ENTER"})).outcome == "blocked"
