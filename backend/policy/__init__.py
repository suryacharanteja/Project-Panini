"""PANINI v2 policy engine (docs/panini-enhancements/lld/V2-03-policy-engine.md).

`current()` is the process-wide policy; the harness installs one backed by its store. The call-scoped
context variables let one approval from the policy cover the v1 confirmation prompts nested inside the same
tool call (so the person is asked once), and let shadow mode observe whether v1 would have asked.
"""

from contextvars import ContextVar

from .engine import Decision, Grant, Policy, new_grant, profile_name, tier

_policy: Policy | None = None
# Set by the policy hook after the person approved this exact tool call; nested v1 prompts then pass.
preapproved: ContextVar[bool] = ContextVar("panini_preapproved", default=False)
# A mutable cell per tool call: True once a v1 confirmation was requested inside it (shadow comparison).
v1_asked: ContextVar[list | None] = ContextVar("panini_v1_asked", default=None)


def current() -> Policy:
    global _policy
    if _policy is None:
        _policy = Policy()
    return _policy


def install(policy: Policy) -> Policy:
    global _policy
    _policy = policy
    return policy


def note_v1_confirmation():
    """Called wherever v1 asks the person (TaskContext.confirm)."""
    cell = v1_asked.get()
    if cell is not None:
        cell.append(True)


def v1_confirmation_preapproved() -> bool:
    """One concrete v2 review also covers legacy prompts nested in that same tool call."""
    note_v1_confirmation()
    return preapproved.get()


__all__ = [
    "Decision",
    "Grant",
    "Policy",
    "current",
    "install",
    "new_grant",
    "note_v1_confirmation",
    "preapproved",
    "profile_name",
    "tier",
    "v1_asked",
]
