# V2-18: Personas and delegation (group threads)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 4 · P1 · M (5 days) |
| Depends on | V2-03 (grants), V2-06 (run engine and sub-runs) |
| Flag | `PANINI_PERSONAS` (default `false`) |
| Status | Not started |

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
  default_space_id TEXT, memory_scope TEXT NOT NULL DEFAULT 'global',   -- global | persona | none
  memory_write INTEGER NOT NULL DEFAULT 1, computer_id TEXT, model_tier TEXT,
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

- [ ] Two personas with different grants proven isolated (tools, memories, computers).
- [ ] A group thread shows attributed messages and receipts from a lead and a member.
- [ ] Revoking a persona grant mid-run stops that persona's next effect.

## 9. Rollout and rollback

Flag on after step 5. Rollback: flag off → every run uses `panini`; persona rows and grants remain.

## 10. Open questions

- Per-persona model choice (for example a cheaper model for a researcher)? Supported through `model_tier`;
  default inherits the run engine's router.
