import pytest

from backend import v2flags
from backend.admin.schema import BY_KEY, GROUPS


def test_every_flag_defaults_off_except_policy_shadow(monkeypatch):
    for key in v2flags.FLAGS:
        monkeypatch.delenv(key, raising=False)
    on = {key for key in v2flags.FLAGS if v2flags.enabled(key)}
    assert on == {"PANINI_V2_POLICY"}  # Shadow mode only logs; it never changes a decision.
    assert v2flags.value("PANINI_V2_POLICY") == "shadow"


def test_choices_reject_unknown_values_and_free_text_lists(monkeypatch):
    monkeypatch.setattr(
        v2flags, "redteam_gate", lambda path=None: {"passed": True, "reason": ""}
    )  # Parsing only.
    monkeypatch.setenv("PANINI_REMOTE_MODE", "everywhere")
    assert v2flags.value("PANINI_REMOTE_MODE") == "off"
    monkeypatch.setenv("PANINI_REMOTE_MODE", "Relay ")
    assert v2flags.value("PANINI_REMOTE_MODE") == "relay"
    monkeypatch.setenv("PANINI_CHANNELS", "slack, telegram,,")
    assert v2flags.listed("PANINI_CHANNELS") == {"slack", "telegram"}
    monkeypatch.setenv("PANINI_V2_REGISTRY", "true")
    assert v2flags.enabled("PANINI_V2_REGISTRY")


def test_unknown_flag_fails_loudly():
    with pytest.raises(KeyError):
        v2flags.value("PANINI_V2_REGISTERY")


def test_admin_lists_every_flag_in_the_v2_group():
    assert GROUPS["v2"] == "PANINI v2"
    for key, flag in v2flags.FLAGS.items():
        setting = BY_KEY[key]
        assert setting.group == "v2" and setting.advanced and setting.help
        assert setting.default == flag.default
        if setting.type == "enum":
            assert setting.options == list(flag.choices)


def test_status_reports_items(monkeypatch):
    monkeypatch.delenv("PANINI_V2_ONE_BRAIN", raising=False)
    status = v2flags.status()
    assert status["items"]["V2-06"] == {"flag": "PANINI_V2_ONE_BRAIN", "enabled": False}
