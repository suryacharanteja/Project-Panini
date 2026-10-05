"""Shared configuration. Explicit environment variables take precedence."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(os.environ.get("PANINI_RUNTIME_ROOT") or Path(__file__).resolve().parent.parent)
load_dotenv(ROOT / ".env", override=False)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")


def configured(name: str) -> bool:
    from . import private_mode

    if private_mode.active() and name in {"OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "ASSEMBLYAI_API_KEY"}:
        return False
    value = os.getenv(name, "").strip()
    return bool(value and not value.startswith("your_") and value != "change-me")


def jev_gates() -> bool:
    """Jev routing and completion checks (on by default whenever Jev is configured)."""
    if os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() == "private":
        return False
    enabled = os.getenv("PANINI_JEV_GATES", "true").strip().lower() not in {"0", "false", "no", "off"}
    return enabled and configured("AI_GATEWAY_API_KEY")


PROVIDER_TIMEOUT = 45.0
# Jev checks that only save tokens or time give up quickly and fall back.
JEV_GATE_TIMEOUT = 4.0
MAX_TEXT_LENGTH = 20_000
MAX_AUDIO_BYTES = 10 * 1024 * 1024
HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() not in {"0", "false", "no"}
EXTENSION_PLANNER_MODEL = os.getenv("EXTENSION_PLANNER_MODEL", "gpt-4.1-2025-04-14").strip()
# Small model for simple extraction and wording (task plans, voice parsing). Decisions are
# Jev's; this model only proposes or writes text.
FAST_MODEL = os.getenv("PANINI_FAST_MODEL", "gpt-4.1-mini").strip() or EXTENSION_PLANNER_MODEL
# Browser proposer screenshots: "auto" sends low detail first and high detail only after an
# action failed or changed nothing; "low" or "high" force one.
SCREENSHOT_DETAIL = os.getenv("PANINI_SCREENSHOT_DETAIL", "auto").strip().lower()


def allowed_origins() -> list[str]:
    explicit = os.getenv("ALLOWED_ORIGINS", "")
    origins = [origin.strip().rstrip("/") for origin in explicit.split(",") if origin.strip()]
    for origin in origins:
        url = urlsplit(origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
        ):
            raise ValueError("ALLOWED_ORIGINS must contain HTTP(S) origins without paths or credentials")
        try:
            url.port
        except ValueError:
            raise ValueError("ALLOWED_ORIGINS contains an invalid port") from None
    return origins
