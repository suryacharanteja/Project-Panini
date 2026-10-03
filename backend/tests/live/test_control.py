"""V2-12 takeover: the agent is blocked while you hold a hand, and must look again after you hand it back."""

import pytest

from backend.live.control import ControlBlocked, Takeover


def test_agent_is_blocked_while_the_owner_holds_control_and_must_re_snapshot_after_release():
    events = []
    takeover = Takeover(on_change=lambda hand, target, view: events.append((hand, view["holder"])))
    takeover.gate("managed", "default", observe=False)  # Never taken: no state, no block.
    takeover.take("managed", "default")
    with pytest.raises(ControlBlocked, match="You have control of PANINI's browser"):
        takeover.gate("managed", "default", observe=True)
    takeover.gate("sandbox", "panini", observe=False)  # Other hands carry on.
    view = takeover.release("managed", "default")
    assert view["holder"] == "agent" and view["resumeSnapshotRequired"]
    with pytest.raises(ControlBlocked, match="Look at it again"):
        takeover.gate("managed", "default", observe=False)
    takeover.gate("managed", "default", observe=True)  # A snapshot clears it.
    takeover.gate("managed", "default", observe=False)
    assert events == [("managed", "human"), ("managed", "agent")]


def test_the_agent_can_ask_for_a_takeover():
    takeover = Takeover()
    view = takeover.request_takeover("chrome", "tab-1", "Please sign in to your bank.")
    assert view["requested"] and view["request"]["status"] == "waiting"
    assert takeover.take("chrome", "tab-1")["request"]["status"] == "taken"
    assert takeover.holding()[0]["hand"] == "chrome"
    assert takeover.release("chrome", "tab-1")["request"] is None


def test_saying_carry_on_gives_back_every_hand_you_hold(monkeypatch):
    from backend import live
    from backend.panini.coordinator import hand_back

    monkeypatch.setenv("PANINI_LIVE_VIEW", "true")
    assert hand_back("carry on") is None  # Nothing held: an ordinary sentence for the planner.
    live.takeover().take("desktop", "pc")
    assert hand_back("draw a cat") is None
    assert hand_back("Okay, carry on.") == "Thanks, carrying on."
    assert live.takeover().state("desktop", "pc").holder == "agent"
