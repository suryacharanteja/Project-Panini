"""Argument redaction for the audit log and application logs.

Credential *values* never reach this function — tools receive handles and the
kernel injects values at the boundary (DD-115). Redaction is therefore defence
in depth rather than the primary control.
"""

from __future__ import annotations

import re
from typing import Any

REDACT_KEYS = frozenset({
    "password", "passwd", "token", "api_key", "apikey", "secret",
    "authorization", "auth", "credential", "credentials",
    "access_token", "refresh_token", "client_secret", "private_key",
    "session_key", "bearer",
})

# Values that look like secrets even under an innocuous key name.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9]{16,}$"),            # OpenAI-style
    re.compile(r"^ghp_[A-Za-z0-9]{20,}$"),           # GitHub PAT
    re.compile(r"^ya29\.[A-Za-z0-9_\-]{20,}$"),      # Google OAuth
    re.compile(r"^eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\."),  # JWT
)

REDACTED = "<redacted>"
MAX_VALUE_CHARS = 2000


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in REDACT_KEYS:
        return True
    return any(marker in lowered for marker in ("secret", "token", "password", "api_key"))


def _value_looks_secret(value: str) -> bool:
    return any(p.match(value) for p in _SECRET_VALUE_PATTERNS)


def redact(args: Any, _depth: int = 0) -> Any:
    """Return a copy of ``args`` safe to persist.

    Truncates long values so a single large argument cannot bloat the audit
    store, and replaces anything that looks like a credential.
    """
    if _depth > 10:  # defensive against pathological nesting
        return "<max depth>"

    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for key, value in args.items():
            if _key_is_sensitive(str(key)):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = redact(value, _depth + 1)
        return out

    if isinstance(args, (list, tuple)):
        return [redact(v, _depth + 1) for v in args]

    if isinstance(args, str):
        if _value_looks_secret(args):
            return REDACTED
        if len(args) > MAX_VALUE_CHARS:
            return f"{args[:MAX_VALUE_CHARS]}…<truncated {len(args) - MAX_VALUE_CHARS} chars>"
        return args

    return args
