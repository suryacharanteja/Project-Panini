# Project Panini

**P.A.N.I.N.I.** — *Personal Assistant for Natural Information & Neural Interaction*

> A local-first, voice-driven AI personal assistant with a living avatar, intelligent output rendering, and deep integration into daily tools.

---

## Current Status

| Phase | Status |
|---|---|
| 00 — Discovery | ✅ Complete |
| 01 — BRD | 🔲 Not Started |
| 02 — Solution Design | 🔲 Not Started |
| 03 — FRD | 🔲 Not Started |
| 04 — HLD | 🔲 Not Started |
| 05 — LLD | 🔲 Not Started |
| 06 — Implementation | 🔲 Not Started |
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
| Voice STT | faster-whisper (local) | Open-LLM-VTuber |
| Voice TTS | Kokoro (local) | Open-LLM-VTuber |
| Voice VAD | Silero VAD | Open-LLM-VTuber |
| Wake word | OpenWakeWord | Sapphire |
| Avatar | Live2D (+ VRM option) | Open-LLM-VTuber / Naia OS |
| Tool registry | 34-tool base | Thoth |
| Plugin system | Manifest + signed | Sapphire |
| Security model | 4-tier T0–T3 | Naia OS |
| Event bus | SSE pub/sub | Sapphire |
| LLM backend | Factory (Ollama default) | Thoth providers |

---

## Project Structure

```
Project Panini/
├── README.md                          ← This file
│
├── 00_Discovery/                      ✅ Complete
│   ├── 01_Project_Charter/
│   │   ├── 01_Problem_Statement_and_Vision.md
│   │   └── 02_SDLC_Roadmap.md
│   ├── 02_Naming_and_Identity/
│   │   └── 01_Project_Naming_Research.md
│   ├── 03_OSS_Research/
│   │   └── 01_OSS_Repository_Analysis.md
│   └── 04_Design_Decisions/
│       └── 01_Framework_Comparison_Analysis.md
│
├── 01_BRD/                            🔲 Up next
├── 02_Solution_Design/
├── 03_FRD/
├── 04_HLD/
├── 05_LLD/
├── 06_Implementation/
├── 07_Testing/
└── 08_Release/
```

---

## OSS Modules Being Harvested

| Source | Stars | What We Take |
|---|---|---|
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) | ⭐ 13.5k | Full ASR / TTS / VAD pipeline, Live2D avatar, WebSocket server |
| [Thoth](https://github.com/arorakrishh/Thoth) | Growing | 34 tools (Gmail, Calendar, search, shell, filesystem), LLM providers |
| [Sapphire](https://github.com/ddxfish/sapphire) | ⭐ 276 | Plugin loader, event bus, wake word, persona system, GLB avatar |
| [Naia OS](https://github.com/tladus-git/naia-os) | Early | VRM avatar, 4-tier security model, audit log, daemon architecture |

---

## Decision Log

| Date | Decision |
|---|---|
| 2026-09-08 | Project name finalised: **Project Panini** |
| 2026-09-08 | Desktop framework: **Electron** (over Tauri 2) |
| 2026-09-08 | LLM strategy: **Factory pattern, Ollama local default** |
| 2026-09-08 | Avatar: **Live2D primary, VRM as config option** |
| 2026-09-08 | STT: **faster-whisper** (local) |
| 2026-09-08 | TTS: **Kokoro** (local) |
| 2026-09-08 | Wake word: **OpenWakeWord** |
| 2026-09-08 | Build strategy: **Harvest OSS modules, not build from scratch** |

---

*Last updated: 2026-09-08 — Discovery phase complete.*
