"""Configuration must not be able to weaken a security control (DD-145)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from panini.errors import ConfigError
from panini.store.config import (
    FORBIDDEN_KEYS,
    Config,
    SecurityConfig,
    load_config,
)

pytestmark = pytest.mark.security


def write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "panini.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


# ── controls have no keys ───────────────────────────────────────────────────

@pytest.mark.parametrize("dotted", FORBIDDEN_KEYS)
def test_forbidden_key_is_rejected_loudly(tmp_path: Path, dotted: str) -> None:
    """A user who writes one of these should learn it does not exist."""
    parts = dotted.split(".")
    data: dict = {}
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = False

    with pytest.raises(ConfigError, match="does not exist"):
        load_config(write(tmp_path, data))


def test_security_model_has_no_disable_fields() -> None:
    fields = set(SecurityConfig.model_fields)
    for forbidden in ("taint_tracking", "audit_enabled", "require_approval", "always_allow"):
        assert forbidden not in fields


# ── clamping ────────────────────────────────────────────────────────────────

def test_approval_timeout_cannot_be_unbounded(tmp_path: Path) -> None:
    """An unbounded timeout would leave a dialog answerable forever."""
    cfg, warnings = load_config(write(tmp_path, {"security": {"approval_timeout_s": 5}}))
    assert cfg.security.approval_timeout_s == 5
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"security": {"approval_timeout_s": 999_999}}))


def test_clamping_is_reported(tmp_path: Path) -> None:
    """A silently altered setting looks like a bug; say so."""
    cfg = Config()
    cfg.tools.shell.max_output_bytes = 999_999_999
    from panini.store.config import apply_security_floor

    notes = apply_security_floor(cfg)
    assert cfg.tools.shell.max_output_bytes == 102_400
    assert any("security floor" in n for n in notes)


def test_shell_output_capped_at_100kb(tmp_path: Path) -> None:
    """Unbounded output could evict the injection warning from context (DD-130)."""
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"tools": {"shell": {"max_output_bytes": 50_000_000}}}))


# ── measured constraints ────────────────────────────────────────────────────

def test_chunk_cap_above_measured_knee_is_refused(tmp_path: Path) -> None:
    """60 chars is the measured cost knee, not a style preference (DD-124)."""
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, {"voice": {"tts": {"chunk": {"max_chars": 120}}}}))
    assert "5.4" in str(exc.value)  # the error explains why


def test_chunk_ordering_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="min_chars"):
        load_config(
            write(tmp_path, {"voice": {"tts": {"chunk": {
                "first_max_chars": 55, "max_chars": 40,
            }}}})
        )


def test_num_gpu_is_required(tmp_path: Path) -> None:
    """Omitting it is a silent 60% throughput loss no functional test would catch."""
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, {"llm": {"ollama": {"options": {"num_ctx": 8192}}}}))
    assert "60%" in str(exc.value)


# ── defaults and resilience ─────────────────────────────────────────────────

def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    cfg, warnings = load_config(tmp_path / "absent.yaml")
    assert cfg.llm.ollama.options.num_gpu == 29
    assert cfg.voice.stt.model == "base"
    assert any("not found" in w for w in warnings)


def test_empty_file_uses_defaults(tmp_path: Path) -> None:
    p = tmp_path / "panini.yaml"
    p.write_text("", encoding="utf-8")
    cfg, _ = load_config(p)
    assert cfg.voice.tts.chunk.first_max_chars == 40


def test_unknown_key_does_not_brick(tmp_path: Path) -> None:
    """A config from a newer version must not break an older binary."""
    cfg, _ = load_config(write(tmp_path, {"future_feature": {"enabled": True}}))
    assert cfg.llm.provider == "ollama"


def test_defaults_match_measured_decisions() -> None:
    """Guard against a well-meaning edit reverting a measured value."""
    cfg = Config()
    assert cfg.voice.stt.model == "base"              # DD-020, not `small`
    assert cfg.llm.ollama.options.num_gpu == 29       # DD-102
    assert cfg.llm.ollama.options.num_ctx == 8192     # DD-103
    assert cfg.voice.tts.chunk.first_max_chars == 40  # DD-124
    assert cfg.voice.tts.warm_on_start is True        # DD-105
    assert cfg.avatar.prefer_integrated_gpu is True   # DD-101
    assert cfg.memory.facts.retrieval == "lexical"    # DD-119
