"""Negative tests for egress classification and taint escalation.

These assert the system REFUSES or ESCALATES. A happy-path suite would pass with
the entire content-initiated security model broken, which is why these are
marked and run as a blocking gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from panini.security.egress import EgressClassifier, SyncedRootRegistry
from panini.security.permissions import DestinationAllowlist, PermissionResolver
from panini.security.taint import TaintState
from panini.security.types import ApprovalReason, EgressClass, Tier, ToolSpec

pytestmark = pytest.mark.security


# ── fixtures ────────────────────────────────────────────────────────────────

def spec(name: str, tier: Tier, egress: EgressClass, predicate: str | None = None) -> ToolSpec:
    return ToolSpec(name=name, tier=tier, egress_class=egress, egress_predicate=predicate)


@pytest.fixture
def classifier(tmp_path: Path) -> EgressClassifier:
    synced = tmp_path / "OneDrive"
    synced.mkdir()
    return EgressClassifier(SyncedRootRegistry([synced]))


@pytest.fixture
def resolver() -> PermissionResolver:
    return PermissionResolver(DestinationAllowlist({"sarah@example.com"}))


# ── egress classification ───────────────────────────────────────────────────

def test_unknown_egress_class_treated_as_always(classifier: EgressClassifier) -> None:
    """A tool that never declared its egress behaviour must not be trusted."""
    s = spec("mystery_tool", Tier.T1_WRITE, EgressClass.UNKNOWN)
    assert classifier.classify(s, {}, tainted=False).is_egress is True


def test_conditional_without_registered_predicate_fails_closed(
    classifier: EgressClassifier,
) -> None:
    s = ToolSpec(
        name="odd",
        tier=Tier.T1_WRITE,
        egress_class=EgressClass.CONDITIONAL,
        egress_predicate="not_registered",
    )
    assert classifier.classify(s, {}, tainted=False).is_egress is True


def test_conditional_requires_predicate_at_construction() -> None:
    with pytest.raises(ValueError, match="requires an egress_predicate"):
        ToolSpec(name="bad", tier=Tier.T1_WRITE, egress_class=EgressClass.CONDITIONAL)


def test_shell_is_always_egress(classifier: EgressClassifier) -> None:
    """curl is one command away — no argument inspection makes shell safe."""
    s = spec("shell_exec", Tier.T2_EXECUTE, EgressClass.ALWAYS)
    ev = classifier.classify(s, {"command": "ls -la"}, tainted=False)
    assert ev.is_egress is True


def test_calendar_with_attendees_is_egress(classifier: EgressClassifier) -> None:
    """Attendees receive an invitation — a local-looking write that transmits."""
    s = spec("calendar_create", Tier.T1_WRITE, EgressClass.CONDITIONAL, "calendar_has_attendees")
    ev = classifier.classify(
        s,
        {"title": "Sync", "description": "secret data", "attendees": ["x@evil.com"]},
        tainted=False,
    )
    assert ev.is_egress is True
    assert ev.destination == "x@evil.com"
    assert "secret data" in (ev.payload_preview or "")


def test_calendar_without_attendees_is_not_egress(classifier: EgressClassifier) -> None:
    s = spec("calendar_create", Tier.T1_WRITE, EgressClass.CONDITIONAL, "calendar_has_attendees")
    ev = classifier.classify(s, {"title": "Solo work", "attendees": []}, tainted=False)
    assert ev.is_egress is False


def test_search_query_is_egress_when_tainted(classifier: EgressClassifier) -> None:
    """The query itself is outbound data."""
    s = spec("web_search", Tier.T0_READ, EgressClass.CONDITIONAL, "query_carries_content")
    assert classifier.classify(s, {"query": "api key sk-123"}, tainted=True).is_egress is True


def test_search_query_not_egress_when_clean(classifier: EgressClassifier) -> None:
    s = spec("web_search", Tier.T0_READ, EgressClass.CONDITIONAL, "query_carries_content")
    assert classifier.classify(s, {"query": "weather"}, tainted=False).is_egress is False


def test_fs_write_to_synced_folder_is_egress(
    classifier: EgressClassifier, tmp_path: Path
) -> None:
    s = spec("fs_write", Tier.T1_WRITE, EgressClass.CONDITIONAL, "fs_write_synced")
    target = tmp_path / "OneDrive" / "leak.txt"
    ev = classifier.classify(s, {"path": str(target), "content": "data"}, tainted=False)
    assert ev.is_egress is True
    assert "synced:" in (ev.destination or "")


def test_fs_write_to_local_folder_is_not_egress(
    classifier: EgressClassifier, tmp_path: Path
) -> None:
    s = spec("fs_write", Tier.T1_WRITE, EgressClass.CONDITIONAL, "fs_write_synced")
    target = tmp_path / "local" / "notes.txt"
    assert classifier.classify(s, {"path": str(target)}, tainted=False).is_egress is False


# ── taint escalation ────────────────────────────────────────────────────────

def test_t1_escalates_to_t2_when_tainted(
    resolver: PermissionResolver, classifier: EgressClassifier
) -> None:
    s = spec("fs_write", Tier.T1_WRITE, EgressClass.NONE)
    d = resolver.decide(s, classifier.classify(s, {}, True), tainted=True)
    assert d.effective_tier is Tier.T2_EXECUTE
    assert d.was_escalated is True
    assert d.reason is ApprovalReason.TAINT_ESCALATED
    assert d.approval_required is True


def test_t0_is_not_escalated(resolver: PermissionResolver, classifier: EgressClassifier) -> None:
    """Escalating reads would prompt on every inbox summary — approval fatigue."""
    s = spec("fs_read", Tier.T0_READ, EgressClass.NONE)
    d = resolver.decide(s, classifier.classify(s, {}, True), tainted=True)
    assert d.effective_tier is Tier.T0_READ
    assert d.approval_required is False


def test_egress_while_tainted_always_requires_approval(
    resolver: PermissionResolver, classifier: EgressClassifier
) -> None:
    """Even a T0 read prompts if it can transmit — this is the T-07 defence."""
    s = spec("web_fetch", Tier.T0_READ, EgressClass.ALWAYS)
    d = resolver.decide(s, classifier.classify(s, {"url": "http://x/?d=1"}, True), tainted=True)
    assert d.approval_required is True
    assert d.reason is ApprovalReason.EGRESS_TAINTED


def test_unknown_destination_prompts_even_when_clean(
    resolver: PermissionResolver, classifier: EgressClassifier
) -> None:
    s = spec("gmail_send", Tier.T1_WRITE, EgressClass.ALWAYS)
    d = resolver.decide(s, classifier.classify(s, {"to": "stranger@x.com"}, False), tainted=False)
    assert d.approval_required is True


def test_clean_t0_no_egress_does_not_prompt(
    resolver: PermissionResolver, classifier: EgressClassifier
) -> None:
    s = spec("fs_read", Tier.T0_READ, EgressClass.NONE)
    d = resolver.decide(s, classifier.classify(s, {}, False), tainted=False)
    assert d.approval_required is False
    assert d.reason is None


# ── taint state ─────────────────────────────────────────────────────────────

def test_taint_is_monotonic() -> None:
    t = TaintState()
    assert t.tainted is False
    t.mark("gmail:inbox")
    assert t.tainted is True
    t.mark("web:example.com")
    assert t.tainted is True
    assert t.sources == ["gmail:inbox", "web:example.com"]


def test_taint_has_no_clear_method() -> None:
    """The absence is the design — taint ends only when the Turn is discarded."""
    assert not hasattr(TaintState(), "clear")
    assert not hasattr(TaintState(), "reset")


def test_taint_sources_deduplicated() -> None:
    t = TaintState()
    t.mark("gmail:inbox")
    t.mark("gmail:inbox")
    assert t.sources == ["gmail:inbox"]


def test_taint_sources_are_a_copy() -> None:
    t = TaintState()
    t.mark("a")
    t.sources.append("forged")
    assert t.sources == ["a"]
