# PANINI v2 delivery plan

Updated 2026-10-05. Owner authorized all LLDs. This tracks the complete v2 program; the register also calls V2-08–12 "Phase 2". No additional scope approval is needed.

## Delivery order

1. Qualify existing foundations V2-00–12 against their acceptance evidence; retain feature flags until gates pass.
2. Complete V2-18 persona isolation and delegation, then V2-19 Spaces with CAS and review idempotency.
3. Complete V2-15 Android PWA passkeys, push and real-device qualification; finish V2-14 production transport qualification.
4. V2-16 inbound SIP and V2-17 channels with configurable accounts and sandbox tests, then real account qualification.
5. V2-20 outbound calls (including the LLD compliance gate), V2-21 Android hands, V2-22 teach-by-demo, V2-23 MCP, V2-24 continuity, V2-25 triggers.
6. V2-26 universal hands and V2-27 offline/private routing, with the specified comparative evaluation gates.

Owner decision on 2026-10-04 supersedes the earlier red-team gate: red-team qualification and its enablement enforcement are disabled and deferred to future development. `PANINI_REDTEAM_ENFORCE` defaults off; remote and phone-line configuration no longer requires a passing red-team file. Pairing, signed decisions and action permissions remain enforced. Feature flags remain opt-in. Two live avatar WebRTC voice probes passed; real SIP, physical Android and signing qualification still require configured infrastructure and hardware. Unchecked original red-team requirements below describe future development, not a current release gate.

## All LLD acceptance checks

Prior Done labels below are inherited implementation labels. Every release claim still requires reproducible evidence. Unchecked requirements remain open; this checklist never upgrades a status automatically.

### V2-00: Program setup

Implementation register: **Done**. [Source LLD](lld/V2-00-program-setup.md).

Dependencies: —.

- [ ] `panini-core/capabilities/` contains only `browser`, `cli`, `desktop`, `search`.
- [ ] `git status` shows no OpenDots files; `references/README.md` is tracked.
- [ ] Every flag in `FLAGS` appears in Admin with help text; all default off.
- [ ] `schema_migrations` exists on a fresh and on an upgraded v1 database.
- [ ] `npm test` green.

### V2-01: Eval baseline and scorecard

Implementation register: **Done**. [Source LLD](lld/V2-01-eval-scorecard.md).

Dependencies: V2-00.

- [x] 36 cases exist (20 live, 16 offline); browser, desktop and persona cases are added by their items.
- [x] Offline suite runs in `npm test` in seconds without network.
- [ ] v1 live baseline recorded (3 samples) and referenced in the register evidence log.
- [ ] `compare` fails on an injected regression and passes on identical runs.

### V2-02: Capability and tool registry

Implementation register: **Done**. [Source LLD](lld/V2-02-capability-registry.md).

Dependencies: V2-00.

- [ ] With `PANINI_V2_REGISTRY=true`, all existing backend tests pass and the offline scorecard matches v1.
- [ ] No code outside `backend/registry/` names the five capabilities in a list or prompt (grep check in
      `test_boundaries.py`), except v1 code paths kept for rollback.
- [ ] Admin shows every manifest with readiness and tools.
- [ ] A new capability can be added with a manifest and a hand class and no other edits (proven by a test
      capability in `backend/tests/registry/fixtures/`).

### V2-03: Policy engine

Implementation register: **Done**. [Source LLD](lld/V2-03-policy-engine.md).

Dependencies: V2-02.

- [ ] Shadow mode shows zero cases where v2 allows what v1 would have asked.
- [ ] I5, I7, I8 tests green.
- [ ] Admin Permissions page edits a grant and a running test run stops at its next effect.
- [ ] `enforce` passes the offline scorecard with no new regressions.

### V2-04: Receipts and artifacts ledger

Implementation register: **Done**. [Source LLD](lld/V2-04-receipts-ledger.md).

Dependencies: V2-02 (call pipeline), V2-00 (migrations).

- [ ] Every registry call writes begin/end entries; I1 test green.
- [ ] Simulated crash after dispatch never re-dispatches; review interrupt raised.
- [ ] `scripts/panini-ledger-verify.py` verifies a 10,000-entry chain in under 2 s.
- [ ] Admin Activity shows receipts with redacted args, evidence and artifacts.

### V2-05: Interrupts (durable approvals, questions, choices, reviews, takeovers)

Implementation register: **Done**. [Source LLD](lld/V2-05-interrupts.md).

Dependencies: V2-03 (decisions and levels), V2-04 (receipts).

- [ ] With the flag on, all existing confirmation tests pass and the shell looks the same.
- [ ] A background task needing approval parks, is approved from the phone, and completes without repeating
      earlier effects.
- [ ] I3, I4, I8 tests green.
- [ ] Interrupts survive a restart as specified in §5.9.

### V2-06: One brain (run engine, model router, budgets)

Implementation register: **Done**. [Source LLD](lld/V2-06-one-brain.md).

Dependencies: V2-01 (scorecard), V2-02, V2-03, V2-04, V2-05.

- [ ] Scorecard (live, 3 samples): success ≥ v1 baseline; `simple` p50 ≤ v1 + 10 %; cost per verified task
      ≤ v1 + 15 %; safety 100 %.
- [ ] Avatar, voice, console, LAN phone and background tasks run on the engine; `planning.route` unused.
- [ ] I2 test green; parked runs resume after a restart without repeating effects.
- [ ] The memory rule holds: Desktop and Paint live tests pass with the flag on.

### V2-07: AG-UI adapter and inline cards

Implementation register: **Done**. [Source LLD](lld/V2-07-agui-adapter.md).

Dependencies: V2-05 (interrupts), V2-06 (run engine events).

- [x] `@ag-ui/client` streams a PANINI run with tool calls and an approval interrupt, and resumes it.
- [x] Shell shows ToolCard, InterruptCard and live preview; v1 confirmation tests still pass.
- [x] Malformed-event test proves one bad card cannot unmount the interface.

### V2-08: Playwright sidecar and managed browser

Implementation register: **Done**. [Source LLD](lld/V2-08-playwright-sidecar.md).

Dependencies: V2-02, V2-03, V2-04.

- [x] Managed sessions persist logins across PANINI restarts (sign in once to a test site, restart, still
      signed in).
- [x] Browser golden tasks on `managed` ≥ the v1 `chromium` baseline; median latency per action ≤ v1.
- [ ] Packaged runtime grows ≤ 15 MB for this item; integrity gate passes.
- [x] Killing the sidecar mid-action yields `unknown_effect` (effect tools) or `failed` (observe tools),
      never a false success.

### V2-09: Claw CDP bridge (your Chrome through Playwright)

Implementation register: **Done**. [Source LLD](lld/V2-09-claw-cdp-bridge.md).

Dependencies: V2-08.

- [x] Trusted-input demo reports `isTrusted=true` for clicks and key presses in an attached tab.
- [x] I9 test green; the policy tables match; unknown methods are denied in both places.
- [x] Detaching the tab, pressing Cancel on Chrome's bar, or switching tabs outside the workspace stops the
      run with a clear message.
- [ ] Browser golden tasks in your Chrome: success ≥ v1 extension baseline with fewer model calls per action
      (no propose-6/Jev-select round trip).

### V2-10: Browser vocabulary v2, verification and traces

Implementation register: **Done**. [Source LLD](lld/V2-10-browser-vocabulary.md).

Dependencies: V2-08 (sidecar), V2-09 (your Chrome).

- [x] All 80 Playwright tools are mapped; the model never sees owner-only tools.
- [ ] Browser golden tasks pass on chrome and managed with success ≥ v1 and fewer model calls per action.
- [x] Password refusal, stale-ref abort and upload approval proven by tests.
- [x] A managed run's receipt links a trace that opens in the Playwright trace viewer.

### V2-11: Sandbox computers (OpenBot on Docker)

Implementation register: **Done**. [Source LLD](lld/V2-11-sandbox-computers.md).

Dependencies: V2-02, V2-03, V2-04; Docker Desktop (register D4).

- [x] Images build from the pinned revision; the patch check passes; digests recorded.
- [x] All ported tests green; live isolation and persistence checks pass.
- [ ] An untrusted link opens in the sandbox when `PANINI_UNTRUSTED_BROWSING=sandbox`, and the agent reads it
      there.
- [x] Revoking the shell permission during a 30 s command cancels it within 1 s.

### V2-12: Live view and human takeover

Implementation register: **Done**. [Source LLD](lld/V2-12-live-view-takeover.md).

Dependencies: V2-07 (cards), V2-08 (Playwright sessions), V2-11 (sandbox).

- [ ] One LiveView shows chrome, managed, sandbox and desktop frames for a run that uses them.
- [ ] Take/give back works on each hand with the resume-snapshot rule enforced.
- [ ] The animated pointer appears in the managed browser and your Chrome during actions.
- [ ] Remote viewing of chrome/desktop is off until granted.

### V2-13: Injection firewall and guardian

Implementation register: **Red-team disabled; deferred to future development by owner**. [Source LLD](lld/V2-13-guardian.md).

Dependencies: V2-03 (taint and policy), V2-01 (eval harness).

- [ ] Red-team gate met (100 % no silent effects, ≥ 95 % legitimate completion) on the live run.
- [ ] Guardian adds ≤ 400 ms p50 to guarded effects.
- [ ] Remote, phone, channel and MCP-server flags refuse to enable without a passing red-team result.

### V2-14: Remote fabric (device keys, transports, blind relay, ingress, push)

Implementation register: **Built (gated)**. [Source LLD](lld/V2-14-remote-fabric.md).

Dependencies: V2-05 (interrupts). Red-team qualification enforcement is deferred by owner.

- [ ] A phone pairs by QR with SAS confirmation and works over LAN, Tailscale and relay.
- [ ] Relay sees only ciphertext (I6 test); revocation is immediate.
- [ ] A verified OpenAI test webhook and a WhatsApp challenge reach the PC through Funnel or the relay.
- [ ] Push notifications arrive on Android and on an installed iOS PWA.

### V2-15: Phone app v2 (approvals, passkeys, push, share, live)

Implementation register: **Partial: Android PWA built; passkeys and device qualification pending**. [Source LLD](lld/V2-15-phone-app.md).

Dependencies: V2-14 (fabric), V2-12 (live view), V2-05 (interrupts).

- [ ] A `tap` approval completes from an Android notification without opening the app.
- [ ] A `verify` approval requires biometrics and fails if the action changes after the prompt was shown.
- [ ] Sharing a link from another app starts a PANINI thread.
- [ ] Stop on the phone stops a desktop task within 1 s.

### V2-16: Phone line (inbound calls over SIP)

Implementation register: **In progress: inbound lifecycle and UI built; live SIP qualification pending**. [Source LLD](lld/V2-16-phone-line-inbound.md).

Dependencies: V2-14 (ingress), V2-15 (approvals on the phone), V2-06 (run engine); V2-13 gate.

- [ ] Real inbound call reaches PANINI; unknown callers rejected by default.
- [ ] Owner data is unavailable until PIN or push verification.
- [ ] An effect requested by voice is approved on the phone and completes; voice approval is refused.
- [ ] Call receipt appears in the thread with duration and cost.

### V2-17: Messaging channels (Slack, Telegram, WhatsApp, email)

Implementation register: **Not started**. [Source LLD](lld/V2-17-channels.md).

Dependencies: V2-06 (run engine), V2-13 (guardian gate), V2-14 (WhatsApp ingress only).

- [ ] Telegram and Slack live with allow-lists; WhatsApp and email pass their integration tests (live when
      accounts exist).
- [ ] Ported OpenDots Slack tests green.
- [ ] Provider errors never appear in replies or logs (safe-failure test).
- [ ] A conversation started in Telegram continues on the avatar (thread binding).

### V2-18: Personas and delegation (group threads)

Implementation register: **In progress**. [Source LLD](lld/V2-18-personas.md).

Dependencies: V2-03 (grants), V2-06 (run engine and sub-runs).

- [ ] Two personas with different grants proven isolated (tools, memories, computers).
- [ ] A group thread shows attributed messages and receipts from a lead and a member.
- [ ] Revoking a persona grant mid-run stops that persona's next effect.

### V2-19: Spaces and pages (PANINI's notebook)

Implementation register: **In progress**. [Source LLD](lld/V2-19-spaces-pages.md).

Dependencies: V2-05 (reviews), V2-07 (shared UI and Admin conversations); V2-18 for persona access.

- [x] Create, edit with conflicts, nest, search, soft delete and restore work in Admin.
- [x] "Show me the draft before saving" produces a review card; approve saves once; decline saves nothing.
- [ ] PANINI cites a page found by `pages_search` in an answer.

### V2-20: Outbound calls on your behalf

Implementation register: **In progress**. Reviewed SIP lifecycle, scheduling, usage receipts, quote-cited interpretations and scoped availability reads are built; real SIP/IVR, live lookup/outcome accuracy, billing and physical Android qualification remain open. [Source LLD](lld/V2-20-outbound-calls.md).

Dependencies: V2-16 (telephony foundation), V2-13 (callee-injection cases), V2-15 (approvals).

- [ ] Compliance checklist signed; flag refuses to enable otherwise.
- [ ] Live test: disclosure spoken first; brief respected; outcome receipt correct.
- [ ] Emergency and premium numbers impossible to call (tests).
- [ ] Take over transfers the call to your phone.

### V2-21: Android hands

Implementation register: **Not started**. [Source LLD](lld/V2-21-android-hands.md).

Dependencies: V2-08 (Node sidecar pattern), V2-03 (policy), V2-12 (live view).

- [ ] Wireless-debugging pairing works from Admin; the phone appears as a hand.
- [ ] Golden tasks pass; the message send requires approval; banking apps are refused.
- [ ] Live view shows the phone screen on the PC.

### V2-22: Teach by demonstration and self-healing skills

Implementation register: **In progress**. [Source LLD](lld/V2-22-teach-by-demonstration.md).

Dependencies: V2-10 (browser vocabulary), V2-04 (receipts), V2-05 (reviews); desktop broker changes.

- [x] A browser recording becomes a reviewed skill that replays with different inputs.
- [ ] The healer repairs a changed label and proposes a patch; nothing changes without approval.
- [ ] A desktop recording replays in Notepad; passwords never appear in recordings.

See the [browser checkpoint report](qualification-teach-browser-2026-10-05.md). Desktop recording,
frame-scoped replay and live healer qualification remain pending.

### V2-23: PANINI as an MCP server

Implementation register: **In progress**. [Source LLD](lld/V2-23-mcp-server.md).

Dependencies: V2-02 (registry), V2-03 (policy), V2-05 (interrupts); V2-13 gate.

- [ ] Claude Code and Codex can connect (HTTP and stdio) and list PANINI tools.
- [ ] Observation works without prompts; every effect asks the owner; receipts show the client.
- [ ] Revoking a client token stops further calls immediately.

### V2-24: Continuity and presence

Implementation register: **In progress**. [Source LLD](lld/V2-24-continuity-presence.md).

Dependencies: V2-14 (fabric), V2-15 (phone app), V2-16 (phone line), V2-17 (channels).

- [ ] With the PC locked, approvals arrive on the phone within 5 s; resolving anywhere clears them everywhere.
- [x] "Continue on my PC" from the phone opens the thread on the avatar.
- [ ] Urgent escalation calls the owner only when opted in.

### V2-25: Ambient triggers and responsibilities

Implementation register: **In progress**. [Source LLD](lld/V2-25-triggers-responsibilities.md).

Dependencies: V2-06 (run engine), V2-14 (webhook ingress), V2-18 (personas); V2-19 for digests.

- [ ] File, OS and webhook triggers fire within 5 s; email and calendar within their polling windows.
- [ ] No trigger loops on PANINI's own writes; storms are capped.
- [ ] A responsibility produces a weekly digest page and pauses itself after repeated failures.

### V2-26: Universal hands protocol (one snapshot/ref vocabulary)

Implementation register: **In progress**. [Source LLD](lld/V2-26-universal-hands-protocol.md).

Dependencies: V2-10 (browser vocabulary), V2-21 (Android tree); desktop worker.

- [x] Desktop snapshot works in Win32, WPF and WinUI apps (including where `FromPoint` fails).
- [ ] Fine tools meet the evaluation gate on the desktop golden tasks.
- [x] One grammar across web, Windows, Android and sandbox (shared parser tests).

### V2-27: Local and offline mode

Implementation register: **In progress**. Loopback local model, transient fallback, private-preview refusal boundaries, strict local evidence gates and packaged offline dictation into the shell composer are built; blocking-proxy egress proof, TTS, physical microphone testing, complete push-to-talk task and quality qualification remain open. [Source LLD](lld/V2-27-local-offline-mode.md).

Dependencies: V2-06 (model router).

- [ ] With the network disconnected, "open Notepad and type hello" works by push-to-talk in `fallback` mode.
- [ ] `private` mode makes zero requests to cloud hosts (verified with a blocking proxy in tests).
- [ ] The quality guard disables local tiers that underperform.


## Current development checkpoint

V2-18 has a working backend and Admin implementation with tests and browser smoke; see its implementation-evidence section for remaining requirements. It is not marked Done. Full-program shipping is incomplete. No production deployment, signed installer, or Dot parity is certified by this checkpoint.

V2-19 now integrates PDF/Markdown export, validated saved receipts/results/conversations, library
moves/history/access/outline controls and reviewed agent operations. Full repository gates passed
with 798 backend tests, final browser smoke passed, and one live gpt-4.1 page citation passed.
See [Spaces qualification](qualification-spaces-2026-10-04.md) for evidence and remaining
cross-LLD/surface integrations. Red-team remains disabled and deferred.

The next phone/team increment delivers action-bound passkeys, phone notebooks and exports, and a live
role-scoped Scout→Quill check. Final gates passed with 819 backend tests and 80 focused checks.
[Program continuation qualification](qualification-program-2026-10-04.md) records exact evidence and
remaining implementation/infrastructure. Full release promotion now checks every LLD acceptance list
against the exact clean source commit before the existing signing and clean-VM gates.

The next increment implements durable specialist/team/reviewer approval continuation and spending accounting across attempts. Its local HTTP/SSE qualification uses scripted models and an isolated test hand. Cross-surface persona presentation, later integrations and live-provider evaluation remain outstanding.

The Windows foundation increment adds opaque broker refs, hierarchical UIA snapshots and concrete-policy
registry actions. Win32 and WPF fixtures and installed WinUI Notepad pass; see
[Windows qualification](qualification-desktop-fine-2026-10-05.md). Macros, desktop recording, other hand
normalizers and the coarse/fine comparison remain pending. Coarse remains the default.

## MCP delivery checkpoint — 2026-10-05

V2-23 implemented the SDK transports, curated local hands, owner approvals, client-bound receipts and
browser sessions, Admin controls and packaged stdio bridge. Its follow-up adds per-client sandbox scope,
owner-shared personas with intersected permissions, interrupt-card grants and actual slot activity.
Windows AppContainer execution, workspace isolation and OS worker cancellation have source SDK evidence.
Remaining live external-agent, desktop/browser and model requirements and qualification
are recorded in [the MCP report](qualification-mcp-2026-10-05.md); no all-LLD completion is claimed.

## Continuity delivery checkpoint — 2026-10-05

V2-24 adds native and encrypted phone presence, durable routing/quiet hours, retraction and same-thread
PC/phone handoff. A real Electron smoke restores the avatar's original agent thread and opens Live;
Admin rules save through the real gateway/backend at desktop/mobile widths. A manual owner-call request
remains digest-bound and owner-reviewed. Automatic urgent escalation, voice/spoken handoffs and physical
Android/Wear OS/live push/SIP qualification remain pending. See
[continuity evidence](qualification-continuity-2026-10-05.md). Full-program promotion remains open.
