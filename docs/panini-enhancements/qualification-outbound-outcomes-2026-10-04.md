# V2-20 outcome interpretations development increment — 2026-10-04

V2-20 remains in progress. This increment builds on `575dbef`; red-team remains disabled and deferred.
The call receipt remains immutable. A separate report offers quote-cited model interpretations and optional
owner assessment. No follow-up effect is executed, even when the owner accepts an interpretation.

## Delivered

- Durable per-call reports tied to the reviewed brief digest, call receipt and transcript artifact hash.
- Only completed answered calls with monitored disclosure and callee transcript text are eligible.
- Fresh tool-free interpretation context: reviewed brief and complete bounded speaker turns, no memory.
- Strict structured proposals, exact callee quotation checks, reviewed outcome indices and commitment
  permissions. Assistant speech cannot serve as callee evidence. Explicit unresolved findings prevent
  an achieved proposal. These checks do not establish semantic entailment or actual task achievement.
- Input/output bounds, 3,000 output tokens, thirty-second deadline, daily attempt cap, policy/pause checks
  before and after interpretation. One attempt per call; interrupted/running-at-restart work is not retried.
- Inbox assessment bound to the exact source and report digest. Existing paired-owner decision rules apply;
  phone-line speech cannot accept. Reviews expire after one hour and may reopen; receipts remain separate.
- Admin and Android PWA quotes, suggested result, owner assessment, fixed follow-up suggestions and explicit
  unverified wording. The PWA restores current state before requesting review; shell cache advances to v9.
- Frozen-runtime migration validation for `outbound_reports`.

## Verification

The increment adds **25 deterministic checks**. The final focused telephony/harness/policy run passed
**241 tests**, and the full repository check passed **950 backend tests**, with the CLI, gateway, PWA,
Search, Admin, shell, offline evaluation, formatting, dependency pin and TypeScript checks also passing.
The retained warnings and CLI skip are unrelated existing suite conditions, not live qualification.

Checks include exact/wrong-speaker quotes, prohibited commitment types, missing/contradictory outcomes,
context limits, source tampering, invalid model output, immutable original receipts, accepted/rejected
assessment, expiry/reopening, daily limits, cancellation, deadline, pause/source changes during model work,
SQLite reopen and no automatic retry. The exact outgoing model request is inspected without a network
client to verify strict schema, no tools, stateless storage and output cap.

The browser smoke uses the actual Admin/PWA modules and authenticated backend, a throwaway vault/database,
fake SIP/voice and a scripted interpreter. It exercises quoted reports, PC owner assessment and phone
restore-before-review plus existing scheduling/cancellation/note/transfer flows. Desktop and phone-width
screenshots were inspected. This is not physical Android or full encrypted phone-to-SIP qualification.

Evidence: `artifacts/outbound-outcomes` contains repository and focused logs, built-Admin log, browser log,
screenshots, source bundle, unsigned package log and release integrity report. The integrity report checks
the exact clean source commit, staged and shipped runtime hashes, migration 0028, PWA source/cache, ZIP CRC,
reverse applicability of the source patch and the packaged runtime smoke. There is no configured Git remote
or production deployment destination; distribution is the local unsigned development installer/bundle.

## Remaining qualification

- A live outcome model benchmark with confirmed/declined/conditional/contradictory/voicemail cases. Exact
  quote matching and strict JSON are not semantic proof. Owner acceptance records an assessment; it does
  not independently confirm a reservation or calendar booking.
- Configured outbound-enabled organization, owned test number and TLS/Opus/SRTP SIP trunk; real audible
  disclosure, supported IVR sending, actual takeover, voicemail behavior and physical Android testing.
- Approved read-only brain lookups, verified voice/trunk billing and wider program acceptance evidence.
- Publisher signing, clean-VM installation and production release qualification.

The [official structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
describes strict schemas and required nullable properties. PANINI also checks exact transcript quotations
and keeps the output explicitly unverified; schema conformity alone cannot establish truth.
