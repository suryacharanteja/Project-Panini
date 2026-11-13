# V2-25: Ambient triggers and responsibilities

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · L (1.5 weeks) |
| Depends on | V2-06 (run engine), V2-14 (webhook ingress), V2-18 (personas); V2-19 for digests |
| Flag | `PANINI_TRIGGERS` (default `false`) |
| Status | In progress: development implementation and fixture tests; live source qualification pending |

## 1. Outcome

PANINI reacts to the world, not just to schedules: a file appears in Downloads, an email arrives from your
bank, a meeting starts in ten minutes, a webhook fires, your PC wakes up. You describe the rule once and it
runs with least privilege. Beyond single rules, you can hand PANINI a **responsibility** ("keep my invoices
filed", "watch my flight") with a charter, success criteria, cadence and a weekly report, which is the
"persistent responsibility lifecycle" OpenDots lists as further work.

## 2. Scope and non-goals

- In scope: event sources (files, email, calendar, webhooks, OS events, existing schedules and watches),
  the event model, trigger rules with filters, optional Jev conditions, cooldowns and quiet hours, execution
  as durable background tasks, loop prevention, the responsibility lifecycle with health and digests, agent
  tools to propose triggers and responsibilities (reviewed), Admin pages.
- Not in scope: phone location triggers (native app later), arbitrary code in rules.

## 3. Current state

`harness/tasks.py` durable tasks with recurrence (`every_minutes`), watches that notify only on change
(`harness/proactive.py`), projects with sequential plans (`harness/projects.py`, `project_planner.py`),
model-free suggestions. No event sources.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenMuse goals and page watches with backoff (`PANINI-AGENTIC-DESIGN.md` §5) | Goal-driven checks with backoff | Responsibilities own checks with backoff on failures |
| OpenDots `src/server/runner.ts` | Lease ownership checked every 100 ms; 90 s run limit; revoked permission aborts; release on stop with "queued for restart" | Trigger runs use the durable worker with the same ownership rules (already in `tasks.py`) |
| OpenDots README ("Schedules are recurring instructions, not a complete goal or event-trigger system") | The gap to close | This item |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/triggers/bus.py` | Event bus: normalize, dedupe, match rules, enqueue |
| `backend/triggers/sources/files.py` | `watchfiles` (already in the lock) on chosen folders, debounced 2 s |
| `backend/triggers/sources/mcp_events.py` | Owner-bound read-only MCP polling: email every minute, calendar every five minutes |
| `backend/triggers/sources/webhooks.py` | Generic ingress hooks (V2-14 `generic:<name>`) |
| `backend/triggers/sources/os_events.py` | Shell-forwarded `powerMonitor` events (lock, unlock, resume, AC/battery), network change |
| `backend/triggers/rules.py` | Rule model, matching, conditions, cooldowns |
| `backend/triggers/responsibilities.py` | Lifecycle, health, digests |
| `backend/harness/migrations/0033_triggers.sql` | `triggers`, `trigger_events`, `responsibilities` (0017 was used earlier) |
| Admin `src/pages/Automations.tsx` | Rules and responsibilities (extends the existing automations area) |

### 5.2 Event model

```python
@dataclass(frozen=True)
class TriggerEvent:
    source: str            # files | email | calendar | webhook | os | schedule
    kind: str              # created | modified | received | starting_soon | fired | resumed | ...
    data: dict             # redacted summary: {path, name, size} | {from, subject} | {title, start} | {hook, fields}
    occurred_at: float
    dedupe_key: str        # e.g. path+mtime, message-id, event id+start, webhook delivery id
    tainted: bool          # email, webhook and calendar content from others are tainted
```

### 5.3 Rules

```json
{"id": "tr-invoices", "name": "File invoices", "enabled": true, "persona": "panini",
 "source": "files", "match": {"kind": ["created"], "path_glob": "C:/Users/charan/Downloads/*invoice*.pdf"},
 "condition": null,
 "action": {"goal": "Read {event.path}, extract vendor, date and amount, and save a summary page in the Finance Space.",
            "budget_usd": 0.10},
 "cooldown_s": 60, "max_per_day": 20, "quiet_hours": null}
```

- `match` is declarative: kinds, globs, sender/subject contains, calendar title contains, webhook name and
  field equals. No code.
- `condition` (optional): one Jev boolean question over the event's redacted data ("Is this email a payment
  failure notice?"), threshold 0.7; failing that, the rule does not fire.
- Execution: `tasks.submit(goal_rendered, origin="trigger:<id>", kind="trigger")` → background run with the
  rule's persona and budget; results go to notices and presence routing (V2-24). Effects park on interrupts
  (V2-05) as background policy requires.
- Rendering: `{event.*}` placeholders only; event text is inserted as untrusted data in a quoted block, never
  as instructions.

### 5.4 Loop and storm prevention

- File events caused by PANINI itself are ignored: if a ledger receipt wrote the same path within 10 s, the
  event is dropped.
- Dedupe by `dedupe_key` (7 days); cooldown per rule; `max_per_day`; a global cap of 200 trigger runs per day.
- A rule that fails 5 times in a row is paused and a notice explains why.

### 5.5 Responsibilities

```sql
CREATE TABLE IF NOT EXISTS responsibilities(id TEXT PRIMARY KEY, title TEXT NOT NULL, persona_id TEXT NOT NULL,
  charter TEXT NOT NULL, success_criteria TEXT NOT NULL, cadence_minutes INTEGER, trigger_ids TEXT NOT NULL,
  report TEXT NOT NULL DEFAULT 'weekly', escalation TEXT NOT NULL, status TEXT NOT NULL,   -- proposed | active | paused | retired
  health TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL);
```

| Phase | What happens |
| --- | --- |
| proposed | The agent (tool `propose_responsibility`) or the person drafts the charter, success criteria, triggers, cadence and escalation; a review interrupt shows it |
| active | Triggers and periodic checks run; each run is attributed to the responsibility; health tracks last check, misses, failures, interventions |
| report | Daily or weekly digest page in the persona's Space (V2-19): what happened, what is pending, what needs you |
| escalation | Rules such as "ask me before paying anything" or "call me if a flight is cancelled" (V2-24 urgent) |
| paused / retired | By the person, or automatically after repeated failures (paused) with a notice |

Examples shipped as templates: "Keep Downloads tidy" (files), "Invoice filing" (files + email), "Flight
watch" (email + web check), "Meeting prep" (calendar: 10 minutes before a meeting, summarize related emails
and pages).

### 5.6 Agent tools

| Tool | Effect | Notes |
| --- | --- | --- |
| `create_trigger(rule)` | local_write | Always a review interrupt before activation |
| `list_triggers()`, `pause_trigger(id)` | observe / local_write | |
| `propose_responsibility(spec)` | local_write | Review before activation |

## 6. Implementation steps

1. Event model, bus, dedupe, rules store; files source; Admin list.
2. Execution through durable tasks; loop and storm prevention.
3. OS events from the shell; webhooks through ingress; email and calendar sources.
4. Conditions with Jev; quiet hours.
5. Responsibilities: lifecycle, health, digests, templates; agent tools with reviews.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Matching (globs, kinds, fields); cooldown; caps; dedupe | `backend/tests/triggers/test_rules.py` |
| Unit | Self-caused file events ignored (ledger correlation) | `backend/tests/triggers/test_loops.py` |
| Integration | Drop a fixture invoice into a watched folder → background run → page created → notice | `backend/tests/triggers/test_files_flow.py` |
| Integration | Responsibility: proposed → review → active → digest page | `backend/tests/triggers/test_responsibility.py` |
| Eval | Red-team: a webhook payload with injected instructions does not cause effects | `evals/redteam/trigger_*` |

## 8. Acceptance criteria

- [ ] File, OS and webhook triggers fire within 5 s; email and calendar within their polling windows.
- [ ] No trigger loops on PANINI's own writes; storms are capped.
- [ ] A responsibility produces a weekly digest page and pauses itself after repeated failures.

## 9. Rollout and rollback

Flag on after step 2 with the files source; others follow. Rollback: flag off stops the bus; rules remain.

## 10. Open questions

- Let responsibilities spend budget proactively (for example re-check a flight hourly near departure)?
  Recommendation: yes within the responsibility's own daily budget, shown in its health panel.

## Development evidence and remaining qualification

The trigger bus, file watcher, signed generic webhook, shell-forwarded OS events, schedule journal,
read-only MCP polling, reviewed rules, background worker integration, responsibility reports and Admin
controls are implemented behind `PANINI_TRIGGERS=false` by default. `backend/tests/triggers/test_flow.py`
exercises real local folder watching, signed webhook rejection and dedupe, worker execution, quotas,
source grants and a scripted responsibility report. Sandbox export now records its host file write before
and after saving. A real watched-folder integration test confirms that this PANINI-created file does not
queue another trigger run. These fixture tests do not qualify a live email or calendar provider, actual
sleep/wake delivery on a physical PC, correlation of every PANINI file write, or a seven-day report
interval. The acceptance boxes above remain open until those checks are run. Red-team qualification is
disabled and deferred by owner direction.
