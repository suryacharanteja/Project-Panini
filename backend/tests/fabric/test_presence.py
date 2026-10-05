import pytest

from backend.fabric.presence import Presence
from backend.harness.store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "presence.sqlite3")
    clock = [1000.0]
    presence = Presence(store, clock=lambda: clock[0])
    yield presence, clock
    store.close()


def pc(idle=0, **changes):
    return {"idle_s": idle, "locked": False, "suspended": False, "avatar_visible": True, **changes}


def test_presence_boundaries_and_stale_pc_do_not_imply_owner_activity(state):
    presence, clock = state
    assert presence.status()["state"] == "away"
    for idle, expected in [(0, "at_pc"), (119, "at_pc"), (120, "near_pc"), (899, "near_pc"), (900, "away")]:
        assert presence.signal("pc", pc(idle))["state"] == expected
    presence.signal("pc", pc())
    clock[0] += 30
    assert presence.status()["state"] == "away"
    assert presence.status()["signals"][0]["fresh"] is False


def test_phone_freshness_is_distinct_from_recent_presence_and_clock_rollback(state):
    presence, clock = state
    presence.signal("phone:paired", {"foreground": True})
    clock[0] += 60
    assert presence.status()["state"] == "away_with_phone"
    assert presence.status()["signals"][0]["fresh"] is False
    clock[0] += 540
    assert presence.status()["state"] == "away"
    clock[0] = 999
    assert presence.status()["state"] == "away"


def test_lock_suspend_call_precedence_and_push_signal(state):
    presence, _ = state
    presence.signal("push", {})
    assert presence.signal("pc", pc(locked=True))["state"] == "away_with_phone"
    assert presence.signal("pc", pc(suspended=True))["state"] == "away_with_phone"
    presence.signal("pc", pc())
    assert presence.status(on_call=True)["state"] == "on_call"
    assert presence.status()["state"] == "at_pc"


def test_restart_restores_history_but_never_a_fresh_pc(state):
    presence, clock = state
    presence.signal("pc", pc())
    presence.signal("phone:paired", {"foreground": False})
    recovered = Presence(presence.store, clock=lambda: clock[0])
    assert recovered.status()["state"] == "away_with_phone"
    assert all(not signal["fresh"] for signal in recovered.status()["signals"])


@pytest.mark.parametrize(
    "source,data",
    [
        ("pc", pc(float("nan"))),
        ("pc", pc(-1)),
        ("pc", pc(locked="false")),
        ("phone:p", {"foreground": 1}),
        ("channel", {"timestamp": 9}),
        ("other", {}),
    ],
)
def test_signals_reject_spoofed_timestamps_and_wrong_types(state, source, data):
    with pytest.raises(ValueError):
        state[0].signal(source, data)
