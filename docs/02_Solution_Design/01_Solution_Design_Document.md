# Project Panini — Solution Design Document (SDD)

**Document Type:** Solution Design  
**Phase:** 02 — Solution Design  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## Document Control

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-09-09 | Initial draft |

### Inputs

| Document | Provides |
|---|---|
| `01_BRD/01_Business_Requirements_Document.md` | 42 business requirements (BR-nnn) |
| `00_Discovery/.../02_Decision_Register.md` | All locked decisions (DD-nnn / DX-nnn) |
| `00_Discovery/.../03_License_Audit.md` | Sourcing constraints |
| `00_Discovery/.../05_Hardware_Validation_Results.md` | Measured performance envelope |
| `00_Discovery/05_Security/01_Threat_Model.md` | Security controls SC-1 – SC-9 |

### Purpose

This document defines **how** Project Panini is structured to satisfy the BRD. It establishes the architecture, component sourcing strategy, and key patterns. It does **not** specify function signatures or schemas — that is the LLD.

---

## 1. Solution Overview

Panini is a **four-process desktop application**:

```
┌──────────────────────────────────────────────────────────────────┐
│  ELECTRON MAIN  (Node.js)                            DD-010      │
│  Window & tray lifecycle · GPU pinning · Python sidecar spawn    │
└────────────┬─────────────────────────────────┬───────────────────┘
             │ Electron IPC                    │ spawn / supervise
┌────────────▼──────────────────┐  ┌───────────▼───────────────────┐
│  ELECTRON RENDERER (React)    │  │  PANINI BACKEND (Python)      │
│                    DD-011     │  │                    DD-012     │
│  · VRM avatar    (DD-030)     │◄─┤  · Voice pipeline             │
│  · Output panels (DD-081)     │  │  · Conversation engine        │
│  · Approval dialogs (DD-114)  │  │  · Tool registry  (DD-050)    │
│  · Text input    (DD-035)     │  │  · Security kernel(DD-110-117)│
│                               │  │  · Plugin host    (DD-070)    │
│      WebSocket ── audio/chat  │  │                               │
│      SSE ─────── events       │  │        FastAPI :8760          │
└───────────────────────────────┘  └───────────┬───────────────────┘
                        DD-013                 │ HTTP
                                   ┌───────────▼───────────────────┐
                                   │  OLLAMA  (separate process)   │
                                   │  qwen2.5:7b-q4  num_gpu=29    │
                                   │              DD-041, DD-102-104│
                                   └───────────────────────────────┘
```

**Why four processes:**
- Electron main owns OS integration (tray, window, GPU preference) — DD-091, DD-101
- Renderer is sandboxed and replaceable; the same React app runs in a browser for development
- Python backend hosts all AI work; crashes there do not kill the UI — DD-014
- Ollama is externally managed, upgradeable independently, and already handles VRAM lifecycle

---

## 2. Architecture Principles

| # | Principle | Consequence |
|---|---|---|
| **AP-1** | **Instructions come only from the user** | Provenance is a first-class property of every context element (DD-110) |
| **AP-2** | **Security is a gate, not a guideline** | Taint and egress checks sit in the invocation path, not inside individual tools (DD-111, DD-112) |
| **AP-3** | **Everything swappable behind an interface** | STT, TTS, VAD, LLM, avatar all resolve through factories (DD-021, DD-023, DD-040) |
| **AP-4** | **One conversation engine, many adapters** | Voice and text are input adapters onto identical downstream logic (DD-035) |
| **AP-5** | **Reuse upstream; fork only when necessary** | See §4 sourcing classification (DD-005) |
| **AP-6** | **Config over code** | Model, avatar, tools, and providers are selected in `panini.yaml` (DD-092) |
| **AP-7** | **Fail visible, not silent** | A failed tool surfaces in the UI; it never silently degrades |

---

## 3. Logical Architecture

The Python backend is layered. **Dependencies point downward only.**

```
┌───────────────────────────────────────────────────────────┐
│ L1  TRANSPORT            FastAPI · WebSocket · SSE        │
│     Protocol only — no business logic          DD-013     │
├───────────────────────────────────────────────────────────┤
│ L2  ORCHESTRATION        Conversation engine · Turn model │
│     Owns the turn lifecycle and taint state    DD-111     │
├───────────────────────────────────────────────────────────┤
│ L3  SECURITY KERNEL      Provenance · Taint · Permissions │
│                          Egress · Audit                   │
│     ⚠️ ALL tool calls pass through here    DD-110-117     │
├───────────────────────────────────────────────────────────┤
│ L4  CAPABILITY           LLM providers · Tool registry ·  │
│                          Plugin host       DD-040,050,070 │
├───────────────────────────────────────────────────────────┤
│ L5  VOICE                ASR · TTS · VAD · Wake word      │
│                                        DD-020-027,105,106 │
├───────────────────────────────────────────────────────────┤
│ L6  PERSISTENCE          History · Audit log · Config     │
│                                        DD-065,092,116     │
└───────────────────────────────────────────────────────────┘
```

### Layer responsibilities

| Layer | Owns | Explicitly does **not** own |
|---|---|---|
| **L1 Transport** | Connection lifecycle, framing, serialisation | Any decision about content |
| **L2 Orchestration** | Turn lifecycle, LLM loop, tool-call dispatch, streaming | Permission decisions |
| **L3 Security Kernel** | Provenance, taint, tier checks, egress checks, audit | Tool implementations |
| **L4 Capability** | Provider abstraction, tool registration, plugin loading | Whether a call is permitted |
| **L5 Voice** | Audio in/out, transcription, synthesis, wake detection | Conversation state |
| **L6 Persistence** | Durable storage | Business rules |

**The critical invariant:** L2 cannot reach L4 without passing through L3. This is enforced structurally — the tool registry is not injected into the orchestrator; only the security-kernel-wrapped façade is.

---

## 4. Component Sourcing Strategy

Discovery used "harvest" for all reuse. That conflates three materially different strategies with different maintenance and legal consequences. This section replaces it.

### 4.1 Sourcing classes

| Class | Meaning | Upstream fixes | When to use |
|---|---|---|---|
| **DEPENDENCY** | Installed from a package registry; unmodified | ✅ Automatic | Default — always prefer |
| **FORK** | Copied with a recorded upstream commit; modified | ⚠️ Manual, feasible | Interface mismatch requiring change |
| **REIMPLEMENT** | Written from scratch, informed by upstream design | ❌ None | Licence prohibits copying, or scope is small |
| **REFERENCE** | Studied only; no code taken | ❌ N/A | Ideas useful, code unusable |

### 4.2 Classification

| Component | Source | Class | Rationale |
|---|---|---|---|
| **STT (faster-whisper)** | PyPI `faster-whisper` | **DEPENDENCY** | Actively maintained; no modification needed |
| **TTS (Kokoro)** | PyPI `kokoro-onnx` | **DEPENDENCY** | Validated as-is |
| **VAD (Silero)** | PyPI `silero-vad` | **DEPENDENCY** | Standard package |
| **Wake word** | PyPI `openwakeword` | **DEPENDENCY** | Sapphire was only a wrapper — no value in forking (DD-025) |
| **Audio devices** | PyPI `sounddevice` | **DEPENDENCY** | Trivial wrapper written by us (DD-026) |
| **ASR/TTS factory pattern** | Open-LLM-VTuber (MIT) | **REFERENCE** | Pattern is simple; our interface differs. Copying adds no value |
| **Ollama client** | PyPI `ollama` | **DEPENDENCY** | Official client |
| **LLM provider abstraction** | Thoth (Apache 2.0) | **REIMPLEMENT** | ~200 lines; our interface differs materially |
| **Gmail tool** | Thoth (Apache 2.0) | **FORK** | Real OAuth complexity worth reusing; needs our tier/egress annotations |
| **Calendar tool** | Thoth (Apache 2.0) | **FORK** | Same rationale |
| **Web search tool** | Thoth (Apache 2.0) | **FORK** | Dual-backend logic worth keeping |
| **Filesystem tool** | — | **REIMPLEMENT** | Must be written against our path-safety and egress rules |
| **Shell tool** | — | **REIMPLEMENT** | Security-critical — must not inherit unreviewed behaviour |
| **Browser tool** | PyPI `playwright` | **DEPENDENCY** | Use the library directly |
| **VRM avatar** | npm `@pixiv/three-vrm` | **DEPENDENCY** | Naia OS is a consumer of this library, not a source (DD-030) |
| **Live2D avatar** | Live2D Cubism Web SDK | **DEPENDENCY** ⚠️ | Proprietary SDK; optional path only (DD-031) |
| **Plugin loader** | Sapphire | **REIMPLEMENT** | 🔴 AGPL — code copying prohibited (DD-074) |
| **Plugin signing** | PyPI `cryptography` | **DEPENDENCY** | ed25519 from the library directly |
| **Event bus** | Sapphire | **REIMPLEMENT** | 🔴 AGPL (DD-074) |
| **Persona system** | Sapphire | **REIMPLEMENT** | 🔴 AGPL (DD-074) |
| **Permission model** | Naia OS (Apache 2.0) | **REFERENCE** | Tier concept adopted; our implementation adds taint + egress |
| **Security kernel** | — | **REIMPLEMENT** | Novel — no upstream equivalent exists |

### 4.3 What this changes

| | Discovery ("harvest") | This document |
|---|---|---|
| Components copied as code | ~23 | **3 forks** (Gmail, Calendar, Web search) |
| Components as dependencies | not distinguished | **9** — receive upstream fixes automatically |
| Components reimplemented | 7 (AGPL-driven only) | **8** — adds shell + filesystem, for security reasons |

**Consequence:** the maintenance burden is far lower than "copy 23 modules" implied, and the security-critical surface (shell, filesystem, security kernel) is entirely our own code, reviewed by us.

> **Fork discipline:** every FORK records its upstream repository, commit SHA, and a summary of local changes in `NOTICES.md`. Apache 2.0 requires modified files to be marked (License Audit §6).

---

## 5. Key Architectural Patterns

### 5.1 The Turn — unit of conversation and taint

A **Turn** is created when the user speaks or types, and closed when the assistant finishes responding. It is the scope for taint (DD-111).

```
Turn
├── id, started_at
├── input: { text, provenance: USER, modality: voice | text }
├── context_elements: [ { content, provenance, source } ]
├── tainted: bool          ← true once any RETRIEVED element is added
├── taint_sources: [ ... ] ← shown in approval dialogs
├── tool_invocations: [ ... ]
└── output_chunks: [ ... ]
```

**Taint is monotonic within a turn** — once set it cannot be cleared. A new user-initiated turn starts clean. This is deliberately conservative: it is better to over-prompt than to miss a chain.

### 5.2 Security kernel as a mandatory gate

Every tool call follows one path:

```
Orchestrator
    │  invoke("gmail_send", args)
    ▼
┌─────────────────── SECURITY KERNEL ───────────────────┐
│ 1. Resolve tool → tier (T0-T3) + egress flag          │
│ 2. Effective tier = tier, raised to ≥T2 if turn tainted│
│ 3. Egress tool + tainted → force approval             │
│ 4. Approval required? → prompt user, await decision    │
│ 5. Write audit entry (before execution)                │
│ 6. Execute                                             │
│ 7. Write audit outcome                                 │
│ 8. Tag result provenance = RETRIEVED → taint the turn  │
└────────────────────────┬───────────────────────────────┘
                         ▼
                   Tool implementation
```

Steps 2, 3, and 8 are what distinguish this from a conventional permission model.

**Step 8 is easy to overlook and essential:** a tool's *output* is untrusted content. Reading email taints the turn — that is the whole point.

### 5.3 Input adapters — voice/text parity (DD-035)

```
Voice: mic → VAD → wake word → ASR ──┐
                                     ├──► InputAdapter ──► Turn ──► Orchestrator
Text:  keyboard ─────────────────────┘
```

Both adapters emit the same normalised input with `provenance: USER`. Nothing downstream knows or cares which was used. This gives parity for free and makes the pipeline testable without a microphone.

### 5.4 Output routing — content-typed panels (DD-080, DD-081)

The LLM is instructed to tag response segments by content type. The orchestrator emits typed chunks; the renderer maps type → panel component.

| Type | Panel | Notes |
|---|---|---|
| `text` | ChatPanel | Markdown |
| `code` | CodePanel | Syntax highlighted, copy |
| `image` | ImagePanel | Lightbox |
| `video` | VideoPanel | Embedded player |
| `data` | DataPanel | Table / JSON tree |
| `diagram` | DiagramPanel | Mermaid |
| `chart` | ChartPanel | Plotly |
| `action` | ActionPanel | Approval affordance |

**Fallback:** an unrecognised or missing type renders as `text`. The panel system must never lose content because a tag was malformed.

### 5.5 Streaming and the TTS pipeline (DD-105, DD-106)

Validation showed naive synthesis gives 8.83 s time-to-first-audio on a long reply, and that uneven chunking causes audible gaps despite healthy aggregate throughput.

```
LLM token stream
      │
      ▼
 SentenceAssembler ──► emits on sentence boundary,
      │                splits any chunk > ~65 chars at clause boundaries
      ▼
  TTS Queue  (worker synthesises ahead, ≥1 chunk buffered)
      │
      ▼
 Audio stream → renderer → playback + avatar lip-sync
```

Design rules from measurement:
- **First chunk shortest** — governs perceived latency
- **Uniform chunk duration thereafter** — prevents starvation
- **Model warmed at startup** — cold call costs 1.76 s vs 0.30 s (DD-105)

### 5.6 Provider abstraction (DD-040)

```
LLMProvider (interface)
├── OllamaProvider     ← default, local (DD-041)
├── OpenAIProvider
├── AnthropicProvider
└── GoogleProvider
```

Selection is config-driven. The active provider is surfaced to the UI at all times, and switching local → cloud requires explicit acknowledgement (DD-113).

---

## 6. Data Flows

### 6.1 Voice turn — no tools (simplest path)

```
"Hey Panini, what's 15% of 240?"

wake word (~0.1s) → VAD end-point → ASR base int8 CPU (0.75s)
  → Turn created, provenance USER
  → LLM prompt eval (0.25s) → stream tokens (35 tok/s)
  → SentenceAssembler → TTS first chunk (0.31s)
  → audio + lip-sync

Measured composite: ≈1.8–2.2s to first audio
```

### 6.2 Tool turn — with taint (the security-relevant path)

```
"Read my last 5 emails and reply to the one from Sarah"

USER input → Turn (clean)
  → LLM requests gmail_read
      → Security kernel: T0 read, turn clean → no prompt
      → execute → results tagged RETRIEVED
      → ⚠️ TURN NOW TAINTED
  → LLM requests gmail_send
      → Security kernel:
          tier T1 → raised to T2 (tainted)
          egress tool + tainted → force approval
      → PROMPT: "Send email to sarah@example.com?
                 Preview: ...
                 ⚠️ This action follows content read from your inbox."
      → user approves → execute → audit
```

**If an email body had contained an injected instruction**, the same gate fires. The assistant cannot send anything without the user seeing the destination and payload. That is the containment boundary (DD-111, DD-112).

---

## 7. Technology Stack

| Layer | Technology | Decision |
|---|---|---|
| Desktop shell | Electron | DD-010 |
| Renderer | React + TypeScript + Vite | DD-011 |
| 3D avatar | Three.js + `@pixiv/three-vrm` | DD-030 |
| Backend | Python 3.12 + FastAPI + uvicorn | DD-012 |
| Transport | WebSocket (audio/chat) + SSE (events) | DD-013 |
| LLM runtime | Ollama — `qwen2.5:7b-instruct-q4_K_M` | DD-041, DD-104 |
| STT | faster-whisper `base`, int8, CPU | DD-020 |
| TTS | kokoro-onnx, CPU | DD-022 |
| VAD | Silero | DD-024 |
| Wake word | OpenWakeWord | DD-025 |
| Browser automation | Playwright | DD-051 |
| Persistence | SQLite | DD-065 |
| Config | YAML — `panini.yaml` | DD-092 |
| Crypto | `cryptography` (ed25519, Fernet) | DD-070, DD-066 |

---

## 8. Deployment Topology

```
Machine: i7-13650HX · 64 GB RAM · RTX 3050 6 GB + Intel UHD    DD-095

  Electron main ──── spawns ──► Python backend (uvicorn :8760)
       │                              │
       │                              └──HTTP──► Ollama :11434
       │                                          └─ RTX 3050, 4,857 MiB, num_gpu=29
       └── Renderer ── Intel UHD iGPU (avatar, 0 MiB RTX)    DD-101

  CPU (20 threads): STT · TTS · VAD · wake word              DD-097
```

**Resource allocation is deliberate, and measured:**

| Resource | Allocation | Measured |
|---|---|---|
| RTX 3050 VRAM | LLM only | 4,857 MiB peak / 6,144 available |
| Intel iGPU | Avatar rendering | 0.39 ms/frame |
| CPU threads | Voice pipeline | STT 0.75 s, TTS 0.31 s TTFA |
| System RAM | Everything else | 64 GB, ample |

**Startup sequence:**
1. Electron main starts, pins renderer to integrated GPU (DD-101)
2. Spawns Python backend; waits for health check
3. Backend warms TTS model (DD-105) and loads wake-word model
4. Backend verifies Ollama reachable; does **not** preload the model
5. Renderer connects WebSocket + SSE
6. Tray icon becomes active

**Shutdown:** window close hides to tray; the daemon continues (DD-090). Ollama unloads the model after `keep_alive: 5m` idle (DD-099).

---

## 9. Cross-Cutting Concerns

| Concern | Approach |
|---|---|
| **Configuration** | Single `panini.yaml`; layered defaults → user overrides; hot-reload where safe (DD-092) |
| **Logging** | Structured JSON to file; separate from the audit log, which is append-only and tool-inaccessible (DD-116) |
| **Error handling** | Tool failures surface in the UI as an error panel; the conversation continues (AP-7) |
| **Startup health** | Each subsystem reports ready/failed; the UI shows degraded state rather than failing silently |
| **Versioning** | Config carries a schema version; migrations on upgrade |
| **Testing** | Text input adapter (DD-035) enables full pipeline testing without audio hardware |

---

## 10. Traceability — BRD to Design

| BR | Requirement | Realised by |
|---|---|---|
| BR-001 – 003 | Voice interaction, wake word, interruption | L5 Voice · §5.3 |
| BR-004, 006 | NLU, context retention | L2 Orchestration · §5.6 |
| BR-005 | Text parity | §5.3 input adapters |
| BR-007 – 009, 011 | Avatar, lip-sync, desktop pet | Renderer · §5.5 |
| BR-010 | Content-typed output | §5.4 panel routing |
| BR-012 | Personas | L4 — REIMPLEMENT |
| BR-013 – 020 | Connectors | L4 tool registry · §4.2 |
| BR-022, 023 | Offline operation, no third-party transmission | Ollama default · §5.6 |
| BR-024 | Provider transparency | §5.6 · DD-113 |
| BR-025 | Credential protection | L3 · DD-115 |
| BR-026 | Action audit | L3/L6 · DD-116 |
| BR-027 – 029 | Approval, read-without-prompt, disable capability | **L3 security kernel · §5.2** |
| BR-030, 030a–c | Injection defence, taint, egress preview | **L3 · §5.2 · §6.2** |
| BR-031 – 034 | Model switching, extensibility, config | §5.6 · L4 · DD-092 |
| BR-035 – 038 | Background daemon, responsiveness, VRAM release | §8 deployment |

**All 42 business requirements map to a design element.** No orphans.

---

## 11. Design Risks

| ID | Risk | Mitigation |
|---|---|---|
| **SR-01** | Security kernel becomes a bottleneck or is bypassed by a future contributor | Structural enforcement — the raw tool registry is never injected into L2; only the wrapped façade is |
| **SR-02** | LLM emits malformed content-type tags, breaking panel routing | Fallback to `text`; never drop content (§5.4) |
| **SR-03** | Taint tracking causes approval fatigue on tool-heavy turns | Read-only tools never prompt when clean; monitor real usage and tune (DD-114, SC-5) |
| **SR-04** | Three FORKs drift from upstream | Record commit SHAs; review upstream quarterly |
| **SR-05** | Electron fails to pin the integrated GPU on some driver configurations | Detect actual renderer at startup; warn and adjust VRAM budget (DD-101) |
| **SR-06** | Sentence chunking mis-splits on abbreviations ("Dr.", "e.g.") | Use a proper sentence segmenter, not a naive regex — flagged for LLD |

---

## 12. Open Items for LLD

| # | Item | Notes |
|---|---|---|
| 1 | Conversation memory architecture | DO-002 still open — short-term vs long-term vs vector store |
| 2 | Exact egress tool classification list | DD-112 requires an exhaustive list; completeness is the control |
| 3 | Sentence segmentation library choice | SR-06 — naive regex is insufficient |
| 4 | Approval dialog UX detail | Must convey destination, payload, and taint source legibly |
| 5 | Plugin manifest schema | DD-070 |
| 6 | Audit log schema | DD-065, DD-116 |
| 7 | Wake-word and VAD end-pointing benchmarks | Validation Tests 8 & 9 outstanding |

---

## 13. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |

**On approval, the next phase is `03_FRD`.**
