# Outbound calls development increment — 2026-10-04

V2-20 remains in progress. This is an unsigned local development candidate, not a live SIP qualification
or full LLD completion. Red-team qualification/enforcement remains disabled and deferred.

## Delivered

- Strict owner-reviewed call briefs with protected exclusions and limited permitted commitments.
- Geographic E.164 validation, service/premium/emergency blocking, conservative timezone calling windows.
- TLS trunk validation, credentials in the Windows user vault and explicit owner checklist acknowledgement.
- Exact brief/nonce/digest review replacement; policy, approval strength and access rechecked at dispatch.
- Single-use calls with durable attempts, daily cap, no automatic retry after ambiguous creation or restart.
- Live SIP creation, immediate sideband attach, lifecycle events, disclosure monitoring and bounded hangup.
- Third-party voice context contains only the brief; no owner memories, connectors or desktop tools.
- Additional owner decisions, payment/credential refusal, do-not-call persistence.
- PC and paired-phone monitoring, end/transfer controls and notes; digest/transcript-linked receipts.
- Conservative outcomes: no confirmed task achievement, commitments, cost or actual takeover inferred from mocks.

## Verification

45 new outbound checks cover destinations, windows, mandatory exclusions, trunk credentials, review editing,
channel/voice approval rejection, policy changes, altered brief rejection, lifecycle/disclosure, transfer request,
notes, receipts/hash chain, ambiguous creation, database reopen recovery, sideband failure, cancellation,
pause/flag/number revocation, daily caps and owner decisions. Existing telephony/harness/policy tests also run.

The browser preview runs real Admin APIs, durable reviews, manager and ledger against fake SIP and voice.
It verifies setup, credential clearing, brief/edit, exact review resolution, call lifecycle, note, hangup,
transcript and receipt, with desktop and 412px layouts. It uses an isolated throwaway database/vault and
cannot send real calls. Evidence is retained in artifacts/outbound.

The real phone call screen also exercises note/transfer through a fake fabric adapter to the real
authenticated backend controls. This verifies browser UI behavior, not a physical device or a complete
encrypted phone-to-SIP connection. Focused telephony, policy and harness checks: **179 passed**.

Transfer keeps the sideband alive until the provider closes the source session. A thirty-second deadline
ends an unconfirmed transfer; an accepted refer is never presented as confirmed owner takeover.

The repository check passed **888 backend tests**, plus CLI, gateway, Android PWA, Search, Admin,
shell, type/format checks and offline evaluations. Existing dependency/Windows transport warnings and the
CLI skip remain recorded in the log. These checks do not establish live-provider qualification.

## Remaining qualification

- Configured TLS/Opus/SRTP SIP trunk, outbound-enabled OpenAI organization and test number owned by the user.
- Real audible first disclosure, conversation/voicemail behavior, transfer completion and physical Android testing.
- Supported keypad/IVR automation: official documentation describes DTMF send as a notification, not a client command.
- Scheduled calling windows, approved read-only brain lookups, fully interpreted outcomes and cost accounting.
- Existing signing, clean-VM installation, multi-day and remaining LLD release requirements.

The owner checklist must be acknowledged in the product by the owner. Test acknowledgements exist only in
throwaway fixtures; no production acknowledgement or call approval was created by this development run.
