# V2-20 scoped call reads development increment — 2026-10-04

V2-20 remains in progress. This increment builds on `fb33605` and adds a working adapter contract for
reviewed facts and calendar free/busy reads during an outbound call. Red-team remains disabled and deferred.
No real SIP call, audible playback, live calendar/account qualification or physical Android result is claimed.

## Delivered

- A fresh, tool-free selector restricted to the callee question, reviewed purpose, allowed facts and exact
  availability windows. No owner agent, memory, previous conversation, notebook or general tool catalogue.
- Backend-constructed replies from an exact permitted fact or validated free/busy projection. Known excluded
  fact categories are filtered before the selector and refused before speaking. Unknown/ambiguous/failed
  selections return to the existing owner-decision path; they cannot execute a write or create a booking.
- Up to three owner-reviewed availability windows within seven days, each at most four hours, with explicit
  timezone offsets. Server, tool and configuration/catalogue fingerprint enter the exact approval digest.
- Only discovered read-only, non-destructive MCP tools with an explicit owner background-read grant and
  the exact availability input contract are eligible. No grants are automatically created or inherited
  from the owner principal. Current external phone-line policy, source fingerprint and call authorization
  are checked before/after model work and connector dispatch, including after connector initialization.
- A 300-token output limit and twenty-second read/reply deadline, with the existing twelve-request-per-call
  and daily-call caps. Call ending cancels read tasks. One durable attempt per delegation ID; interrupted
  reads recover as unavailable without automatic re-dispatch. Original completed receipts stay immutable.
- Admin source/window controls and PC/PWA receipt IDs. Local dates convert to exact UTC offsets for review.
  Empty lookup scope stays omitted from stored legacy briefs to preserve approval/report digests; strict
  owner-agent function schemas require an explicit empty array and inline nested definitions.
- Migration 0029, frozen-runtime table verification and PWA cache version v10.

## Availability adapter contract

This is an adapter interface, not an already configured Google/Outlook calendar integration. Discover the
adapter in Admin Connectors and explicitly grant its read-only tool background access, then include the
exact windows in a call brief and approve that review. Production adapters/accounts need live qualification.

The MCP tool must declare `readOnlyHint: true`, must not declare `destructiveHint: true`, and accept exactly:

```json
{"type":"object","additionalProperties":false,"required":["start","end"],
 "properties":{"start":{"type":"string"},"end":{"type":"string"}}}
```

The backend sends only these reviewed offset-time strings. It accepts MCP `structuredContent`, or one
JSON text content block, containing exactly:

```json
{"start":"2026-10-08T20:00:00Z","end":"2026-10-08T21:00:00Z",
 "complete":true,"busy":[{"start":"2026-10-08T20:15:00Z","end":"2026-10-08T20:45:00Z"}]}
```

Both coverage endpoints must equal the reviewed window; incomplete coverage cannot imply free. Extra event
fields, malformed intervals, errors and oversized data fail. Busy overlaps use half-open intervals. Only
`free`/`busy`, the exact window and `booking_verified: false` are retained in the lookup result; raw connector
content, event titles and attendee details are never fed to the selector or voice. MCP annotations and a
provider's completeness assertion do not independently establish provider correctness; qualification must
verify the adapter's real behavior. Availability never confirms or reserves an appointment.

## Verification

**36 new deterministic checks** cover bounded windows, strict nested schemas, legacy serialization,
coverage/overlap, private event rejection, tool-free context, exact MCP arguments, durable replay, fact
selection, invalid selectors, grant/config/catalogue/policy revocation, stale windows, in-flight changes,
call end, timeout, review replacement and SQLite restart without receipt rewriting or retry.

The final telephony/harness/policy run passed **277 tests**. The repository gate passed **986 backend tests**
and the CLI, gateway, Android PWA, Search, Admin, shell, offline evaluation, pin, formatting and TypeScript
checks. Initial runs exposed a strict-schema fixture mismatch and a source-list TypeScript guard issue;
both were corrected before the final gate. Existing suite warnings and the CLI skip remain in the logs.

The real browser smoke uses the actual Admin/PWA modules, authenticated backend and a throwaway database/
vault, with fake SIP/voice/calendar and a scripted selector. It exercises adding/editing a reviewed window,
UTC binding and source fingerprint, exact free/busy dispatch, replay refusal, lookup receipts, PC report
assessment, phone restore-before-review and existing schedule/cancel/note/transfer flows. Phone-width
availability inputs and desktop/mobile/PWA screenshots were inspected. No actual calendar account or
physical Android device participated; this is not a full encrypted phone-to-SIP qualification.

Evidence is under `artifacts/outbound-reads`: final and initial check logs, focused tests, Admin build,
browser log/screenshots, complete program audit, source bundle, unsigned package log and integrity report.
The integrity report binds the exact clean commit, checks every staged/shipped runtime hash, migration 0029,
phone source/cache, source-bundle CRC/reverse patch and the packaged runtime smoke. Distribution remains a
local unsigned development installer and source bundle; no production promotion is claimed.

## Remaining requirements

- Real adapter/account behavior and live selector accuracy, especially ambiguous dates and outside-scope
  questions. Current deterministic tests establish scope bounds, not semantic accuracy of a live model.
- Configured SIP organization/trunk and owned test destination, real audible disclosure/playback, supported
  IVR sending, confirmed owner takeover, voicemail/conversation behavior and physical Android testing.
- Verified voice/trunk/model billing, publisher signing, clean-VM installation and wider LLD acceptance.

Full V2-20 and full-program production qualification remain open. Red-team stays disabled and deferred.
