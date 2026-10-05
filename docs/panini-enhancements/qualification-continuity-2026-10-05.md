# V2-24 continuity development qualification

## Delivered implementation

- SQL migration 0032 persists expiring presence, bounded commands, routing settings and delivery records.
  The authenticated shell submits native powerMonitor signals; the paired phone submits encrypted
  foreground heartbeats. Server timestamps and independent freshness/recentness prevent spoofed clocks
  or a restart from making a stale PC appear active. Disabled presence collects no new PC/phone signals.
- Existing interrupt, notice and foreground-result paths publish durable items. Routing is recalculated
  from presence; delayed pending routes are cancelled on resolution. Defaults include quiet hours
  22:00–07:00, configurable timezone and delays, and deferred background notices after verified owner
  calls. Push-service acceptance, native handling, queued channel work and unknown effects are distinct.
- Resolution closes native cards/notifications and sends a resolved push. The PWA closes matching
  notifications, prevents out-of-order opening pushes from reviving resolved ids, and refreshes open
  Inbox cards without destroying another card's draft. Telegram retraction cancels queued messages or
  edits exact provider message ids, including a resolution racing delivery. A channel with an unknown
  provider outcome cannot be claimed retracted.
- Owner-authenticated handoff restores an existing thread without making a replacement session or
  granting approval authority. Native acceptance requires the controller lease and preserves active
  avatar task/voice history. A verified source owner call drains before the PC command is published.
  Electron shows the preserved transcript, binds the next agent request to that thread and opens the
  actual signed-in Live page. Phone history and typed deep links retrieve that same owner thread.
- "Review a call to me" uses the configured owner number and a fixed two-minute brief, no commitments,
  no private conversation contents and no voicemail. Existing digest-bound approval/checklist/policy/
  calling-window/budget controls remain required; the continuity code never resolves its own approval.

## Evidence and limits

The focused suites exercise the state machine, TTL/clock rollback/restart, routing/quiet-hour boundaries,
resolution races, strict controller/fabric boundaries, exact thread restoration, source-call draining,
channel edits, legacy foreground runs and owner-call approval binding. Real encrypted fabric transport
and WebCrypto decisions are used. Provider deliveries/calls in these source tests are fixtures.

`scripts/panini-continuity-smoke.mjs` runs real Electron, the real gateway/backend and built Admin in an
isolated Windows profile. It observes native powerMonitor (including genuine long idle time), saves
real routing and timezone settings at 1440/412 px, restores the original transcript/agent binding and
opens the real Live page. It invokes no SIP dial. `scripts/panini-phone-smoke.mjs` additionally covers
fresh encrypted phone presence and the original-thread handoff command, alongside existing signed
approvals, virtual passkey and notebook/call-preview operations. This is Android-sized Chromium;
physical Android/Wear OS is not qualified.

Initial integration failures were repaired: missing gateway PUT forwarding, delayed first heartbeat,
ambiguous select labeling, legacy runs without a brain run id, and incorrectly using the code terminal
viewer for continuity. The final native smoke uses the actual Live page. Earlier failed fixture logs
are not passing evidence.

## Remaining requirements

Automatic urgent escalation is **not implemented**. It still needs explicit immutable owner/trunk-bound
standing authorization, current policy enforcement, rates/budgets, timers, revocation and cancellation.
Spoken avatar/call prompts and voice handoff commands are also not implemented. Live Android/Wear OS
push/action/retraction within five seconds, preferred-channel qualification and real SIP owner calls
remain pending. V2-24 is In progress; full LLD completion, production promotion and Dot equivalence are
not certified. Red-team remains disabled and deferred, and PANINI_PRESENCE defaults off.
