# V2-18: Personas and delegation (group threads)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 4 · P1 · M (5 days) |
| Depends on | V2-03 (grants), V2-06 (run engine and sub-runs) |
| Flag | `PANINI_PERSONAS` (default `false`) |
| Status | In progress |

## 1. Outcome

PANINI becomes a small team: you create specialists (a researcher, a writer, an ops helper), each with a name,
role, instructions, voice, permissions, memory scope, default Space and optionally its own sandbox computer.
You can talk to one directly, or ask PANINI to coordinate them in one conversation where each member's work is
attributed, isolated by its own permissions, and receipted. This is the delegation and group-conversation
layer OpenDots lists as future work.

## 2. Scope and non-goals

- In scope: persona model and store, default persona (today's `persona.md`), Admin CRUD, grants per persona
  (V2-03 subjects), memory scopes, persona prompts and caching, persona selection by surface and by name,
  `delegate_to_persona`, `run_team` (lead plans and delegates; optional reviewer), group threads with
  attribution, voice per persona, avatar colour variants, budgets per delegation.
- Not in scope: multi-user identities (one owner), persona-to-persona free chat without a goal.

## 3. Current state

- One identity: `harness/learning.py:persona()` reads `persona.md` (editable in Admin) and opens every prompt.
- Sub-agents (`run_subagents`) get fresh contexts at depth 1; parallel only for search.
- Memories have a `scope` column (default `global`), used by the agent with `scope=None` today.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/server/workspace.ts` (Dots: `name`, `instructions`, `spaceId` default destination, `spaceIds` access list, `researchAllowed`, `memoryAllowed`, learning settings) and `WorkspaceDialog.tsx` (Dot settings UI) | Specialist model and editing UX; default destination plus access list for Spaces | Persona fields (§5.2); Spaces access from V2-19 |
| OpenDots `dot-agent.ts` prompt: "You are ${dot.name}, a specialist Dot… Role instructions: …"; tools derived from permissions; run aborted if permissions change | Per-specialist prompt and tool set; revocation | Persona identity block first (cache-stable); grants and epochs (V2-03) |
| OpenDots `computer-service.ts` (one computer per Dot, credential derived from the Dot id) | Persona computers | `computer_id = persona id` (V2-11) |
| OpenMuse identity (name, tone, avatar) | Editable persona presentation | Name, voice, colour |
| AG-UI `SUBAGENT_STARTED/FINISHED` | Attribution in UIs | Delegations emit sub-run events (V2-07) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/personas/store.py` | CRUD, validation, default persona |
| `backend/personas/prompt.py` | Identity block per persona; cache keys |
| `backend/personas/routing.py` | Persona selection (surface default, explicit mention, Jev name detection) |
| `backend/brain/control_tools.py` | `delegate_to_persona`, `run_team` |
| `backend/harness/migrations/0013_personas.sql` | `personas` |
| Admin `src/pages/Personas.tsx` | Create/edit/archive, permissions switches, test chat |
| Shell/phone | Persona chips in timelines; persona voice on the avatar when it speaks |

### 5.2 Model

```sql
CREATE TABLE IF NOT EXISTS personas(
  id TEXT PRIMARY KEY,                 -- slug: "panini", "scout", "quill"
  name TEXT NOT NULL, role TEXT NOT NULL, instructions TEXT NOT NULL,
  voice TEXT, color TEXT NOT NULL,     -- brand palette token
  default_space_id TEXT, memory_scope TEXT NOT NULL DEFAULT 'persona',   -- global | persona | none
  memory_write INTEGER NOT NULL DEFAULT 0, computer_id TEXT, model_tier TEXT,
  archived INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
```

- `panini` always exists and mirrors `persona.md` (editing either updates both).
- Permissions are **grants** with `subject = "persona:<id>"` (V2-03). Admin shows them as switches (OpenDots
  style): Web research · Your Chrome · Managed browser · Desktop · PANINI-CLI · Sandbox browser/files/shell ·
  Channels send · Spaces read/write · Memory read/write. New personas start with web research and Spaces read
  only.
- Memory: `global` reads and writes global memories; `persona` reads global profile/preferences and writes
  only `scope = persona:<id>`; `none` neither reads nor writes.

### 5.3 Prompts

Identity block (first, stable for caching): `You are <name>, <role>, part of PANINI working for <owner>.
Instructions: <instructions>.` followed by PANINI's principles and the registry sections for the persona's
offered tools. Cache key per persona: `panini-agent:<persona_id>`.

### 5.4 Selection

| Situation | Persona |
| --- | --- |
| Surface default | Admin setting per surface (avatar, phone app, each channel account, phone line, MCP clients) |
| Explicit mention | "@Scout …" in console/phone/channels, or "Scout, …" by voice: Jev question "Is the person addressing persona X by name?" for configured names (≥ 0.8) |
| Thread already bound to a persona | That persona, unless the person addresses another |

### 5.5 Delegation

| Tool (lead persona) | Behaviour |
| --- | --- |
| `delegate_to_persona(persona_id, task, share_context)` | Starts a sub-run with the target persona's grants, memory scope and computer; `share_context=false` (default) passes only the task text; `true` adds a summary of the current thread (untrusted to the delegate). The lead waits for the result (`SUBAGENT_*` events). Budget: at most 50 % of the lead's remaining budget unless the person raised it. |
| `run_team(goal, members[], reviewer?)` | The lead writes a plan (`update_plan`), assigns steps to members (sequential or parallel for read-only steps, as `run_subagents` does today), collects results, optionally asks the reviewer persona to critique, and writes the final answer. Each member's receipts carry `persona_id`. |

Rules:

- A delegate can never exceed its own grants because the delegation is a run with that persona as subject.
- A delegate cannot delegate further (depth limit 1, as `run_subagents`).
- Approvals raised by a delegate are shown as "Scout wants to …" (persona name in the interrupt title).

### 5.6 Group threads

A thread may contain messages from several personas. Each assistant message carries `persona_id`; UIs show a
chip with the persona's name and colour. The lead persona (the thread's bound persona) owns the
conversation; members speak only through delegations or when addressed directly.

### 5.7 Failure modes

| Failure | Behaviour |
| --- | --- |
| Persona archived while a run uses it | Grants epoch bump → run cancelled at the next check (V2-03) |
| Delegate lacks a needed grant | Its tool returns `blocked`; the lead may ask the person to grant it (interrupt) or reassign |
| Name collision with a common word | Selection requires the configured name at the start of the utterance or an `@` mention |

## 6. Implementation steps

1. Store, migration, default persona sync with `persona.md`.
2. Grants per persona (subjects in V2-03) and Admin switches.
3. Prompt identity blocks and cache keys; memory scopes in recall/remember.
4. Selection (surface defaults, mentions, Jev names).
5. `delegate_to_persona`, then `run_team`; sub-run events and receipts attribution.
6. UI chips, persona voices on the avatar, Admin test chat.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | CRUD validation; `panini` cannot be deleted; `persona.md` sync | `backend/tests/personas/test_store.py` |
| Unit | Grants isolation: a delegate without the desktop grant cannot reach desktop tools | `backend/tests/personas/test_grants.py` |
| Unit | Memory scopes: persona memories invisible to other personas; `none` reads nothing | `backend/tests/personas/test_memory_scope.py` |
| Integration | `run_team` research → write with two personas; receipts attributed; budget split | `backend/tests/personas/test_team.py` |
| Eval | Two persona cases added to the scorecard (delegation and isolation) | `evals/cases/personas/` |

## 8. Acceptance criteria

- [x] Two personas with different grants proven isolated (tools, memories, computers). Evidence: `test_memory_read_write_and_delete_are_scoped`, `test_policy_does_not_inherit_owner_or_surface_grants`, `test_member_receipts_computers_and_resume_keep_identity` (2026-10-06).
- [x] A group thread shows attributed messages and receipts from a lead and a member. Evidence: `test_team_attribution_depth_and_budget`, `test_avatar_finish_message_has_public_identity_and_durable_attribution`, `test_persona_resume.py` (2026-10-06).
- [x] Revoking a persona grant mid-run stops that persona's next effect. Evidence: `test_revocation_cancels_in_flight_persona_in_shadow_mode`, grant-revocation case in `test_persona_resume.py` (2026-10-06).

## 9. Rollout and rollback

Flag on after qualification. Rollback: flag off → new runs use `panini`; specialist resume checkpoints are refused, and active specialists retain their restricted principal. Persona rows and grants remain.

## 10. Open questions

- Per-persona model choice (for example a cheaper model for a researcher)? Supported through `model_tier`;
  default inherits the run engine's router.

## 11. Implementation evidence — 2026-10-04

Development checkpoint on `codex/v2-delivery`; this LLD remains **In progress**.

Implemented:

- SQLite migration and validated persona CRUD; PANINI identity file synchronization and protected default identity.
- Explicit persona grants, expiry-aware scoped memory, permission changes and archive revocation; specialist policy is enforced even when owner policy is in shadow mode.
- Explicit mentions, confidence-gated voice addressing, per-surface defaults and durable thread persona binding.
- Fresh-context delegation, explicit quarantined context sharing, sequential teams/reviewers, depth-one delegation and per-child budget allocation.
- Teams stop when a member cannot finish and report the unfinished chain; a failed reviewer cannot be presented as a completed review.
- Persona identity and budget retained in resume checkpoints; disabling Personas refuses old specialist resumes rather than escalating them to owner privileges.
- Durable lead/member/reviewer continuation across approvals, with completed team results retained in the lead checkpoint and stable member receipt keys. Declining or abandoning a team closes its parked member attempts.
- Cumulative cost, step and active-time budgets retained across attempts; run rows record only new own spending, and daily limits count recorded costs once. An over-budget model turn cannot dispatch its tools.
- Assigned sandbox computer selection, Chrome/managed browser permission separation, attributed run principals and ledger receipts.
- Admin persona editor, permission switches, defaults, effect-free identity preview and attributed conversation messages.
- Authenticated phone persona chooser and task-scoped attributed replies, exposing only public identity fields. Archived identities remain readable in history; disabled Personas hides the metadata.
- Avatar persona name, palette accents and an attributed conversation panel, retaining the JARVIS canvas and its existing status animations. Final `finish` replies are attributed and stored as well as intermediate messages.

Default adjustment: new specialists use `memory_scope=persona` and `memory_write=false`; they have no memory grant by default. PANINI itself retains global memory. This makes a new specialist private by default while preserving the explicit global setting.

Verification: `backend/tests/personas/test_personas.py` covers isolation, context sharing, grants/expiry, archived identities, routing, team budget allocation, receipt attribution, sandbox selection, resume identity and rollback. `npm run personas:smoke` runs the built Admin UI against a loopback-only throwaway backend: create/edit/grants/test/reload/mobile/archive. Provider replies in this smoke are scripted; it is not live model qualification.

Remaining before LLD completion:

- Live research-to-writing team qualification, and a live listening check of spoken handoff (built and unit-tested; see below). WebRTC probes are recorded separately from real SIP qualification.
- Spaces access/default-destination integration (V2-19), and channel permission integration when V2-17 exists.

Continuation evidence: `backend/tests/agui/test_persona_resume.py` exercises the real HTTP/SSE, policy, ledger and SQLite checkpoint paths with scripted models and a local test hand. Teams, direct delegates and reviewers each pause twice on identical writes; completed research is not repeated, each approved write happens once, and every model turn is charged once. The stream runner is recreated between requests. Additional cases cover decline, flag rollback, archive, grant revocation and exhausted cost/time budgets. This qualifies the local continuation path, not a live-provider or remote-device deployment.

`PANINI_PERSONAS` stays off by default. Red-team qualification is disabled and deferred to future development by owner; pairing and action permissions continue to apply.

Presentation evidence: `npm run phone:smoke` exercises encrypted pairing, the Scout chooser, attributed task history, signed approvals, reload, offline recovery and revocation against a throwaway loopback fabric with a scripted response. Six phone identity palettes pass contrast checks in light and dark themes. `npm run personas:ui:smoke` checks PANINI/Scout/Quill identity changes, canvas palette changes, escaped message text, compact and full-stage layouts through the shell preview bridge. Screenshots were inspected. The bridge mocks native window controls; this is browser qualification, not physical Android, Electron installation or live voice qualification.


## Voice reconnect evidence — 2026-10-04

The avatar now fetches an authenticated public persona catalog and starts media with the selected persona's voice and private instructions. Manual switching stops current work and reconnects media within the conversation. Bounded history follows the same persona only; archive/rollback is checked before delegation. Generation checks protect new media from delayed old-session closure. `test_voice_adapter.py`, `test_voice_personas_api.py` and the avatar UI smoke cover these paths.

The latest opt-in `npm run personas:voice:live` probe passed Marin and Cedar using real Live sessions, seeded text history and synthetic silent WebRTC peers. It waits for connected media and first assistant transcript output from the backend-result speech path. It does not qualify completed audible playback, physical Android, a real SIP call, multi-speaker handoff or a live team benchmark. Earlier greeting timeouts and cleanup limitations are recorded in the [qualification evidence](../qualification-2026-10-04.md). V2-18 remains **In progress**.


### Decoded audio continuation — 2026-10-04

The opt-in voice probe now validates received and rendered non-silent samples, a quiet tail, context acknowledgment and clean provider session closure for Marin and Cedar, with seeded text history. Both passed. This is a bounded synthetic browser check, not subjective listening, physical Android, real SIP or long conversation qualification. Multi-speaker handoff, live research-to-writing team qualification, evaluation scorecard integration and later-LLD integrations remain open. See the [latest evidence and probe limitations](../qualification-2026-10-04.md).

### Live team continuation — 2026-10-04

`scripts/panini-persona-team-live.py` passed Scout research followed by Quill writing with real gpt-4.1,
synthetic notebook facts, separate persona grants, source citations, attributed durable messages and
metered child runs. The prompt now tells team members to perform only their configured role; the earlier
run produced overlapping roles despite correct facts. The latest run produced distinct research facts and
a two-sentence writing result. Evidence retains earlier attempts, including a probe setup error. This is
one bounded notebook team scenario, not broad team, web research, multi-speaker or scorecard qualification.

## Multi-speaker voice handoff and scorecard cases — 2026-10-06

- **Handoff:** a whole utterance asking for another persona ("hand me to Scout", "let me talk to the writer",
  "back to PANINI") is not a task. `personas.routing.handoff_target` matches it only when it names an existing,
  unarchived persona exactly, so "switch to dark mode" or "tell Scout to hand me the report" stay ordinary
  requests. The current persona says "Handing you to Scout.", and the server publishes `voice.handoff`.
- **Context:** the next persona's voice history gets a short recap of the owner's own recent words (at most four
  utterances, 1,200 characters), marked as context. The previous persona's replies are never copied, so persona
  isolation holds.
- **Voice:** a live voice cannot change mid-session, so each speaker is its own media connection. On
  `voice.handoff` the shell selects that persona and, after the goodbye line (about 1.8 s), reconnects audio in its
  voice through the same path as the Voice specialist picker.
- **Run attribution fix:** a delegate's run row now records its persona (`runs.persona_id`); before, every run was
  stored as `panini`.
- **Scorecard:** `evals/cases/personas/` adds two cases:
  - `offline-delegation`: PANINI delegates to Scout and reports Scout's result. Unbacked sources stay `partial`.
  - `offline-isolation`: Scout has no computer grant, so `write_file` is not even offered and no file is written.

  Cases declare their specialists (`personas:`, only id/name/role/instructions/colour/voice, so a case file cannot
  widen grants) and can expect `personas_ran` / `personas_not_ran`. Persona cases run on the v2 brain under every
  engine, and the lead and its delegates share one scripted queue (`{"shared": true, "steps": [...]}`). Both cases
  run in `npm test` (18/18 offline).
- **Tests:** `backend/tests/panini/test_voice_adapter.py` (handoff matching, goodbye line, event, owner-only recap,
  next voice, archived persona), `backend/tests/brain/test_brain.py` (attribution), and `evals/tests/test_cases.py`.
- **Not yet qualified:** an audible handoff on a live provider (OpenAI credits were exhausted during this work) and
  the live research-to-writing team benchmark.
