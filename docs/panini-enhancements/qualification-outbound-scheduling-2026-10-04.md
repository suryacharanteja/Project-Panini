# V2-20 scheduled calls development increment — 2026-10-04

V2-20 remains in progress. This increment builds on commit `9c3b09d`; it does not certify real SIP,
audible disclosure, IVR, owner takeover, physical Android or production readiness. Red-team remains
disabled and deferred. Existing owner approvals and the outbound checklist remain enforced.

## Delivered

- Specific reviewed call times (explicit offset, within seven days) or the next destination calling window.
- A fixed start deadline of at most fifteen minutes, clipped to every destination timezone's 20:00 cutoff.
- Exact brief/time approval digests; editing invalidates the previous review. Approval must arrive within
  at most one hour, or sooner when the start deadline is nearer.
- Durable unattempted schedules across restart, bounded expiry, cancellation notices and PC/PWA controls.
- Policy, flag, pause, setup, number block, hours, approval strength and daily cap rechecked before dispatch.
- No retries after ambiguous creation, missed windows or a completed attempt. PANINI must be running.
- Strict owner-agent function schemas for call briefs and nullable scheduling.
- Cumulative voice usage and final provider usage recorded separately, including closure during shutdown.
- Explicit incomplete finalization; no invented USD charge or task completion. Keypad event counts without
  digit retention; sanitized provider failure codes and ringing-watchdog no-answer status.
- PWA cache version advanced so existing installations can receive the updated call screen.

## Verification

New deterministic checks cover times/offsets, calling-window cutoffs, no early dialing, one attempt,
editing, pause/flag/policy/number/setup revocation, expiry, busy lines, actual SQLite reopen, owner-agent
schema/review dispatch, usage finalization during close, malformed usage and keypad replay.

The repository check passed with 924 backend tests, the CLI/gateway/PWA/Search/Admin/shell suites,
offline evaluations, format, pin and type checks. Final review added a vault-write failure regression
and moved durable approval revocation before credential changes. The final backend rerun passed
**925 tests**, and the final telephony/harness/policy run passed **216 tests**. This increment adds
**37 new checks**. Existing five dependency/Windows-pipe warnings and the CLI skip remain in the logs.

The real browser smoke exercises Admin setup, brief replacement, chosen time, exact approval, waiting,
scheduled dispatch and cancellation. It also exercises the actual PWA call screen's notes, cancellation
and transfer controls through a fake fabric adapter to the real authenticated backend. Desktop and
412px screenshots are checked. The preview uses a throwaway database/vault, a controlled clock and fake
SIP/voice. It cannot place real phone calls. These are browser checks, not physical Android tests or a
complete encrypted phone-to-SIP qualification.

Evidence is retained in `artifacts/outbound-followup`: repository/targeted check logs, built-Admin log,
browser log/screenshots, program acceptance audit, unsigned package log and release integrity report.
The integrity report ties the candidate to its clean source commit and checks runtime files, migrations,
PWA source, bundle CRC and source patch. Previous release evidence remains under `artifacts/outbound`.

## Remaining requirements

- Configure an outbound-enabled OpenAI organization, TLS/Opus/SRTP trunk and an owned test destination.
  This development shell has no `OPENAI_API_KEY`, `SIP_USERNAME` or `SIP_PASSWORD`; no real call was made.
- Real audible first disclosure, voicemail/conversation behavior, transfer completion and Android hardware
  qualification. ADB is not on PATH, but the installed SDK's `adb devices -l` returned no attached
  devices. No physical device result is claimed.
- Supported IVR sending, approved read-only brain lookups and interpreted outcomes linked to explicit
  callee evidence. Task achievement, commitments and follow-ups remain unconfirmed.
- Verified voice/trunk cost accounting, publisher signing, clean-VM install and the wider program gates.

The official [SIP guide](https://developers.openai.com/api/docs/guides/voice-sip) describes keypad
notifications and forbids treating call creation retries as deduplicated. The
[session guide](https://developers.openai.com/api/docs/guides/live-conversations#usage-and-graceful-close)
specifies cumulative usage and final closure. A keypad notification does not establish a supported tone
command; final usage alone does not establish a complete telephone bill.

The owner checklist must be acknowledged in the product by the owner. All acknowledgements and approvals
created during these tests exist only in isolated fixtures; none were created in production data.
