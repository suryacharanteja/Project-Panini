"""Error taxonomy for Panini.

SecurityError and its subclasses are never caught by generic handlers and never
retried. Each has an explicit handler that surfaces to the user.
"""

from __future__ import annotations


class PaniniError(Exception):
    """Base for all Panini errors."""


# ── configuration ────────────────────────────────────────────────────────────

class ConfigError(PaniniError):
    """Configuration is missing, malformed, or invalid."""


# ── LLM providers ────────────────────────────────────────────────────────────

class ProviderError(PaniniError):
    """LLM provider failure."""


class ProviderUnavailable(ProviderError):
    """The configured provider is unreachable.

    Never triggers a fallback to a different provider: silently switching from a
    local model to a cloud one would transmit personal content to a third party
    without the user's knowledge (FR-214, BR-023).
    """

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"Provider {provider!r} unavailable: {detail}")
        self.provider = provider
        self.detail = detail


# ── tools ────────────────────────────────────────────────────────────────────

class ToolError(PaniniError):
    """Tool execution failed."""


class ToolTimeout(ToolError):
    """Tool exceeded its time limit (FR-406)."""


class ToolNotAvailable(ToolError):
    """Tool is unknown, disabled, or unconfigured (FR-404, FR-426)."""


class ToolDefinitionError(PaniniError):
    """Tool failed registration validation.

    Raised when a tool omits its tier or egress classification. A tool that does
    not declare its security properties cannot register (Tools LLD §2.1).
    """


# ── security ─────────────────────────────────────────────────────────────────

class SecurityError(PaniniError):
    """Base for security failures. Never suppressed, never retried."""


class ApprovalDenied(SecurityError):
    """The user declined, or the approval timed out (FR-525)."""

    def __init__(self, tool_name: str, reason: str = "denied by user") -> None:
        super().__init__(f"Approval for {tool_name!r} {reason}")
        self.tool_name = tool_name


class EgressBlocked(SecurityError):
    """An outbound transmission was refused."""


class PathEscape(SecurityError):
    """A path resolved outside allowed roots, or into a protected location.

    Covers both traversal outside ``allow_roots`` (FR-416) and any resolution
    into the audit or credential stores (FR-563).
    """


class AuditWriteFailure(SecurityError):
    """The audit record could not be written.

    Propagates to ToolGateway and aborts the invocation. An action that cannot
    be accounted for does not happen (HLD §8).
    """


class CredentialError(SecurityError):
    """Credential store failure. Never includes the credential value."""


# ── voice ────────────────────────────────────────────────────────────────────

class VoiceError(PaniniError):
    """Audio capture, transcription, or synthesis failure."""


# ── persistence ──────────────────────────────────────────────────────────────

class StoreError(PaniniError):
    """Persistence failure."""


class StoreCorrupted(StoreError):
    """Integrity check failed.

    Corruption of the audit store is fatal to startup; corruption of the main
    store degrades gracefully (DD-143).
    """
