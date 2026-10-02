# V2-24: Continuity and presence

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · M (5 days) |
| Depends on | V2-14 (fabric), V2-15 (phone app), V2-16 (phone line), V2-17 (channels) |
| Flag | `PANINI_PRESENCE` (default `false`) |
| Status | Not started |

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
- [ ] "Continue on my PC" from the phone opens the thread on the avatar.
- [ ] Urgent escalation calls the owner only when opted in.

## 9. Rollout and rollback

Flag on after step 3. Rollback: flag off → v1 behaviour (avatar and inbox) plus V2-15 pushes for every
interrupt.

## 10. Open questions

- Quiet hours (no pushes at night except urgent)? Recommendation: yes, default 22:00–07:00 local, editable.
