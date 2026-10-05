"""Agent settings. Credentials come from the environment or PANINI's own .env, never the workspace."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PANINI_ROOT = Path(__file__).resolve().parents[6]
EFFORTS = ("minimal", "low", "medium", "high")
DEFAULT_SANDBOX = "native" if os.name == "nt" else "none"
DEFAULT_MODEL = "auto"
DEFAULT_ROUTER_FALLBACK_MODEL = "gpt-4.1"
DEFAULT_ROUTER_MODELS = (
    "gpt-4.1-nano:cheap:Fast mechanical edits, tiny lookups, formatting, and simple shell checks",
    "gpt-4.1-mini:balanced:Routine coding, bounded debugging, command-line setup, and test repair",
    "gpt-4.1:strong:Harder multi-file coding, architecture, unfamiliar failures, and higher blast radius work",
)


def load_panini_env():
    """The terminal CLI runs in any folder, so the workspace's own .env is never loaded."""
    if os.getenv("PANINI_LOCAL_MODE", "off").strip().lower() == "fallback":
        from .local_quality import evaluation_active

        if evaluation_active():
            return  # The isolated real-local scorecard never imports a saved cloud credential.
    if configured_key():
        return
    roots = [Path(os.environ["PANINI_RUNTIME_ROOT"])] if os.getenv("PANINI_RUNTIME_ROOT") else []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in [*roots, PANINI_ROOT]:
        path = root / ".env"
        if path.is_file():
            from dotenv import load_dotenv

            load_dotenv(path, override=False)
            if configured_key():
                return
    if os.name == "nt":
        from .auth import read_key

        key = read_key()
        if key:
            os.environ["OPENAI_API_KEY"] = key


def configured_key():
    value = os.getenv("OPENAI_API_KEY", "").strip()
    return bool(value) and not value.startswith("your_")


def _integer(name, default, low, high):
    try:
        return max(low, min(high, int(os.getenv(name, default))))
    except ValueError:
        return default


def command_mode():
    mode = os.getenv("PANINI_CLI_SANDBOX", DEFAULT_SANDBOX).strip().lower() or DEFAULT_SANDBOX
    if mode not in {"none", "native", "codex", "docker"}:
        raise ValueError(f"Invalid PANINI_CLI_SANDBOX={mode!r}; choose none, native, codex, or docker")
    return mode


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    reasoning_effort: str | None = None
    max_steps: int = 60
    # Every turn resends the whole context; older tool outputs are dropped past this budget.
    context_tokens: int = 96_000
    command_timeout: int = 120
    request_timeout: float = 180.0
    sandbox: str = DEFAULT_SANDBOX  # "none", "native", "codex", or "docker"

    @classmethod
    def from_env(cls, *, model=None, reasoning_effort=None):
        effort = reasoning_effort or os.getenv("PANINI_CLI_REASONING_EFFORT") or None
        return cls(
            model=(
                model
                or os.getenv("PANINI_CLI_MODEL")
                or os.getenv("JEV_REV_CODE_MODEL")
                or os.getenv("PANINI_AGENT_MODEL")
                or os.getenv("PANINI_PLANNER_MODEL")
                or os.getenv("EXTENSION_PLANNER_MODEL")
                or cls.model
            ),
            reasoning_effort=effort if effort in EFFORTS else None,
            max_steps=_integer("PANINI_CLI_MAX_STEPS", cls.max_steps, 1, 500),
            context_tokens=_integer("PANINI_CLI_CONTEXT_TOKENS", cls.context_tokens, 20_000, 1_000_000),
            command_timeout=_integer("PANINI_CLI_COMMAND_TIMEOUT", cls.command_timeout, 5, 600),
            sandbox=command_mode(),
        )
