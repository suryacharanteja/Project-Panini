# V2-20: Outbound calls on your behalf

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · L (1.5 weeks) |
| Depends on | V2-16 (telephony foundation), V2-13 (callee-injection cases), V2-15 (approvals) |
| Flag | `PANINI_OUTBOUND_CALLS` (default `false`) |
| Status | Not started |
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

## 3. Current state

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
- Phone menus: listen and use `transport.dtmf.send` with the digits that match the brief (for example
  "press 2 for appointments"); never enter account numbers or PINs.
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
- [ ] Emergency and premium numbers impossible to call (tests).
- [ ] Take over transfers the call to your phone.

## 9. Rollout and rollback

Opt-in after the checklist; start with numbers from the owner and contacts only (`verify` for others).
Rollback: flag off; scheduled calls are cancelled.

## 10. Open questions

- Let PANINI call you (the owner) for urgent approvals when you are away from the PC and the phone app is not
  answered? This is presence escalation (V2-24) using this item; recommendation: opt-in, owner number only.
