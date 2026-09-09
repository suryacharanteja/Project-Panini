"""Negative tests for audit append-only enforcement (DD-141).

These assert the database REFUSES mutation. If they pass while the triggers are
missing, the accountability guarantee in FR-560/FR-563 is fiction.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from panini.errors import AuditWriteFailure
from panini.security.types import (
    ApprovalReason,
    GateDecision,
    Outcome,
    Provenance,
    Tier,
)
from panini.store.audit import AuditStore

pytestmark = pytest.mark.security


def decision(escalated: bool = False) -> GateDecision:
    return GateDecision(
        declared_tier=Tier.T1_WRITE,
        effective_tier=Tier.T2_EXECUTE if escalated else Tier.T1_WRITE,
        approval_required=True,
        reason=ApprovalReason.TAINT_ESCALATED if escalated else ApprovalReason.TIER,
        is_egress=True,
        destination="sarah@example.com",
    )


@pytest.fixture
async def store(tmp_path: Path):
    s = AuditStore(tmp_path / "audit.db")
    await s.open()
    yield s
    await s.close()


async def _insert(store: AuditStore, invocation_id: str = "inv-1", **kw) -> int:
    return await store.begin(
        session_id=kw.get("session_id", "sess-1"),
        turn_id=kw.get("turn_id", "turn-1"),
        invocation_id=invocation_id,
        tool_name=kw.get("tool_name", "gmail_send"),
        args=kw.get("args", {"to": "sarah@example.com", "body": "hi"}),
        provenance=Provenance.RETRIEVED,
        tainted=kw.get("tainted", True),
        taint_sources=kw.get("taint_sources", ["gmail:inbox"]),
        decision=kw.get("decision", decision()),
    )


# ── the triggers ────────────────────────────────────────────────────────────

async def test_delete_is_refused(store: AuditStore, tmp_path: Path) -> None:
    await _insert(store)
    conn = sqlite3.connect(tmp_path / "audit.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit WHERE id=1")
    conn.close()


async def test_resolved_entry_is_immutable(store: AuditStore, tmp_path: Path) -> None:
    aid = await _insert(store)
    await store.complete(aid, Outcome.OK, approval_granted=True)

    conn = sqlite3.connect(tmp_path / "audit.db")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE audit SET outcome='denied' WHERE id=?", (aid,))
    conn.close()


async def test_identity_fields_frozen_even_while_pending(
    store: AuditStore, tmp_path: Path
) -> None:
    """A caller must not be able to resolve a row while rewriting what it recorded."""
    aid = await _insert(store)
    conn = sqlite3.connect(tmp_path / "audit.db")
    for column, value in [
        ("tool_name", "fs_read"),
        ("args_redacted", "{}"),
        ("tainted", 0),
        ("effective_tier", 0),
        ("is_egress", 0),
        ("turn_id", "other-turn"),
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(f"UPDATE audit SET {column}=? WHERE id=?", (value, aid))
    conn.close()


async def test_pending_entry_can_be_resolved(store: AuditStore) -> None:
    """The legitimate write path still works (FR-562)."""
    aid = await _insert(store)
    await store.complete(aid, Outcome.OK, approval_granted=True)
    entry = (await store.query(turn_id="turn-1"))[0]
    assert entry.outcome == "ok"
    assert entry.approval_granted is True
    assert entry.ts_end is not None


async def test_store_exposes_no_delete_or_update(store: AuditStore) -> None:
    """The missing methods are the design intent made obvious."""
    assert not hasattr(store, "delete")
    assert not hasattr(store, "update")
    assert not hasattr(store, "purge")


# ── recorded content ────────────────────────────────────────────────────────

async def test_escalation_is_visible_in_the_record(store: AuditStore) -> None:
    """Both tiers are stored so the log shows *why* a prompt appeared."""
    await _insert(store, "inv-esc", decision=decision(escalated=True))
    entry = (await store.query())[0]
    assert entry.declared_tier == int(Tier.T1_WRITE)
    assert entry.effective_tier == int(Tier.T2_EXECUTE)
    assert entry.was_escalated is True
    assert entry.approval_reason == ApprovalReason.TAINT_ESCALATED.value


async def test_taint_sources_recorded(store: AuditStore) -> None:
    await _insert(store, "inv-src", taint_sources=["gmail:inbox", "web:example.com"])
    entry = (await store.query())[0]
    assert entry.taint_sources == ["gmail:inbox", "web:example.com"]


async def test_credentials_are_redacted(store: AuditStore) -> None:
    await _insert(
        store, "inv-red",
        args={"to": "x@y.com", "api_key": "sk-abc123def456ghi789", "password": "hunter2"},
    )
    entry = (await store.query())[0]
    assert "sk-abc123def456ghi789" not in entry.args_redacted
    assert "hunter2" not in entry.args_redacted
    assert "<redacted>" in entry.args_redacted
    assert "x@y.com" in entry.args_redacted  # non-secret args survive


async def test_secret_looking_value_redacted_under_innocuous_key(store: AuditStore) -> None:
    await _insert(store, "inv-pat", args={"note": "ghp_abcdefghijklmnopqrstuvwxyz01"})
    entry = (await store.query())[0]
    assert "ghp_abcdefghijklmnopqrstuvwxyz01" not in entry.args_redacted


# ── failure behaviour ───────────────────────────────────────────────────────

async def test_write_failure_raises_audit_write_failure(tmp_path: Path) -> None:
    """AuditWriteFailure is what makes the gateway abort the tool call."""
    store = AuditStore(tmp_path / "audit.db")
    await store.open()
    await store.close()  # connection now gone
    with pytest.raises(AuditWriteFailure):
        await _insert(store)


async def test_duplicate_invocation_id_rejected(store: AuditStore) -> None:
    await _insert(store, "inv-dup")
    with pytest.raises(AuditWriteFailure):
        await _insert(store, "inv-dup")
