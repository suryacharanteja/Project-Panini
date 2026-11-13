"""V2-26 common snapshot grammar and sandbox ref translation."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.hands.android_tree import tree as android_tree
from backend.hands.sandbox import SandboxHand
from backend.hands.snapshot import Node, normalize_aria, parse, render
from backend.tests.hands.test_sandbox import World


def test_all_hand_prefixes_round_trip_and_hide_passwords():
    for prefix in "ewas":
        nodes = [
            Node(f"{prefix}1", "window", "One", depth=0),
            Node(f"{prefix}2", "textbox", "Password field", "top secret", {"password"}, (1, 2, 30, 40), 1),
        ]
        text = render(nodes)
        assert "top secret" not in text
        restored = parse(text)
        assert restored[0] == nodes[0]
        assert restored[1].value is None
        assert restored[1].box == (1, 2, 30, 40)
    with pytest.raises(ValueError, match="duplicate"):
        render([Node("s1", "button"), Node("s1", "button")])


def test_playwright_to_sandbox_grammar_keeps_only_addressable_nodes():
    source = (
        "### Page\nPage URL: https://example.com/\n"
        '- window "Owner" [ref=e1]\n'
        '  - textbox "Password" [ref=e2] [value="secret"] [box=1,2,3,4]\n'
        '  - button "Save" [ref=e3] [disabled] [cursor=pointer]'
    )
    text, mapping = normalize_aria(source)
    assert mapping == {"s1": "e1", "s2": "e2", "s3": "e3"}
    assert "secret" not in text and "Password field" in text
    nodes = parse(text)
    assert nodes[1].box == (1, 2, 3, 4)
    assert "disabled" in nodes[2].states
    web, web_mapping = normalize_aria(source, prefix="e")
    assert [node.role for node in parse(web)] == ["window", "textbox", "button"]
    assert web_mapping == {"e1": "e1", "e2": "e2", "e3": "e3"}
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_aria('- button "One" [ref=e1]\n- button "Two" [ref=e1]')


def test_android_tree_uses_the_same_parser_and_a_refs():
    xml = (
        '<hierarchy><node text="Open" content-desc="" class="android.widget.Button" '
        'package="com.example" resource-id="com.example:id/open" enabled="true" '
        'password="false" focused="false" bounds="[1,2][31,42]" /></hierarchy>'
    )
    _, _, text = android_tree(xml, generation=1)
    nodes = parse(text)
    assert len(nodes) == 1 and nodes[0].ref == "a1001"
    assert nodes[0].role == "button" and nodes[0].box == (1, 2, 30, 40)


def test_sandbox_uses_its_snapshot_generation_for_translated_refs(tmp_path):
    world = World(tmp_path)
    received = []

    def respond(request):
        received.append((str(request.url), json.loads(request.content or b"{}")))
        if str(request.url).endswith("/snapshot"):
            return httpx.Response(200, json={"text": '- button "Save" [ref=e7]', "snapshotId": 9})
        return httpx.Response(200, json={"text": "clicked"})

    world.handler = respond
    hand = SandboxHand(None, {"harness": SimpleNamespace(sandbox=world.service)})
    ctx = SimpleNamespace(principal=SimpleNamespace(persona=None))
    spec = lambda name: SimpleNamespace(name=name)  # noqa: E731
    try:
        snapshot = asyncio.run(hand.call(ctx, spec("sandbox_snapshot"), {}))
        assert snapshot.outcome == "ok" and "[ref=s1001]" in snapshot.data["text"]
        stale = asyncio.run(hand.call(ctx, spec("sandbox_click"), {"ref": "s1001", "snapshotId": 8}))
        assert stale.outcome == "blocked" and "Stale" in stale.content
        assert not any(url.endswith("/click") for url, _ in received)
        clicked = asyncio.run(hand.call(ctx, spec("sandbox_click"), {"ref": "s1001", "snapshotId": 9}))
        assert clicked.outcome == "ok" and received[-1][1]["ref"] == "e7"
        reused = asyncio.run(hand.call(ctx, spec("sandbox_click"), {"ref": "s1001", "snapshotId": 9}))
        assert reused.outcome == "blocked" and "Stale" in reused.content
        second = asyncio.run(hand.call(ctx, spec("sandbox_snapshot"), {}))
        assert "[ref=s2001]" in second.data["text"]
        old_generation = asyncio.run(hand.call(ctx, spec("sandbox_click"), {"ref": "s1001", "snapshotId": 9}))
        assert old_generation.outcome == "blocked"
    finally:
        world.store.close()
