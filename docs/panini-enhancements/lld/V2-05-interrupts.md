# V2-05: Interrupts (durable approvals, questions, choices, reviews, takeovers)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 1 · P0 · M (4–5 days) |
| Depends on | V2-03 (decisions and levels), V2-04 (receipts) |
| Flag | `PANINI_V2_INTERRUPTS` (default `false`) |
| Status | Not started |

## 1. Outcome

Whenever PANINI needs the person (approve an action, answer a question, choose a target, review a draft,
take over for a sign-in), it opens one durable interrupt that every authorized surface sees and any of them
can resolve. Decisions bind to the exact action, the first decision wins, retries are safe, and background
work waits for the person instead of failing.

## 2. Scope and non-goals

- In scope: interrupt store and service, five kinds, levels and surface rules, expiry, first-decision-wins,
  restore-before-decide, edit-before-approve for reviews, previews, v1 confirmation and review compatibility,
  background parking with pre-approved digests, LAN phone resolution of `tap` interrupts, API, events.
- Not in scope: push delivery and device signatures (V2-14/15 add them to the `decided_by` field this item
  defines), passkeys (`VERIFY` is resolvable only at the PC until V2-15), AG-UI projection (V2-07).

## 3. Current state

| Mechanism | Where | Limit |
| --- | --- | --- |
| Foreground confirmation | `TaskContext.confirm` (`coordinator.py:219`): nonce + action digest, 60 s, future in `session.confirmations`; shell polls `GET/POST /sessions/{id}/confirmations/{cid}` with `ConfirmationDecision{nonce, digest, decision}` | In memory, shell only, lost on restart |
| Clarification | Planner `reply` + `clarification=true` → `session.pending`; `TARGET_AMBIGUOUS` → `task.choices` and `resume_choice_id` | Session-bound, avatar only |
| Agent question | `ask_user` ends the run with outcome `clarification` | The run ends; context is rebuilt on the next turn |
| Background review | `tasks.open_review/reviews/resolve` (`harness/tasks.py:246-292`), `rerun | dismiss`, 24 h | Only after an unknown outcome; cannot ask before acting |
| Background confirmation | `BackgroundContext.confirm` raises `NEEDS_PERSON` (`harness/runtime.py`) | Background work cannot get approval at all |
| Phone | `mobile.py` `/api/reviews/{id}` resolves reviews | No foreground approvals |

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/client/page-review-decision.ts` + `PageReviewCard.tsx` | **Restore before decide**: "a previous save may have committed even if its response never arrived"; the card checks for an existing receipt before showing buttons, and decisions are idempotent per tool call | `GET` before `resolve`; resolving with the same decision twice returns the first result; buttons disabled until the restore check returns |
| OpenDots `src/shared/page-review.ts` (`review_space_page` tool: draft schema, "call once, then wait") | Reviews as a first-class tool | `review` interrupts carry a schema'd draft; agents get a `request_review` tool |
| AG-UI interrupts (`docs.ag-ui.com/concepts/interrupts`) | `{id, reason, message, toolCallId, responseSchema, expiresAt}`; resume entries `{interruptId, status: resolved|cancelled, payload}`; every open interrupt must be addressed; idempotent replay; reject after expiry | Field names and semantics adopted so V2-07 maps one to one |
| GPT-Live SIP accept/reject ("first decision wins; competing decisions return `decision_already_made`") | Concurrency rule | Same rule and error code across surfaces |
| OpenMuse reviewed actions (content hash, expiry, atomic claim, outcome unknown) | Durable review semantics | Interrupt rows with digest, expiry and atomic resolution |
| OpenMuse UX ("review screens that show the exact recipient and effect") | Previews | `preview` builders per effect class (§5.6) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/interrupts/store.py` | SQLite persistence, atomic resolution |
| `backend/interrupts/service.py` | `Interrupts`: open, wait, resolve, cancel, expire; fan-out subscribers |
| `backend/interrupts/previews.py` | Preview builders per effect class |
| `backend/interrupts/compat.py` | v1 confirmation and review adapters |
| `backend/routes/interrupts.py` | Shell, console and phone routes |
| `backend/harness/migrations/0006_interrupts.sql` | Table |
| `backend/panini/coordinator.py` | `TaskContext.confirm` → interrupts when the flag is on |
| `backend/harness/runtime.py` | `BackgroundContext.confirm` → park instead of `NEEDS_PERSON` |
| `backend/harness/tasks.py` | New task status `waiting_input`; reviews become `review` interrupts |
| Shell `src/renderer/` | Confirmation UI reads interrupts (unchanged look; adds question and choice kinds) |
| `backend/harness/mobile_app/` | Lists open `tap` interrupts with Approve/Decline |

### 5.2 Model

```python
@dataclass
class InterruptSpec:
    kind: Literal["approval", "question", "choice", "review", "takeover"]
    reason: str                      # tool_call | clarification | target_choice | draft_review | plan_approval |
                                     # unknown_outcome | sign_in_needed | human_verification
    title: str                       # "Send email"
    message: str                     # "Send this email to a@b.com?"
    preview: dict                    # see §5.6
    action: dict | None              # canonical action; digest computed from it
    response_schema: dict | None     # approval: {"approved": bool}; question: {"answer": str}; choice: {"choice_id": enum}
    level: ApprovalLevel
    ttl_s: float
    tool_call_id: str | None = None  # AG-UI binding
```

Default TTLs: avatar foreground approval 60 s (v1), other foreground 10 min, background 24 h, question 24 h,
takeover 30 min. A foreground run waiting for an interrupt keeps its lease renewed by the shell as today; Stop
cancels the interrupt and the run.

### 5.3 Storage

```sql
CREATE TABLE IF NOT EXISTS interrupts(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, thread_id TEXT, task_id TEXT, kind TEXT NOT NULL,
  reason TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, preview TEXT NOT NULL,
  action TEXT, action_digest TEXT, response_schema TEXT, level TEXT NOT NULL, nonce TEXT NOT NULL,
  tool_call_id TEXT, created_at REAL NOT NULL, expires_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',             -- open | resolved | cancelled | expired
  decision TEXT, decided_by TEXT, decided_at REAL);
CREATE INDEX IF NOT EXISTS interrupts_open ON interrupts(status, expires_at);
CREATE INDEX IF NOT EXISTS interrupts_run ON interrupts(run_id);
```

### 5.4 Resolution rules

```python
async def resolve(self, interrupt_id, *, nonce, action_digest, status, payload, decided_by) -> Interrupt:
    # 1. load; unknown → 404
    # 2. if status already resolved/cancelled: same (status, payload digest) → return existing (idempotent);
    #    different → Fault("DECISION_ALREADY_MADE", 409)
    # 3. expired (now ≥ expires_at) → mark expired → Fault("INTERRUPT_EXPIRED", 410)
    # 4. nonce or action_digest mismatch → Fault("INTERRUPT_CHANGED", 409)   (I3)
    # 5. level check against decided_by: tap → any authorized surface; verify → shell/console on the PC or
    #    a passkey assertion (V2-15); local → the shell controller holding the lease
    # 6. principal check: the run's own principal may not resolve if it is background/external (I8);
    #    voice and channel principals can never resolve approval/review/takeover (I4); they may answer a
    #    question/choice only as the owner's turn on the thread the interrupt is bound to
    # 7. payload validated against response_schema
    # 8. atomic: UPDATE … SET status=?, decision=?, decided_by=?, decided_at=? WHERE id=? AND status='open'
    #    → rowcount 0 means another surface won: re-read and apply rule 2
    # 9. publish interrupt.resolved; wake waiters
```

`decided_by = {surface, principal_kind, device_id, method: "tap"|"local"|"passkey", signature?}`.

**Edit before approve** (reviews only): when `response_schema` allows edits (for example an email body or a
page draft), the decision payload may contain edited fields. The service recomputes the action from the
edited fields, stores the new digest in `decision.edited_digest`, and the receipt records
`edited_by_owner: true`. The tool runs the edited action, never the original.

### 5.5 Background parking

1. A background run reaches an effect whose decision is `park` (V2-03).
2. `BackgroundContext.interrupt(spec)` opens the interrupt (24 h), checkpoints the task
   (`tasks.checkpoint`), sets task status `waiting_input` with `waiting_on: interrupt_id`, and ends the
   current attempt **without** counting it as a failure.
3. On approval, the service requeues the task with `pre_approved: [action_digest]`.
4. The re-run proceeds normally. Earlier effects are skipped by ledger replay (V2-04 §5.4); when it reaches an
   action whose digest is pre-approved, policy returns `allow` for that digest once.
5. If the re-run takes a different path, any new effect parks again (no blanket approval).
6. V2-06 replaces step 4's re-run with true checkpoint resume to save tokens; the safety rule is unchanged.

### 5.6 Previews

| Effect | Preview fields |
| --- | --- |
| communicate | channel, recipients (all of them), subject, full text, attachments (names, sizes) |
| transact | site/app, item, amount and currency when detectable, account shown on screen |
| destroy | what (paths, counts, item names), recoverable or not |
| share_out | destination (URL, agent, server), data summary, file names and sizes |
| account | site, what changes (sign-in, setting, permission) |
| install | package and source |
| any browser/desktop effect | highlighted screenshot artifact of the target (V2-10/V2-26), URL or window title |

Previews come from the hand's observation and the policy `Decision.preview`. They never include credentials.

### 5.7 API and events

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /api/panini/interrupts?status=open` | shell token | Open interrupts for the shell (all threads) |
| `POST /api/panini/interrupts/{id}/resolve` | shell controller | Resolve (`method: local`) |
| `GET/POST /api/harness/interrupts…` | console session | Same for the console (`method: tap`, or `local` from loopback) |
| `GET/POST /api/interrupts…` on the phone listener | device cookie | `tap` interrupts only (until V2-15) |
| `GET/POST /sessions/{sid}/confirmations/{cid}` (v1) | shell | Compatibility: maps to the interrupt with the same id |

Request body: `{command_id, nonce, action_digest, status: "resolved"|"cancelled", payload}` (v1's
`ConfirmationDecision{nonce, digest, decision}` is translated: `approve` → resolved `{approved: true}`,
`reject` → resolved `{approved: false}`).

Events: `interrupt.opened` (interrupt view without nonce for observers), `interrupt.resolved`,
`interrupt.expired`. The shell keeps listening to `task.awaiting_confirmation` until it switches.

### 5.8 Agent tools

| Tool | Kind | Notes |
| --- | --- | --- |
| `ask_user(question)` (existing) | question | Foreground: the run waits (it no longer ends) up to the TTL; on timeout the run ends with `clarification` as in v1 |
| `request_review(title, draft, schema)` | review | For drafts the person should see before saving or sending (V2-19 pages, V2-20 call briefs, V2-22 skills) |
| `choose(question, options[])` | choice | Replaces `TARGET_AMBIGUOUS` round trips when the brain owns the choice |
| `request_takeover(reason)` | takeover | Sign-in, CAPTCHA, payment entry: the person acts, then resolves "done" |

### 5.9 Failure modes

| Failure | Behaviour |
| --- | --- |
| Restart with open foreground interrupts | Their runs were cancelled with the process; interrupts are marked `cancelled` with reason "PANINI restarted" (no orphaned approvals) |
| Restart with parked background interrupts | Remain open; the task stays `waiting_input` |
| Two surfaces approve and decline at once | One wins (rule 8); the other gets `DECISION_ALREADY_MADE` and refreshes |
| Clock skew on phone | Expiry is checked on the PC only |

## 6. Implementation steps

1. Store + service + migration; resolution rules with tests (rules 1–9).
2. `TaskContext.confirm` and the v1 confirmation routes on interrupts (behind the flag); shell unchanged.
3. Previews for communicate, transact, destroy, share_out; screenshot attachment hook.
4. `ask_user` waits in foreground; `choose`, `request_review`, `request_takeover` tools; shell renders
   question/choice/review/takeover cards (simple first versions).
5. Background parking: `waiting_input` status, pre-approved digests, worker requeue; reviews migrated.
6. Console and LAN phone routes; the phone app lists and resolves `tap` interrupts.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Rules 1–9, including concurrent resolution (two tasks racing) | `backend/tests/interrupts/test_resolve.py` |
| Unit | Edit-before-approve recomputes digest; original never runs | `backend/tests/interrupts/test_review_edit.py` |
| Invariant | I3 (digest mismatch rejected), I4 (voice/channel principals cannot resolve), I8 | `backend/tests/interrupts/test_invariants.py` |
| Integration | v1 shell confirmation flow unchanged end to end (existing tests pass with the flag on) | `backend/tests/panini/` |
| Integration | Background task parks, approval from the LAN phone API, re-run completes, earlier effect not repeated | `backend/tests/harness/test_parking.py` |
| Eval | `background.needs-approval` case passes on v2 (fails on v1 by design) | `evals/cases/background/` |

## 8. Acceptance criteria

- [ ] With the flag on, all existing confirmation tests pass and the shell looks the same.
- [ ] A background task needing approval parks, is approved from the phone, and completes without repeating
      earlier effects.
- [ ] I3, I4, I8 tests green.
- [ ] Interrupts survive a restart as specified in §5.9.

## 9. Rollout and rollback

Default on at the Phase 1 gate. Rollback: flag off; open interrupts are cancelled at startup.

## 10. Open questions

- Should a foreground approval that times out on the PC fall through to the phone automatically? This is
  presence routing (V2-24); until then, foreground TTL stays 60 s on the avatar.
