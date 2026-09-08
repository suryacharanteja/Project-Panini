# Project Panini — Open Source Repository Analysis

**Document Type:** Discovery  
**Phase:** 00 — Discovery  
**Status:** Complete  
**Date:** 2026-09-08  

---

## Overview

Four open-source repositories were analysed as candidate bases for Project Panini. Rather than building from scratch, the strategy is to harvest the strongest modules from each and compose them into one unified system. This document records what each repository contains, what is valuable, what is missing, and the exact modules identified for reuse.

---

## Repository 1: Open-LLM-VTuber

**URL:** https://github.com/Open-LLM-VTuber/Open-LLM-VTuber  
**Stars:** 13,492 (as of 2026-09-08)  
**Language:** Python (backend) + TypeScript (frontend)  
**License:** MIT  

### Directory Structure Mapped

```
Open-LLM-VTuber/
├── src/open_llm_vtuber/
│   ├── asr/                        ← Speech-to-Text engine layer
│   │   ├── asr_interface.py        ← Abstract base for all STT engines
│   │   ├── asr_factory.py          ← Factory: creates STT instance from config
│   │   ├── faster_whisper_asr.py   ← Local GPU/CPU Whisper (primary choice)
│   │   ├── openai_whisper_asr.py   ← OpenAI Whisper API
│   │   ├── groq_whisper_asr.py     ← Groq-hosted Whisper
│   │   ├── azure_asr.py            ← Azure Speech
│   │   ├── sherpa_onnx_asr.py      ← Sherpa ONNX (offline, mobile-ready)
│   │   ├── whisper_cpp_asr.py      ← Whisper.cpp (C++ inference)
│   │   ├── fun_asr.py              ← FunASR (Chinese-optimised)
│   │   └── utils.py
│   │
│   ├── tts/                        ← Text-to-Speech engine layer
│   │   ├── tts_interface.py        ← Abstract base for all TTS engines
│   │   ├── tts_factory.py          ← Factory: creates TTS instance from config
│   │   ├── edge_tts.py             ← Microsoft Edge TTS (free, good quality)
│   │   ├── kokoro_tts.py           ← Kokoro (local, high quality)
│   │   ├── bark_tts.py             ← Bark (local, expressive)
│   │   ├── openai_tts.py           ← OpenAI TTS API
│   │   ├── elevenlabs_tts.py       ← ElevenLabs API
│   │   ├── azure_tts.py            ← Azure TTS
│   │   ├── gpt_sovits_tts.py       ← GPT-SoVITS (voice cloning)
│   │   ├── cosyvoice_tts.py        ← CosyVoice v1 & v2
│   │   ├── piper_tts.py            ← Piper (offline)
│   │   ├── melo_tts.py             ← MeloTTS
│   │   ├── fish_audio_tts.py       ← Fish Audio API
│   │   ├── coqui_tts.py            ← Coqui TTS
│   │   ├── minimax_tts.py          ← MiniMax API
│   │   ├── cartesia_tts.py         ← Cartesia
│   │   ├── spark_tts.py            ← Spark TTS
│   │   ├── siliconflow_tts.py      ← SiliconFlow
│   │   ├── pyttsx3_tts.py          ← pyttsx3 (system TTS fallback)
│   │   └── sherpa_onnx_tts.py      ← Sherpa ONNX TTS
│   │
│   ├── vad/                        ← Voice Activity Detection
│   │   ├── vad_interface.py        ← Abstract base for VAD
│   │   ├── vad_factory.py          ← Factory pattern
│   │   └── silero.py               ← Silero VAD (primary, local)
│   │
│   ├── agent/                      ← LLM agent layer
│   │   ├── agent_factory.py        ← Creates agent from config
│   │   ├── input_types.py          ← Input type definitions
│   │   ├── output_types.py         ← Output type definitions
│   │   ├── stateless_llm_factory.py
│   │   ├── transformers.py
│   │   ├── agents/                 ← Agent implementations
│   │   └── stateless_llm/          ← Stateless LLM backends
│   │
│   ├── config_manager/             ← Config loading + validation
│   ├── conversations/              ← Conversation state management
│   ├── translate/                  ← Translation support (multilingual)
│   ├── utils/                      ← Shared utilities (tts_preprocessor.py etc.)
│   ├── live2d_model.py             ← Live2D avatar controller
│   ├── server.py                   ← FastAPI WebSocket server
│   ├── routes.py                   ← API route definitions
│   ├── websocket_handler.py        ← WebSocket message handler
│   ├── message_handler.py          ← Message processing pipeline
│   ├── chat_history_manager.py     ← Conversation history
│   └── service_context.py          ← Application service state
│
├── frontend/                       ← Web UI (submodule)
├── live2d-models/                  ← Live2D model assets
├── avatars/                        ← Character definitions
├── characters/                     ← Character config files
├── prompts/                        ← System prompt templates
├── conf.yaml                       ← Master config
└── mcp_servers.json                ← MCP server registry
```

### What We Take

| Module | Files | Why |
|---|---|---|
| **Full ASR layer** | `asr/` (all files) | 7 STT engines, factory pattern, clean interface — production-grade |
| **Full TTS layer** | `tts/` (all files) | 20+ TTS engines, factory pattern — most comprehensive available |
| **Full VAD layer** | `vad/` (all files) | Silero VAD with factory pattern — battle-tested |
| **Live2D avatar** | `live2d_model.py` + frontend | Emotion mapping, lip-sync, desktop-pet mode — kept as optional driver |
| **WebSocket server** | `server.py`, `routes.py`, `websocket_handler.py` | Real-time audio/text streaming proven |
| **Config manager** | `config_manager/` | YAML-based swappable config pattern |

### What We Skip

| Module | Reason |
|---|---|
| `agent/` LLM routing | Replace with our own LLM abstraction (cleaner) |
| `live/` Bilibili streaming | Not relevant — personal assistant context |
| `translate/` | Phase 2 feature |

---

## Repository 2: Thoth (arorakrishh/Thoth)

**URL:** https://github.com/arorakrishh/Thoth  
**Language:** Python + Streamlit  
**Note:** Also mirrored at Complete-Robot/Thoth  

### Directory Structure Mapped

```
Thoth/
├── tools/                          ← 34 tool modules
│   ├── base.py                     ← Base class all tools inherit from
│   ├── registry.py                 ← Tool registration + discovery
│   ├── gmail_tool.py               ← Gmail read/compose/delete
│   ├── calendar_tool.py            ← Google Calendar read/write
│   ├── web_search_tool.py          ← Tavily + DuckDuckGo search
│   ├── filesystem_tool.py          ← File read/write/list/delete
│   ├── shell_tool.py               ← Shell/terminal execution
│   ├── browser_tool.py             ← Browser automation
│   ├── image_gen_tool.py           ← Image generation (OpenAI/Google/xAI)
│   ├── video_gen_tool.py           ← Video generation (Veo, Grok)
│   ├── weather_tool.py             ← Weather data
│   ├── calculator_tool.py          ← Math/calculator
│   ├── wikipedia_tool.py           ← Wikipedia search
│   ├── arxiv_tool.py               ← arXiv paper search
│   ├── youtube_tool.py             ← YouTube transcript
│   ├── url_reader_tool.py          ← URL/web page reader
│   ├── memory_tool.py              ← Conversation memory
│   ├── health_tracker_tool.py      ← Health/fitness tracking
│   ├── chart_tool.py               ← Plotly chart generation
│   ├── mermaid_tool.py             ← Mermaid diagram generation
│   └── ... (34 total)
│
├── channels/                       ← Messaging channel integrations
│   ├── telegram/
│   ├── whatsapp/
│   ├── discord/
│   └── slack/
│
├── providers/                      ← LLM provider integrations
│   ├── openai_provider.py
│   ├── anthropic_provider.py
│   ├── google_provider.py
│   ├── ollama_provider.py
│   └── ... (10+ providers)
│
├── mcp_client/                     ← MCP protocol client
├── plugins/                        ← Plugin system
├── buddy/                          ← Companion features
└── bundled_skills/                 ← Pre-packaged skills
```

### Tool Permission Model (Tiered)

Thoth implements tiered permissions for tool execution:
- **Read tier:** Gmail read, Calendar read, filesystem read — always allowed
- **Write tier:** Gmail compose, Calendar create, file write — requires confirmation
- **Destructive tier:** Gmail delete, file delete, shell commands — requires explicit approval

### What We Take

| Module | Files | Why |
|---|---|---|
| **Gmail tool** | `tools/gmail_tool.py` | Full read/compose/delete with OAuth, tiered permissions |
| **Calendar tool** | `tools/calendar_tool.py` | Google Calendar CRUD |
| **Web search tool** | `tools/web_search_tool.py` | Tavily + DuckDuckGo dual-backend |
| **Filesystem tool** | `tools/filesystem_tool.py` | Safe file operations |
| **Shell tool** | `tools/shell_tool.py` | Shell execution with safety |
| **Browser tool** | `tools/browser_tool.py` | Web automation |
| **Tool base + registry** | `tools/base.py`, `tools/registry.py` | Registration/discovery pattern |
| **Permission model** | Tiered logic in tools | Safety architecture |
| **LLM providers** | `providers/` | Multi-provider abstraction |
| **Chart/diagram tools** | `tools/chart_tool.py`, `tools/mermaid_tool.py` | Rich output rendering |

### What We Skip

| Module | Reason |
|---|---|
| Streamlit UI | Replacing with custom React + Electron UI |
| `channels/` messaging | Phase 2 feature |
| `health_tracker_tool.py` | Phase 2 feature |

---

## Repository 3: Sapphire (ddxfish/sapphire)

**URL:** https://github.com/ddxfish/sapphire  
**Stars:** 276  
**Language:** Python (backend) + JavaScript (frontend)  

### Directory Structure Mapped

```
sapphire/
├── core/
│   ├── plugin_loader.py            ← Dynamic plugin load/unload at runtime
│   ├── plugin_verify.py            ← ed25519 plugin signature verification
│   ├── hooks.py                    ← Hook types: prompt_inject, ghost_inject, pre_chat
│   ├── event_bus.py                ← Thread-safe pub/sub, SSE, 50-event replay
│   ├── event-bus.js                ← Frontend EventSource consumer
│   ├── ghost_messages.py           ← Per-turn metadata injection (no cache break)
│   ├── settings_defaults.json      ← Default config
│   ├── routes/                     ← FastAPI route definitions
│   ├── chat/
│   │   ├── chat_streaming.py       ← Streaming SSE response handler
│   │   └── function_manager.py     ← Tool/function registration with scopes
│   ├── stt/
│   │   ├── recorder.py             ← Adaptive VAD + silence detection
│   │   └── utils.py                ← can_transcribe() guard
│   ├── tts/
│   │   └── tts_server.py           ← Kokoro subprocess server (port 5012)
│   ├── wakeword/
│   │   ├── wake_detector.py        ← OpenWakeWord integration
│   │   └── audio_recorder.py       ← Audio capture
│   └── audio/
│       └── device_manager.py       ← Cross-platform audio device singleton
│
├── plugins/                        ← 27 built-in plugins
│   ├── avatar/                     ← GLB avatar with animation tracks
│   ├── comfyui/                    ← ComfyUI image generation
│   ├── google-calendar/            ← Google Calendar
│   ├── discord/                    ← Discord integration
│   ├── telegram/                   ← Telegram integration
│   ├── email/                      ← Email functionality
│   ├── github/                     ← GitHub integration
│   ├── homeassistant/              ← Home Assistant
│   ├── memory/                     ← Memory management
│   ├── image-gen/                  ← Image generation
│   ├── elevenlabs/                 ← ElevenLabs TTS
│   ├── voice-commands/             ← Voice command processing
│   ├── webcam/                     ← Webcam integration
│   ├── mcp_client/                 ← MCP client plugin
│   ├── ssh/                        ← SSH/remote
│   ├── claude-code/                ← Claude Code integration
│   ├── commandline/                ← Command line execution
│   ├── agents/                     ← Multi-agent support
│   ├── toolmaker/                  ← Custom tool builder
│   └── ... (27 total)
│
├── functions/                      ← Core tool function implementations
├── user/
│   ├── plugins/                    ← User-installed plugins (private)
│   ├── personas/                   ← Persona definitions + avatars
│   │   ├── personas.json
│   │   └── avatars/
│   └── settings.json               ← User config overrides
│
└── main.py                         ← Runner with auto-restart (exit code 42)
```

### Plugin Architecture Deep-Dive

- **Manifest-driven:** Each plugin declares capabilities in a manifest file
- **Signed:** ed25519 signatures prevent tampering (SHA256 of all .py/.json/.js/.css files)
- **Hot-reload:** Enable/disable in UI with no server restart
- **Scoped:** Each plugin declares capability scopes via `capabilities.scopes`
- **Hook types:**
  - `prompt_inject` — modifies system prompt
  - `ghost_inject` — adds per-turn metadata without breaking prompt cache
  - `pre_chat` — runs before each chat turn

### Event Bus Deep-Dive

- **Thread-safe pub/sub** in `core/event_bus.py`
- **50-event replay buffer** for late subscribers
- **15-second keepalive pings**
- **Boot version tracking** — detects server restarts without clearing browser cache
- **Events published:** AI typing, TTS/STT state, chat switches, wakeword detection, settings changes, errors

### What We Take

| Module | Files | Why |
|---|---|---|
| **Plugin loader** | `core/plugin_loader.py`, `core/plugin_verify.py` | Best-in-class dynamic plugin system with security |
| **Hook system** | `core/hooks.py` | Clean extensibility without coupling |
| **Event Bus** | `core/event_bus.py`, `core/event-bus.js` | Decoupled inter-module communication |
| **Wake word** | `core/wakeword/wake_detector.py` | OpenWakeWord integration |
| **Audio device manager** | `core/audio/device_manager.py` | Cross-platform audio handling |
| **Persona system** | `user/personas/` | Switchable assistant personalities |
| **Ghost messages** | `core/ghost_messages.py` | Per-turn injection without cache break |
| **GLB avatar plugin** | `plugins/avatar/` | Rigged GLB models with animation tracks |
| **ComfyUI plugin** | `plugins/comfyui/` | Local image generation integration |
| **Plugin manifest pattern** | Plugin structure | Security + discoverability standard |

### What We Skip

| Module | Reason |
|---|---|
| `core/tts/tts_server.py` (Kokoro subprocess) | Use Open-LLM-VTuber's TTS layer instead (more engines) |
| `core/stt/` | Use Open-LLM-VTuber's ASR layer instead (more engines) |
| Bitcoin plugin | Not relevant |

---

## Repository 4: Naia OS (tladus-git/naia-os)

**URL:** https://github.com/tladus-git/naia-os  
**Stars:** Early stage (new repo)  
**Language:** TypeScript/React (shell) + Rust (Tauri) + Python (agent)  
**Platform:** Linux (primary), Windows potential  

### Directory Structure Mapped

```
naia-os/
├── shell/                          ← Tauri 2 desktop app
│   ├── src/                        ← React frontend
│   ├── src-tauri/                  ← Rust Tauri backend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── playwright.config.ts        ← E2E tests
│
├── agent/                          ← LLM + tools runtime (moving to separate repo)
├── gateway/                        ← Tool/channel/memory bridge (merging into agent)
│
├── voice-server/
│   └── minicpm-o/                  ← MiniCPM-O voice processing
│
├── .agents/                        ← AI context (YAML/JSON, token-optimized)
├── .users/                         ← Human docs (Markdown, multilingual, 14 languages)
│
├── config/                         ← systemd units, daemon scripts
├── os/                             ← OS integration tests
├── flatpak/                        ← Flatpak packaging
├── recipes/                        ← BlueBuild OS image recipes
├── benchmarks/zep-ce/              ← Zep CE memory benchmarks
└── assets/character/               ← VRM character assets (genderless, female variants)
```

### Architecture: 4-Repo Ecosystem

```
naia-os (shell/frontend)
    ↓
naia-agent (LLM + tools runtime)
    ├── naia-adk (workspace + skills)
    └── alpha-memory (storage/memory)
```

**Communication:** stdio JSON lines protocol (shell ↔ agent)  
**Always-on:** Naia Gateway daemon keeps AI running when app closes  
**Spawn logic:** App launch → gateway health check → reuse if running → else auto-spawn

### 4-Tier Security Model

| Tier | Level | Permissions |
|---|---|---|
| T0 | Read | File reads, info queries — no confirmation |
| T1 | Write | File writes, calendar events — soft confirmation |
| T2 | Execute | Shell commands, browser actions — explicit approval modal |
| T3 | Dangerous | System changes, deletions — hard approval + audit log |

All executions logged to SQLite audit database.

### VRM Avatar System

- **Library:** Three.js + `@pixiv/three-vrm`
- **Emotions:** 6 facial expressions (joy, sadness, surprise, thinking + 2 more)
- **Lip-sync:** Real-time from TTS audio stream
- **Gaze tracking:** Dynamic eye tracking
- **Character assets:** `/assets/character/` — genderless default + female variant

### What We Take

| Module | Files | Why |
|---|---|---|
| **VRM avatar renderer** | `shell/src/` Three.js VRM components | Most complete open-source VRM+emotion+lipsync stack |
| **4-tier security model** | Permission/approval logic in agent | Safety architecture for destructive tool calls |
| **SQLite audit log** | Tool execution logging | Accountability for all actions |
| **Daemon architecture** | `config/` systemd units, gateway spawn logic | Always-on background process pattern |
| **Approval modal UI** | Shell UI component | User confirmation for T2/T3 actions |
| **Skill registry pattern** | `naia-adk` workspace | Named skill invocation model |

### What We Skip

| Module | Reason |
|---|---|
| Tauri shell (entire) | Using Electron instead (better WebRTC, more mature) |
| BlueBuild OS recipes | Linux OS customization — not relevant |
| Flatpak packaging | Linux-specific |
| MiniCPM-O voice server | Using Open-LLM-VTuber ASR/TTS instead |

---

## Module Harvest Summary

| Source Repo | Module | Target in Panini |
|---|---|---|
| Open-LLM-VTuber | `asr/` (full) | `backend/voice/asr/` |
| Open-LLM-VTuber | `tts/` (full) | `backend/voice/tts/` |
| Open-LLM-VTuber | `vad/` (full) | `backend/voice/vad/` |
| Open-LLM-VTuber | `live2d_model.py` + frontend | `frontend/src/avatar/live2d/` *(optional driver)* |
| Open-LLM-VTuber | `server.py`, `websocket_handler.py` | `backend/server/` |
| Thoth | `tools/base.py`, `tools/registry.py` | `backend/tools/` |
| Thoth | `tools/gmail_tool.py` | `backend/tools/gmail.py` |
| Thoth | `tools/calendar_tool.py` | `backend/tools/calendar.py` |
| Thoth | `tools/web_search_tool.py` | `backend/tools/web_search.py` |
| Thoth | `tools/filesystem_tool.py` | `backend/tools/filesystem.py` |
| Thoth | `tools/shell_tool.py` | `backend/tools/shell.py` |
| Thoth | `providers/` | `backend/llm/providers/` |
| Sapphire | `core/plugin_loader.py` | `backend/plugins/loader.py` |
| Sapphire | `core/hooks.py` | `backend/plugins/hooks.py` |
| Sapphire | `core/event_bus.py` | `backend/events/event_bus.py` |
| Sapphire | `core/wakeword/` | `backend/voice/wakeword/` |
| Sapphire | `core/audio/device_manager.py` | `backend/voice/audio_devices.py` |
| Sapphire | `plugins/avatar/` (GLB) | `frontend/src/avatar/glb/` |
| Sapphire | `user/personas/` | `backend/personas/` |
| Naia OS | Three.js VRM components | `frontend/src/avatar/vrm/` *(primary driver)* |
| Naia OS | 4-tier permission model | `backend/security/permissions.py` |
| Naia OS | SQLite audit log | `backend/security/audit.py` |
| Naia OS | Daemon spawn logic | `backend/daemon/gateway.py` |
