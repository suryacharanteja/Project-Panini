import asyncio
import time

import pytest

from backend.core_contracts import ApprovalLevel, EffectClass, Principal, ToolResult, ToolSpec
from backend.harness.store import Store
from backend.policy import Policy, new_grant
from backend.policy.effects import classify, classify_command, classify_words

OWNER = Principal()
PHONE = Principal(surface="phone_app")
SLACK = Principal(surface="slack")
MCP = Principal(kind="mcp_client", surface="mcp", client_id="claude")


def spec(name="browser", effect="submit", dynamic=True, group="core", capability="chrome_direct"):
    return ToolSpec(
        name,
        capability,
        "h",
        name,
        name,
        {"type": "object", "additionalProperties": False},
        EffectClass(effect),
        dynamic_effect=dynamic,
        group=group,
    )


@pytest.fixture(autouse=True)
def balanced(monkeypatch):
    monkeypatch.setenv("PANINI_APPROVALS", "risky")
    monkeypatch.delenv("PANINI_POLICY_PROFILE", raising=False)


@pytest.fixture
def policy(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    yield Policy(store)
    store.close()


# Parity with the v1 approval corpus (backend/tests/test_approvals.py)
ORDINARY = [
    "Senior AI Manager",
    "Search by title, skill, or company",
    "Search",
    "Search jobs",
    "Scroll down",
    "Date posted",
]
RISKY = [
    "Easy Apply",
    "Place order",
    "Pay now",
    "Send",
    "Post",
    "Delete account",
    "Sign in",
    "Connect",
    "Buy now",
]


@pytest.mark.parametrize("label", ORDINARY)
def test_ordinary_clicks_are_allowed_locally(policy, label):
    assert policy.decide(OWNER, spec(), {"action": "click", "element": label}).action == "allow"


@pytest.mark.parametrize("label", RISKY)
def test_risky_clicks_ask_locally(policy, label):
    decision = policy.decide(OWNER, spec(), {"action": "click", "element": label})
    assert decision.action == "ask" and decision.level is ApprovalLevel.TAP


def test_commands_match_v1(policy):
    run = spec("run_command", "local_exec", capability="computer")
    assert policy.decide(OWNER, run, {"command": "python primes.py"}).action == "allow"
    # Words inside a harmless command are not risky (v1 judges commands by command patterns only).
    assert policy.decide(OWNER, run, {"command": "Set-Content todo.txt 'buy milk'"}).action == "allow"
    for command in ("rm -rf build", "git push origin main", "pip install x", "curl https://x"):
        assert policy.decide(OWNER, run, {"command": command}).action == "ask", command


def test_cautious_profile_asks_for_every_effect(policy, monkeypatch):
    monkeypatch.setenv("PANINI_APPROVALS", "all")
    assert policy.decide(OWNER, spec(), {"action": "click", "element": "Senior AI Manager"}).action == "ask"
    assert policy.decide(OWNER, spec("list_files", "observe", False), {}).action == "allow"


def test_word_and_command_classes():
    assert classify_words("Place order") is EffectClass.TRANSACT
    assert classify_words("Send message") is EffectClass.COMMUNICATE
    assert classify_words("Delete account") is EffectClass.DESTROY
    assert classify_words("Sign in") is EffectClass.ACCOUNT
    assert classify_words("Card number") is EffectClass.CREDENTIAL
    assert classify_words("Save") is EffectClass.SUBMIT
    assert classify_words("Seven") is None
    assert classify_command("Remove-Item x")[0] is EffectClass.DESTROY
    assert classify_command("winget install x")[0] is EffectClass.INSTALL
    assert classify_command("curl https://x") == (EffectClass.SUBMIT, {"network"})


def test_effects_never_go_below_the_static_class():
    effect, _, _ = classify(spec("list_files", "local_write", False), {})
    assert effect is EffectClass.LOCAL_WRITE
    effect, _, taints = classify(spec(), {"action": "read"})
    assert effect is EffectClass.OBSERVE and taints == {"web"}


def test_remote_channel_and_external_tiers(policy):
    pay = {"action": "click", "element": "Pay now"}
    assert policy.decide(PHONE, spec(), pay).level is ApprovalLevel.VERIFY
    assert policy.decide(SLACK, spec(), pay).level is ApprovalLevel.VERIFY
    assert policy.decide(MCP, spec(), pay).action == "deny"
    assert policy.decide(MCP, spec(), {"action": "click", "element": "Search"}).level is ApprovalLevel.TAP


def test_credential_is_always_denied_even_with_a_grant(policy):
    """Invariant I5."""
    policy.put_grant(new_grant(levels={"credential": "none"}))
    decision = policy.decide(OWNER, spec(), {"action": "type", "element": "Password", "text": "x"})
    assert decision.action == "deny"


def test_background_parks_and_cannot_self_approve(policy):
    """Invariant I8: background work asks a person (park) instead of approving itself."""
    run = spec("run_command", "local_exec", capability="computer")
    assert policy.decide(OWNER, run, {"command": "rm -rf x"}, background=True).action == "park"


def test_grants_most_specific_wins_and_expire(policy):
    run = spec("run_command", "local_exec", capability="computer")
    policy.put_grant(new_grant(capability="computer", levels={"destroy": "deny"}))
    exact = new_grant(tools=("run_command",), levels={"destroy": "none"}, expires_at=time.time() + 60)
    policy.put_grant(exact)
    assert policy.decide(OWNER, run, {"command": "rm x"}).action == "allow"
    policy.revoke(exact.id)
    policy.put_grant(
        new_grant(tools=("run_command",), levels={"destroy": "none"}, expires_at=time.time() - 1)
    )
    assert policy.decide(OWNER, run, {"command": "rm x"}).action == "deny"


def test_remote_high_risk_needs_an_exact_short_lived_grant_to_drop_verify(policy):
    pay = {"action": "click", "element": "Pay now"}
    policy.put_grant(new_grant(subject="surface:phone_app", levels={"transact": "tap"}))
    assert policy.decide(PHONE, spec(), pay).level is ApprovalLevel.VERIFY
    policy.put_grant(
        new_grant(
            subject="surface:phone_app",
            tools=("browser",),
            levels={"transact": "tap"},
            expires_at=time.time() + 3600,
        )
    )
    assert policy.decide(PHONE, spec(), pay).level is ApprovalLevel.TAP


def test_acting_after_untrusted_content_asks(policy):
    run = spec("run_command", "local_exec", capability="computer")
    assert policy.decide(OWNER, run, {"command": "python tool.py"}, taint={"email"}).action == "ask"
    assert policy.decide(OWNER, run, {"command": "python tool.py"}, taint=set()).action == "allow"
    # Web content is the guardian's job (V2-13): web taint alone gates neither clicks nor benign commands.
    next_click = {"action": "click", "element": "Next"}
    assert policy.decide(OWNER, spec(), next_click, taint={"web"}).action == "allow"
    assert policy.decide(OWNER, run, {"command": "python tool.py"}, taint={"web"}).action == "allow"
    assert policy.decide(OWNER, spec(), next_click, taint={"channel"}).action == "ask"


def test_coarse_capabilities_ask_per_action_inside(policy):
    run = spec("desktop_run", "submit", True, capability="capabilities")
    assert policy.decide(OWNER, run, {"instruction": "send the email in Outlook"}).action == "allow"


def test_epoch_bumps_and_pause_denies(policy):
    before = policy.epoch()
    policy.put_grant(new_grant(levels={}))
    assert policy.epoch() == before + 1
    policy.pause(True)
    assert policy.decide(OWNER, spec("list_files", "observe", False), {}).action == "deny"
    policy.pause(False)
    assert policy.decide(OWNER, spec("list_files", "observe", False), {}).action == "allow"


def test_pause_cancels_an_in_flight_call_within_100ms(tmp_path):
    """Invariant I7."""
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext

    async def scenario():
        store = Store(tmp_path / "h.sqlite3")
        policy = Policy(store)
        hook = PolicyHook("enforce", policy)
        ctx = ToolContext()
        target = spec("list_files", "observe", False)
        await hook.before(ctx, target, {})

        async def slow():
            await asyncio.sleep(5)
            return ToolResult("ok")

        task = asyncio.ensure_future(hook.around(ctx, target, {}, slow))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        policy.pause(True)
        result = await task
        store.close()
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())
    assert result.outcome == "cancelled" and elapsed < 0.1


def test_enforce_asks_once_and_preapproves_nested_v1_prompts(tmp_path):
    from backend.policy import preapproved
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext

    asked, nested = [], []

    async def confirm(summary, detail):
        asked.append(summary)
        return True

    async def scenario():
        store = Store(tmp_path / "h.sqlite3")
        hook = PolicyHook("enforce", Policy(store))
        ctx = ToolContext(confirm=confirm)
        target = spec("run_command", "local_exec", capability="computer")
        assert await hook.before(ctx, target, {"command": "rm -rf x"}) is None

        async def hand():
            nested.append(preapproved.get())
            return ToolResult("ok")

        await hook.around(ctx, target, {"command": "rm -rf x"}, hand)
        store.close()

    asyncio.run(scenario())
    assert len(asked) == 1 and nested == [True]


def test_enforce_refuses_deny_park_and_declines(tmp_path):
    from backend.policy.hook import PolicyHook
    from backend.registry.context import ToolContext, ToolView

    async def no(summary, detail):
        return False

    async def scenario():
        store = Store(tmp_path / "h.sqlite3")
        hook = PolicyHook("enforce", Policy(store))
        run = spec("run_command", "local_exec", capability="computer")
        declined = await hook.before(ToolContext(confirm=no), run, {"command": "rm x"})
        parked = await hook.before(ToolContext(view=ToolView(background=True)), run, {"command": "rm x"})
        nobody = await hook.before(ToolContext(), run, {"command": "rm x"})
        password = {"action": "type", "element": "Password", "text": "x"}
        denied = await hook.before(ToolContext(), spec(), password)
        store.close()
        return declined, parked, nobody, denied

    declined, parked, nobody, denied = asyncio.run(scenario())
    assert "did not approve" in declined.content and "background" in parked.content
    assert "nobody" in nobody.content and "Not allowed" in denied.content


def test_shadow_records_only_disagreements(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    policy = Policy(store)
    run = spec("run_command", "local_exec", capability="computer")
    stricter = policy.decide(OWNER, run, {"command": "python x.py"}, taint={"email"})
    policy.record_shadow("browser", stricter, v1_ask=False)
    policy.record_shadow(
        "browser", policy.decide(OWNER, spec(), {"action": "click", "element": "Send"}), True
    )
    assert policy.shadow_stats() == {"more_permissive": 0, "stricter": 1}
    store.close()


def test_v1_approval_functions_feed_shadow_mode(tmp_path, monkeypatch):
    from backend import policy as policy_module
    from backend.capabilities import approvals

    store = Store(tmp_path / "h.sqlite3")
    policy_module.install(Policy(store))
    monkeypatch.setenv("PANINI_V2_POLICY", "shadow")
    assert approvals.browser_action_needs_approval({"kind": "click", "label": "Pay now"}) is True
    assert approvals.code_action_needs_approval({"type": "commandExecution", "command": "ls"}) is False
    assert policy_module.current().shadow_stats() == {"more_permissive": 0, "stricter": 0}
    store.close()


def test_policy_api(tmp_path):
    from fastapi.testclient import TestClient
    from panini_cli.agent.model import ScriptedModel

    from backend.app import create_app
    from backend.harness.runtime import Harness
    from backend.harness.skills import Skills

    h = Harness(
        Store(tmp_path / "h.sqlite3"),
        model_factory=lambda: ScriptedModel([]),
        skills=Skills(user_root=tmp_path / "skills"),
    )
    app = create_app(harness_factory=lambda: h, console_token="t" * 32)
    headers = {"x-panini-console": "t" * 32}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        view = client.get("/api/harness/policy", headers=headers).json()
        assert view["profile"] == "balanced" and view["matrix"]["credential"] == ["deny"] * 5
        made = client.post(
            "/api/harness/policy/grants",
            json={"tools": ["run_command"], "levels": {"destroy": "tap"}},
            headers=headers,
        )
        assert len(client.get("/api/harness/policy", headers=headers).json()["grants"]) == 1
        bad = client.post("/api/harness/policy/grants", json={"levels": {"magic": "tap"}}, headers=headers)
        assert bad.status_code == 422
        assert client.post("/api/harness/policy/pause", json={"paused": True}, headers=headers).json()[
            "paused"
        ]
        assert h.policy.paused()
        assert client.delete(f"/api/harness/policy/grants/{made.json()['id']}", headers=headers).json()[
            "revoked"
        ]
