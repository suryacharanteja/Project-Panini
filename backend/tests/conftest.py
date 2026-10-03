import os

import pytest

# backend.app builds the app at import and loads the developer's .env; explicit values win over .env.
# Pin capability switches so tests never depend on which capabilities that machine has turned on.
for _name in ("PANINI_CLI_ENABLED", "JEV_REV_DESKTOP_ENABLED", "PANINI_CAPABILITY_ROUTER_ENABLED"):
    os.environ[_name] = "false"


@pytest.fixture(autouse=True)
def isolated_runtime_data(tmp_path, monkeypatch):
    # Pairings and other runtime state must never touch the developer's real profile.
    monkeypatch.setenv("PANINI_DATA_DIR", str(tmp_path / "panini-data"))
    monkeypatch.delenv("PANINI_CONSOLE_TOKEN", raising=False)
    # Jev routing and completion checks call a live service; tests opt in with a fake.
    monkeypatch.setenv("PANINI_JEV_GATES", "false")
    # Confirmation mechanics are tested in strict mode; the risky default has its own tests.
    monkeypatch.setenv("PANINI_APPROVALS", "all")


@pytest.fixture(autouse=True)
def reset_v2_services():
    """Process-wide v2 services (policy, ledger, interrupts) are installed by a Harness; never leak them."""
    yield
    from backend import interrupts, ledger, policy

    policy._policy = None
    ledger._ledger = ledger._artifacts = None
    interrupts._service = None


@pytest.fixture(autouse=True)
def _fresh_live_state(monkeypatch):
    """Live view frames and takeover state (V2-12) are process-wide; each test starts clean."""
    from backend import live

    monkeypatch.setattr(live, "_hub", None)
    monkeypatch.setattr(live, "_takeover", None)
