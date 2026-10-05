# PANINI enhancements: live PANINI version 2 implementation register

**Created:** 2026-10-02 · **Status:** implementation in progress, release qualification incomplete · **Owner:** Charan
**Scope:** PANINI v2, "one brain, many bodies, many hands". Harvests CopilotKit OpenDots, OpenBot and
OpenMuse, and Playwright 1.63, and goes beyond them where PANINI's position (a real PC, a real browser, a
real phone line) allows.

| Document | Purpose |
| --- | --- |
| This register | Phase plan, work items, gates, decisions, risks, evidence log. **Update it as items land.** |
| [HLD-panini-v2.md](HLD-panini-v2.md) | Target architecture, shared contracts (§5), flows, trust boundaries, data model, migration |
| [harvest-map.md](harvest-map.md) | Every harvested file and feature: what we take, how, licence, and which item uses it |
| [lld/](lld/) | One low-level design per work item. Implement items from these, one at a time. |
| [lld/_TEMPLATE.md](lld/_TEMPLATE.md) | Template for any new item added later |

Status legend: **Not started** · **In progress** · **In review** · **Done** · **Deferred** · **Dropped**.

---

## 1. Governing vision

```text
SURFACES   avatar · console · phone app · phone line · Slack/Telegram/WhatsApp · email · A2A · MCP server
    │
FABRIC     identity · device keys · Tailscale / blind relay · webhook ingress · push · approvals that follow you
    │
BRAIN      one run engine + agent loop · capability registry (manifests) · policy & grants · interrupts ·
           personas · model router & budgets · Jev gates & verifier · guardian · memory/skills/learning
    │
HANDS      Windows desktop (UIA) · PANINI-CLI · your Chrome (Claw + CDP + Playwright) · managed Playwright
           profile · sandbox computers (OpenBot) · Android (Playwright _android) · phone line (GPT-Live SIP)
    │
EVIDENCE   snapshot-bound actions · hash-chained receipts · artifacts (traces, video, transcripts) ·
           verification before "done" · live view · eval scorecard
```

**Principles** (full list in HLD §2): local-first; one brain; capabilities are data; every effect is
classified, decided, receipted and verified; approvals follow the person; voice is never authorization;
least privilege revocable at any time; untrusted content stays data; harvest, don't depend; strangler
migration behind flags.

---

## 2. How to implement an item (definition of done)

1. **Pick the next item** whose dependencies are Done (§4). Set its status to In progress here.
2. **Read** its LLD, the HLD §5 contracts, and the harvest sources the LLD names.
3. **Branch** `v2/<id>-<slug>` (for example `v2/V2-02-registry`). Implement the LLD's §6 steps in order;
   each step is sized for one reviewable commit.
4. **Tests:** write the LLD's §7 tests. `npm test` must stay green. Live checks follow the LLD and the
   project memory rule: verify Desktop and browser work live, not only with unit tests.
5. **Evidence:** run the LLD's acceptance checks (§8) and the scorecard (V2-01) where the LLD says so.
   Paste the commands and results into §9 (evidence log).
6. **Flag:** the feature stays behind its flag until its **phase gate** (§3) passes.
7. **Update** this register: status, date, evidence link, and any changed decisions or risks.
8. If the design had to change, amend the LLD in the same PR, and the HLD if a shared contract changed.

---

## 3. Phase plan

Dates are indicative for one developer pairing with an AI coding agent, starting Monday 2026-10-05.
Phase 5 tracks are independent and can run in parallel or be reordered.

| Phase | Goal | Items | Indicative window | Exit gate |
| --- | --- | --- | --- | --- |
| **0 · Groundwork** | Make change safe and measurable | V2-00, V2-01 | 2026-10-05 → 10-09 | Flags and migrations in `main`; v1 baseline scorecard recorded (success, p50/p95 latency, cost, interventions) |
| **1 · One brain** | Registry, policy, receipts, interrupts, one agent loop, AG-UI | V2-02 → V2-07 | 10-12 → 10-30 | Scorecard ≥ v1 on success; simple-task p50 ≤ v1 + 10 %; I1–I3, I7, I8 tests green; interrupts resolvable from shell, console and LAN phone; v1 planner off by default |
| **2 · Hands v2** | Playwright everywhere, your Chrome with CDP, sandbox computers, live view | V2-08 → V2-12 | 11-02 → 11-27 | Browser golden tasks ≥ v1 (managed and your Chrome); trusted-input test page passes; I9 test green; sandbox isolation checks pass; takeover/handback works for Chrome, managed and sandbox; Browser Use off by default |
| **3 · Reach** | Guardian, remote fabric, phone app, phone line, channels | V2-13 → V2-17 | 11-30 → 2027-01-01 | Red-team suite ≥ 95 % (gate for enabling any remote surface); remote approvals with device signatures and passkeys; inbound call live test; Slack and Telegram live tests; I4, I6, I10 green |
| **4 · Team & notebook** | Personas with delegation, Spaces/pages | V2-18, V2-19 | 01-04 → 01-22 | Two personas with different grants proven isolated; group-thread delegation demo; page review-before-save, revision conflicts and restore-before-decide tests |
| **5 · Moonshots** | Outbound calls, Android, teach-by-demo, MCP server, continuity, triggers | V2-20 → V2-25 | 01-25 → 03-19 | Per item (each LLD §8); outbound calls additionally need the owner's sign-off on the compliance checklist |
| **6 · Frontier** | Beyond OpenDots/OpenMuse | V2-26, V2-27 | from 03-22, continuous | Per item |

---

## 4. Master register

Priorities: **P0** blocks its phase · **P1** core to its phase · **P2** valuable, can slip · **P3** optional.
Sizes: **S** ≤ 2 days · **M** 3–5 days · **L** 1–2 weeks.

| ID | Work item | Ph | Pri | Size | Depends on | Flag | LLD | Exit criteria (summary) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V2-00 | Program setup: OpenDots reference move, flags, migrations, notices | 0 | P0 | S | — | — | [LLD](lld/V2-00-program-setup.md) | Snapshot moved to `references/opendots/` and ignored; `v2flags` + migration runner tested; notices updated | Done |
| V2-01 | Eval baseline and scorecard | 0 | P0 | M | V2-00 | — | [LLD](lld/V2-01-eval-scorecard.md) | 40 golden tasks (offline + live); v1 baseline stored; `scripts/panini-eval.py --compare` works | Done |
| V2-02 | Capability and tool registry (manifests) | 1 | P0 | M | V2-00 | `PANINI_V2_REGISTRY` | [LLD](lld/V2-02-capability-registry.md) | All five v1 capabilities and the agent's direct tools served from manifests; MCP connectors synthesized; prompts generated from the registry; parity tests | Done |
| V2-03 | Policy engine: effect classes, grants, epochs, taint | 1 | P0 | M | V2-02 | `PANINI_V2_POLICY` | [LLD](lld/V2-03-policy-engine.md) | Shadow mode agrees with `approvals.py` on the existing corpus; grant epochs stop runs ≤ 100 ms; taint escalation tests | Done |
| V2-04 | Receipts and artifacts ledger | 1 | P0 | M | V2-02 | `PANINI_V2_LEDGER` | [LLD](lld/V2-04-receipts-ledger.md) | Every tool call receipted (I1); chain verifies; idempotent replay skips dispatched operations after a kill | Done |
| V2-05 | Interrupts: durable approvals, questions, reviews | 1 | P0 | M | V2-03, V2-04 | `PANINI_V2_INTERRUPTS` | [LLD](lld/V2-05-interrupts.md) | Shell confirmations run on interrupts; background runs park and resume; first decision wins; restore-before-decide | Done |
| V2-06 | One brain: run engine, model router, budgets | 1 | P0 | L | V2-01 – V2-05 | `PANINI_V2_ONE_BRAIN` | [LLD](lld/V2-06-one-brain.md) | Coordinator turns, voice, background, A2A and phone requests all run on the engine; scorecard gate met; planner off | Done |
| V2-07 | AG-UI adapter and inline cards | 1 | P1 | M | V2-05, V2-06 | `PANINI_V2_AGUI` | [LLD](lld/V2-07-agui-adapter.md) | `@ag-ui/client` conformance test; interrupt → resume round trip; tool and review cards in shell and console | Done |
| V2-08 | Playwright sidecar and managed browser | 2 | P0 | M | V2-02 – V2-04 | `PANINI_BROWSER_DRIVER=playwright` | [LLD](lld/V2-08-playwright-sidecar.md) | Sidecar starts under the job object; managed persistent profile on Edge/Chrome; demo-page golden tasks pass | Done |
| V2-09 | Claw CDP bridge: your Chrome through Playwright | 2 | P0 | L | V2-08 | `PANINI_CLAW_CDP` | [LLD](lld/V2-09-claw-cdp-bridge.md) | Attached tab driven by Playwright with trusted input; method policy (I9) in relay and extension; detach stops the run | Done |
| V2-10 | Browser vocabulary v2, verification, traces | 2 | P0 | M | V2-08, V2-09 | (driver) | [LLD](lld/V2-10-browser-vocabulary.md) | 80 Playwright tools mapped to effect classes; stale-ref guard; password refusal; verify-tools as evidence; trace/video artifacts | Done |
| V2-11 | Sandbox computers (OpenBot on Docker) | 2 | P1 | L | V2-02 – V2-04 | `PANINI_SANDBOX` | [LLD](lld/V2-11-sandbox-computers.md) | Per-persona computers; isolation checks (second computer cannot see first's files); persistence across stop/start; revocation cancels | Done |
| V2-12 | Live view and human takeover | 2 | P1 | M | V2-07, V2-08, V2-11 | `PANINI_LIVE_VIEW` | [LLD](lld/V2-12-live-view-takeover.md) | Frames from Chrome, managed, sandbox and desktop in one panel; take/release with resume-snapshot rule | Done |
| V2-13 | Injection firewall and guardian | 3 | P0 | M | V2-03, V2-01 | `PANINI_GUARDIAN` | [LLD](lld/V2-13-guardian.md) | Red-team suite ≥ 95 %; taint provenance end to end; guardian pre-dispatch review on tainted effects | Red-team disabled/deferred by owner; guardian remains partial |
| V2-14 | Remote fabric: device keys, Tailscale, blind relay, ingress, push | 3 | P0 | L | V2-05 | `PANINI_REMOTE_MODE` | [LLD](lld/V2-14-remote-fabric.md) | QR pairing with device keys; E2E envelopes through the relay (I6); webhook ingress verified on the PC; Web Push delivered | Built; red-team enforcement disabled; production transport qualification pending |
| V2-15 | Phone app v2: approvals, passkeys, push, share, live | 3 | P0 | L | V2-14, V2-12 | (remote) | [LLD](lld/V2-15-phone-app.md) | Passkeys verified through real fabric with virtual authenticator; phone notebook and exports; hardware, input and conversation qualification pending | In progress |
| V2-16 | Phone line: inbound calls over SIP | 3 | P1 | L | V2-14, V2-15, V2-06 | `PANINI_PHONE_LINE` | [LLD](lld/V2-16-phone-line-inbound.md) | Allow-listed caller reaches PANINI; PIN gate; effects approved on the phone; call receipt in the thread | In progress: local SIP lifecycle and phone controls built; real SIP qualification pending |
| V2-17 | Messaging channels: Slack, Telegram, WhatsApp, email | 3 | P1 | L | V2-06, V2-13, V2-14 | `PANINI_CHANNELS` | [LLD](lld/V2-17-channels.md) | Slack and Telegram live; WhatsApp through ingress; allow-lists; safe failures; thread bindings | In progress: Telegram text and shared manager built; remaining providers/live qualification pending |
| V2-18 | Personas and delegation (group threads) | 4 | P1 | M | V2-03, V2-06 | `PANINI_PERSONAS` | [LLD](lld/V2-18-personas.md) | Persona CRUD in Admin; per-persona grants and memory scope; `delegate_to_persona`; group-thread demo | In progress |
| V2-19 | Spaces and pages notebook | 4 | P1 | M | V2-05, V2-07 | `PANINI_SPACES` | [LLD](lld/V2-19-spaces-pages.md) | Editor/reviews, saved sources, PDF export, library controls and one live citation verified; cross-LLD/surface and release qualification pending | In progress |
| V2-20 | Outbound calls on your behalf | 5 | P2 | L | V2-16 | `PANINI_OUTBOUND_CALLS` | [LLD](lld/V2-20-outbound-calls.md) | Approved brief → call with disclosure → DTMF/IVR → outcome receipt; compliance checklist signed | In progress: reviewed SIP lifecycle, scheduled windows, PC/PWA controls, usage receipts, quote-cited interpretations and scoped availability reads built; real calls, keypad and live qualification pending |
| V2-21 | Android hands | 5 | P2 | L | V2-08, V2-03 | `PANINI_ANDROID` | [LLD](lld/V2-21-android-hands.md) | Wireless-debugging pairing; UI snapshot with refs; tap/fill/swipe with policy; Chrome on Android via Playwright | In progress: registry hand, Admin/PWA stop controls and shared grammar built; physical Android and remaining acceptance pending |
| V2-22 | Teach by demonstration and self-healing skills | 5 | P2 | L | V2-10, V2-04 | `PANINI_TEACH` | [LLD](lld/V2-22-teach-by-demonstration.md) | Browser and desktop recordings become reviewed skills; replay passes; healer repairs a changed locator | In progress: browser, single named-iframe, WPF and Notepad recording/replay built; app launch recording, Save As, crops, gestures, broader frames and live healer qualification pending |
| V2-23 | PANINI as an MCP server | 5 | P2 | M | V2-02, V2-03, V2-05 | `PANINI_MCP_SERVER` | [LLD](lld/V2-23-mcp-server.md) | `panini-mcp` usable from Claude Code; observe tools free; every effect interrupts the owner | In progress |
| V2-24 | Continuity and presence | 5 | P2 | M | V2-14 – V2-17 | `PANINI_PRESENCE` | [LLD](lld/V2-24-continuity-presence.md) | Start on a call, continue on PC; approvals routed by presence; watch-actionable notifications | In progress |
| V2-25 | Ambient triggers and responsibilities | 5 | P2 | L | V2-06, V2-14, V2-18 | `PANINI_TRIGGERS` | [LLD](lld/V2-25-triggers-responsibilities.md) | File, email, calendar, webhook and OS triggers; rules with cooldown; responsibility lifecycle and digests | In progress: development path tested, live qualification pending |
| V2-26 | Universal hands protocol (one snapshot/ref vocabulary) | 6 | P2 | L | V2-10, V2-21 | `PANINI_DESKTOP_TOOLS=fine` | [LLD](lld/V2-26-universal-hands-protocol.md) | Desktop UIA snapshot with refs; same `*_snapshot/_click/_type` across web, Windows, Android, sandbox | In progress: shared grammar, Windows fine hand and browser/Android/sandbox normalization built; macros and golden-task comparison pending |
| V2-27 | Local and offline mode | 6 | P3 | L | V2-06 | `PANINI_LOCAL_MODE` | [LLD](lld/V2-27-local-offline-mode.md) | On-device STT/TTS; local model fallback for routing and simple tasks; privacy mode blocks cloud calls | In progress: local fallback, coordinator/desktop admission, catalog-grounded short draft route, offline dictation and paired-scorecard quality guard built; installed model passed 0/8 planner and 1/1 reviewed safety cases under tightened scoring, with no cloud baseline or qualified tier; live task, blocking-proxy, TTS and physical microphone pending |

### 4.1 Your 16 ideas → items

| Idea | Items |
| --- | --- |
| 1 Capability registry | V2-02 (+ V2-03 policy, V2-04 receipts) |
| 2 One agent loop; Jev as verifier | V2-06 |
| 3 Claw with CDP, Playwright drivers, wider vocabulary | V2-08, V2-09, V2-10 |
| 4 AG-UI adapter, computer and review cards | V2-07 (+ V2-12 live view) |
| 5 Remote relay, phone app with push approvals | V2-14, V2-15 |
| 6 Inbound phone calls over SIP, voice receipts | V2-16 |
| 7 Sandbox computer tier | V2-11 |
| 8 Personas | V2-18 |
| 9 Spaces as PANINI's notebook | V2-19 |
| 10 Slack, Telegram, WhatsApp on their own SDKs | V2-17 |
| 11 Outbound calls on your behalf | V2-20 |
| 12 Android hands | V2-21 |
| 13 Teach by demonstration | V2-22 |
| 14 PANINI as an MCP server | V2-23 |
| 15 Continuity across devices | V2-24 |
| 16 Ambient triggers | V2-25 |
| Added foundations | V2-00, V2-01, V2-04, V2-05, V2-13 |
| Added frontier | V2-26, V2-27 |

---

## 5. One step ahead of OpenDots and OpenMuse

Both templates are good references. They are also explicit about what they leave out: OpenDots' README lists
multi-Dot conversations, automatic delegation, event triggers, a responsibility lifecycle, file attachments and
shared editing as further work, and both require CopilotKit's hosted Intelligence service.

| Capability | OpenDots | OpenMuse | PANINI v2 | Item |
| --- | --- | --- | --- | --- |
| Where the brain lives | Hosted Intelligence threads (required) | Hosted Intelligence (required) | On the PC: local threads, memory, learning, receipts | V2-06 |
| Hands | Container computer per Dot | Docker computer, Playwright worker | Real Windows desktop, your real Chrome with trusted input, managed profile, sandbox computers, Android, phone line | V2-09, V2-11, V2-16, V2-21 |
| Browser engine | Read-only fetch (JS off, 50 requests) + container browser | Playwright worker | Playwright's full 80-tool MCP engine on your Chrome, a managed profile, and sandboxes | V2-08 – V2-10 |
| Proof of work | Prompt rule "never claim without a tool result" | Run records | Hash-chained receipts; every effect needs a verification (assertion, postcondition, hash) | V2-04, V2-10 |
| Approvals | In-chat HITL card (one surface) | Reviewed actions (hash, expiry, atomic claim) | Durable interrupts on any device, digest-bound, device-signed, passkey for high risk, watch | V2-05, V2-15, V2-24 |
| Remote access | Self-host the whole app | Self-host | Blind relay with E2E envelopes, or Tailscale; the PC stays the brain | V2-14 |
| Calls | In-app WebRTC | — | Real phone numbers in (SIP) and out (with disclosure and approved briefs) | V2-16, V2-20 |
| Specialists | Separate Dots; delegation listed as future work | Identity only | Personas with delegation and group threads | V2-18 |
| Automation | Recurring instructions; triggers listed as future work | Goals and page watches | Event triggers (files, mail, calendar, webhooks, OS) and a responsibility lifecycle | V2-25 |
| Learning | Hosted "learning containers" | — | Local lessons (v1) plus teach-by-demonstration with self-healing replay | V2-22 |
| Injection defence | Untrusted-data prompt text | Untrusted-data prompt text | Taint provenance, guardian pre-dispatch review, red-team gate | V2-13 |
| Interop | AG-UI client | AG-UI | AG-UI, A2A, MCP client **and** MCP server | V2-07, V2-23 |
| Observation model | Per-tool | Per-tool | One snapshot/ref vocabulary across web, Windows, Android, sandbox | V2-26 |
| Offline | — | — | Local STT/TTS and model fallback | V2-27 |

---

## 6. Decisions needed from you

| # | Decision | Options | Recommendation | Needed before |
| --- | --- | --- | --- | --- |
| D1 | Remote transport | Tailscale only · PANINI Relay only · both | **Both**: Tailscale as the private default; the relay for inbound calls, WhatsApp and webhooks | V2-14 |
| D2 | SIP trunk and number | Twilio · Telnyx · Bandwidth · Vobiz · other | Pick the provider with numbers in your country and TLS SIP; GPT-Live needs `transport=tls` | V2-16 |
| D3 | Claw `debugger` permission | Accept (required, not optional, in Chrome) · separate "Claw Pro" extension | **Accept** in Claw v4; Chrome shows a "started debugging" bar while attached, which doubles as a visible indicator | V2-09 |
| D4 | Docker Desktop for sandbox computers | Required for V2-11 · skip the sandbox tier | **Required** (already installed and verified on this PC) | V2-11 |
| D5 | WhatsApp | Meta Cloud API with a business account · skip | Cloud API only; unofficial WhatsApp automation breaks its terms | V2-17 |
| D6 | Android hands mode | ADB wireless debugging now · wait for a native companion | **ADB first** (Playwright `_android`); native accessibility companion later | V2-21 |
| D7 | Outbound call scope | Bookings and enquiries only · broader | Start with bookings, enquiries and reminders to numbers you approve per call | V2-20 |
| D8 | Retire Browser Use | After V2-10 gate · keep both | Retire after one phase as fallback | V2-10 |
| D9 | Where Spaces UI lives | Admin (React) · console (plain pages) | **Admin** (React 19, TanStack Router; port the OpenDots Tiptap editor) | V2-19 |
| D10 | Relay hosting | Cloudflare Workers · self-hosted Node relay | Cloudflare Workers + Durable Objects (free tier); the Node relay doubles as the test fixture | V2-14 |

---

## 7. Risks

| # | Risk | Impact | Mitigation | Owner item |
| --- | --- | --- | --- | --- |
| R1 | One brain is slower or costlier than the planner for simple requests | Voice feels sluggish | Model router (fast tier for simple turns), prompt caching, fast-path tools, scorecard gate | V2-06 |
| R2 | CDP in your real Chrome can read every cookie | Session theft | Method policy in relay **and** extension (I9); tab-scoped sessions; no browser-wide commands | V2-09 |
| R3 | Remote surfaces widen the attack surface | Forged approvals, injection via channels | Device keys, E2E envelopes, signed decisions, passkeys, guardian and red-team gate before enabling | V2-13, V2-14 |
| R4 | Telephony fraud and cost | Bills, abuse | Caller allow-list + PIN, reject unknown, per-call and per-day budgets, rate limits | V2-16, V2-20 |
| R5 | Legal rules for automated calls and recording | Compliance exposure | AI disclosure, consent announcements, calling hours, per-call approval; no audio recording by default | V2-20 |
| R6 | Playwright internals change (`lib/entry/mcp.js`, `aria-ref`) | Browser hand breaks on upgrade | Pin 1.63.0; contract tests on tool names and schemas; upgrade only through V2-08's checklist | V2-08 |
| R7 | OpenBot pinned revision ages | Security fixes missed | Pinned build with fail-closed patch (harvested); quarterly review | V2-11 |
| R8 | Scope (28 items) outruns capacity | Half-built features | Phase gates; Phase 5–6 optional; each item ships independently behind a flag | Register |
| R9 | Installer size and process count grow | Slower installs, more failure points | System Edge/Chrome channels (no bundled browsers); one sidecar process; integrity manifest | V2-08 |
| R10 | Provider dependence (OpenAI for agent, voice, phone) | Outage stops PANINI | Model router with provider abstraction; local mode | V2-06, V2-27 |
| R11 | Debugger infobar and DevTools conflicts in Chrome | Attach fails or confuses the person | Clear UX text; detect "another debugger attached"; fall back to v1 content-script actions | V2-09 |
| R12 | Duplicate effects after crashes | Double messages or orders | Ledger operation keys; unknown outcomes go to review, never replay | V2-04 |

---

## 8. Licences and attribution

| Source | Licence | How we use it | Attribution |
| --- | --- | --- | --- |
| OpenDots (CopilotKit, snapshot of 2026-10-01) | MIT | Patterns and ported code (review card logic, computer service, voice receipts, Slack allow-list, URL guard) | Header comment in ported files + `THIRD_PARTY_NOTICES.md` |
| OpenBot (CopilotKit, `b6932d3`) | MIT | Supervisor and computer image built from the pinned revision | `deployment/computers/LICENSE.openbot` |
| OpenMuse (CopilotKit) | MIT | Patterns already reviewed (`PANINI-AGENTIC-DESIGN.md` §5) | Design doc reference |
| Playwright 1.63.0 | Apache-2.0 | Bundled MCP server used as-is; CDP relay protocol ported to Python | NOTICE text in `runtime/licenses/upstream`; header in `backend/hands/cdp_relay.py` |
| AG-UI (`ag-ui-protocol`, `@ag-ui/client`) | MIT | Event models and encoder | Dependency licences |
| Tiptap 3 | MIT | Page editor | Dependency licences |
| Slack Bolt for Python | MIT | Socket Mode channel | Dependency licences |
| pywebpush | MPL-2.0 | Web Push sending | Dependency licences |

---

## 9. Evidence log

Add one row per verification. Link logs or screenshots under `artifacts/` (not committed) or paste the
command output summary.

| Date | Item | Check | Command / method | Result |
| --- | --- | --- | --- | --- |
| 2026-10-02 | V2-00 | Flags, Admin v2 group, migrations, reference move, pin check | npm test | Green: backend 431 passed (11 new), CLI 124, frontend/search/admin/shell suites, typechecks |
| 2026-10-02 | V2-01 | Offline scorecard in npm test; v1 live baseline | scripts/panini-eval.py run --mode offline; run --mode live --engine v1 | Offline 16/16 (no network, ~2 s). v1 live baseline 20/20, p50 3.72 s, p95 13.27 s, USD 0.14 total (artifacts/evals/baseline-v1-live-1.json; one sample, more to follow) |
| 2026-10-02 | V2-02 | Registry parity, no-bypass, API, Admin page | npm test; PANINI_V2_REGISTRY=true pytest backend/tests; offline scorecard compare; Admin /build/capabilities in browser | Backend 444 passed (13 new); 431/431 also pass with the registry on; offline scorecard registry-on vs off: GATE PASS; Capabilities page renders manifests, effects and readiness |
| 2026-10-02 | V2-03 | Policy parity, invariants I5/I7/I8, enforce scorecards | npm test; PANINI_V2_REGISTRY=true PANINI_V2_POLICY=enforce scorecards (offline + live) | Backend 477 passed (33 policy). Enforce offline 16/16. Enforce live 18/20 first run: multi-step-rate showed web-taint over-gating (fixed: web content moves to the guardian, V2-13; rechecked PASS); newest-download is model variance (also flips on v1). Pause cancels in-flight calls in <100 ms. |
| 2026-10-02 | V2-04 | Chain, I1 receipts incl. refusals, crash recovery, API, verify speed | npm test; offline scorecard with registry+enforce+ledger | Backend 488 passed (11 ledger): tamper detection, 4-thread concurrent appends, replay/unknown/proceed table, recovery, redaction sweep, retention; 10,000 entries verify < 2 s; offline 16/16 with all Phase-1 flags on so far |
| 2026-10-02 | V2-05 | Resolution rules, I3/I4/I8, race, shell flow, background park→phone approve→resume→replay | npm test; pytest backend/tests/interrupts; full suite with PANINI_V2_INTERRUPTS=true | Backend 499 passed (11 interrupt tests); 488/488 existing tests pass with interrupts on (shell dialog flow unchanged); the end-to-end test proved a resumed task replays its completed step instead of repeating it |
| 2026-10-02 | V2-06 | Backend tests flags off/on; offline scorecard v1+v2; live phase1 gate | pytest backend/tests; panini-eval.py run --engine v2 (offline, live); compare --gate phase1 | 527 passed; offline 16/16 both engines; live v2 20/20, simple p50 3.62s vs 3.47s, cost/verified $0.0066 vs $0.0070: GATE PASS (artifacts/evals/20261002T094920Z-fbe237-v2-live.json). Fast tier opt-in: gpt-4.1-mini cut cost ~70% but raised simple p50 to 4.48s (gate fail), so PANINI_FAST_MODEL defaults to the agent model. |
| 2026-10-02 | V2-07 | Backend AG-UI tests, admin conformance + card tests, npm test, live console round trip | pytest backend/tests/agui; vitest (admin); npm test; dev server + browser | 11 backend + 17 admin tests; every recorded event validates against @ag-ui/core EventSchemas; npm test green (531 backend); live: tool cards + answer, approval card → approve → resumed at pending call and deleted scratch file; decline left it untouched. Fixed a V2-04 redaction gap (token in command previews). |
| 2026-10-02 | V2-08 | Session/hand/net-guard tests, real headless Edge via Playwright MCP, catalogue contract, live browser cases, npm test | pytest backend/tests/hands; panini-eval.py --engine v2 with PANINI_BROWSER_DRIVER=playwright; npm test | 35 tests incl. real Edge demo (1.7 s) and cookie persistence across restarts; 70-tool catalogue pinned and checked; live: separate-browser heading 6.2 s pass (v1 partial 22.6 s), open youtube 10.9 s pass (v1 34.1 s); driver adds 10.95 MB; npm test green (566 backend). Packaged smoke deferred to the release gate. |
| 2026-10-02 | V2-09 | Policy parity + I9 tests, relay/bridge/hand tests, real Playwright->relay->Edge, extension tests, live Claw v4 E2E | pytest backend/tests/hands/test_cdp.py; node --test frontend/test/extension-cdp.test.mjs; scripts/cdp-spike.py; scripts/verify-claw-cdp.py; npm test | 26 + 3 tests; spike 12/12 tools through the relay (29 methods pinned, all allowed); live E2E with Claw v4 in isolated Edge 4/4: trusted click+keys, extension refuses Network.getAllCookies, detach stops PANINI; npm test green (592 backend). Needs the person to load Claw v4 in their Chrome (re-approve debugger permission). |
| 2026-10-03 | V2-10 | Tool-table/manifest, safety and evidence tests, real managed run with trace, live browser cases, npm test | pytest backend/tests/hands; panini-eval.py --engine v2 (PANINI_BROWSER_DRIVER=playwright); scripts/verify-claw-cdp.py; npm test | All 70 Playwright 1.63 tools mapped (35 offered); 15 new tests incl. real trace zip; live open-youtube 9.2 s and separate-browser 6.1 s verified; removed model-chosen filename args; fixed relay event ordering (intermittent 60 s stall gone, 3/3 full runs); npm test green (607 backend). |
| 2026-10-03 | V2-11 | Ported OpenDots service tests, tools, routing, owner routes, supervisor patch tests, Admin page in browser, npm test | pytest backend/tests/hands/test_sandbox.py; node --test frontend/test/sandbox-harden.test.mjs; npm test | 15 + 2 tests (revocation cancels in <1 s with mocked HTTP); Admin Computers page renders; npm test green (622 backend). Live checks (scripts/panini-sandbox-live.py) need Docker Desktop running and the OpenBot image build (downloads) approved. |
| 2026-10-03 | V2-11 | Runtime ownership (ADR-0001) + live sandbox checks on Docker Desktop | pytest backend/tests/hands/test_sandbox*.py; scripts/panini-sandbox.py build; scripts/panini-sandbox-live.py; npm test | PANINI starts the engine and supervisor itself (ask once); 9 runtime + 15 service tests; pinned images built and patch verified; live 4/4: isolation, persistence, revocation stopped a 30 s command in 0.05 s, browsing in the sandbox; containers loopback-only, cap_drop ALL, no-new-privileges; npm test green (631 backend). |
| 2026-10-03 | V2-11 | Podman live + native AppContainer sandbox | npm test; scripts/panini-sandbox-live.py on Podman; test_native_sandbox.py | green; Podman 4/4; native 5/5 + live service run |
| 2026-10-03 | V2-12 | Live view and takeover for every hand | npm test; backend/tests/live (incl. real-browser screencast); Admin Live page live | green; frames <=4 fps only while watched; take/input/give back live; pointer seen in frames |
| 2026-10-03 | V2-13 | Quarantine, poisoning rules, gate, Safety page; guardian and red-team cases not built | npm test; Admin Safety page live | green; gate holds gated surfaces off |
| 2026-10-03 | V2-14 | Fabric built and tested; off until the V2-13 red-team gate passes | npm test; backend/tests/fabric (incl. Node relay e2e + I6); Admin Remote access live | green; 28 fabric tests; relay saw ciphertext only |
| — | — | — | — | — |

---

## 10. Change log

| Date | Change |
| --- | --- |
| 2026-10-02 | Register, HLD, harvest map and 28 LLDs created from the capability analysis and the harvest of OpenDots, OpenBot, OpenMuse and Playwright 1.63 |

### 2026-10-04 — Full-program delivery authorization

Owner authorized delivery of all v2 LLDs. Work continues on `codex/v2-delivery`. Existing Done labels describe prior implementation evidence, not a new release certification. V2-13 red-team work remains in progress and aside by owner instruction; its remote enablement gate remains binding. V2-15 has an Android PWA implementation (commits `1f6dd35`, `90ed7bb`) and browser smoke evidence, but passkeys, physical Android and production HTTPS qualification remain incomplete. V2-18 persona implementation is in progress; no full-program completion or Dot parity is claimed.

## Voice and inbound phone checkpoint — 2026-10-04

The latest owner instruction supersedes the 2026-10-04 checkpoint's earlier red-team wording: qualification and enforcement are disabled and deferred to future development. `PANINI_REDTEAM_ENFORCE` is off by default; pairing and action permissions continue to apply. Original red-team acceptance criteria remain future work.

V2-18 now includes an authenticated avatar voice chooser, selected-persona startup, scoped reconnect history and protection against delayed old-media close events. Two real synthetic WebRTC probes (Marin/Cedar) reached connected media and first assistant transcript output using the backend-result speech path. Full playback, multi-speaker handoff and a live team benchmark are not qualified.

V2-16 includes signed ingress, SIP accept/attach/reject/hangup, caller registry and salted keypad PINs, paired-device identity confirmation, forced policy enforcement for call actions, durable delegation replay protection, bounded calls and daily budgets, call transcript/receipt finalization and Android PWA call controls. Local lifecycle/approval tests pass. Real SIP number, public HTTPS ingress, audio quality, provider cost accounting and the remaining LLD lifecycle cases are pending. V2-16 and V2-18 remain In progress.

Evidence: [qualification record](qualification-2026-10-04.md). Full suite passed with 755 backend tests; final focused suite passed 41 after budget and finalization fixes. Admin, phone and avatar browser smokes and builds passed. This checkpoint ships as a local developer bundle, not a signed production installer or full-program delivery.


### Decoded audio and lifecycle continuation — 2026-10-04

Two real Live synthetic browser probes passed decoded audio/quiet-tail checks and clean provider closure. The preceding analyser-only failure remains in the evidence. V2-16 now delivers call receipts into its thread and phone Inbox with restart/crash deduplication, retains late transcript evidence, honors pause and records hangup/cancellation uncertainty. Full suite: 768 backend tests passed; final focused suite: 36 passed; phone smoke and builds passed. ADB found no connected Android device; real SIP/HTTPS infrastructure remains unavailable. V2-16/V2-18 remain In progress; priced call cost, unknown-caller modes, full surface integration, physical/live qualification and other LLDs remain open. Red-team remains disabled/deferred. [Qualification record](qualification-2026-10-04.md).

### 2026-10-04 — Android hands development checkpoint

V2-21 registry tools, grounded effects, mandatory ledger, shadow-mode cancellation, authenticated Admin setup, one-fps live view and PC/PWA stop controls implemented. V2-26 shared grammar and role mapping started. Physical Android and remaining LLD acceptance remain open. See [qualification report](qualification-android-hands-2026-10-04.md). Red-team remains disabled and deferred.

### 2026-10-05 — MCP development checkpoint

V2-23 now has the official SDK HTTP transport, protected per-client stdio credentials, owner Admin
registration/grants/revocation, isolated browser contexts, mandatory effect approvals, progress/images,
receipts and an external task runner without owner memories or connectors. Source integration uses
real transports, a scripted model, fixture browser/desktop hands and real Windows AppContainer commands.
The follow-up adds isolated sandbox scope, explicitly shared personas with intersected permissions,
signed interrupt-card grants and actual slot activity. V2-23 remains In progress: live external-agent,
desktop/browser and model qualification remain open.
See [qualification record](qualification-mcp-2026-10-05.md). Red-team remains disabled and deferred.

### 2026-10-05 — Continuity development checkpoint

V2-24 implements expiring native/phone presence, durable configurable routing and quiet hours,
notification retraction, same-thread native handoff/Live view and a fixed owner-call review.
Native Electron and encrypted Android-sized Chromium have source integration evidence.
Automatic urgent escalation and spoken/voice handoffs remain unimplemented; physical Android/Wear OS,
live push/channel and owner SIP qualification remain open. V2-24 remains In progress. See
[the continuity qualification record](qualification-continuity-2026-10-05.md).
