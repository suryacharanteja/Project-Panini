# Project Panini — License Audit

**Document Type:** Discovery — Compliance  
**Phase:** 00 — Discovery  
**Status:** Complete — ACTION REQUIRED  
**Date:** 2026-09-09  

---

## Purpose

Before harvesting code from external repositories (DD-005), the license of each source must be verified. Copying code from an incompatible license into Project Panini creates legal obligations that may conflict with the project's goals.

---

## 1. Audit Results

| Repository | License | Copyleft? | Safe to copy code? | Verdict |
|---|---|---|---|---|
| **Open-LLM-VTuber** | **MIT** | No | ✅ Yes | ✅ **CLEAR** |
| **Thoth** (arorakrishh) | **Apache 2.0** | No | ✅ Yes (with attribution) | ✅ **CLEAR** |
| **Naia OS** (tladus-git) | **Apache 2.0** | No | ✅ Yes (with attribution) | ✅ **CLEAR** |
| **Sapphire** (ddxfish) | **AGPL-3.0** | ⚠️ **Yes — strong network copyleft** | ❌ **No** | 🔴 **BLOCKED** |

---

## 2. 🔴 CRITICAL FINDING — Sapphire is AGPL-3.0

### What this means

AGPL-3.0 is the **strongest copyleft license in common use**. Its Section 13 ("Remote Network Interaction") states:

> If you modify the Program, your modified version must prominently offer all users interacting with it remotely through a computer network an opportunity to receive the Corresponding Source.

And the core copyleft clause:

> You must license **the entire work, as a whole**, under this License to anyone who comes into possession of a copy.

### Consequence for Project Panini

If **any** Sapphire code is copied into Panini:

1. **The entire Project Panini codebase becomes AGPL-3.0.** Not just the copied file — the whole work.
2. All source must be published if Panini is ever distributed.
3. **DX-003 (future cloud/SaaS extension) becomes legally constrained** — AGPL closes the SaaS loophole. Hosting Panini as a service would require publishing all source to every user.
4. Any future commercial or proprietary direction is foreclosed.

### Assessment

For a **purely private, never-distributed, personal tool**, AGPL imposes no practical obligation — copyleft triggers on *distribution* or *network interaction by others*, neither of which applies to single-user local use.

**However**, this contradicts two locked decisions:
- **DX-003** explicitly lists cloud/SaaS as a *future consideration*
- The project is being built with formal SDLC documentation, implying possible future distribution

Relying on "we'll never distribute it" is a fragile assumption to build architecture on.

---

## 3. Affected Decisions

The following decisions depend on Sapphire code and are now **INVALID as written**:

| ID | Decision | Sapphire Dependency | Status |
|---|---|---|---|
| **DD-025** | Wake word — OpenWakeWord | `core/wakeword/wake_detector.py` | ⚠️ Must re-source |
| **DD-026** | Audio device manager | `core/audio/device_manager.py` | ⚠️ Must re-source |
| **DD-070** | Plugin system (manifest + ed25519 + hot-reload) | `core/plugin_loader.py`, `core/plugin_verify.py` | ⚠️ Must re-source |
| **DD-071** | Plugin hooks | `core/hooks.py` | ⚠️ Must re-source |
| **DD-072** | Event bus (SSE, replay buffer) | `core/event_bus.py` | ⚠️ Must re-source |
| **DD-073** | Persona system | `user/personas/` | ⚠️ Must re-source |

---

## 4. Recommended Resolution

**Sapphire moves from "code source" to "design reference."**

Ideas, architecture, and patterns are **not** copyrightable — only their concrete expression is. We may study Sapphire's approach and implement our own version; we may not copy its code.

| Decision | Replacement approach |
|---|---|
| **DD-025** Wake word | Use **OpenWakeWord directly** — it is the upstream library (Apache 2.0). Sapphire is only a wrapper. **No loss.** |
| **DD-026** Audio devices | Use **`sounddevice`** or **PyAudio** directly (MIT/BSD). Trivial to implement. **No loss.** |
| **DD-070/071** Plugin system | Implement our own using Python `importlib` + manifest JSON. Signature verification via `cryptography` lib (Apache 2.0). **~1–2 days work.** |
| **DD-072** Event bus | Implement with `asyncio` pub/sub + FastAPI `StreamingResponse` for SSE. **Standard pattern, ~1 day.** |
| **DD-073** Personas | Trivial — JSON config + system-prompt templating. **~2 hours.** |

**Net impact:** Adds roughly **3–5 days** of implementation. Removes all legal risk. Preserves the freedom to distribute, open-source under any license, or extend to cloud later.

---

## 5. ⚠️ SECONDARY FINDING — Live2D Cubism SDK

**Relevant to:** DD-031 (Live2D as optional avatar driver)

### Terms

| Aspect | Finding |
|---|---|
| Development / trial | **Free** — no license needed |
| Release | **License required upon release** |
| Individuals & small businesses | **Exempt from fees** (excluding "Expandable Applications") |
| VTuber-type apps revenue threshold | **20,000,000 JPY** (~USD 130k) annual — above this, a paid Publication License is mandatory |
| Model assets | Live2D sample models carry a **separate license** (`LICENSE-Live2D.md`), *not* MIT |

### Assessment

For personal, non-commercial, single-user use: **acceptable — no fee, no blocker.**

But it is a **proprietary SDK with revenue-triggered obligations**, unlike VRM which is fully open.

### Impact on DD-030 / DD-031

This **strengthens** the existing decision to make **VRM 3D primary**:

| | VRM (DD-030) | Live2D (DD-031) |
|---|---|---|
| SDK license | Open (`@pixiv/three-vrm`, MIT) | Proprietary, revenue-triggered |
| Model assets | Free (VRoid Hub, CC licenses) | Sample models separately licensed |
| Commercial path | Unrestricted | Requires Live2D agreement |

**Recommendation:** Keep VRM as primary. Retain Live2D as optional, but document the licensing caveat so it is never accidentally shipped as default.

---

## 6. Attribution Obligations (Apache 2.0)

Thoth and Naia OS are Apache 2.0. Copying their code requires:

1. Include a copy of the Apache 2.0 license
2. Retain all copyright, patent, trademark, and attribution notices
3. **Mark modified files** as changed
4. Include a `NOTICE` file if the original has one

**Action:** Create `NOTICES.md` at project root before any code is copied.

---

## 7. Summary of Required Actions

| # | Action | Priority | Affects |
|---|---|---|---|
| 1 | Reclassify Sapphire as **design reference only — no code copying** | 🔴 Critical | DD-025, 026, 070–073 |
| 2 | Re-source wake word from **OpenWakeWord upstream** (Apache 2.0) | 🔴 Critical | DD-025 |
| 3 | Re-source audio devices from **sounddevice/PyAudio** | 🔴 Critical | DD-026 |
| 4 | Plan **own implementation** of plugin system, event bus, personas | 🔴 Critical | DD-070–073 |
| 5 | Document Live2D licensing caveat; confirm VRM primary | 🟡 Medium | DD-030, DD-031 |
| 6 | Create `NOTICES.md` for Apache 2.0 attribution | 🟡 Medium | Thoth, Naia OS |
| 7 | Verify Live2D sample-model license before using any model asset | 🟡 Medium | DD-031 |

---

## 8. Post-Audit License Position

If all actions are taken, Project Panini's dependency licenses will be:

| Source | License | Compatible with any target license? |
|---|---|---|
| Open-LLM-VTuber | MIT | ✅ Yes |
| Thoth | Apache 2.0 | ✅ Yes |
| Naia OS | Apache 2.0 | ✅ Yes |
| OpenWakeWord | Apache 2.0 | ✅ Yes |
| sounddevice / PyAudio | MIT / BSD | ✅ Yes |
| `@pixiv/three-vrm` | MIT | ✅ Yes |
| Sapphire | *(not used)* | — |

**Result: Project Panini remains free to be licensed however you choose — MIT, proprietary, or commercial.**
