# PANINI v2 development qualification — 2026-10-04

Status: development checkpoint; full-program delivery and production release remain incomplete.

## Verification

- `npm test`: passed. Backend 724 passed; eval tests 7 passed; offline scripted scorecard 16/16; CLI 125 passed and 1 skipped; frontend 101 passed; phone 10 passed; search 95 passed; Admin 18 passed; shell 12 passed. Formatting, dependency pins, Ruff and TypeScript checks passed.
- Subsequent team failure handling: focused persona, brain and AG-UI suites passed (see `artifacts/personas/final-targeted.log`); Ruff and formatting checked again.
- `npm run admin:build`: passed.
- `npm run personas:smoke`: passed against a throwaway loopback backend with scripted replies. Covers create/edit/permissions/test/reload/archive and desktop/mobile layout; screenshots inspected.

The full test run reported four dependency deprecations and an existing Windows closed-pipe cleanup warning in the Playwright hand test. No test failed. The browser smoke is not physical Android or live model qualification.

## Delivered development scope

V2-18 persona storage, permission isolation, scoped memory, selection, bounded delegation/teams, receipt attribution, checkpoint identity protection and an Admin editor. Detailed implementation and remaining acceptance work are in the V2-18 LLD. Personas remain off by default.

## Outstanding release work

All 28 LLDs are tracked in `delivery-plan-v2.md`; this checkpoint does not complete them. The increments below add durable team continuation and visual persona presentation. V2-18 still needs complete voice/team qualification, integration with later LLDs and live evaluation. The latest owner decision disables red-team qualification and enforcement and defers them to future development; earlier checkpoint descriptions below are historical.

Prior multi-day evaluation ended 6/9 after the third sample failed with exhausted API credits; it does not qualify this release. Live provider qualification needs available credits. Remote Android install qualification needs a stable HTTPS origin and physical device testing. A signed production installer and an external deployment destination are not available. This repository has no Git remote configured.

Local source and compiled Admin can be bundled for developer review. Such a bundle is not a signed installer or a production deployment and contains no product data, pairing keys or credentials.

## Next increment: durable team continuation

Implemented SQLite-backed progress for teams, direct specialist delegation and reviewers. Resume selects the waiting lead, returns the member's result to it, skips completed members and retains the member's receipt operation keys. Tool occurrence counts survive pauses, so two identical approved writes execute once each. Decline closes the waiting run tree and explicitly preserves earlier completed work.

Resume retains cumulative cost, steps and active time; each attempt records only new own cost and tokens. Daily spending checks add only unrecorded cost. Tools from an over-budget model turn are refused before dispatch. Unfinished specialist tools or failed team calls cannot produce a verified-complete result.

Verification for this increment:

- `npm test`: passed, including 737 backend tests, 7 eval tests, 16/16 scripted offline cases, 125 CLI tests (1 skipped), 101 frontend, 10 phone, 95 search, 18 Admin and 12 shell tests. Formatting, pins, Ruff and TypeScript passed. The existing five backend warnings remain.
- After final completion-status and decline-message fixes, persona, AG-UI, brain and harness suites: **125 passed**, one dependency deprecation warning. Ruff and formatting passed again.
- `backend/tests/agui/test_persona_resume.py`: team/member/reviewer double pauses, stream-runner recreation, one-time identical effects and model cost accounting; decline after zero or one completed write; rollback, archive, grant revocation, exhausted cost/time and failed quarantined sources.

This is local scripted qualification against the real HTTP/SSE, policy, ledger and checkpoint pipeline with a test hand. Physical Android, live providers, cross-surface voices and a production deployment remain unqualified. V2-18 stays In progress; red-team stays in progress and aside.

## Next increment: phone and avatar persona presentation

Added public persona names and colors to the authenticated fabric, a phone Ask chooser, task-scoped reply history, and avatar identity/palette switching with attributed conversation messages. The JARVIS canvas and status animations remain. Final finish-tool replies now receive attribution. Presentation metadata contains only id, name and color; private instructions and grants stay outside this API. The phone service-worker cache advances to v4.

Verification for this increment:

- `npm test`: passed, including 740 backend tests, 7 eval tests, 16/16 scripted offline cases, 125 CLI tests (1 skipped), 101 frontend, 12 phone, 95 search, 18 Admin and 12 shell tests. Pins, formatting, Ruff and TypeScript passed. The same five backend dependency/Windows cleanup warnings remain.
- The final avatar finish-reply test was added after the full suite started: persona and fabric focused suites passed **24 tests**. Ruff passed again.
- `npm run phone:smoke`: passed encrypted pairing, persona selection, attributed reply, signed approval, reload, live preview, share, offline recovery and removal. All six chip palettes passed 4.5:1 contrast checks against the phone background in light and dark themes.
- `npm run personas:ui:smoke`: passed PANINI/Scout/Quill identity and palette changes, escaped message rendering, compact and full-stage screenshots. Screenshots inspected. Native window controls are mocked by the browser preview.
- `npm run build --workspace @panini/shell`: passed for the final source.

Provider replies are scripted in the phone preview. Physical Android installation, native Electron release, live voice switching and production deployment are not qualified by these checks. This is a local developer bundle. V2-18 remains In progress; red-team remains in progress and aside.

## Current increment: persona voice reconnect and inbound phone foundation

Owner decision: red-team qualification and enablement enforcement are **disabled and deferred to future development**. `PANINI_REDTEAM_ENFORCE=false` is the default. Admin Remote and Safety show future development without claiming a passing result. Explicitly enabling enforcement restores the original gate. Pairing, device authentication, signed approvals and action permissions remain enforced.

Implemented:

- Avatar voice-persona selection and reconnect. Selected voice and private instructions configure the provider session; public chooser metadata excludes instructions. History is bounded and scoped to the selected persona. Switching stops current work and reconnects audio; delayed closure of the old media generation cannot close the new connection. Archive and flag rollback are revalidated before delegation.
- Inbound SIP foundation: verified provider ingress, caller screening, PBKDF2 PINs, session accept/attach/reject/hangup, phone identity confirmation, independent per-call threads, forced action policy enforcement, durable delegation deduplication, stop, registered-owner transfer, timeout and daily budgets, transcript artifacts and call receipts. Speech and DTMF cannot authorize effects. Caller removal/persona changes end the call. Failed receipt storage preserves duration accounting and releases the live slot without claiming a completed receipt. No audio recording is enabled.
- Admin Phone line setup, DPAPI webhook-secret storage and caller management. Android PWA call monitor, verification through signed Inbox decisions, transfer/end controls with restore-before-control checks. Service-worker cache v5. The loopback preview uses a throwaway database and mocked SIP transport.

Verification:

- `npm test`: passed, including **755 backend tests**, 7 evaluation tests, 16/16 scripted offline cases, 125 CLI tests (1 skipped), 101 frontend, 12 phone, 95 search, 18 Admin and 12 shell tests. Pins, Prettier, Ruff and TypeScript passed. Five existing dependency/Windows cleanup warnings remain. Log: `artifacts/personas/voice-call-final-check.log`.
- After the full suite, added bounded Admin call settings, remaining-daily-minute enforcement and receipt-storage failure recovery. Final focused suites: **41 passed**, one dependency warning. Log: `artifacts/personas/voice-call-final-targeted.log`. Telephony cases exercise the actual agent/policy/interrupt/ledger paths with scripted models and a test computer hand; no destructive host commands run.
- `npm run phone:smoke`, `npm run personas:smoke`, `npm run personas:ui:smoke`: passed. Phone call identity confirmation uses the real encrypted fabric and signed Inbox approval; end clears the active call. Avatar smoke selects Scout, reconnects to Quill and delivers a delayed old-media-close event. Admin creates a caller and stores a webhook secret; password fields clear after save. Mobile screenshots inspected.
- Final Admin and shell builds passed. Ruff, formatting and shell typecheck passed again after implementation fixes.
- `npm run personas:voice:live`: latest probe **2/2 passed**, Marin and Cedar, at 2026-10-04T05:12:10Z. Real Live sessions accepted seeded text history, synthetic silent WebRTC peers connected, and first assistant transcript output arrived through `connection.speak`. Evidence: `artifacts/personas/voice-live.json`. An earlier seeded-history greeting attempt timed out for Cedar; the retained report includes it. An initial earlier timeout predates that report's attempt retention. This is a narrow successful rerun, not a reliability benchmark. Probe cleanup before full speech completion emitted `context_injection_incomplete`; completed audible playback is not qualified.

Remaining requirements:

- Real SIP trunk/number, provider SIP project access, public HTTPS signed webhook and a live owner call through PIN, backend task, device approval and receipt. No real telephone call was placed by these tests.
- Full audible playback, interruption/reconnect quality, multi-speaker handoff and live research-to-writing team qualification. Full V2-18 provider qualification remains pending.
- V2-16 cost pricing, unknown-caller ask/message modes, later transcript/receipt lifecycle cases and full surface/run-engine integration remain incomplete. Duration and provider usage are recorded; priced call cost is not yet computed.
- Physical Android PWA installation, passkeys, production transport, signed installer and remaining LLDs. The repository has no Git remote or production deployment destination.

This is a **local developer release**. V2-16 and V2-18 remain In progress; all 28 LLDs and Dot equivalence are not complete. Credentials, pairing keys and product databases are excluded from the bundle.
