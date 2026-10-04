# V2-20: Outbound calls on your behalf

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · L (1.5 weeks) |
| Depends on | V2-16 (telephony foundation), V2-13 (callee-injection cases), V2-15 (approvals) |
| Flag | `PANINI_OUTBOUND_CALLS` (default `false`) |
| Status | In progress: reviewed SIP lifecycle, scheduled calling windows, owner controls and usage receipts built; quote-cited interpretations built; real calls, keypad automation and live outcome qualification pending |
| Decision | Register D7 (call scope); compliance checklist signed by the owner before enabling |

## 1. Outcome

"PANINI, call the dentist and move my appointment to next week." PANINI drafts a call brief (who, why, what
it may say and agree to), you approve it on your phone, PANINI places a real phone call, says up front that
it is an AI calling for you, navigates phone menus, handles the conversation within the brief, lets you
listen in or take over, and reports the outcome with a transcript.

## 2. Scope and non-goals

- In scope: call briefs and their review, number validation and blocking, disclosure, GPT-Live outbound SIP
  sessions, sideband handling (ringing, answered, failed, DTMF in and out), third-party privacy mode for the
  brain, mid-call owner approvals ("hold"), live monitoring, take over (refer to your phone), outcomes and
  receipts, limits and the compliance checklist.
- Not in scope: marketing or bulk calling (never), recording audio by default, calls to emergency or premium
  numbers (blocked), automatic retries without approval.

## 3. State when this LLD was written

V2-16 provides accepted inbound calls, `PhoneConnection` (attach, DTMF events), call records, budgets and
receipts. No outbound capability.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| GPT-Live SIP guide, outbound section | `POST /v1/live/sessions` with `transport.type: "sip"`, `transport.destination` (E.164), `transport.trunk.provider_url` (`sips:…:5061`, no credentials in the URL), `transport.trunk.auth` (`type: "digest"`, username, password), `transport.trunk.caller_number`; attach the sideband immediately (only the last 3 seconds of events are replayed); progress events `transport.ringing`, `transport.answered`, `transport.failed` (with `error.code`/`message`); `transport.dtmf.send`; ringing timeout 3 minutes; connected call 2 hours; request body ≤ 1 MiB | `backend/telephony/outbound.py` |
| PANINI A2A privacy rule (`harness/runtime.py`: answers going to another agent exclude the person's personal information; `private=False` turns memory off) | Third-party context | The brain runs calls with `private=False` plus the brief's allowed facts only |
| OpenDots voice lifecycle and receipts (V2-16 harvest) | Hangup on every failure path, receipts | Reused |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/telephony/outbound.py` | `OutboundCalls`: brief → approval → session create → attach → lifecycle |
| `backend/telephony/brief.py` | Brief schema, validation, rendering for review |
| `backend/telephony/numbers.py` | E.164 parsing (`phonenumbers`, Apache-2.0), region/timezone, block rules |
| `backend/brain/control_tools.py` | `place_call(brief)` tool (communicate) |
| Admin `src/pages/Phone.tsx` | Outbound settings, compliance checklist, trunk credentials (DPAPI) |

### 5.2 Call brief

```python
class CallBrief(StrictModel):
    callee_name: str                      # "Smile Dental"
    number: str                           # E.164
    number_source: Literal["owner", "contact", "web", "page", "email"]
    purpose: str                          # "Move Tuesday 3 pm cleaning to next week"
    owner_first_name: str
    may_disclose: list[str]               # ["owner's first name", "appointment date", "phone number for callback"]
    must_not_disclose: list[str] = ["payment card details", "government ID numbers", "home address", "passwords or codes"]
    acceptable_outcomes: list[str]        # ["any weekday next week after 2 pm"]
    may_commit: list[str] = []            # ["confirm a new appointment time"]; payments are never in this list
    fallback: Literal["take_message", "ask_callback", "end_politely"] = "ask_callback"
    voicemail: Literal["leave_message", "hang_up"] = "leave_message"
    language: str = "en"
    max_minutes: int = Field(default=10, ge=1, le=30)
```

The agent builds the brief from the conversation and calls `place_call(brief)`. Policy classifies it as
`communicate` (and `transact` when `may_commit` includes bookings); the interrupt is a **review** showing the
whole brief, editable before approval. Level: `tap` for numbers from the owner or contacts, `verify` for
numbers found on the web or in email (taint) and for international calls.

### 5.3 Number rules (`numbers.py`)

| Rule | Behaviour |
| --- | --- |
| Emergency and service numbers (112, 911, 999, 000, 100–102, 108, 110, 119, 120, and the region's list from `phonenumbers` short-number metadata) | Blocked; PANINI tells the person to call themselves |
| Premium-rate, shared-cost, short codes | Blocked |
| Calling hours | 09:00–20:00 in the callee's timezone (from the number's region); outside → schedule for the next window (a background task with the approved brief) or ask |
| Do-not-call | Numbers marked by the owner or where a callee asked not to be called again (§5.6) are blocked |
| Daily cap | `PANINI_OUTBOUND_PER_DAY` (default 5) |

### 5.4 Session

```json
POST /v1/live/sessions
{"session": {"type": "live", "model": "<VOICE_MODEL>", "instructions": "<outbound instructions + brief>",
             "audio": {"output": {"voice": "<persona voice>"}}, "delegation": {"type": "client"}},
 "transport": {"type": "sip", "destination": "+14155550123",
               "trunk": {"provider_url": "sips:sip.provider.example:5061",
                         "auth": {"type": "digest", "username": "…", "password": "…"},
                         "caller_number": "+14155550100"}}}
```

Attach the sideband immediately after creation. Events: `transport.ringing` → status `ringing`;
`transport.answered` → `answered`; `transport.failed` → `failed` with the provider's error code (not its
message) in the record.

### 5.5 Disclosure and behaviour (instructions)

- First sentence, always, before anything else: "Hello, this is PANINI, an AI assistant calling on behalf of
  <owner_first_name>." The engine checks the first output transcript; if it does not contain the disclosure
  within 10 seconds of answer, it hangs up (fail closed).
- Stay within the brief; share only `may_disclose`; never share `must_not_disclose`; never agree to payments.
- Anything outside the brief (a deposit, a different service, a new commitment): say "Let me check with
  <owner> — one moment" and ask the owner (below); if no answer within 60 seconds, use the fallback.
- If asked "Are you a robot / AI?", answer truthfully.
- If the callee asks not to be called again, apologize, confirm, end the call, and record do-not-call.
- Phone menus: use a supported provider/hosted tool to select menu options that match the brief; never
  enter account numbers or PINs. `transport.dtmf.send` is a notification, not a client command. The
  supported sending mechanism and real menu qualification remain pending.
- Voicemail: leave a short message with the purpose and the callback number only if `voicemail=leave_message`.

### 5.6 Brain during the call

- Delegated requests run with principal `Principal(kind="external_agent", surface="phone_line")`-equivalent
  restrictions: `private=False` (no memories), tools limited to read-only lookups the brief needs (calendar
  free/busy if granted, the conversation's facts), no effects except through owner approvals.
- **Owner approvals mid-call:** a `choice` or `approval` interrupt to the phone app ("The clinic offers
  Thursday 4 pm with a USD 20 deposit. Accept?"); the voice keeps the callee on hold politely.
- **Monitoring:** live transcript on the phone app and avatar; buttons **Take over** (`POST …/refer
  {target_uri: "tel:<owner number>"}`), **End call** (`…/hangup`), **Add note** (a short instruction delivered
  to the voice model through the delegation channel, for example "Ask for afternoon slots only").

### 5.7 Outcome and receipt

```json
{"status": "achieved" | "partial" | "declined" | "voicemail" | "no_answer" | "busy" | "failed",
 "summary": "Moved the cleaning to Thursday 14 Oct, 2:30 pm.",
 "commitments": ["Appointment Thu 14 Oct 2:30 pm"],
 "follow_ups": ["Add to calendar"],
 "duration_s": 212, "cost_usd": 0.19, "transcript_artifact_id": "…"}
```

The receipt (ledger) links the approved brief digest, the call record and the transcript artifact. The
summary is produced from the transcript as untrusted data and marks anything not explicitly confirmed by the
callee as unconfirmed. Follow-ups become suggestions (for example a calendar entry needing its own approval).

### 5.8 Compliance checklist (Admin, signed once and stored with a timestamp)

- I will use outbound calls only for personal tasks (bookings, enquiries, reminders), not marketing.
- PANINI always discloses that it is an AI calling on my behalf.
- I am responsible for local rules on automated calls and recording; recording is off.
- Calls happen only within calling hours and never to blocked numbers.
- I approve every call brief.

## 6. Implementation steps

1. `numbers.py` with block rules and hours; tests with numbers from several regions.
2. `CallBrief`, `place_call` tool, review rendering and edit-before-approve.
3. Session create + immediate attach; progress events; limits; hangup on every failure path.
4. Disclosure check; outbound instructions; DTMF send.
5. Third-party brain mode; mid-call owner approvals with hold; monitoring controls (take over, end, note).
6. Outcomes, receipts, do-not-call; compliance checklist gate in Admin.
7. Live test calls to a test number you own.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Number blocking (emergency, premium, short codes), hours scheduling, daily cap | `backend/tests/telephony/test_numbers.py` |
| Unit | Brief validation; `must_not_disclose` defaults cannot be removed; payments never in `may_commit` | `backend/tests/telephony/test_brief.py` |
| Integration | Fake API: create → ringing → answered → disclosure seen → delegated lookup → end → receipt | `backend/tests/telephony/test_outbound_flow.py` |
| Integration | Missing disclosure within 10 s → hangup | same |
| Integration | Mid-call approval: approved → continues; timeout → fallback | same |
| Eval | Red-team: callee asks for card number / tries to change the goal → refused | `evals/redteam/call_*` |
| Live | Call your own second phone with a test brief; check disclosure, DTMF menu (test IVR), take over | evidence log |

## 8. Acceptance criteria

- [ ] Compliance checklist signed; flag refuses to enable otherwise.
- [ ] Live test: disclosure spoken first; brief respected; outcome receipt correct.
- [x] Emergency and premium numbers impossible to call (tests).
- [ ] Take over transfers the call to your phone.

## 9. Rollout and rollback

Opt-in after the checklist; start with numbers from the owner and contacts only (`verify` for others).
Rollback: flag off; scheduled calls are cancelled.

## 10. Open questions

- Let PANINI call you (the owner) for urgent approvals when you are away from the PC and the phone app is not
  answered? This is presence escalation (V2-24) using this item; recommendation: opt-in, owner number only.

## 11. Development increment — 2026-10-04

Migration 0026 persists outbound settings, checklist acknowledgement, reviewed briefs, single-use dispatch
attempts and do-not-call numbers. Credentials use the existing Windows DPAPI vault; APIs and validation
failures expose no credential values. Changing trunk/owner setup cancels prior briefs and requires renewed
acknowledgement. The raw feature flag does not bypass the effective checklist/credential dispatch gate.

Call briefs are strictly bounded and preserve mandatory exclusions. This first increment supports English
and a finite set of appointment/reservation/callback commitments. libphonenumber rejects short, emergency,
premium, shared-cost and service destinations. Calling hours require every possible metadata timezone to
be inside 09:00–20:00. Outside-hours approvals cancel without dialing; the owner can review a new brief in
the next window. Automatic scheduling is still pending.

The owner reviews the full brief through existing durable interrupts. Edits replace the nonce and digest.
Policy, current approval strength, exact persisted brief digest, pause, flag, number blocks and daily attempt
limits are checked again immediately before dispatch. Owner/contacts start at tap; externally sourced or
international numbers require verify, raised further by policy. Every provider create attempt consumes the
brief once. Unknown creation, interruption and restart never automatically create another call.

The adapter creates a Live SIP session and attaches immediately. It tracks ringing/answering/failure,
checks the first output transcript against the exact AI disclosure and hangs up on a mismatch or ten-second
deadline. Ringing, connected duration, pause/flag revocation, sideband failure and shutdown are bounded.
Unknown hangup/finalization remains explicit. The voice receives only the reviewed facts, no owner memories,
connectors or desktop tools. Additional requests use owner interrupts; payment/credential requests are
refused. Read-only brain lookups and more complete mid-call outcome interpretation remain pending.

Admin Phone line adds setup/checklist, editable briefs, end/takeover/note controls, transcript, receipts and
number blocking. The paired Android PWA shares the outbound monitor, end/transfer controls and owner notes.
Takeover sends a refer to the configured owner number; acceptance by the provider is not claimed as proven
phone takeover. Receipts link the approved digest and untrusted transcript; summaries mark achievement,
commitments and costs unconfirmed rather than fabricating them.

The current official SIP documentation calls `transport.dtmf.send` a notification from a hosted tool, not
a client command. Keypad/IVR automation is therefore pending a supported implementation and qualification.
Sources: [SIP guide](https://developers.openai.com/api/docs/guides/voice-sip),
[Create session](https://developers.openai.com/api/reference/resources/live/methods/create),
[server controls](https://developers.openai.com/api/docs/guides/voice-server-controls).

Fake-provider integration and real browser checks are development evidence. No real SIP call, audible
disclosure, physical Android device, transfer completion or production qualification is claimed.
Red-team work remains disabled and deferred by the owner.

## 12. Scheduled calling and usage increment — 2026-10-04

Migration 0027 adds one durable schedule to the reviewed brief. The owner or owner agent can select an
explicit ISO time with an offset, at most seven days ahead, or the next permitted calling window. The
exact computed UTC time, every possible destination timezone and the latest start (at most fifteen
minutes later, never past any 20:00 cutoff) are included in the approval digest and preview. Changing
either the brief or time replaces its approval. The review itself expires after at most one hour;
resolved approval remains bound to its original start window. Immediate calling remains the default.

Approved calls persist as scheduled until due. Restart can resume an unattempted approved call within
that same window. Missed windows cancel and notify; they never slide to a later day. The dispatcher
rechecks the exact brief/time digest, policy/approval strength, calling hours, setup, flags, pause,
do-not-call and daily attempt limit. Setup changes invalidate scheduled calls; pause/disable, blocks
and policy changes cancel them when observed. A busy line cannot extend the reviewed deadline.
Ambiguous provider creation remains single-use and is never retried. PC and paired PWA show reviewed
local time and provide pending-call cancellation. PANINI must be running during the reviewed window.

Receipts retain cumulative observed voice seconds and separately record final provider usage only
when session closure reports it. Final events arriving during shutdown are preserved. Invalid usage
never becomes billing evidence; USD cost remains unavailable without verified pricing/trunk charges.
Ringing watchdog expiry has a no-answer outcome; other provider failures keep only a bounded code,
never a provider message. Keypad notifications are observed and deduplicated without storing digits;
observing them does not implement or qualify outbound IVR tones.

Development checks use isolated fake SIP/voice and real storage, reviews, UI and ledger. Live audible
disclosure, SIP/IVR, actual takeover, interpreted task outcomes, approved read-only brain lookups,
physical Android and production release qualification remain open. See the
[qualification report](../qualification-outbound-scheduling-2026-10-04.md).


## 13. Quoted outcome interpretations — 2026-10-04

Migration 0028 links a separate interpretation to a completed answered call with monitored disclosure
and an intact, untrusted transcript artifact. The immutable call receipt is retained. A fresh model receives
only the reviewed brief and merged speaker turns: no tools, memory, owner connectors or follow-up effects.
Strict structured output is bounded to 3,000 output tokens, thirty seconds and one attempt per call; daily
interpretation attempts have a separate cap using `PANINI_OUTBOUND_PER_DAY`. Unknown or interrupted work
becomes unavailable, never automatically retried. Pending unattempted work can resume after restart.

Every finding must quote an exact callee utterance. Assistant statements, unknown goals and commitments
outside reviewed permissions are rejected. Achieved proposals must cover every reviewed outcome and contain
no explicitly unresolved, declined or voicemail finding. These deterministic checks establish quote matching
and structural consistency, **not semantic entailment or a real booking**. Results always remain model
interpretations, even after owner assessment. Fixed follow-ups are suggestions; they never run an action.

Optional Inbox review binds the source digest and exact report digest, uses the existing owner-only decision
rules, expires after one hour and records a separate linked assessment receipt. Expired reviews can reopen;
replayed decisions do not duplicate receipts. Accepting never updates a calendar, sends a message or grants
new call permissions. Admin and paired PWA show quotes, proposed status, assessment and suggestions. The PWA
restores current report state before asking for review; its shell cache includes the new module.

The 25 new deterministic checks, full repository checks and real browser/fake-provider flows are development
evidence. No live model outcome benchmark, actual booking, real audible SIP call or physical Android test is
claimed. V2-20 and production qualification remain open; red-team remains disabled and deferred. See the
[qualification report](../qualification-outbound-outcomes-2026-10-04.md).
