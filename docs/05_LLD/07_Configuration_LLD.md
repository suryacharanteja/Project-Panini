# Project Panini — Configuration LLD

**Document Type:** Low Level Design  
**Phase:** 05 — LLD  
**Module:** `panini/store/config.py`  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## 1. Principles

| # | Principle | Consequence |
|---|---|---|
| **CF-1** | One authored file — `panini.yaml` (DD-092) | No settings scattered across the codebase |
| **CF-2** | Every setting has a documented default | The file may be empty and the system still runs |
| **CF-3** | **Configuration cannot disable a security control** | Enforced by a security floor (§5) |
| **CF-4** | Validation errors name the key and the problem (FR-712) | No opaque startup failures |
| **CF-5** | Config is authored by the user; the app does not rewrite it | UI-mutable state lives in the `settings` table (Persistence LLD §3.4) |

---

## 2. Layering

```
Code defaults (pydantic Field defaults)
        ▼  overridden by
config/panini.yaml
        ▼  overridden by
Environment variables (PANINI__section__key)
        ▼  constrained by
⚠️ SECURITY FLOOR — cannot be weakened by any layer
```

Environment override exists for testing and secrets injection; the security floor applies **after** all layers.

---

## 3. Schema

Complete `panini.yaml` with every value traced to its decision.

```yaml
schema_version: 1

# ─────────────────────────────────────────────────────────────
llm:
  provider: ollama                          # DD-041 — local default

  ollama:
    host: http://localhost:11434
    model: qwen2.5:7b-instruct-q4_K_M       # DD-104 — validated
    options:
      num_gpu: 29                           # DD-102 ⚠️ omitting = −60% throughput
      num_ctx: 8192                         # DD-103 — 4,857 MiB, 1,287 spare
      temperature: 0.7
      keep_alive: 5m                        # DD-099 — release VRAM when idle

  openai:    { api_key_handle: openai_key,    model: gpt-4o-mini }
  anthropic: { api_key_handle: anthropic_key, model: claude-sonnet-5 }
  google:    { api_key_handle: google_key,    model: gemini-2.0-flash }

  max_tool_calls_per_turn: 10               # DD-123

# ─────────────────────────────────────────────────────────────
voice:
  enabled: true

  stt:
    engine: faster-whisper
    model: base                             # DD-020 ⚠️ `small` FAILED (2.31s vs 1.5s)
    compute_type: int8
    device: cpu                             # DD-097
    beam_size: 1
    language: en
    initial_prompt: "Panini is an AI assistant."   # DD-126 layer 1
    corrections:                                    # DD-126 layer 2
      Ponyny:  Panini
      Ponyney: Panini
      Pawnee:  Panini
      Ponini:  Panini
      Pawnini: Panini

  tts:
    engine: kokoro
    device: cpu                             # DD-097
    voice: af_heart
    speed: 1.0
    warm_on_start: true                     # DD-105 — cold 1.76s vs warm 0.30s
    chunk:
      first_max_chars: 40                   # DD-124 — TTFA ≈0.53s
      max_chars: 55                         # DD-124 — below the 62-char knee
      hard_max_chars: 60                    # DD-124 — never enter steep regime
      min_chars: 15
    buffer_depth: 1                         # DD-106

  vad:
    engine: silero
    threshold: 0.5
    speech_start_frames: 3                  # ~96 ms debounce
    speech_end_frames: 25                   # ~800 ms — charged to latency budget
    max_utterance_s: 30

  wakeword:
    enabled: true
    engine: openwakeword                    # DD-025
    model: hey_panini
    threshold: 0.5
    suppress_while_speaking: true           # FR-103 — self-trigger prevention

# ─────────────────────────────────────────────────────────────
avatar:
  type: vrm                                 # DD-030 — Live2D optional (DD-031)
  model_path: assets/avatar/default.vrm
  prefer_integrated_gpu: true               # DD-101 — keeps RTX free for the LLM
  idle_animation: true

# ─────────────────────────────────────────────────────────────
memory:                                      # DD-118 — three tiers
  working_turns: 6
  summary_enabled: true
  summary_every_turns: 6
  facts:
    enabled: true
    budget_tokens: 300
    retrieval: lexical                      # DD-119 — not semantic in v1
    score_threshold: 0.25

# ─────────────────────────────────────────────────────────────
tools:
  enabled:
    fs_read: true
    fs_list: true
    fs_write: true
    fs_delete: true
    shell_exec: true
    web_search: true
    web_fetch: true
    gmail_read: false                       # off until OAuth configured
    gmail_search: false
    gmail_send: false
    gmail_delete: false
    calendar_read: false
    calendar_create: false
    calendar_update: false
    calendar_delete: false
    browser_navigate: false
    browser_interact: false
    browser_extract: false
    mcp_call: false

  filesystem:
    allow_roots:                            # FR-416
      - ~/Documents/Panini
      - ~/Downloads
    max_read_bytes: 1048576

  shell:
    timeout_s: 30
    max_output_bytes: 102400
    working_dir: ~/Documents/Panini

  web:
    search_backend: tavily                  # DD-131 — DuckDuckGo fallback allowed
    fetch_timeout_s: 15
    max_redirects: 3
    max_response_bytes: 1048576

  mcp:
    servers: []                             # explicit allowlist only

# ─────────────────────────────────────────────────────────────
security:
  synced_roots: []                          # auto-detected; add manually if missed
  approval_timeout_s: 120                   # → DENY on timeout
  egress_allowlist: []                      # DD-136 — added via UI, not here
  fatigue_warning_threshold: 15             # DD-138 — approvals/session

# ─────────────────────────────────────────────────────────────
ui:
  window_mode: full                         # full | compact
  theme: system

daemon:
  start_minimised: false
  autostart_on_login: false                 # FR-705
  tray_enabled: true                        # FR-702

logging:
  level: INFO
  file: logs/panini.log
  max_size_mb: 50
  backups: 3
```

---

## 4. Validation (`config.py`)

### 4.1 pydantic models

```python
class TtsChunkConfig(BaseModel):
    first_max_chars: int = Field(40, ge=15, le=60)
    max_chars:       int = Field(55, ge=15, le=60)
    hard_max_chars:  int = Field(60, ge=15, le=60)
    min_chars:       int = Field(15, ge=5,  le=30)

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        if not (self.min_chars <= self.first_max_chars <= self.max_chars <= self.hard_max_chars):
            raise ValueError(
                "voice.tts.chunk: require min ≤ first_max ≤ max ≤ hard_max "
                f"(got {self.min_chars}, {self.first_max_chars}, "
                f"{self.max_chars}, {self.hard_max_chars})"
            )
        return self
```

`le=60` on the chunk caps is the measured knee (DD-124) expressed as a constraint. Raising it above 60 would silently enter the 5.4× cost regime — so the schema refuses.

### 4.2 Error reporting (FR-712)

```
Configuration error in config/panini.yaml:

  voice.tts.chunk.max_chars
    Value 120 exceeds maximum 60.
    Kokoro synthesis cost rises 5.4× above ~62 characters (DD-124),
    which causes audible gaps during playback.

  llm.ollama.options.num_gpu
    Required key is missing.
    Omitting num_gpu reduces throughput by ~60% (DD-102).

2 errors. See docs/05_LLD/07_Configuration_LLD.md
```

Each error names the key, the problem, **and why the constraint exists**. A constraint without a reason gets worked around.

### 4.3 Startup assertions

| Assertion | Failure |
|---|---|
| `llm.ollama.options.num_gpu` present | Startup error (DD-102) |
| `avatar.model_path` exists | Warning; avatar disabled, assistant runs |
| `tools.filesystem.allow_roots` non-empty when any fs tool enabled | Startup error |
| Every `allow_root` resolves and exists | Warning per missing root |
| Enabled connector has its credential handle | Tool disabled with a message (FR-426) |

---

## 5. ⚠️ Security Floor

**Configuration must not be able to disable a security control.** Certain values are absent from the schema entirely; others are clamped after loading.

### 5.1 Not configurable — no key exists

| Control | Why it is not exposed |
|---|---|
| Taint tracking (DD-111) | The load-bearing control; a disable switch is a vulnerability |
| Provenance tagging (DD-110) | Same |
| Egress classification (DD-112) | Same |
| Audit logging (DD-065, DD-141) | FR-560 requires every invocation recorded |
| `DENY_ROOTS` (audit, credentials) | Tools LLD §4.4 — a user-editable deny list could be emptied |
| Approval requirement for T2/T3 | FR-522, FR-523 |
| "Always allow" for T2/T3 | DD-114 — deliberately absent |
| Tool `tier` and `egress_class` | Declared in code; config only enables/disables tools |

> **Tools can be turned off, never made more permissive.** `tools.enabled` is a reduction of capability only.

### 5.2 Clamped after loading

```python
SECURITY_FLOOR = {
    "security.approval_timeout_s":      (5, 600),    # never infinite
    "tools.shell.timeout_s":            (1, 300),
    "tools.shell.max_output_bytes":     (1024, 102_400),   # DD-130 cap
    "tools.web.max_response_bytes":     (1024, 1_048_576),
    "tools.filesystem.max_read_bytes":  (1024, 1_048_576),
    "llm.max_tool_calls_per_turn":      (1, 25),     # DD-123
}

def apply_floor(cfg: Config) -> Config:
    for path, (lo, hi) in SECURITY_FLOOR.items():
        v = get_path(cfg, path)
        clamped = min(max(v, lo), hi)
        if clamped != v:
            log.warning("Config %s = %s clamped to %s (security floor)", path, v, clamped)
            set_path(cfg, path, clamped)
    return cfg
```

**Clamping is logged, never silent.** A user who set an out-of-range value learns that it was constrained and why.

**Why `approval_timeout_s` is capped at 600:** an unbounded timeout would leave a dialog pending indefinitely; combined with a walk-away, that is an approval that never resolves and a turn that never ends. Timeout means deny (Frontend LLD §6.1 A-8).

### 5.3 `allow_roots` cannot override `DENY_ROOTS`

Deny is checked **before** allow (Tools LLD §4.1). Setting `allow_roots: ["C:\\"]` grants broad filesystem access — the user's prerogative — but still cannot reach the audit database or credential store.

---

## 6. Hot Reload (FR-713)

`watchfiles` observes `panini.yaml`; changes are debounced 500 ms.

| Tier | Settings | Behaviour |
|---|---|---|
| **Hot** | `llm.*.temperature` · `voice.tts.voice`/`speed` · `ui.*` · `logging.level` · `tools.enabled` · `memory.*` | Applied immediately |
| **Warm** | `voice.stt.corrections` · `security.*` · `tools.filesystem.allow_roots` | Applied at the next turn boundary |
| **Cold** | `llm.provider`/`model` · `voice.*.engine` · `avatar.type` · `daemon.*` | Requires restart; the UI shows a "restart to apply" banner |

**Security-relevant settings are Warm, never Hot.** Changing `allow_roots` mid-turn could alter the permission surface between a tool call's check and its execution — a TOCTOU window. Applying at a turn boundary removes it.

A reload that fails validation **keeps the previous configuration** and surfaces the error. A bad edit never leaves the assistant unconfigured.

---

## 7. Schema Migration

```python
CONFIG_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: lambda c: c,        # current
}
```

On load, if `schema_version` is below current, migrations run in order and the file is rewritten **once**, with a `.bak` retained. This is the sole case where the application writes to `panini.yaml` (CF-5), and it is announced in the log.

Unknown keys produce a warning, not an error — a config from a newer version should not brick an older binary.

---

## 8. First-Run Generation

If `panini.yaml` is absent, a fully commented file is written from the defaults, with every measured value annotated:

```yaml
  stt:
    # `base` measured 0.75s for a 3s utterance.
    # `small` was rejected: 2.31s, exceeding the 1.5s requirement (DD-020).
    model: base
```

The generated file **is** the documentation. A user editing it sees why each value is what it is.

---

## 9. Test Matrix

| Test | Asserts | Ref |
|---|---|---|
| `test_empty_config_uses_defaults` | CF-2 | — |
| `test_missing_num_gpu_fails_startup` | DD-102 guard | §4.3 |
| **`test_chunk_max_above_60_rejected`** | Measured knee enforced | DD-124 |
| `test_chunk_ordering_validated` | §4.1 | — |
| **`test_no_key_disables_taint`** | Absent from schema | §5.1 |
| **`test_no_key_disables_audit`** | Absent from schema | §5.1 |
| **`test_allow_roots_cannot_reach_audit`** | Deny wins | §5.3 |
| `test_approval_timeout_clamped` | §5.2 | — |
| `test_clamping_is_logged` | Never silent | §5.2 |
| `test_shell_output_clamped_to_100kb` | DD-130 | §5.2 |
| `test_security_settings_are_warm_not_hot` | TOCTOU | §6 |
| `test_invalid_reload_keeps_previous` | §6 | — |
| `test_error_names_key_and_reason` | FR-712 | §4.2 |
| `test_unknown_key_warns_not_errors` | §7 | — |
| `test_first_run_generates_commented_file` | §8 | — |
| `test_env_override_applies` | §2 | — |
| `test_env_override_still_floored` | Floor is last | §2 |

---

## 10. New Decisions Required

| Proposed ID | Decision |
|---|---|
| **DD-145** | **Security floor** — settings that would weaken a control are either absent from the schema or clamped after load, with clamping logged. Configuration can reduce capability, never expand permission. |
| **DD-146** | **Security-relevant settings are Warm-reload, never Hot** — applying mid-turn would open a TOCTOU window between a permission check and execution. |
| **DD-147** | **Measured constraints are encoded in the schema** — e.g. chunk caps `le=60` from the DD-124 knee. A value that would silently degrade behaviour is refused at load. |
| **DD-148** | **Validation errors state the reason, not only the rule.** A constraint without a rationale gets worked around. |
| **DD-149** | **The generated config file is the documentation** — first-run output annotates each value with its measurement and decision ID. |

---

## 11. Open Items

| # | Item | Disposition |
|---|---|---|
| 1 | Settings UI vs file editing | Frontend exposes a subset; the file remains authoritative |
| 2 | Per-persona configuration overrides | Deferred — personas are Could-have (BR-012) |
| 3 | Whether `allow_roots` should default to a narrower set | Currently `~/Documents/Panini` and `~/Downloads`; review after use |

---

## 12. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |
