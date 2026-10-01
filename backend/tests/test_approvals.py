import pytest

from backend.capabilities import approvals


@pytest.fixture(autouse=True)
def risky(monkeypatch):
    monkeypatch.setenv("PANINI_APPROVALS", "risky")


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "click", "label": "Senior AI Manager"},
        {"kind": "fill", "label": "Search by title, skill, or company", "text": "AI manager"},
        {"kind": "press_enter", "label": "Search"},
        {"kind": "submit", "label": "Search jobs"},
        {"kind": "navigate", "url": "https://www.linkedin.com/jobs"},
        {"kind": "scroll", "label": "Scroll down"},
        {"kind": "click", "label": "Date posted", "href": "https://www.linkedin.com/messaging"},
    ],
)
def test_ordinary_browser_actions_run_without_asking(action):
    assert not approvals.browser_action_needs_approval(action)


@pytest.mark.parametrize(
    "label",
    [
        "Easy Apply",
        "Place order",
        "Pay now",
        "Send",
        "Post",
        "Delete account",
        "Sign in",
        "Connect",
        "Buy now",
    ],
)
def test_irreversible_or_outward_browser_actions_ask(label):
    assert approvals.browser_action_needs_approval({"kind": "click", "label": label})


def test_code_edits_run_and_only_risky_commands_ask():
    assert not approvals.code_action_needs_approval({"type": "fileChange"})
    assert not approvals.code_action_needs_approval(
        {"type": "commandExecution", "command": "python primes.py"}
    )
    assert approvals.code_action_needs_approval({"type": "commandExecution", "command": "rm -rf build"})
    assert approvals.code_action_needs_approval(
        {"type": "commandExecution", "command": "git push origin main"}
    )


def test_desktop_asks_only_for_risky_controls():
    assert not approvals.desktop_action_needs_approval("invoke", "Seven")
    assert approvals.desktop_action_needs_approval("invoke", "Save")


def test_strict_mode_asks_for_everything(monkeypatch):
    monkeypatch.setenv("PANINI_APPROVALS", "all")
    assert approvals.browser_action_needs_approval({"kind": "click", "label": "Senior AI Manager"})
    assert approvals.code_action_needs_approval({"type": "fileChange"})
