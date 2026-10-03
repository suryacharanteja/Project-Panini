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

All 28 LLDs are tracked in `delivery-plan-v2.md`; this checkpoint does not complete them. V2-18 still needs parked team continuation, cross-surface presentation, integration with later LLDs and live evaluation. Red-team work remains in progress and aside.

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
