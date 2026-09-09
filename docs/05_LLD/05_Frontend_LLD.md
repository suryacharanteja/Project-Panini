# Project Panini — Frontend LLD

**Document Type:** Low Level Design  
**Phase:** 05 — LLD  
**Modules:** `src/renderer/`, `src/electron/`  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## Resolves

| Open item | Source | Section |
|---|---|---|
| Viseme generation for lip-sync | HLD open item #9 | §4 |
| Approval dialog design | Security Kernel LLD §8.2 | §6 |
| Memory view (fact confirmation) | DD-121 | §7 |
| Allowlist UX | Security Kernel LLD §11 | §6.6 |

---

## 1. Module Layout

```
src/renderer/src/
├── app/
│   ├── App.tsx              Layout, mode switching
│   └── store.ts             Zustand — client state
├── transport/
│   ├── chat.ts              WebSocket: text, tokens, approvals
│   ├── audio.ts             WebSocket: PCM in, synthesised out
│   └── events.ts            SSE: EventSource with reconnect
├── avatar/
│   ├── AvatarView.tsx       Driver-agnostic host
│   ├── drivers/vrm.ts       Three.js + @pixiv/three-vrm
│   ├── drivers/live2d.ts    Optional (FR-307)
│   └── visemes.ts           ⚠️ Audio → mouth shapes
├── panels/
│   ├── PanelRouter.tsx      content_type → component
│   └── {Chat,Code,Image,Video,Data,Diagram,Chart,Action}Panel.tsx
├── security/
│   ├── ApprovalDialog.tsx   ⚠️ Last line of defence
│   ├── ProviderIndicator.tsx
│   ├── MemoryView.tsx       Fact review + confirmation (DD-121)
│   └── AuditView.tsx
├── voice/
│   ├── Capture.ts           Mic → PCM frames
│   ├── Playback.ts          Audio queue + interruption
│   └── VoiceIndicator.tsx   State display
└── input/
    └── TextInput.tsx        FR-130

src/electron/
├── main.ts                  Window, tray, sidecar
├── gpu.ts                   Integrated-GPU pinning (DD-101)
└── preload.ts               Context bridge
```

---

## 2. Electron Main

### 2.1 GPU pinning (DD-101, FR-309)

```ts
// BEFORE app.whenReady()
app.commandLine.appendSwitch('force_low_power_gpu');
app.disableHardwareAcceleration.call; // NOT used — we want GPU, just the integrated one
```

Validation measured the renderer already selecting Intel UHD by default, leaving the RTX entirely free. This makes that explicit rather than incidental.

**Verification at startup (FR-310):** the renderer reports `UNMASKED_RENDERER_WEBGL`; if it names the discrete GPU, `gpu.ts` publishes a warning and the backend reduces its VRAM budget by 600 MiB.

### 2.2 Window modes

| Mode | Properties |
|---|---|
| Full | Standard window, panels + avatar |
| Compact | `frame: false`, `transparent: true`, `alwaysOnTop: true`, avatar only (FR-315) |

Compact mode uses `-webkit-app-region: drag` on the avatar for repositioning (FR-316).

### 2.3 Tray (FR-702)

Icon reflects voice state (idle / listening / speaking / muted). Menu: Show · Mute · Provider (read-only) · Quit.

**Closing the window hides to tray; it does not quit** (FR-701). Quit is deliberate and explicit.

---

## 3. Transport

### 3.1 SSE with reconnect

```ts
class EventStream {
  private backoff = 500;                 // ms, doubling to 10 s
  connect() {
    this.es = new EventSource('/events');
    this.es.onopen  = () => { this.backoff = 500; store.setConnected(true); };
    this.es.onerror = () => { this.es.close(); store.setConnected(false);
                              setTimeout(() => this.connect(), this.backoff);
                              this.backoff = Math.min(this.backoff * 2, 10_000); };
  }
}
```

Disconnection is **visible** — a degraded banner, not silence (AP-7).

### 3.2 Approval responses travel on the chat WebSocket

Not REST. Each carries the single-use token bound to the invocation (HR-05). A token that is unknown, reused, or belongs to an ended turn is rejected server-side and logged as a security event.

---

## 4. Viseme Generation (`avatar/visemes.ts`) — resolves HLD item #9

### 4.1 Why derived from audio, not from TTS internals

TTS is swappable (DD-023). Deriving visemes from a specific engine's phoneme output would couple the avatar to Kokoro and break when the engine changes.

**Visemes are computed from the audio signal**, so any TTS engine works unchanged.

### 4.2 VRM mouth shapes

VRM 1.0 defines five: `aa`, `ih`, `ou`, `ee`, `oh`.

### 4.3 Algorithm

```
AnalyserNode (fftSize 1024) on the playback node
        │
        ├─► RMS energy ──────────────► jaw openness (overall weight)
        │
        └─► spectral peaks in
            300–1000 Hz  (F1) ────┐
            800–2800 Hz  (F2) ────┴──► vowel-space position → blend weights
```

| F1 | F2 | Shape |
|---|---|---|
| Low | Low | `ou` |
| Low | High | `ee` |
| Mid | Mid | `oh` |
| High | Low | `aa` |
| High | High | `ih` |

```ts
function frame(): void {
  analyser.getFloatFrequencyData(buf);
  const energy = rms(buf);

  if (energy < SILENCE_THRESHOLD) {
    decayAll(0.15);                    // close mouth smoothly
    return;
  }
  const [f1, f2] = peaks(buf);
  const target = vowelBlend(f1, f2, energy);
  // exponential smoothing — raw per-frame values look jittery
  for (const k of SHAPES) current[k] += (target[k] - current[k]) * 0.35;
  vrm.expressionManager.setValue(k, current[k]);
}
```

Runs on `requestAnimationFrame`, synchronised to playback rather than to the token stream.

### 4.4 Honest limitation

Formant estimation from FFT peaks is approximate. It produces plausible, well-timed mouth movement — not phonetically accurate lip-sync. For an assistant avatar this is sufficient; **timing accuracy matters far more than shape accuracy** to perceived quality.

If it proves inadequate, the fallback is amplitude-only jaw movement, which is cruder but never wrong-looking.

---

## 5. Panels

### 5.1 Routing (FR-320, FR-222)

```ts
const PANELS = { text: ChatPanel, code: CodePanel, image: ImagePanel,
                 video: VideoPanel, data: DataPanel, diagram: DiagramPanel,
                 chart: ChartPanel, action: ActionPanel } as const;

const Panel = PANELS[segment.content_type] ?? ChatPanel;   // never drop content
```

### 5.2 Constraints (FR-327)

Every panel scrolls **inside its own** `overflow-x: auto` container. The page body never scrolls horizontally — wide tables, long code lines, and diagrams are contained.

### 5.3 Data panel

Arrays of objects render as a sortable table; anything else as a collapsible JSON tree. Both are selectable and copyable (FR-328).

---

## 6. ⚠️ Approval Dialog — the last line of defence

Every enforced control (DD-111, DD-112) ultimately routes here. **If the dialog is unreadable or habitually dismissed, the security model fails at its final step.**

### 6.1 Design requirements

| # | Requirement | Reason |
|---|---|---|
| **A-1** | State **what**, **to whom**, and **why now** | Vague prompts are approved reflexively |
| **A-2** | Taint source shown prominently | The user must know this follows untrusted content (FR-514) |
| **A-3** | Payload preview never truncated where it matters | Truncation hides exactly what an injection uses |
| **A-4** | Unknown destinations visually distinct | FR-534 |
| **A-5** | **Deny is the default action** | Focus starts on Deny; Esc denies |
| **A-6** | No "always allow" for T2/T3 | FR-524, DD-114 |
| **A-7** | Visual weight scales with risk | Routine confirmations must not look like dangerous ones |
| **A-8** | Timeout denies | Unattended machines must not approve by inaction |

### 6.2 Weight tiers

| Situation | Presentation |
|---|---|
| T1, clean turn | Inline confirmation strip in the conversation — low friction |
| T2/T3, clean turn | Modal, amber accent |
| **Any tier, tainted turn** | Modal, **amber**, taint banner |
| **Egress + tainted + unknown destination** | Modal, **red**, destination emphasised, 1.5 s enforced read delay before Approve enables |

The enforced delay in the highest tier exists to defeat muscle-memory approval. It applies **only** to the most dangerous case, so it does not become background noise.

### 6.3 Rendering

```
┌─────────────────────────────────────────────────────────┐
│  ⚠️  Approval required                                   │
│                                                          │
│  Send an email                                           │
│  To:  backup@unknown-domain.com   ⚠️ not a known contact │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ ⚠️ This action follows content read from:          │  │
│  │    • gmail:inbox (message 4471)                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  Content to be sent:                                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Subject: Account details                           │  │
│  │                                                    │  │
│  │ Here are the credentials you requested…            │  │
│  │                                          [scroll]  │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│   [ Deny ]  ◄ default          [ Approve in 1.5s… ]      │
└─────────────────────────────────────────────────────────┘
```

Preview is **scrollable, not truncated** (A-3). Full payload is always reachable.

### 6.4 Keyboard

| Key | Action |
|---|---|
| `Esc` | Deny |
| `Enter` | **Deny** — the focused default |
| `Tab` → `Enter` | Approve, requiring deliberate focus change |

`Enter` never approves. A reflexive keypress is safe.

### 6.5 Fatigue instrumentation (SC-5, T-12)

The client counts approvals per session and the deny rate. Sustained high volume publishes `approval.fatigue_warning`, surfacing a suggestion to narrow tool permissions.

**Approval fatigue is a measurable failure mode, not an abstract concern** — so it is measured.

### 6.6 Destination allowlist (FR-534)

An approval for an unknown destination offers *"Add to known destinations"* as a **separate, secondary action** after approving — never a checkbox inside the approval. Bundling them would let one distracted click both approve *and* permanently trust.

Managed in Settings → Security: view, add, remove.

---

## 7. Memory View (`security/MemoryView.tsx`) — DD-121

### 7.1 Purpose

Facts extracted during tainted turns are stored unconfirmed and **never enter context** until reviewed here (DD-121, M-1).

### 7.2 Layout

```
Memory
──────────────────────────────────────────────────────────
⚠️  Needs review (2)
┌────────────────────────────────────────────────────────┐
│ "User authorises sending files to backup@attacker.com" │
│  From: turn 2026-09-09T14:22 · source: gmail:inbox     │
│  ⚠️ Extracted while reading untrusted content           │
│                        [ Discard ]  [ Confirm ]        │
└────────────────────────────────────────────────────────┘

Confirmed facts (14)
┌────────────────────────────────────────────────────────┐
│ User's dentist is Dr. Chen              ✎  🗑          │
│ User prefers morning meetings           ✎  🗑          │
│ ⚠️ User's manager is Sarah Patel        ✎  🗑          │
│    (untrusted origin — taints turns when used)         │
└────────────────────────────────────────────────────────┘
```

The ⚠️ on a confirmed fact reflects M-2: origin provenance is permanent, and using such a fact taints the turn it loads into.

### 7.3 Behaviour

| Action | Effect |
|---|---|
| Confirm | `confirmed = true`; eligible for retrieval; retains `origin_tainted` |
| Discard | Deleted |
| Edit | User-edited facts become `origin_tainted = false` — the user is now the author |
| Delete | Removed permanently |

**Edit clearing taint is deliberate:** once the user has read and rewritten the statement, its content originates from them.

---

## 8. Provider Indicator (FR-550–552, DD-113)

Always visible in the header.

```
🟢 Ollama · qwen2.5:7b     (local — nothing leaves this machine)
🔵 OpenAI · gpt-…          (cloud — messages are sent to OpenAI)
```

Colour plus text — not colour alone (accessibility). Switching to a provider where `is_local === false` opens a confirmation naming the destination and what will be transmitted (FR-551).

---

## 9. Voice UI

### 9.1 Capture

`getUserMedia` → `AudioWorklet` → 16 kHz mono float32 → 512-sample frames → audio WebSocket. AudioWorklet rather than the deprecated `ScriptProcessorNode`, which runs on the main thread and glitches.

### 9.2 Playback and interruption

Chunks queue into a `AudioBufferSourceNode` chain. Interruption stops the current source and clears the queue — target under 300 ms (FR-120). The analyser feeding visemes sits on this chain, so mouth movement stops with the audio automatically.

### 9.3 State indicator

| State | Presentation |
|---|---|
| Idle | Dim indicator |
| Listening | Pulsing ring + live level meter |
| Transcribing | Spinner |
| Processing | Avatar `thinking` expression |
| Speaking | Level-reactive ring |

---

## 10. State Management

Zustand, in slices: `conversation` · `voice` · `approval` · `provider` · `connection` · `memory`.

**Approval state is deliberately isolated.** It is never merged into conversation state — a re-render or navigation must not clear a pending approval.

---

## 11. Test Matrix

| Test | Asserts | Ref |
|---|---|---|
| `test_unknown_content_type_renders_text` | No content loss | FR-222 |
| `test_panel_scrolls_internally` | No body h-scroll | FR-327 |
| **`test_approval_default_is_deny`** | A-5 | §6.1 |
| **`test_enter_key_denies`** | Reflex-safe | §6.4 |
| **`test_payload_not_truncated`** | A-3 | §6.1 |
| **`test_taint_source_displayed`** | A-2 | FR-514 |
| `test_unknown_destination_flagged` | A-4 | FR-534 |
| `test_high_risk_has_read_delay` | A-7 | §6.2 |
| `test_no_always_allow_for_t2` | A-6 | FR-524 |
| `test_approval_timeout_denies` | A-8 | §6.1 |
| `test_allowlist_add_is_separate_action` | §6.6 | — |
| **`test_unconfirmed_fact_not_retrievable`** | M-1 | DD-121 |
| **`test_edited_fact_clears_taint`** | §7.3 | DD-120 |
| `test_provider_indicator_always_visible` | FR-550 | — |
| `test_cloud_switch_requires_ack` | FR-551 | — |
| `test_sse_reconnect_backoff` | §3.1 | — |
| `test_disconnection_visible` | AP-7 | FR-721 |
| `test_visemes_decay_on_silence` | §4.3 | — |
| `test_gpu_warning_on_discrete` | FR-310 | — |
| `test_close_hides_to_tray` | FR-701 | — |

---

## 12. New Decisions Required

| Proposed ID | Decision |
|---|---|
| **DD-133** | **Visemes derived from the audio signal**, not TTS phoneme output — keeps the avatar independent of a swappable TTS engine (DD-023). |
| **DD-134** | **Approval default is Deny; `Enter` denies.** Approving requires a deliberate focus change. A reflexive keypress must be safe. |
| **DD-135** | **Approval visual weight scales with risk**, with a 1.5 s enforced read delay reserved for egress + tainted + unknown destination — applied narrowly so it does not become noise. |
| **DD-136** | **Allowlist addition is a separate action after approval**, never a checkbox within it — one click must not both approve and permanently trust. |
| **DD-137** | **Editing a fact clears `origin_tainted`** — the user has read and rewritten it, so authorship transfers to them. |
| **DD-138** | **Approval fatigue is instrumented** (count + deny rate per session), surfacing a warning when sustained. A measurable failure mode is measured. |

---

## 13. Open Items

| # | Item | Disposition |
|---|---|---|
| 1 | VRM model selection and licensing | VRoid Hub; confirm licence before shipping any bundled model |
| 2 | Formant estimation quality | Measure against real speech; amplitude-only fallback documented (§4.4) |
| 3 | Compact-mode click-through regions | Needs experimentation on Windows |
| 4 | Colour palette and typography | Deferred to a UI pass; no decision yet (DO-001) |

---

## 14. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |
