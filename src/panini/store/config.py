"""Configuration loading, validation, and the security floor (DD-145..149).

Two rules govern this module:

* **Configuration cannot disable a security control.** Taint tracking, provenance
  tagging, egress classification, audit logging, and the T2/T3 approval
  requirement have no keys — there is nothing to find and nothing to switch off.
  ``tools.enabled`` reduces capability; nothing here expands permission.
* **Measured constraints are encoded in the schema.** A value that would silently
  degrade behaviour is refused at load rather than discovered in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from panini.errors import ConfigError

CURRENT_SCHEMA_VERSION = 1


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")  # unknown keys warn, never brick (§7)


# ── LLM ──────────────────────────────────────────────────────────────────────

class OllamaOptions(_Base):
    # DD-102 — omitting num_gpu costs ~60% throughput (22.09 → 35.26 tok/s).
    # Required rather than defaulted so a hand-written config cannot lose it silently.
    num_gpu: int = Field(..., ge=0, le=999)
    num_ctx: int = Field(8192, ge=512, le=32768)      # DD-103
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    keep_alive: str = "5m"                             # DD-099


class OllamaConfig(_Base):
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct-q4_K_M"          # DD-104
    options: OllamaOptions = Field(default_factory=lambda: OllamaOptions(num_gpu=29))


class LlmConfig(_Base):
    provider: str = "ollama"                           # DD-041 — local default
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    max_tool_calls_per_turn: int = Field(10, ge=1, le=25)   # DD-123


# ── voice ────────────────────────────────────────────────────────────────────

class SttConfig(_Base):
    engine: str = "faster-whisper"
    # DD-020 — `small` was rejected at 2.31 s against a 1.5 s requirement.
    model: str = "base"
    compute_type: str = "int8"
    device: str = "cpu"                                # DD-097
    beam_size: int = Field(1, ge=1, le=5)
    language: str = "en"
    initial_prompt: str = "Panini is an AI assistant."  # DD-126 layer 1
    corrections: dict[str, str] = Field(               # DD-126 layer 2
        default_factory=lambda: {
            "Ponyny": "Panini", "Ponyney": "Panini", "Pawnee": "Panini",
            "Ponini": "Panini", "Pawnini": "Panini",
        }
    )


class TtsChunkConfig(_Base):
    """Chunk caps from the measured Kokoro cost knee (DD-124).

    Synthesis cost per character rises 5.4× above ~62 characters while audio
    duration stays linear. ``le=60`` keeps every chunk in the cheap regime — it
    is a throughput constraint, not only a time-to-first-audio one.
    """

    first_max_chars: int = Field(40, ge=15, le=60)
    max_chars: int = Field(55, ge=15, le=60)
    hard_max_chars: int = Field(60, ge=15, le=60)
    min_chars: int = Field(15, ge=5, le=30)

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        if not (self.min_chars <= self.first_max_chars <= self.max_chars <= self.hard_max_chars):
            raise ValueError(
                "require min_chars ≤ first_max_chars ≤ max_chars ≤ hard_max_chars "
                f"(got {self.min_chars}, {self.first_max_chars}, "
                f"{self.max_chars}, {self.hard_max_chars})"
            )
        return self


class TtsConfig(_Base):
    engine: str = "kokoro"
    device: str = "cpu"                                # DD-097
    voice: str = "af_heart"
    speed: float = Field(1.0, ge=0.5, le=2.0)
    warm_on_start: bool = True                         # DD-105 — 1.76 s cold vs 0.30 s warm
    chunk: TtsChunkConfig = Field(default_factory=TtsChunkConfig)
    buffer_depth: int = Field(1, ge=1, le=8)


class VadConfig(_Base):
    engine: str = "silero"
    threshold: float = Field(0.5, ge=0.1, le=0.9)
    speech_start_frames: int = Field(3, ge=1, le=20)
    speech_end_frames: int = Field(25, ge=5, le=100)
    max_utterance_s: int = Field(30, ge=5, le=120)


class WakeWordConfig(_Base):
    enabled: bool = True
    engine: str = "openwakeword"
    model: str = "hey_panini"
    threshold: float = Field(0.5, ge=0.1, le=0.95)
    suppress_while_speaking: bool = True               # FR-103


class VoiceConfig(_Base):
    enabled: bool = True
    stt: SttConfig = Field(default_factory=SttConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    vad: VadConfig = Field(default_factory=VadConfig)
    wakeword: WakeWordConfig = Field(default_factory=WakeWordConfig)


# ── avatar / memory ──────────────────────────────────────────────────────────

class AvatarConfig(_Base):
    type: str = "vrm"                                  # DD-030
    model_path: str = "assets/avatar/default.vrm"
    prefer_integrated_gpu: bool = True                 # DD-101
    idle_animation: bool = True


class FactsConfig(_Base):
    enabled: bool = True
    budget_tokens: int = Field(300, ge=0, le=2000)
    retrieval: str = "lexical"                         # DD-119
    score_threshold: float = Field(0.25, ge=0.0, le=1.0)


class MemoryConfig(_Base):
    working_turns: int = Field(6, ge=1, le=50)         # DD-118
    summary_enabled: bool = True
    summary_every_turns: int = Field(6, ge=2, le=50)
    facts: FactsConfig = Field(default_factory=FactsConfig)


# ── tools ────────────────────────────────────────────────────────────────────

class FilesystemToolConfig(_Base):
    allow_roots: list[str] = Field(default_factory=lambda: ["~/Documents/Panini"])
    max_read_bytes: int = Field(1_048_576, ge=1024, le=1_048_576)


class ShellToolConfig(_Base):
    timeout_s: int = Field(30, ge=1, le=300)
    max_output_bytes: int = Field(102_400, ge=1024, le=102_400)   # DD-130
    working_dir: str = "~/Documents/Panini"


class WebToolConfig(_Base):
    search_backend: str = "tavily"                     # DD-131
    fetch_timeout_s: int = Field(15, ge=1, le=120)
    max_redirects: int = Field(3, ge=0, le=10)
    max_response_bytes: int = Field(1_048_576, ge=1024, le=1_048_576)


class ToolsConfig(_Base):
    enabled: dict[str, bool] = Field(default_factory=dict)
    filesystem: FilesystemToolConfig = Field(default_factory=FilesystemToolConfig)
    shell: ShellToolConfig = Field(default_factory=ShellToolConfig)
    web: WebToolConfig = Field(default_factory=WebToolConfig)


# ── security ─────────────────────────────────────────────────────────────────

class SecurityConfig(_Base):
    """User-adjustable security *settings*.

    Note what is absent: there is no key for taint tracking, provenance,
    egress classification, audit logging, or the T2/T3 approval requirement.
    Their absence is the control (DD-145).
    """

    synced_roots: list[str] = Field(default_factory=list)
    approval_timeout_s: int = Field(120, ge=5, le=600)   # never unbounded
    egress_allowlist: list[str] = Field(default_factory=list)
    fatigue_warning_threshold: int = Field(15, ge=1, le=200)   # DD-138


# ── root ─────────────────────────────────────────────────────────────────────

class UiConfig(_Base):
    window_mode: str = "full"
    theme: str = "system"


class DaemonConfig(_Base):
    start_minimised: bool = False
    autostart_on_login: bool = False
    tray_enabled: bool = True


class LoggingConfig(_Base):
    level: str = "INFO"
    file: str = "logs/panini.log"
    max_size_mb: int = Field(50, ge=1, le=500)
    backups: int = Field(3, ge=0, le=20)


class Config(_Base):
    schema_version: int = CURRENT_SCHEMA_VERSION
    llm: LlmConfig = Field(default_factory=LlmConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    avatar: AvatarConfig = Field(default_factory=AvatarConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


# ── security floor ───────────────────────────────────────────────────────────

# Bounds re-applied after every layer, including environment overrides, so no
# path exists that lands a value outside them (DD-145).
SECURITY_FLOOR: dict[str, tuple[float, float]] = {
    "security.approval_timeout_s": (5, 600),
    "tools.shell.timeout_s": (1, 300),
    "tools.shell.max_output_bytes": (1024, 102_400),
    "tools.web.max_response_bytes": (1024, 1_048_576),
    "tools.filesystem.max_read_bytes": (1024, 1_048_576),
    "llm.max_tool_calls_per_turn": (1, 25),
}

# Keys that would weaken a control. Present in a file, they are rejected loudly
# rather than ignored — a user who wrote one should learn it does not exist.
FORBIDDEN_KEYS = (
    "security.taint_tracking",
    "security.disable_taint",
    "security.audit_enabled",
    "security.disable_audit",
    "security.require_approval",
    "security.always_allow",
    "tools.filesystem.deny_roots",     # deny list is code-owned (Tools LLD §4.4)
)


def _get_path(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _set_path(obj: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def apply_security_floor(cfg: Config) -> list[str]:
    """Clamp out-of-range values. Returns human-readable notes for the log.

    Clamping is always reported — a silently altered setting teaches the user
    nothing and looks like a bug.
    """
    notes: list[str] = []
    for dotted, (lo, hi) in SECURITY_FLOOR.items():
        try:
            current = _get_path(cfg, dotted)
        except AttributeError:
            continue
        clamped = min(max(current, lo), hi)
        if clamped != current:
            _set_path(cfg, dotted, type(current)(clamped))
            notes.append(
                f"{dotted} = {current} clamped to {clamped} (security floor)"
            )
    return notes


def _check_forbidden(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for dotted in FORBIDDEN_KEYS:
        node: Any = raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node is not None:
            errors.append(
                f"  {dotted}\n"
                f"    This key does not exist. Security controls cannot be disabled by\n"
                f"    configuration (DD-145). Use tools.enabled to reduce capability."
            )
    return errors


# ── loading ──────────────────────────────────────────────────────────────────

def _format_validation_error(exc: ValidationError, path: Path) -> str:
    """Errors state the reason, not only the rule (DD-148)."""
    lines = [f"Configuration error in {path}:", ""]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  {loc}")
        lines.append(f"    {err['msg']}")
        if "num_gpu" in loc:
            lines.append(
                "    Omitting num_gpu reduces throughput by ~60% "
                "(22.09 → 35.26 tok/s, DD-102)."
            )
        if "chunk" in loc:
            lines.append(
                "    Kokoro synthesis cost rises 5.4× above ~62 characters "
                "(DD-124), causing audible gaps during playback."
            )
        lines.append("")
    lines.append(f"{len(exc.errors())} error(s). See docs/05_LLD/07_Configuration_LLD.md")
    return "\n".join(lines)


def load_config(path: Path) -> tuple[Config, list[str]]:
    """Load and validate configuration. Returns the config and any warnings."""
    warnings: list[str] = []

    if not path.exists():
        cfg = Config()
        warnings.append(f"{path} not found; using defaults")
        apply_security_floor(cfg)
        return cfg, warnings

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")

    forbidden = _check_forbidden(raw)
    if forbidden:
        raise ConfigError(
            "Configuration error in "
            f"{path}:\n\n" + "\n".join(forbidden)
        )

    try:
        cfg = Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, path)) from exc

    warnings.extend(apply_security_floor(cfg))
    return cfg, warnings
