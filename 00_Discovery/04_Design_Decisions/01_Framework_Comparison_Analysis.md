# Project Panini — Framework Comparison Analysis

**Document Type:** Discovery  
**Phase:** 00 — Discovery  
**Status:** Decision Made  
**Date:** 2026-09-08  

---

## 1. Desktop Framework Decision: Tauri 2 vs Electron vs Web-Only

### Comparison Matrix

| Dimension | Tauri 2 | Electron | Web-Only |
|---|---|---|---|
| Installed size | ~10-15 MB | ~150-250 MB | 0 (browser) |
| RAM consumption | 40-80 MB | 150-400 MB | ~200 MB+ |
| Rendering engine | OS WebView (WebView2/WebKit) | Bundled Chromium | User's browser |
| Rendering consistency | Varies by OS | Identical everywhere | Varies by browser |
| File system access | ✅ via Rust | ✅ via Node | ❌ |
| Shell / terminal | ✅ Rust | ✅ Node | ❌ |
| System tray | ✅ | ✅ | ❌ |
| Global shortcut | ✅ | ✅ | ❌ |
| Auto-start on boot | ✅ | ✅ | ❌ |
| Background always-on | ✅ | ✅ | ❌ |
| Transparent window / desktop pet | ⚠️ Early support | ✅ Mature | ❌ |
| WebRTC reliability | ⚠️ WebView2 quirks | ✅ Mature Chromium | ✅ |
| Three.js / VRM avatars | ✅ (via WebView) | ✅ (Chromium WebGL) | ✅ |
| Web Audio API | ✅ | ✅ | ✅ |
| Backend language | Rust | Node.js | JS/TS |
| Learning curve (solo dev) | High (adds Rust) | Low (stays JS/TS) | Lowest |
| Startup time | ~1-2s | ~3-5s | Varies |
| Security surface | Smaller (Rust) | Larger (Chromium) | Browser-enforced |
| Ecosystem maturity | Growing | Very mature (10+ yrs) | N/A |
| Desktop-pet avatar | ⚠️ | ✅ Proven | ❌ |

### Scoring (Weighted for Panini's Specific Requirements)

| Dimension | Weight | Tauri 2 | Electron | Web-Only |
|---|---|---|---|---|
| Bundle size | 3% | 10 | 5 | 10 |
| RAM / performance | 8% | 9 | 6 | 7 |
| Rendering consistency | 12% | 7 | 10 | 6 |
| Native system access | 15% | 9 | 9 | 3 |
| Development ease (solo dev) | 22% | 5 | 9 | 10 |
| Security | 10% | 9 | 6 | 7 |
| Ecosystem maturity | 8% | 6 | 10 | 7 |
| Avatar + desktop-pet | 12% | 6 | 9 | 4 |
| WebRTC / voice reliability | 10% | 6 | 9 | 7 |
| **WEIGHTED TOTAL** | **100%** | **6.97** | **8.42** | **6.55** |

### Decision: **Electron**

**Rationale specific to Project Panini:**

1. **Python sidecar makes Rust irrelevant:** The core AI work (STT, LLM, TTS) runs in a Python process. Electron's Node.js IPC for bridging Python ↔ frontend is simpler than Tauri's Rust commands with no capability difference.

2. **Desktop-pet floating avatar is proven in Electron:** Open-LLM-VTuber (our avatar base) already uses transparent/frameless windows in their reference implementations.

3. **WebRTC/voice reliability is the #1 feature:** Electron ships Chromium — full WebRTC stack with no platform quirks. WebView2 (Tauri on Windows) has known audio device enumeration and echo cancellation issues.

4. **Solo dev, 3 languages already:** Python (AI backend) + JavaScript/React (frontend) + now Rust (Tauri) = too much cognitive overhead. Electron keeps it Python + JS.

5. **20% weight on dev ease:** For a personal project, velocity matters more than 10 MB of installer savings.

---

## 2. AI/LLM Architecture Decision

### Options Considered

| Option | Pros | Cons |
|---|---|---|
| Single LLM provider (OpenAI only) | Simple, fast to build | Lock-in, cloud-only, cost |
| Factory pattern (swappable provider) | Flexible, future-proof | Slightly more upfront work |
| Local LLM only (Ollama) | Full privacy, free | Slower, quality gap on small models |
| Hybrid (local default + cloud optional) | Best of both | Moderate complexity |

### Decision: **Factory Pattern with Hybrid Default**

- Default: Local Ollama (privacy-first)
- Optional: OpenAI / Gemini / Anthropic API (for tasks needing stronger reasoning)
- Config-driven switch — no code change required
- Reuse: Thoth's `providers/` abstraction + Open-LLM-VTuber's `agent/stateless_llm/`

---

## 3. Avatar System Decision

### Options Considered

| Option | Technology | Source | Quality |
|---|---|---|---|
| Live2D | Live2D Web SDK, Cubism 4/5 | Open-LLM-VTuber | Anime-style, very expressive |
| VRM 3D | Three.js + @pixiv/three-vrm | Naia OS | 3D, more realistic, VTuber-standard |
| GLB rigged | Three.js + GLB loader | Sapphire | Custom models, animation tracks |
| 2D illustrated | Static image + CSS animations | Custom | Simplest, least immersive |

### Decision: **Dual Avatar Support (Live2D + VRM), config-selectable**

- `AvatarRenderer` component accepts a driver interface
- Live2D driver: ported from Open-LLM-VTuber (most mature lip-sync)
- VRM driver: ported from Naia OS Three.js implementation
- User selects avatar type in `panini.yaml`
- Emotion signals fed from LLM output → both drivers translate to facial expressions

---

## 4. Voice Pipeline Decision

### STT Selection

| Engine | Mode | Quality | Latency | Decision |
|---|---|---|---|---|
| faster-whisper | Local | High | ~0.5s | ✅ **Primary (local)** |
| OpenAI Whisper API | Cloud | Highest | ~1-2s | Optional upgrade |
| Sherpa ONNX | Local, tiny | Good | <0.3s | Future mobile/low-power mode |

### TTS Selection

| Engine | Mode | Quality | Decision |
|---|---|---|---|
| Kokoro | Local | Very high | ✅ **Primary (local)** |
| Edge TTS | Free cloud | Good | Fallback |
| ElevenLabs | Cloud | Highest | Optional |

### Wake Word

| Engine | Decision |
|---|---|
| OpenWakeWord | ✅ Selected — from Sapphire, offline, custom model support |

---

## 5. Output Rendering Decision

**Problem:** Different query types deserve different visual presentations.

| Query Type | Output Panel | Example |
|---|---|---|
| Code / scripts | `CodePanel` — syntax highlighted, copy button | "Write me a Python script" |
| Images | `ImagePanel` — lightbox, download | "Generate an image of..." |
| Video | `VideoPanel` — embedded player | "Show me a video about..." |
| Data / lists | `DataPanel` — table or JSON tree | "List my emails" |
| Actions | `ActionPanel` — confirmation modal | "Delete that email" |
| General text | `ChatPanel` — markdown rendered | "Explain quantum computing" |
| Diagrams | `DiagramPanel` — Mermaid rendered | "Draw the architecture" |
| Charts | `ChartPanel` — Plotly interactive | "Show my calendar this week" |

**Decision:** Smart panel routing — LLM output tagged with content type → React panel selector component routes to correct panel.

---

## Summary of All Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Desktop framework | **Electron** | WebRTC, mature avatar, Python IPC ease |
| LLM architecture | **Factory + Hybrid** | Flexibility + local-first |
| Avatar primary | **Live2D** (VRM as option) | Proven lip-sync, mature |
| STT | **faster-whisper** (local) | Quality + speed balance |
| TTS | **Kokoro** (local) | Best local quality |
| Wake word | **OpenWakeWord** | Offline, custom models |
| Plugin system | **Sapphire pattern** | Most mature, signed, hot-reload |
| Tool registry | **Thoth pattern** | 34 tools ready, tiered permissions |
| Security model | **Naia OS 4-tier** | T0–T3 with audit log |
| Event bus | **Sapphire SSE** | Decoupled, replay buffer |
| Output rendering | **Smart panel routing** | Context-aware rich presentation |
