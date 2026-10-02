# V2-06: One brain (run engine, model router, budgets)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 1 · P0 · L (1.5–2 weeks) |
| Depends on | V2-01 (scorecard), V2-02, V2-03, V2-04, V2-05 |
| Flag | `PANINI_V2_ONE_BRAIN` (default `false`) |
| Status | Not started |

## 1. Outcome

Every turn from every surface (avatar voice and text, console, phone app, background tasks, A2A, and later
phone line, channels, triggers and MCP) runs through one run engine and one agent loop. Jev stops being a
router and becomes what it is best at: cheap gates (tier, selection, barge-in) and the verifier of results.
Simple requests stay fast through model tiers and "reflexes"; complex ones get the full loop without a
six-step cap.

## 2. Scope and non-goals

- In scope: `RunEngine`, `RunContext`, runs table and checkpoints, threads and bindings, model router
  (tiers, escalation, provider abstraction), budget governor, reflexes (Jev-chosen first tool call), control
  tools, finish/evidence rule (I2), sub-runs, coordinator integration, background worker integration,
  scorecard gate, retirement plan for `planning.py`.
- Not in scope: personas (V2-18 plugs into `persona_id`), AG-UI streaming (V2-07 consumes the engine's
  events), local models (V2-27 adds a provider).

## 3. Current state

| Path | Behaviour |
| --- | --- |
| `coordinator.py:657 _run` | Inventory → `route()` → validate 1–6 `Step`s → for each: availability, `open_application` / `direct` / `resolve_target` + `adapter.run` with per-capability timeouts → evidence validation → `_recover` (Jev "recoverable?" then agent) → tidy windows |
| `planning.py:198 route` | `capability_hint` → `jev_route` (single-capability search/code/agent when 7 escalation questions all < 0.4 and the best route ≥ 0.8) → **agent by default** (`PANINI_AGENT_DEFAULT=true`) → LLM planner + `evaluate_plan` only when the agent is unavailable |
| `capabilities/agent.py` | Harness `Agent` with `delegate()` to sibling adapters; maps `reply` to `partial`/`blocked`/`verified_complete`; records `agent_run` evidence |
| `harness/agent.py:543 run` | Jev `_select` (groups, skills, connectors, memories) → loop ≤ 24 steps / 900 s → tool calls → `_jev_done` early finish → `finish` with the evidence rule and `_jev_confirms` |
| `harness/runtime.py run_background` | Same `Agent` with `BACKGROUND_CAPABILITIES=("search",)` and `confirm=None` |

So v1 already routes most turns to the agent. The remaining "second brain" is the direct Jev route to
search/code, the planner fallback, `_recover`, and the separate background composition.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/server/dot-agent.ts` | One agent class serves web chat, Slack, scheduled work and voice compute; per-run permission snapshot + watcher; 90 s run timeout; `maxIterations` loop strategy; system/developer messages from clients filtered out ("match BuiltInAgent's default trust boundary") | One engine for all surfaces; grant epochs (V2-03); budgets instead of fixed iteration caps; client-supplied system text is never accepted from any surface |
| OpenDots `src/server/headless.ts` (`runThreadTurn`: run one turn in an existing thread from the server, return the assistant text, detach cleanly) | Server-initiated turns in persistent threads | `RunEngine.run_turn(thread_id=…)` used by voice, background, channels and triggers |
| OpenDots `src/server/voice.ts` (`compute()`: idempotent by tool-call id, at most six compute turns per call, 90 s each) | Delegated compute from a realtime voice model with idempotency | Voice delegation ids map to `command_id` (v1 already uses `uuid5` from the delegation id) and to run budgets |
| OpenMuse terminal tools (`finish_task`, `ask_user`, `prepare_*`) | Explicit loop terminators | Existing `finish`/`ask_user`; `request_review` (V2-05) for "prepare" flows |
| Playwright MCP `--timeout-action/--timeout-settle` | Settling after actions instead of fixed sleeps | Hands report `settled_ms`; the loop never sleeps |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/brain/engine.py` | `RunEngine`: start, step loop, finish, stop, resume |
| `backend/brain/context.py` | `RunContext` (superset of `TaskContext` and `BackgroundContext`) |
| `backend/brain/loop.py` | The agent loop (moved from `harness/agent.py:Agent.run`, behaviour-preserving) |
| `backend/brain/control_tools.py` | `update_plan`, `ask_user`, `choose`, `request_review`, `request_takeover`, `finish`, `run_subagents` |
| `backend/brain/models.py` | Model router: tiers, providers, escalation, usage accounting |
| `backend/brain/budget.py` | Budget governor (steps, time, tokens, USD per run and per day) |
| `backend/brain/reflex.py` | Jev tier + reflex selection (one Jev call that also does context selection) |
| `backend/brain/threads.py` | Threads (harness sessions) and `thread_bindings` |
| `backend/harness/migrations/0007_runs.sql` | `runs`, `thread_bindings`, thread columns |
| `backend/panini/coordinator.py` | `_run` delegates to the engine under the flag |
| `backend/harness/runtime.py` | `run_background` uses the engine |
| `backend/capabilities/agent.py` | Becomes a thin compatibility hand (`agent_run`) for v1 callers only |

### 5.2 Run lifecycle

```text
start(turn) → Run(state=running) → RunContext
  1. control intents (stop/end/status) handled by the surface before start (v1: coordinator.submit)
  2. reflex.classify(turn, thread) — ONE Jev call:
       tier ∈ {chat, simple, complex}; optional reflex {tool, args} for a single obvious tool call;
       plus v1 _select questions (tool groups, skills, connectors, memories)
  3. if reflex: ctx.call_tool(reflex.tool, reflex.args) through the full pipeline (policy, ledger)
       → if the result answers the turn (Jev "answered?" ≥ 0.7 or tool returned a verified answer) → finish
       → else continue into the loop with the reflex result already in the items
  4. loop (model tier from 2; escalation rules §5.4):
       items → model.respond(prompt, items, registry.model_tools(view)) → tool calls → ctx.call_tool each
       → checkpoint after each round (§5.6) → budget check → Jev early-finish (v1 _jev_done) when eligible
  5. finish: outcome by the evidence rule (§5.5) → Run(state=completed|blocked|failed) → events
```

Reflexes replace `jev_route`'s direct dispatch: same Jev questions, same thresholds (best ≥ 0.8, margin
≥ 0.4, no escalation question ≥ 0.4), but the result is a tool call inside the run (receipted and policed)
instead of a separate execution path. Initial reflex tools: `search_run` (question-type requests),
`computer_open` (just-open requests), `chrome_media_pause/play` and `chrome_skip_ad` (v1 direct browser
actions), `code_run` (single coding goal in the sole workspace).

### 5.3 RunContext

`RunContext` implements every method hands use today, so v1 adapters run unchanged:

| v1 method | v2 behaviour |
| --- | --- |
| `check()` | v1 checks (cancelled, lease, session state) when the run belongs to the avatar surface; plus grant epoch (V2-03), budget, run state |
| `emit(phase, message, **detail)` | Publishes `task.<phase>` to the bound session hub (avatar) **and** `run.*` events to the run's subscribers |
| `dispatch()` | Ledger `dispatch` entry for the current receipt + v1 counter |
| `record_evidence(...)` | Ledger evidence (V2-04) |
| `confirm(action, application, effect, data)` | Approval interrupt (V2-05) |
| `code_activity`, `computer_activity` | Unchanged events + live frames (V2-12) |
| `task` | A `Task`-compatible view over the run (`task_id = run_id`, `capability` = current tool's capability) |

New: `call_tool`, `interrupt`, `taint`, `artifact`, `live_frame` (HLD §5.3).

### 5.4 Model router

```python
class ModelRouter:
    tiers = {
        "fast":   env("PANINI_FAST_MODEL", "gpt-4.1-mini"),      # chat and simple turns
        "agent":  env("PANINI_AGENT_MODEL", <v1 default>),       # complex turns, escalations
        "deep":   env("PANINI_DEEP_MODEL", ""),                  # optional: long plans, code review
        "vision": env("PANINI_VISION_MODEL", <agent>),           # screenshots
    }
    def for_run(self, tier: str) -> "Model"
    def escalate(self, run, reason: str) -> "Model"   # fast → agent → deep
```

Escalation from `fast` to `agent` happens when: a tool call fails twice in a row; the model calls `finish`
with `partial` or `blocked` while budget remains; the model requests a tool outside the offered set; or the
turn turns out to need a plan (`update_plan` with ≥ 3 steps). Items are kept; only the model changes; the
prompt prefix stays byte-identical so caching still applies (v1 `cache_key="panini-agent"`).

Provider abstraction: `Model.respond(prompt, items, tools, cache_key)` is the interface the harness already
uses (`panini_cli.agent.model.create_model`). New providers (Anthropic, Gemini, local in V2-27) implement
it; the router picks per tier from settings.

### 5.5 Finish and the evidence rule (I2)

```python
def final_outcome(run, claimed: str, message: str) -> str:
    if claimed == "verified_complete":
        if not ledger.receipts(run.run_id, effect_at_least="local_write") and not ledger.evidence(run.run_id):
            return "partial"                       # v1 rule: success needs evidence
        if not ledger.verified_after_last_effect(run.run_id):
            return "partial"                       # v2 rule: every effect followed by a check
        if not jev_confirms(run, message):          # v1 _jev_confirms
            return "partial"
    return claimed
```

Plain replies (no tool calls) keep v1 semantics (`capabilities/agent.py:139-142`): with no delegated steps a
reply is `verified_complete` (conversation needs no evidence); after only blocked attempts it is `blocked`.

### 5.6 Runs, checkpoints and resume

```sql
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, persona_id TEXT NOT NULL DEFAULT 'panini',
  principal TEXT NOT NULL, mode TEXT NOT NULL, parent_run_id TEXT, task_id TEXT,
  state TEXT NOT NULL, tier TEXT, created REAL NOT NULL, updated REAL NOT NULL,
  budget TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '{}', checkpoint BLOB, result TEXT);
CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, created);
CREATE TABLE IF NOT EXISTS thread_bindings(surface TEXT NOT NULL, external_id TEXT NOT NULL,
  thread_id TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(surface, external_id));
ALTER TABLE sessions ADD COLUMN persona_id TEXT NOT NULL DEFAULT 'panini';   -- threads
ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'avatar';
```

Checkpoint (zlib-compressed JSON, ≤ 512 KiB): `{items (tool outputs truncated), summary, plan, outcomes,
step, tier, selection}` written after each tool round. `resume(run_id)` reloads it; combined with ledger
replay (V2-04) this makes parked background runs continue exactly where they stopped, replacing V2-05's
"re-run with pre-approved digests" fallback.

### 5.7 Budgets

| Budget | Foreground default | Background default | Setting |
| --- | --- | --- | --- |
| Steps (model rounds) | 24 | 16 | `PANINI_RUN_MAX_STEPS` |
| Wall time | 900 s (avatar keeps v1's 600 s outer timeout until parity is proven) | 900 s | `PANINI_RUN_MAX_SECONDS` |
| Cost per run | USD 0.50 | USD 0.20 | `PANINI_RUN_MAX_USD` |
| Cost per day | USD 5.00 | (shared) | `PANINI_DAILY_MAX_USD` |

Reaching a budget ends the run with `partial` and a plain message ("I stopped at the cost limit for this
task"), except the daily cap in foreground, which opens an approval interrupt: "Today's PANINI spend reached
USD 5. Continue this task?" Costs come from `usage_log` (it already prices OpenAI, Jev and realtime usage).

### 5.8 Threads and bindings

- A thread is a harness session (`harness/sessions.py`): items, summary, compaction. It is
  device-independent.
- `thread_bindings` map a surface's conversation to a thread: avatar session id, console chat id, phone app
  conversation id, call id (V2-16), Slack `channel:thread_ts`, Telegram chat id (V2-17), page id (V2-19).
- The avatar binds each coordinator session to a thread (replacing `AgentAdapter.sessions` in memory). The
  setting `PANINI_AVATAR_THREAD=continue|new` (default `continue` within 4 hours) lets the avatar resume the
  last thread after a restart.

### 5.9 Coordinator integration

```python
# coordinator.py _run (flag on)
ctx = RunContext.for_avatar(self, s, task, principal=avatar_principal(s))
result = await self.brain.run_turn(ctx, text=payload.text, thread_id=thread_for(s),
                                   attachments=None, selected_result=s.results.get(payload.selected_result_id))
task.outcome, task.message, task.state = result.outcome, result.message, state_for(result.outcome)
task.steps = plan_rows(result.plan)          # the shell's step list comes from update_plan
self._results(s, task, Outcome(result.outcome, result.message, result.results, result.evidence_ids))
```

Kept from v1: lease and epoch fencing, barge-in (`_while_busy`), Stop, End, `tidy()` of windows the task
opened, learning hooks (`on_turn`, `on_task_end`), media feedback. Removed under the flag: `route()`,
`_recover()`, the step loop, `UNRECOVERABLE` handling inside steps (faults become tool results the agent sees).

`TARGET_AMBIGUOUS` becomes a `choice` interrupt raised by the hand; the person's answer resumes the same run
(v1 needed a new turn with `resume_choice_id`; that route remains for the shell's existing buttons).

### 5.10 Background, A2A and phone requests

`Harness.run_background(task)` builds `RunContext.for_background(task)` and calls `brain.run_turn` with
`mode="background"`, principal `system` (or `external_agent` for `a2a:` origins, `owner` with surface
`phone_app` for `mobile:` origins). `BACKGROUND_CAPABILITIES` stays as an extra ceiling until policy
enforcement is default (V2-03), then is removed. Watches and project tasks keep their goal templates.

### 5.11 Failure modes

| Failure | Behaviour |
| --- | --- |
| Jev unavailable | Tier `complex`, no reflex, v1 selection fallback (show everything); latency rises, correctness unchanged |
| Model provider error | One retry with backoff; then the fallback provider for the tier if configured; else `failed` with a plain message |
| Checkpoint write fails | Continue the run (checkpoints are an optimisation); log once |
| Budget accounting missing usage | Count the call at its maximum token budget (fail closed on cost) |

### 5.12 Configuration

`PANINI_V2_ONE_BRAIN`, `PANINI_FAST_MODEL`, `PANINI_AGENT_MODEL`, `PANINI_DEEP_MODEL`, `PANINI_VISION_MODEL`,
`PANINI_RUN_MAX_STEPS`, `PANINI_RUN_MAX_SECONDS`, `PANINI_RUN_MAX_USD`, `PANINI_DAILY_MAX_USD`,
`PANINI_AVATAR_THREAD`, `PANINI_REFLEXES` (default `true`). All in Admin (models and v2 groups).

## 6. Implementation steps

1. `RunContext` with v1 compatibility; `runs`/`thread_bindings` migration; threads module.
2. Move the loop from `harness/agent.py` to `brain/loop.py` unchanged (pure refactor; harness `Agent` calls
   it). All harness tests stay green.
3. Model router (tiers, escalation) and budget governor; usage accounting through `usage_log`.
4. `reflex.py`: merge tier, reflex and `_select` into one Jev call; thresholds from `jev_route`.
5. Control tools and the evidence rule with I2.
6. `RunEngine.run_turn`, checkpoints, `resume`, `stop`.
7. Coordinator integration behind the flag; avatar thread binding.
8. Background worker, A2A and phone-origin tasks on the engine; true resume for parked runs.
9. V2 engine in the eval harness (`evals/engines.py:V2RunEngine`); run the scorecard; tune tiers and reflexes
   until the Phase 1 gate passes.
10. Default on; schedule deletion of `route()`/planner prompt/`_recover` for the Phase 2 cleanup PR.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Loop refactor parity (existing `backend/tests/harness/test_harness.py`) | existing |
| Unit | Tier/escalation decisions; budget enforcement; daily cap interrupt | `backend/tests/brain/test_models.py`, `test_budget.py` |
| Unit | Reflex thresholds equal `jev_route` on its recorded cases | `backend/tests/brain/test_reflex.py` |
| Invariant | I2 (effect without later verification cannot be `verified_complete`) | `backend/tests/brain/test_evidence_rule.py` |
| Integration | Avatar turn, Stop mid-tool, barge-in replace, lease loss → cancel | `backend/tests/panini/test_one_brain.py` |
| Integration | Park → restart → resume from checkpoint, no repeated effect | `backend/tests/brain/test_resume.py` |
| Eval | Full offline scorecard v1 vs v2; live scorecard for the gate | `scripts/panini-eval.py compare` |
| Live | `npm run panini:test:unified`, `panini:test:desktop-live`, `panini:test:paint` with the flag on | existing scripts |

## 8. Acceptance criteria

- [ ] Scorecard (live, 3 samples): success ≥ v1 baseline; `simple` p50 ≤ v1 + 10 %; cost per verified task
      ≤ v1 + 15 %; safety 100 %.
- [ ] Avatar, voice, console, LAN phone and background tasks run on the engine; `planning.route` unused.
- [ ] I2 test green; parked runs resume after a restart without repeating effects.
- [ ] The memory rule holds: Desktop and Paint live tests pass with the flag on.

## 9. Rollout and rollback

Development default on after step 7; product default on at the Phase 1 gate. Rollback: flag off restores
`route()` and the v1 step loop (kept until the Phase 2 cleanup). Runs and thread bindings remain; v1 ignores
them.

## 10. Open questions

- Should the avatar show the agent's `update_plan` as the step list even for one-step runs? Recommendation:
  only when the plan has ≥ 2 steps (v1 shows steps for multi-step plans).
