# V2-24: Continuity and presence

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · M (5 days) |
| Depends on | V2-14 (fabric), V2-15 (phone app), V2-16 (phone line), V2-17 (channels) |
| Flag | `PANINI_PRESENCE` (default `false`) |
| Status | In progress |

## 1. Outcome

PANINI knows roughly where you are (at the PC, away with your phone, on a call) and reaches you there:
speaking on the avatar when you are at the desk, a push when you are away, a message in your preferred
channel when the phone app is silent, and (opt-in) a phone call for urgent approvals. Conversations move with
you: start on a call, continue on the PC with the live view up, approve from your watch.

## 2. Scope and non-goals

- In scope: presence signals and state, routing of outputs, notices and interrupts, escalation and
  retraction, handoff commands, watch-friendly notifications, "call me" escalation (via V2-20), Admin rules.
- Not in scope: location tracking (native app later), multi-person households.

## 3. Current state

- The shell has "avatar on" and background (tray) modes, tray status and Windows notifications; the avatar
  appears only when it needs you (`README.md`).
- Foreground confirmations expire after 60 s on the avatar; background results go to the inbox and notices.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots continuous conversation (one thread across text, calls and Slack; call minimized into chat; receipts in the timeline) | One conversation across surfaces | Thread bindings (V2-06) + handoff commands |
| OpenMuse "background updates that link to the saved task" | Notifications that open the exact item | Deep links to threads and interrupts |
| Electron `powerMonitor` (`getSystemIdleTime`, `lock-screen`, `unlock-screen`, `suspend`, `resume`) | PC presence signals | Shell heartbeats |

## 5. Design

### 5.1 Signals and state

| Signal | Source | Freshness |
| --- | --- | --- |
| PC active / idle seconds / locked / suspended | Shell heartbeat every 15 s (`powerMonitor`) | 30 s |
| Avatar visible / minimized to tray | Shell | event |
| Phone app foreground / background / last seen | Phone app heartbeat over the fabric (foreground every 30 s) | 60 s |
| On a PANINI call | Telephony (V2-16/20) | event |
| Recent channel activity | Channel manager | 10 min |

```text
state = on_call            if a PANINI call is active
      | at_pc            if PC active (idle < 120 s) and unlocked
      | near_pc          if PC unlocked and idle < 15 min
      | away_with_phone  if phone seen < 10 min or push delivered recently
      | away             otherwise
```

`presence(surface, state, ts)` rows are kept in memory with the last value persisted for restart.

### 5.2 Routing rules (defaults, editable in Admin)

| Item | at_pc | near_pc | away_with_phone | away | on_call |
| --- | --- | --- | --- | --- | --- |
| Interrupt `tap` | Avatar card (+ voice prompt) | Avatar card + push after 30 s | Push | Push + preferred channel note | Spoken "sent to your phone" + push |
| Interrupt `verify` | Avatar card (passkey or PC) | Push + avatar | Push (passkey) | Push | Push |
| Result of a foreground run | Avatar (voice) | Avatar + push if not seen in 2 min | Push | Preferred channel | Spoken |
| Watch alert / background result | Inbox + toast | Inbox + toast | Push | Preferred channel | After the call |
| Urgent (opt-in: interrupts marked urgent, failures of responsibilities) | Avatar | Push | Push, then call after 5 min (V2-20 owner call) | Call | — |

When an interrupt is resolved on any surface, PANINI retracts it everywhere: removes the avatar card, sends a
"resolved" push that closes the notification (service worker `getNotifications` + `close`), and edits the
channel note.

### 5.3 Handoff

| Command | Effect |
| --- | --- |
| "Continue on my PC" (phone, call) | The shell opens the thread and the live view; the avatar says "Continuing from your phone" |
| "Send it to my phone" (avatar, call) | Push with a deep link to the thread or result |
| "Call me about it" (avatar, phone app) | Owner call (V2-20 with the owner as callee and a fixed brief) |
| Ending a call | The call receipt shows on the avatar and the phone (V2-16) |

### 5.4 Watches

Wear OS mirrors Android notifications including action buttons, so `tap` approvals work from the watch.
Apple Watch mirrors iPhone notifications but web push on iOS has no action buttons, so the watch shows the
request and the decision happens on the phone. No watch app in v2.

## 6. Implementation steps

1. Shell and phone heartbeats; presence state machine.
2. Routing table and Admin editor; integrate interrupts, notices and results.
3. Retraction across avatar, push and channels.
4. Handoff commands and deep links.
5. Urgent escalation with owner calls (needs V2-20).

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | State machine from synthetic signals | `backend/tests/fabric/test_presence.py` |
| Unit | Routing table per state and item; escalation timers; retraction | `backend/tests/fabric/test_routing.py` |
| Live | Lock the PC → approval arrives as push; approve on the watch (Wear OS) → avatar card disappears | evidence log |

## 8. Acceptance criteria

- [ ] With the PC locked, approvals arrive on the phone within 5 s; resolving anywhere clears them everywhere.
- [x] "Continue on my PC" from the phone opens the thread on the avatar.
- [ ] Urgent escalation calls the owner only when opted in.

## 9. Rollout and rollback

Flag on after step 3. Rollback: flag off → v1 behaviour (avatar and inbox) plus V2-15 pushes for every
interrupt.

## 10. Open questions

- Quiet hours (no pushes at night except urgent)? Recommendation: yes, default 22:00–07:00 local, editable.

## Implementation evidence — 2026-10-05

Presence now persists server-timestamped PC, paired-phone, accepted-push and authenticated channel
signals. PC/phone freshness expires at 30/60 seconds; a restart never revives a fresh PC signal.
The shell sends native powerMonitor signals on lock/unlock/suspend/resume and every 15 seconds;
the phone sends foreground signals through the encrypted fabric every 30 seconds.

Durable routes cover approvals, notices and foreground results with editable destinations/delays,
timezone-aware quiet hours, source resolution retractions and explicit transport/PC handling receipts.
Ambiguous sends are not replayed. Channel destinations must be existing authenticated owner private
threads. Telegram notes are cancelled/edited on resolution, including a resolution racing a send.
The phone closes resolved notifications and retains bounded tombstones against delayed opening pushes.

Phone conversation history and handoff preserve the actual thread id. The real Electron smoke restores
the same agent binding and original transcript without creating another session, then opens the actual
owner-authenticated Live page. Active avatar tasks/voice sessions prevent retargeting; a verified owner
source call is ended/drained before its PC handoff is queued. The encrypted Android-sized browser smoke
separately covers the phone command. Physical-device handoff remains unqualified.

"Review a call to me" creates one fixed, digest-bound V2-20 brief for the configured owner number,
with no commitments, no conversation disclosure, a two-minute bound and no voicemail. It requires the
existing owner approval, trunk checklist, calling windows, policy and budget checks.

The next increment adds a separate, revocable seven-day standing grant. It requires a local PC decision
over the exact owner number, fixed two-minute no-voicemail brief, one-call-per-day and three-call-total
limits. Only internally marked urgent responsibility failure notices enter the timer. An unresolved item
waits five minutes and requires away presence, current trunk identity, outbound checklist, policy and
calling hours before one durable call attempt. Resolution and revocation cancel pending attempts; a
rotated trunk revokes the grant. The call service rechecks the grant immediately before provider
dispatch and during an active call. The Admin panel exposes review and revoke controls. The tests use a
fixture SIP provider; no owner number was dialed in this development increment.

The PC handoff now requests a fixed "Continuing from your phone" cue through the controller-lease-scoped
Windows SAPI speech path. The renderer plays its bounded WAV after restoring the original thread and
offers Stop while it is playing. It skips the cue when microphone audio is active; failure leaves the
restored conversation available. The source Electron smoke observed a real WAV and resolved playback.
Physical speaker output and voice-initiated handoff remain unqualified.

Local dictation now recognizes the complete phrases "Send it to my phone" and "Call me about it" when
an existing phone conversation is active and the composer is empty. It invokes the same authenticated
handoff/review actions as the visible buttons. Other transcriptions remain editable drafts. This is
record-then-transcribe control, not a live call voice command; actual microphone and provider qualification
remain pending.

Remaining: spoken call prompts and live voice handoff commands, actual Android/Wear OS
push/action/retraction within five seconds, live preferred channel and real owner SIP qualification.
Urgent call behavior on a physical phone and live provider remains unqualified. The flag remains off
by default. Red-team remains disabled and deferred. See
[qualification record](../qualification-continuity-2026-10-05.md).
