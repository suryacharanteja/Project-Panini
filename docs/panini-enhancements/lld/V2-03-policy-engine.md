# V2-03: Policy engine

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 1 · P0 · M (4–5 days) |
| Depends on | V2-02 |
| Flag | `PANINI_V2_POLICY` = `off` · `shadow` (default) · `enforce` |
| Status | Not started |

## 1. Outcome

Every tool call gets one deterministic decision: allow, ask (at an approval level), park (background), or
deny, with the effect class and plain-language reasons. Decisions depend on the tool, its concrete
arguments, who is asking (principal and surface), the persona's grants, and whether the run has read
untrusted content. Changing a grant or pausing PANINI stops the next effect of every running run.

## 2. Scope and non-goals

- In scope: effect classification (static and dynamic), grant model and store, profiles, surface tiers,
  hard ceilings, taint escalation, grant epochs and revocation, privileged probes, shadow mode, Admin
  permissions page, `approvals.py` compatibility façade.
- Not in scope: the interrupt mechanics (V2-05), guardian model review (V2-13), persona CRUD (V2-18 uses the
  grant model defined here).

## 3. Current state

`backend/capabilities/approvals.py` decides with two regexes:

- `RISKY_WORDS` (pay, purchase, buy, checkout, order, subscribe, donate, transfer, send, post, publish, tweet,
  reply, comment, share, apply, delete, remove, erase, unsubscribe, cancel subscription/order/account, sign in,
  log in, sign up, register, password, passcode, credit card, card number, cvv, confirm, accept, agree,
  authorize, install, download, upload, book, reserve, invite, connect, message, follow, save, submit
  application).
- `RISKY_COMMAND` (rm, del, rmdir, remove-item, format, git push/reset --hard/clean, curl, wget,
  invoke-webrequest/restmethod, iwr, irm, pip/npm/pnpm/yarn install, winget, choco, shutdown,
  restart-computer, reg, sudo, set-executionpolicy, ssh, scp, mklink, takeown, icacls).
- `PANINI_APPROVALS=risky` (default) asks only for risky actions; `all` asks for every effect.
- Callers: `browser_action_needs_approval`, `managed_browser_needs_approval`, `desktop_action_needs_approval`,
  `code_action_needs_approval`; connectors confirm every non-read-only tool; background runs refuse anything
  needing confirmation (`BackgroundContext.confirm` raises `NEEDS_PERSON`).

The result is binary (ask or not) with no notion of who asked, from where, or what was read before.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/server/dot-agent.ts` (`check()` every 100 ms; aborts the run when `paused`, research/memory permissions, Space access or skill delivery change) | Revocation by snapshot comparison during a run | Grant **epochs**: one counter bumped on any change; runs compare at every `check()` and are notified immediately through an event (I7) |
| OpenDots `src/server/computer-service.ts` (`allowed(id, kind, actor)` before and after the request, plus a 50 ms watcher that aborts in-flight requests when permission disappears) | Check before, during and after dispatch | `RunContext.check()` before dispatch and after return; hands with long calls register a cancel callback |
| OpenDots `src/shared/computer-types.ts` (per-Dot `enabled/browser/files/shell` booleans) | Simple, owner-understandable switches | Admin shows grants as per-persona switches per capability group, backed by the general grant model |
| OpenMuse reviewed actions (`PANINI-AGENTIC-DESIGN.md` §5: content hash, expiry, atomic claim) | Decisions bind to a digest of the exact action | `Decision.preview` and the canonical action digest feed the interrupt (V2-05) |
| Playwright MCP `--allowed-origins` / `--blocked-origins` | Origin allow/deny lists | Grant `conditions.domains` / `blocked_domains` checked by policy for navigate and every browser effect |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/policy/effects.py` | Static/dynamic classification; word and command classes (moved from `approvals.py`) |
| `backend/policy/grants.py` | Grant model, profiles, store, epochs |
| `backend/policy/engine.py` | `Policy.decide()` |
| `backend/policy/taint.py` | Labels and escalation rules |
| `backend/policy/probes.py` | Fixed classification probes hands may run (never model-callable) |
| `backend/policy/shadow.py` | Shadow comparison with v1 and its log |
| `backend/capabilities/approvals.py` | Façade: the four v1 functions call `effects.classify` + the profile so v1 paths keep working |
| `backend/harness/migrations/0004_policy.sql` | `grants`, `grant_epochs`, `policy_shadow` |
| `backend/routes/harness.py` | `/api/harness/policy/*` |
| Admin `src/pages/Permissions.tsx` | Profile, matrix, grants, pause-all |

### 5.2 Effect classification

Static class from the manifest; dynamic raise from arguments when `dynamic_effect: true`.

| Signal | Raises to |
| --- | --- |
| Words: pay, purchase, buy, checkout, place order, order now, subscribe, donate, transfer, book, reserve, card number, cvv | `transact` (card number, cvv, password, passcode fields → `credential`) |
| Words: send, post, publish, tweet, reply, comment, share, message, invite, follow, connect, submit application | `communicate` |
| Words: delete, remove, erase, unsubscribe, cancel (subscription/order) | `destroy` |
| Words: sign in, log in, sign up, register, authorize, cancel account, accept, agree | `account` |
| Words: install | `install` · download | `local_write` + taint `download` · upload | `share_out` · save, confirm | `submit` |
| Field type `password`, `autocomplete=one-time-code|cc-number|cc-csc|current-password|new-password` (probe) | `credential` |
| Commands: rm, del, rmdir, remove-item, format, `git reset --hard`, `git clean` | `destroy` |
| Commands: pip/npm/pnpm/yarn install/add, winget, choco | `install` |
| Commands: git push, ssh, scp | `share_out` |
| Commands: curl, wget, iwr, irm, invoke-* | `local_exec` + taint `network` |
| Commands: reg, set-executionpolicy, icacls, takeown, mklink, sudo, shutdown, restart-computer | `account` |
| Browser `navigate` to a domain outside `conditions.domains` (when set) or inside `blocked_domains` | deny |

The word lists keep v1's exact patterns (moved, not rewritten) so classification is a strict refinement of v1.

```python
def classify(spec: ToolSpec, args: dict, observation: dict | None) -> tuple[EffectClass, list[str]]:
    """observation: what the hand knows about the target (aria role/name, field type, label, href, command
    text). Returns the effect (never below spec.effect) and the reasons."""
```

### 5.3 Surfaces, profiles and the base matrix

Surface tiers: **local** (avatar, console on this PC), **remote** (phone app, phone line with a verified
caller), **channel** (Slack, Telegram, WhatsApp, email from an allow-listed owner), **external** (A2A,
MCP clients, unverified callers), **background** (schedules, watches, triggers, project tasks).

`balanced` profile (equals `PANINI_APPROVALS=risky`):

| Effect | local | remote | channel | external | background |
| --- | --- | --- | --- | --- | --- |
| observe, navigate | none | none | none | none | none |
| input | none | none | none | tap | none |
| local_write (inside roots) | none | tap | tap | tap | park |
| local_write (outside roots) | tap | tap | tap | deny | park |
| local_exec (not risky) | none | tap | tap | tap | park |
| submit | none | tap | tap | tap | park |
| share_out, communicate | tap | tap | tap | tap | park |
| transact, account, destroy | tap | verify | verify | deny | park |
| install | tap | verify | deny | deny | park |
| credential | deny | deny | deny | deny | deny |

`cautious` (equals `PANINI_APPROVALS=all`): every effect above `navigate` is at least `tap`. `trusted`: as
`balanced`, but `local_write` inside roots and `local_exec` (not risky) are `none` on remote surfaces too.

`park` means: background runs open an interrupt and wait (V2-05) instead of failing with `NEEDS_PERSON`.

### 5.4 Grants

```python
@dataclass(frozen=True)
class Grant:
    id: str
    subject: str                 # "owner" | "persona:<id>" | "surface:<name>" | "mcp_client:<id>" | "a2a:<id>"
    capability: str = "*"        # manifest id or "*"
    tools: tuple[str, ...] = ("*",)   # glob patterns over tool names
    groups: tuple[str, ...] = ("*",)
    levels: dict[str, str] = field(default_factory=dict)   # effect → ApprovalLevel override
    conditions: dict = field(default_factory=dict)         # surfaces, background, domains, blocked_domains,
                                                            # roots, max_usd_per_run, hours
    expires_at: float | None = None
    note: str = ""
```

Resolution order for one call:

1. Start from the profile matrix cell (effect × surface tier).
2. Apply matching grants for the run's subjects (persona, surface, owner), most specific first
   (tool > group > capability > `*`). A grant may lower or raise the level.
3. Apply **hard ceilings** that grants cannot lower: `credential` → deny; external principals never get
   `none` for effects ≥ `submit`; remote/channel `transact|destroy|install|account` never below `verify`
   unless a grant names the exact tool and expires within 24 hours.
4. Apply **taint escalation** (§5.5).
5. Apply guardian (V2-13) when enabled.

### 5.5 Taint

`Run.taint` is a set of labels added by hands and the brain: `web` (page content), `email`, `channel`,
`call` (transcript), `download`, `mcp` (open-world connector results), `a2a`, `sensitive_local` (files
outside workspaces, memory recall). Rules:

| Rule | Effect |
| --- | --- |
| Run has any of `web, email, channel, call, mcp, a2a` and effect ≥ `submit` with level `none` | raise to `tap`; reason "acting after reading untrusted content" |
| Run has `sensitive_local` and effect is `share_out` or `communicate` | at least `tap`, preview includes what is being sent |
| Run has `download` and effect is `local_exec` on the downloaded path | at least `tap` |

### 5.6 Epochs and revocation

```sql
CREATE TABLE IF NOT EXISTS grant_epochs(id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0);
```

- Any grant change, capability disable, persona permission change or "pause all" increments `epoch`
  (single SQL `UPDATE … SET epoch = epoch + 1`) and publishes `policy.changed` on an in-process
  `asyncio.Event` broadcaster.
- Each `RunContext` remembers `grants_epoch`. `check()` compares; on change it re-reads grants. If the run's
  subject lost any level it had used, or PANINI is paused, `check()` raises `CancelledError` (the v1 cancel
  path), and the registry calls `hand.stop(run_id)` for in-flight calls.
- I7: a test with a hand that blocks for 5 s asserts cancellation within 100 ms of a grant change.

### 5.7 Privileged probes

Hands may ask the policy layer to run a **fixed** probe to classify a target. Probes are registered by name,
reviewed code, and never exposed to models. Examples: `browser.field_info(ref)` →
`{type, autocomplete, name, form_action}`; `desktop.element_info(element_id)` → UIA control type and
`IsPassword`. A missing probe result never lowers a class.

### 5.8 Interface

```python
@dataclass(frozen=True)
class Decision:
    action: Literal["allow", "ask", "park", "deny"]
    level: ApprovalLevel
    effect: EffectClass
    reasons: tuple[str, ...]
    preview: dict                # {effect, tool, target, recipients, amount, domain, data_summary}
    redactions: tuple[str, ...]  # JSON paths to redact in receipts ("text", "contents", "command")
    grant_ids: tuple[str, ...]

class Policy:
    def __init__(self, store, profiles, clock=time.time): ...
    def decide(self, run: Run, spec: ToolSpec, args: dict, observation: dict | None = None) -> Decision
    def epoch(self) -> int
    def pause(self, paused: bool, by: str) -> None
    def grants(self, subject: str | None = None) -> list[Grant]
    def put_grant(self, grant: Grant, by: str) -> Grant      # bumps epoch, audit
    def revoke(self, grant_id: str, by: str) -> None         # bumps epoch, audit
```

### 5.9 Shadow mode

With `PANINI_V2_POLICY=shadow`, every place v1 calls an `approvals.*` function also calls `Policy.decide`
with an equivalent `ToolSpec`/args, and writes disagreements to `policy_shadow`
(`ts, tool, effect, v1_ask, v2_action, v2_level, reasons`). The Admin page shows counts. Switch to
`enforce` when a week of normal use plus the eval suite shows no disagreement where v2 is *more permissive*
than v1. (v2 stricter is acceptable and expected for taint cases.)

### 5.10 API

| Route | Purpose |
| --- | --- |
| `GET /api/harness/policy` | profile, matrix, grants, epoch, paused, shadow stats |
| `PUT /api/harness/policy/profile` | `{profile}` |
| `POST /api/harness/policy/grants` / `DELETE …/{id}` | create/revoke |
| `POST /api/harness/policy/pause` | `{paused: bool}` (also on the tray menu and the phone app) |

Every change is written to the Admin audit log (`backend/admin/service.py` already audits settings).

### 5.11 Failure modes

| Failure | Behaviour |
| --- | --- |
| Classification error | Treat as the manifest's static effect raised to `submit`; reason "could not classify" |
| Grant store unreadable | Fall back to the profile matrix with hard ceilings; log once |
| Epoch read fails | Cancel the run (fail closed) |

### 5.12 As built (2026-10-02)

- `backend/policy/`: `effects.py` (v1 patterns moved verbatim; category classes), `engine.py` (profiles,
  tiers, grants, ceilings, taint, epochs, pause, shadow), `hook.py` (registry stage), `__init__.py`
  (process-wide policy and call-scoped context variables). Grants live in SQLite (`0003_policy.sql`).
- **One prompt per action:** in `enforce` mode the policy asks once; nested v1 confirmations inside the same
  tool call (`TaskContext.confirm`) are pre-approved through a call-scoped context variable.
- **Taint refinement (found by the live scorecard):** only *earlier* reads escalate, and `web` taint does not
  escalate at all in the policy engine. Gating every host action after any web search made common tasks
  ("find today's rate and save it to a file") ask for approval; judging whether an action was *induced* by web
  content is the guardian's job (V2-13). Direct-message taints (email, channel, call, mcp, a2a, shared,
  client_context) still escalate every effect ≥ submit. Risky actions keep asking through the word and
  command rules regardless of taint.
- Commands are judged by command patterns only (words such as "buy" inside a harmless command do not ask),
  matching v1.
- Coarse capability tools (`*_run`) are allowed at the coarse level because their adapters ask per action
  (v1 behaviour); their per-action prompts are compared in shadow mode through `approvals.py`.
- `park` is refused with a clear message until V2-05 adds durable interrupts.
- Admin → Build → Permissions shows the profile matrix, grants (add/remove) and a Pause switch.

## 6. Implementation steps

1. `effects.py`: move word and command regexes; mapping to classes; unit tests on the v1 corpus.
2. Profiles and matrix; `Decision`; `Policy.decide` without grants.
3. Grants store, resolution order, hard ceilings; migration `0004_policy.sql`.
4. Taint labels in `RunContext` (V2-02 hook); escalation rules.
5. Epochs, broadcaster, `check()` integration, pause-all; I7 test.
6. Registry pipeline hook (V2-02 step 6) calls `decide`; probes registry.
7. Shadow mode wiring in v1 callers; Admin page; API routes.
8. Enforce mode: `approvals.py` becomes the façade over `effects` + profile.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Word/command classes; never below static effect | `backend/tests/policy/test_effects.py` |
| Unit | Matrix per profile × tier; hard ceilings | `backend/tests/policy/test_matrix.py` |
| Unit | Grant precedence and expiry | `backend/tests/policy/test_grants.py` |
| Unit | Taint escalation | `backend/tests/policy/test_taint.py` |
| Invariant | I5 (credential always deny), I7 (≤ 100 ms cancel), I8 (background/external cannot self-approve) | `backend/tests/policy/test_invariants.py` |
| Parity | v1 `test_approvals.py` cases produce the same ask/no-ask under `balanced`/`cautious` | `backend/tests/policy/test_parity.py` |

## 8. Acceptance criteria

- [ ] Shadow mode shows zero cases where v2 allows what v1 would have asked.
- [ ] I5, I7, I8 tests green.
- [ ] Admin Permissions page edits a grant and a running test run stops at its next effect.
- [ ] `enforce` passes the offline scorecard with no new regressions.

## 9. Rollout and rollback

`shadow` from the start of Phase 1 (logs only); `enforce` at the Phase 1 gate. Rollback: `off`; grants
remain stored and unused.

## 10. Open questions

- Should "pause all" also hang up an active phone call (V2-16)? Recommendation: yes, after speaking
  "PANINI has been paused".
