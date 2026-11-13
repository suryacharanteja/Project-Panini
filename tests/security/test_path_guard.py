"""Path confinement negative tests.

Every test here asserts a refusal. The ordering tests matter most: resolve
before checking, and deny before allow. Reversing either silently opens a hole
that a happy-path suite would never notice.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from panini.errors import PathEscape
from panini.tools.paths import AccessMode, PathGuard

pytestmark = pytest.mark.security


@pytest.fixture
def layout(tmp_path: Path):
    docs = tmp_path / "Documents" / "Panini"
    audit = tmp_path / "AppData" / "Panini"
    creds = tmp_path / "config" / "panini"
    synced = tmp_path / "OneDrive"
    for d in (docs, audit, creds, synced):
        d.mkdir(parents=True)
    (audit / "audit.db").write_text("evidence", encoding="utf-8")
    (docs / "notes.txt").write_text("hello", encoding="utf-8")
    return {"root": tmp_path, "docs": docs, "audit": audit, "creds": creds, "synced": synced}


@pytest.fixture
def guard(layout) -> PathGuard:
    return PathGuard(
        allow_roots=[layout["docs"], layout["synced"]],
        deny_roots=[layout["audit"], layout["creds"]],
        synced_roots=[layout["synced"]],
    )


# ── the happy path still works ──────────────────────────────────────────────

def test_allowed_path_resolves(guard: PathGuard, layout) -> None:
    rp = guard.resolve_and_check(str(layout["docs"] / "notes.txt"))
    assert rp.path.name == "notes.txt"
    assert rp.is_synced is False


# ── traversal and confinement ───────────────────────────────────────────────

def test_traversal_outside_allow_roots_is_refused(guard: PathGuard, layout) -> None:
    with pytest.raises(PathEscape, match="outside the configured directories"):
        guard.resolve_and_check(str(layout["docs"] / ".." / ".." / "secret.txt"))


def test_absolute_path_outside_roots_is_refused(guard: PathGuard) -> None:
    with pytest.raises(PathEscape):
        guard.resolve_and_check("C:/Windows/System32/config/SAM")


def test_traversal_into_audit_is_refused(guard: PathGuard, layout) -> None:
    """Deny is checked before allow, so this is caught even via a relative walk."""
    sneaky = layout["docs"] / ".." / ".." / "AppData" / "Panini" / "audit.db"
    with pytest.raises(PathEscape, match="protected location"):
        guard.resolve_and_check(str(sneaky))


def test_audit_path_refused_even_when_inside_an_allow_root(layout) -> None:
    """The ordering test: a broad allow_root must not expose the audit store."""
    guard = PathGuard(
        allow_roots=[layout["root"]],          # deliberately over-broad
        deny_roots=[layout["audit"]],
    )
    # A normal file under the broad root is fine …
    assert guard.resolve_and_check(str(layout["docs"] / "notes.txt"))
    # … but the audit store is still unreachable.
    with pytest.raises(PathEscape, match="protected location"):
        guard.resolve_and_check(str(layout["audit"] / "audit.db"))


def test_credentials_path_is_refused(guard: PathGuard, layout) -> None:
    with pytest.raises(PathEscape, match="protected location"):
        guard.resolve_and_check(str(layout["creds"] / "credentials.enc"))


# ── symlinks: resolve before classify ───────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privilege on Windows")
def test_symlink_into_audit_is_refused(guard: PathGuard, layout) -> None:
    link = layout["docs"] / "innocent.txt"
    link.symlink_to(layout["audit"] / "audit.db")
    with pytest.raises(PathEscape, match="protected location"):
        guard.resolve_and_check(str(link))


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privilege on Windows")
def test_symlink_into_synced_folder_classifies_as_synced(guard: PathGuard, layout) -> None:
    """Resolution happens first, so the link cannot disguise the destination."""
    link = layout["docs"] / "local-looking.txt"
    link.symlink_to(layout["synced"] / "leaked.txt")
    rp = guard.resolve_and_check(str(link), AccessMode.WRITE)
    assert rp.is_synced is True


def test_direct_write_to_synced_folder_is_flagged(guard: PathGuard, layout) -> None:
    rp = guard.resolve_and_check(str(layout["synced"] / "notes.txt"), AccessMode.WRITE)
    assert rp.is_synced is True
    assert rp.synced_root == layout["synced"].resolve()


# ── Windows-specific hazards ────────────────────────────────────────────────

def test_unc_path_is_refused(guard: PathGuard) -> None:
    with pytest.raises(PathEscape, match="UNC"):
        guard.resolve_and_check(r"\\attacker\share\payload.txt")


def test_forward_slash_unc_is_refused(guard: PathGuard) -> None:
    with pytest.raises(PathEscape, match="UNC"):
        guard.resolve_and_check("//attacker/share/payload.txt")


def test_alternate_data_stream_is_refused(guard: PathGuard, layout) -> None:
    with pytest.raises(PathEscape, match="alternate data stream"):
        guard.resolve_and_check(str(layout["docs"] / "notes.txt") + ":hidden")


@pytest.mark.parametrize("name", ["CON", "NUL", "COM1", "LPT1", "AUX", "con.txt"])
def test_reserved_device_names_are_refused(guard: PathGuard, layout, name: str) -> None:
    with pytest.raises(PathEscape, match="reserved device name"):
        guard.resolve_and_check(str(layout["docs"] / name))


# ── malformed input ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_path_is_refused(guard: PathGuard, bad: str) -> None:
    with pytest.raises(PathEscape, match="empty path"):
        guard.resolve_and_check(bad)


def test_case_insensitive_containment(layout) -> None:
    """A rule written in one case must not be evaded by another."""
    guard = PathGuard(allow_roots=[layout["docs"]], deny_roots=[layout["audit"]])
    upper = str(layout["audit"]).upper() + os.sep + "audit.db"
    with pytest.raises(PathEscape):
        guard.resolve_and_check(upper)
