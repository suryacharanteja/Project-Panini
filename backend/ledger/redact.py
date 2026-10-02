"""What receipts keep: enough to audit and replay decisions, never typed values, file contents, full
commands or secrets (harvested from OpenDots' audit rule; V2-04 §5.5)."""

import hashlib
import re

from ..harness.memory import _SECRET

TYPED = {"text", "value", "answer", "message", "contents", "body", "password", "code"}
SECRET_QUERY = re.compile(r"([?&](?:token|key|secret|password|auth|sig|signature|code)=)[^&#]+", re.I)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def mask(text: str) -> str:
    # Secret query values first: the general pattern can leave a token's value behind (found in V2-07).
    return _SECRET.sub("[redacted]", SECRET_QUERY.sub(r"[redacted]", text))


def redact_value(key: str, value, extra=()):
    if isinstance(value, dict):
        return {k: redact_value(k, v, extra) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(key, v, extra) for v in value[:50]]
    if not isinstance(value, str):
        return value
    lowered = key.lower()
    if lowered in TYPED or lowered in extra or lowered == "fields":
        return {"chars": len(value), "sha256": _sha(value)}
    if lowered in {"command", "instruction"}:
        return {"preview": mask(value[:200]), "chars": len(value), "sha256": _sha(value)}
    if lowered in {"url", "href", "target"}:
        return mask(SECRET_QUERY.sub(r"\1[redacted]", value))[:2048]
    return mask(value[:500])


def redact_args(args: dict, extra=()) -> dict:
    return {key: redact_value(key, value, set(extra)) for key, value in (args or {}).items()}


def redact_result(text: str, limit: int = 2000) -> str:
    return mask(str(text or ""))[:limit]


def scrub(text: str, secrets=()) -> str:
    """Replace any reflected infrastructure secret (OpenDots computer-service rule)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text
