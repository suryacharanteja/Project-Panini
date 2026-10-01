# PANINI: from capabilities to an agent

**Design · 2026-09-24 · Phase 1 and the core of Phase 2 are built** (see §10)

It answers two questions:

1. Can PANINI-CLI (`panini-cli`) reach the capabilities of the Codex CLI?
2. Should PANINI adopt an agent harness, and what should it take from OpenMuse
   (`PANINI-HARNESS/`)?

## 1. Answers in brief

**An agent harness makes sense.** PANINI is currently a capable *orchestrator*. A
planner turns one instruction into at most six capability steps, and the
execution layer underneath (leases, confirmations, evidence, pairing) is strong.
PANINI is not yet an *agent*:

- It forgets everything when a conversation ends.
- It cannot learn a procedure.
- It cannot keep working in the background.
- It cannot change its plan based on what it observes.
- Its only "tools" are four coarse capabilities.

A harness adds exactly this missing layer. That's the difference between a voice
remote control and an assistant that knows you.

**Use OpenMuse as a source of patterns, not as a dependency.** These parts are
well engineered and worth porting to PANINI's Python backend:

- its durable task engine;
- its reviewed-action store;
- its goals and page watches;
- its editable-memory UX.

The rest is thin or unsuitable:

- Its agent loop is a 6- or 16-step CopilotKit wrapper.
- Its memory has no retrieval.
- Its "Ideas" are regular expressions over email subjects.
- Every deployment requires CopilotKit's hosted Intelligence service, which the
  MIT licence doesn't cover.

**PANINI-CLI can match the Codex CLI for everyday use, but literal parity isn't a
sensible goal.** Codex is about 7,800 files built over years. About fifteen
features deliver most of its value. Two of them are hard:

- OS-level sandboxing on Windows;
- interactive (PTY) commands.

Codex's source is Apache-2.0, so its formats and algorithms (for example
`apply_patch` and exec policy) can be ported with attribution.

**The key design insight:** most of what PANINI-CLI lacks relative to Codex
*is* what the avatar lacks as an agent. That includes sessions, compaction, a plan
tool, project instructions, skills, memory, hooks, MCP and sandbox tiers. Build
these once as harness modules, and both the avatar and `panini-cli` use them.

## 2. Where PANINI stands

| Agentic property | Today | Evidence |
| --- | --- | --- |
| Top-level reasoning | One-shot plan of at most 6 steps. Observations are passed forward, but there's no re-planning loop. | `backend/panini/planning.py`, `coordinator.py:513` |
| Memory across conversations | None. Sessions are in memory with 24 turns of history, and are lost on restart. | `coordinator.py:107`, `coordinator.py:220` |
| User model and preferences | None | — |
| Procedural knowledge (skills) | None; behaviour lives in prompts inside the code | — |
| Tools | Search, Browser, Desktop, Code and `open_application`; no MCP | `backend/capabilities/` |
| Background or scheduled work | None; work ends with the conversation lease | `coordinator.py` |
| Proactivity | None | — |
| Safety and control | **Strong:** fenced leases, nonce-bound confirmations, evidence-gated completion, device pairing, console sign-in, approval enforcement | `ARCHITECTURE.md` |
| Observability and evaluation | Event hub, evidence records and unit/integration tests; no cross-run traces or task-success evals | — |

PANINI already has a production-grade *execution and safety layer*. What's missing
is the *cognitive layer* on top of it.

## 3. Design method

We apply design thinking (Empathize → Define → Ideate → Prototype → Test, as in
Google's Design Sprint) before choosing technology.

**The person.** One person working on a Windows PC, in both technical and
knowledge work. They want *a trustworthy colleague who remembers, keeps working,
and asks before acting.*

**Jobs to be done, and what each requires:**

| The person says | Capability required |
| --- | --- |
| "Remember what I told you and how I like things done." | Memory: profile and episodic, editable |
| "Keep going while I'm away and tell me when it matters." | Durable background tasks, watches, notifications |
| "Learn my routines so I don't explain them twice." | Skills (captured procedures) |
| "Work across my apps and data, safely." | Tools and connectors (MCP), policy, approvals |
| "Show me what you're doing; let me stop or correct you." | Visible plans, receipts, undo, clear control |
| "Don't waste my time." | Fewer prompts for safe actions, concise voice, fast paths |

**"How might we" questions:**

- HMW make PANINI remember without it ever feeling creepy or wrong?
- HMW let it work unattended without losing the person's control?
- HMW turn a task done once into a reliable skill?

**Success metrics.** We use the HEART framework (Happiness, Engagement, Adoption,
Retention, Task success) plus agent-specific signals:

- verified task-success rate;
- intervention rate (confirmations and corrections per task);
- time to result;
- memory precision (helpful recalls versus wrong ones);
- suggestion acceptance rate;
- cost per task;
- how often Stop and Undo are used.

**Trust principles**, from Google's People + AI Guidebook:

- Set the right mental model (say what PANINI can and cannot do).
- Explain every suggestion and memory, with its source.
- Give feedback and control: edit or forget a memory, dismiss a suggestion,
  choose an approval mode.
- Fail gracefully: an "outcome unknown" is reported as unknown, never guessed.

**Prototype and test.** Each phase below ships behind a flag, with a scripted
evaluation set and a short usability session before it becomes the default.

## 4. Target architecture: the PANINI agent harness

```
Surfaces   Avatar (voice) · Web console · panini-core/capabilities/cli · JevRev Claw · (mobile, later)
                         │ existing event hub (optional AG-UI adapter)
┌────────────────────────▼──────────────────────────────────────────────────┐
│ Agent core   gather context → act with tools → observe → verify → repeat  │
│              budgets · plan tool · ask_user · finish (with evidence)       │
├──────────────────┬──────────────────┬─────────────────┬───────────────────┤
│ Context engine   │ Memory           │ Skills          │ Tools             │
│ compaction,      │ profile,         │ SKILL.md,       │ capabilities,     │
│ just-in-time     │ episodic,        │ progressive     │ primitives,       │
│ retrieval, notes │ retrieval        │ disclosure      │ MCP connectors    │
├──────────────────┴──────────────────┴─────────────────┴───────────────────┤
│ Durable runtime  tasks · leases · checkpoints · schedules · watches        │
│ Governance       modes · policy rules · reviews · receipts · sandbox tiers │
│ Observability    traces · evals · cost and latency budgets                 │
└────────────────────────────────────────────────────────────────────────────┘
Existing executors: Search · Browser (Claw / managed) · Desktop broker · PANINI-CLI
```

### 4.1 Agent core

Following Anthropic's *Building effective agents*: use the simplest thing that
works. Keep today's router as a **fast path** (a routing workflow) for clear
single-capability requests, since it's cheaper and quicker. **Escalate to the
agent loop** for multi-step, open-ended or ambiguous goals.

The loop follows the Claude Agent SDK's shape: gather context → take action →
verify work → repeat. It has these properties:

- **Hard budgets** for steps, tokens, wall time and cost.
- **Terminal tools** (the OpenMuse pattern):
  - `finish(summary, evidence_ids)` is the only way to claim success, which
    matches PANINI's existing evidence rule;
  - `ask_user(question)` pauses the task;
  - an unfinished run becomes "waiting for input", never "done".
- **A visible `update_plan` tool**, so the avatar can show a live checklist.

### 4.2 Tools, designed for agents

Following *Writing effective tools for agents*: a few well-named, high-signal
tools with token-efficient results and actionable errors.

- **Capabilities become typed tools:**
  - `web_search(query, window)`
  - `browser_task(goal, target)`
  - `desktop_task(goal, app)`
  - `code_task(goal, workspace)`
  - `open_app(id)`
- **Primitives:**
  - `remember` / `recall` / `forget`
  - `ask_user`
  - `schedule_task`
  - `watch(url, condition)`
  - `notify`
  - `read_artifact`
  - `current_time`
- **MCP connectors** (Model Context Protocol) for Gmail, Calendar, GitHub,
  files and other sources. Tools are namespaced per server, each server gets a
  trust level and approval policy, and tool search defers loading when the
  toolset is large. This replaces bespoke adapters such as OpenMuse's Google code.
- **Later:** Google's Agent2Agent (A2A) protocol, to delegate to other agents.

### 4.3 Context engineering

Following *Effective context engineering for AI agents*, the context window is a
scarce resource:

- **Compaction:** summarise older turns into a structured state note, rather than
  dropping them (which `panini-cli` does today).
- **Just-in-time retrieval:** keep handles and IDs in context and load content
  only when it's needed.
- **Structured notes:** a durable task notebook that survives compaction and
  restarts.
- **Sub-agents with clean contexts:** heavy exploration returns a condensed result
  (the pattern Anthropic describes in its multi-agent research system).

### 4.4 Memory

| Kind | Holds | Written | Read |
| --- | --- | --- | --- |
| Working | Current context | Automatically | Always |
| Profile / semantic | Facts and preferences ("prefers concise answers", "uses PowerShell") with source, scope (global or workspace), confidence and timestamps | Explicit "remember…" immediately; inferred ones only after the person confirms at the end of a task | Top-k relevant, within a token budget |
| Episodic | Summaries of past conversations and tasks, with outcomes and receipts | At the end of each task | Search: "what did we decide about X?" |
| Procedural | Skills (§4.5) | From successful traces, after review | Progressive disclosure |

**Storage.** SQLite in `%LOCALAPPDATA%\PANINI`, with FTS5 full-text search plus
embeddings for hybrid retrieval, stored locally.

**Safety:**
- Memory is injected as labelled *data*, never as instructions, which defends
  against memory poisoning.
- Secrets are never stored.
- Every memory shows its source.
- The person can view, edit, forget or export any of it, and "forget everything"
  is a single action (OpenMuse's UX, which is worth keeping).

**The fix over OpenMuse.** OpenMuse injects *every* memory into each task prompt
(`engine/model.ts`). PANINI retrieves only what's relevant, within a fixed budget.

### 4.5 Skills

Following *Equipping agents for the real world with Agent Skills*, a skill is a
folder containing:

- a `SKILL.md` file with a name and description (in YAML frontmatter) and
  instructions;
- optional scripts and resources.

**Progressive disclosure.** Only each skill's name and description sit in
context. The body loads when the skill is relevant, and resources load when
they're needed. Claude and Codex use the same format, so skills are portable
between them.

**Where skills live:**
- built into PANINI;
- per user in `%LOCALAPPDATA%\PANINI\skills`;
- per project in `.panini/skills`.

**Creating a skill.** After a successful task, the person says "save this as a
skill". The agent drafts a `SKILL.md` from the task trace, and the person reviews
it.

**Running a skill.** Skills compose capabilities. Their scripts run through
PANINI-CLI, under the same approvals.

**Trust.** A skill from an outside source is reviewed before it's enabled.

### 4.6 Durable runtime

This ports OpenMuse's `engine/worker.ts`, `db.ts` and `actions.ts` to Python and
SQLite.

- **A task state machine:** queued → running → waiting_input / waiting_approval
  → scheduled / paused → succeeded / failed / cancelled.
- **Durable leases:**
  - a lease is claimed by compare-and-swap;
  - a heartbeat renews it;
  - checkpoints are fenced by the lease (PANINI's epoch model already works this
    way);
  - an explicit error is raised when a lease is lost.
- **Idempotent tool replay:** each completed operation is cached under a hash of
  the tool name and arguments, so resuming a task never repeats a side effect.
- **Durable reviews:**
  - each external action becomes a proposal bound to its content hash, account
    and target version, with an expiry;
  - it's claimed atomically;
  - after a crash it's marked "outcome unknown" instead of being retried;
  - the person can approve it later from the console, not only within 60 seconds
    in the avatar.
- **Schedules and watches** with backoff and deduplicated alerts, delivered as
  notifications to the avatar, the console and Windows toasts.
- **Open decision:** should work continue while the avatar is closed? The runtime
  could stay resident in the tray (as it already does when minimised) or run as a
  per-user service.

### 4.7 Proactivity

- **Goals and milestones.**
- **Page watches** for changes, text or price thresholds.
- **Suggestions:** generated by a model from recent activity, memory and
  connectors, always with a reason and evidence. They're rate-limited, and each
  dismissal teaches PANINI (it's stored as a negative preference).
- **Proactive does not mean autonomous.** Suggestions never act without approval.

### 4.8 Governance

- **Permission modes** per surface and task: always ask · auto-approve safe reads
  · auto-edit inside the workspace · plan only.
- **Policy rules:**
  - allow and deny command prefixes (like Codex's `execpolicy`);
  - browser domain allowlists;
  - a risk tier for every tool.
- **Receipts and an audit log** for every effect.
- **Sandbox tiers for code:**
  1. the host with a Job object (today);
  2. a Docker container using OpenMuse's hardened profile (§5);
  3. a Windows restricted-token or AppContainer sandbox, later.
- **Prompt-injection defences:**
  - provenance labels on all content;
  - untrusted content kept apart from instructions;
  - an effect that follows the reading of untrusted content always needs
    confirmation.

### 4.9 Observability and evaluation

- **Traces** following the OpenTelemetry GenAI conventions: one span per model
  call and per tool call, with a local trace view in the console.
- **Evaluations:**
  - golden tasks with verifiable outcomes (like `panini-core/capabilities/cli/tests/live-smoke.py`);
  - rubric-based model grading for reports;
  - scripted-model regression tests in CI.
- **Dashboards** for cost and latency, and for the HEART metrics in §3.

### 4.10 Models

- **A provider layer:** the OpenAI Responses API today, with Anthropic and Gemini
  as options.
- **Routing:** a small model for routing and classification, a strong one for
  planning and code.
- **Efficiency:** prompt caching and stateless encrypted reasoning (already in
  PANINI-CLI).
- **Resilience:** a fallback model on provider failure.

## 5. Review of OpenMuse (`PANINI-HARNESS/`, now quarantined)

**What it is.** *OpenMuse* by CopilotKit, MIT-licensed, self-described as alpha.
It's a TypeScript monorepo:

- a Hono API server;
- an Expo client for iOS, Android and web;
- a Playwright browser worker;
- a Docker "computer";
- PGlite or Postgres storage.

The agent runs through the CopilotKit runtime and TanStack AI.

| Module | Quality | Verdict | Home in PANINI |
| --- | --- | --- | --- |
| Durable task worker: CAS leases, heartbeats, fenced checkpoints, run records (`engine/worker.ts`, `db.ts`) | Strong | **Port the pattern** to Python and SQLite | `backend/agent/runtime.py` |
| Idempotent tool replay via operation hashes (`engine/model.ts`) | Strong | **Port** | Runtime |
| Terminal tools `finish_task`, `ask_user`, `prepare_*` (`engine/model.ts`) | Strong | **Port** | Agent core |
| Reviewed actions: content hash, expiry, atomic claim, outcome unknown (`actions.ts`) | Strong | **Port**; extend PANINI's confirmations into durable reviews | Governance |
| Goals and page watches with backoff (`engine/service.ts`) | Good | **Port** | Proactivity |
| Ideas (`service.ts refreshIdeas`) | Good concept; regex rules | **Re-implement** with a model and evidence | Proactivity |
| Memory: `remember_fact` plus edit and forget routes | UX good; no retrieval, all injected | **Keep the UX, build retrieval** | Memory |
| Identity (name, tone, avatar) | Simple | **Adopt** as the avatar persona | Settings |
| Hardened Docker computer: read-only root, `cap-drop ALL`, no network, resource limits, non-root (`computer.ts`, `apps/computer/Dockerfile`) | Strong | **Adopt the profile** as sandbox tier 2 | `panini-cli` |
| Playwright worker with takeover console (`apps/worker`) | Good | **Skip**; PANINI has Claw and a managed browser. Consider borrowing takeover. | — |
| Expo mobile and web client (`apps/mobile`) | Good UX | **Later**, as a companion app; borrow UX patterns now (below) | — |
| Gmail and Calendar adapters (`packages/integrations/google.ts`) | Good | **Prefer MCP connectors**; reuse the review semantics | Tools |
| PDF forms and finance CSV | Vertical workflows | **Optional**, as skills | Skills |
| CopilotKit runtime and Intelligence (Rich Threads) | Proprietary hosted service, required | **Skip**: lock-in, and conversation data leaves the machine | — |
| AG-UI event protocol | Open standard | **Optional adapter** later, to reuse compatible frontends | Gateway |
| OpenBot adapter | Disabled stub | **Skip** | — |

**UX patterns worth borrowing** (from `docs/EXPERIENCE.md`):
- the send button turns into Stop in place, keeping the draft;
- a visible follow-up queue;
- background updates that link to the saved task;
- review screens that show the exact recipient and effect;
- editable identity and memory.

**Limitations to avoid copying:**
- the capped agent loop, with no compaction, skills or MCP;
- polling the whole task table every second (fine for one user, but not a design
  to copy);
- the TypeScript/CopilotKit stack, which doesn't match PANINI's Python backend.

## 6. PANINI-CLI compared with the Codex CLI

Sizes: S is up to a day, M is 2–4 days, L is 1–2 weeks or more. Priority 0 comes
first.

| # | Area | Codex CLI | PANINI-CLI today | Plan | Size | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Project instructions | `AGENTS.md` hierarchy | None | Load `AGENTS.md` / `PANINI.md` from the repo root down to the working folder | S | 0 |
| 2 | Multi-file edits | `apply_patch` grammar | Single-file `edit_file` / `write_file` | Port the `apply_patch` format (Apache-2.0) | M | 0 |
| 3 | Planning | `update_plan` | None | `update_plan` tool, plus display in the CLI and avatar | S | 0 |
| 4 | Sessions | Rollouts; resume and fork | In memory only | JSONL transcripts; `panini-cli resume` / `fork` | M | 0 |
| 5 | Context | Automatic and `/compact` | Drops old tool output | Summarising compaction (shared harness module) | M | 0 |
| 6 | Undo | Checkpoints | None | Git-based checkpoint per turn; `/undo` | M | 0 |
| 7 | Approvals | Policies and exec-policy rules | Ask, approve edits, approve all | Modes, a safe-command allowlist and deny rules | M | 0 |
| 8 | Headless use | `exec --json`, output schema, last-message file | Text output | `--json` event stream, `--output-schema`, `-o FILE` | S | 1 |
| 9 | Web search | Hosted tool | None | Responses API `web_search` tool | S | 1 |
| 10 | Images | `view_image` and image attachments | None | Image input and a `view_image` tool | S | 1 |
| 11 | Interactive commands | `exec_command` + `write_stdin` (PTY) | One-shot commands | ConPTY sessions | L | 1 |
| 12 | MCP | Client and server | None | MCP client (stdio and HTTP), plus a server mode | M–L | 1 |
| 13 | Sandbox | Seatbelt, Landlock, Windows sandbox, network proxy | Job object, no sandbox | Tier 2 Docker (§5), then tier 3 Windows restricted token | L | 1 / 3 |
| 14 | Code review | `/review` | None | Review mode over the git diff | M | 2 |
| 15 | Sub-agents | spawn, wait, send | None | Parallel explorers with clean contexts | L | 2 |
| 16 | Hooks | Before and after tool calls | None | Configurable hooks (shared) | M | 2 |
| 17 | Skills and memory | Skills and memories modules | None | Shared harness modules | M | 2 |
| 18 | Providers and config | Many providers, profiles, `config.toml` | OpenAI via environment variables | Config file, profiles, other providers | M | 2 |
| 19 | Terminal UI | Full-screen | Rich REPL | Status line, `@file` mentions, `/diff`, `/status` | M | 2 |

**Not planned:** ChatGPT-account sign-in, Codex Cloud tasks, the Bazel/Nix build,
and a plugin marketplace.

**Advantages PANINI-CLI already has:**
- voice-first approvals in the avatar;
- browser and desktop capabilities alongside code;
- file postconditions the host verifies itself;
- engine-side enforcement that every action was approved;
- secret files protected by default;
- credentials stripped from command environments.

## 7. Roadmap

Items shared by both answers are built once.

**Phase 1: memory and a real agent (about 2 weeks)**
- **Build:**
  - a SQLite store;
  - profile and episodic memory, with retrieval, an edit/forget UI and export;
  - the agent loop as an escalation path, with capability tools and primitives;
  - `update_plan`, `finish` and `ask_user`;
  - compaction and sessions (also Codex items 3–5);
  - traces.
- **Acceptance:**
  - 20 golden tasks pass;
  - memory precision is checked;
  - existing tests stay green.

**Phase 2: work that keeps going (about 2 weeks)**
- **Build:**
  - durable tasks, schedules, watches and notifications;
  - durable reviews;
  - the skills format, loader and create-from-trace;
  - `AGENTS.md`, `apply_patch`, undo and approval modes (Codex items 1, 2, 6, 7).
- **Acceptance:**
  - kill and restart the runtime in the middle of a task: it resumes without
    repeating any effect;
  - a skill created from a task reproduces that task.

**Phase 3: connected and safer (2–3 weeks)**
- **Build:**
  - MCP connectors;
  - model-generated suggestions;
  - the Docker sandbox tier;
  - web search, images and `--json` (Codex items 8–10, 12, 13).
- **Acceptance:**
  - connector tasks pass under the approval policy;
  - in the sandbox, network access is blocked and writes are confined.

**Phase 4 (later)**
- Sub-agents, hooks, PTY sessions, the Windows sandbox tier, a mobile companion
  app and A2A.

## 8. Decisions needed

1. **Direction.** Port these patterns into PANINI's Python backend (recommended),
   or embed OpenMuse as a Node service?
2. **Order.** Harness Phase 1 first (recommended: it's the "Jarvis" leap and
   delivers Codex items 3–5 along the way), or the PANINI-CLI priority-0 items
   first?
3. **Docker Desktop** as an optional dependency for sandbox tier 2?
4. **Embeddings.** OpenAI embeddings with vectors stored locally?
5. **Background work.** Should PANINI keep working when the avatar is closed
   (tray-resident runtime or a per-user service)?

## 9. References

**Anthropic**
- [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) (2024)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (2025)
- [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) (2025)
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) (2025)
- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) (2025)
- [Building agents with the Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk) (2025)
- [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) (2025)
- [Model Context Protocol](https://modelcontextprotocol.io)

**Google**
- [People + AI Guidebook](https://pair.withgoogle.com/guidebook/)
- [Design Sprint Kit](https://designsprintkit.withgoogle.com/)
- *Measuring the User Experience on a Large Scale* (the HEART framework), Rodden, Hutchinson and Fu, CHI 2010
- *Agents* whitepaper, Wiesinger, Marlow and Vuskovic, 2024
- [Agent Development Kit](https://google.github.io/adk-docs/)
- [Agent2Agent protocol](https://a2a-protocol.org/)

**Source code reviewed**
- OpenMuse (`PANINI-HARNESS/`, MIT)
- The Codex CLI source (Apache-2.0), kept in the cleanup quarantine folder


## 10. Implementation status (2026-09-24)

These are the decisions from §8:

- **Direction:** a native Python harness inside PANINI's backend. OpenMuse was a
  reference, not a dependency.
- **Order:** the harness first, then PANINI-CLI's priority-0 items.
- **Docker Desktop:** not required. The sandbox tier stays on the Phase 3 roadmap.
- **Embeddings:** free, local, hashed n-gram embeddings with SQLite FTS5 hybrid
  ranking. There is no API, no download, and nothing leaves the PC.
- **Background work:** both presences. **Avatar on** shows a live indicator.
  **Background mode** runs from the tray, with a status tooltip and menu and Windows
  notifications.

| Area | Built | Where |
| --- | --- | --- |
| Store | SQLite (WAL) holding memories with FTS5, sessions, tasks, events and reviews | `backend/harness/store.py` |
| Memory | Hybrid retrieval, deduplication, secret refusal, edit/forget/export | `backend/harness/memory.py` |
| Skills | `SKILL.md` format, progressive disclosure, project > user > built-in, create-from-trace skill | `backend/harness/skills.py` |
| Sessions | Persistent conversations; compaction that never splits a tool call | `backend/harness/sessions.py` |
| Agent loop | Plan, capabilities, memory, skills, schedule, `ask_user`, `finish` with the evidence rule | `backend/harness/agent.py` |
| Coordinator | `agent` capability delegating under the same task context; the planner routes to it | `backend/capabilities/agent.py`, `panini/planning.py` |
| Durable tasks | Leases, heartbeats, checkpoints, effect counting, recovery to review, backoff, recurrence, expiring reviews | `backend/harness/tasks.py` |
| API | Console `/api/harness/*` and shell `/api/panini/harness/*` | `backend/routes/harness.py` |
| Shell | Avatar on / background mode, tray status, notifications, indicator | `panini-core/apps/shell/src/main/main.ts`, `renderer/main.tsx` |
| PANINI-CLI | `update_plan`, `AGENTS.md`, saved sessions, `resume`, `sessions` | `panini-core/capabilities/cli/src/panini_cli/agent/` |

### Second pass (the same day)

| Area | Built | Where |
| --- | --- | --- |
| Watches | Recurring checks that notify only when a condition holds, and only once per distinct finding | `harness/proactive.py`, `harness/runtime.py` |
| Inbox | Notices for results, watch alerts and suggestions; read, dismiss and accept | `harness/proactive.py`, `routes/harness.py` |
| Suggestions | Local and model-free: repeated goals become "make it a routine?", reviews about to expire get a warning, and multi-step runs become "save as a skill?". They never act by themselves. | `harness/proactive.py` |
| Connectors | MCP stdio client (`connectors.json`). Read-only tools run freely; tools that change data need confirmation in the foreground and are refused in the background. | `harness/connectors.py` |
| Sub-agents | `run_subagents` gives each sub-agent a fresh context and a depth of 1. They run in parallel only for search; other capabilities run one at a time. | `harness/agent.py` |
| Console | `/agent` page with Inbox, Tasks & watches, Memories, Skills and Connectors | `frontend/public/panini-agent.*` |
| PANINI-CLI | `apply_patch` (all or nothing), `/undo`, approval modes (`read-only`, `ask`, `edits`, `auto`), Docker command sandbox | `panini-core/capabilities/cli/src/panini_cli/agent/{patch,sandbox,loop,cli}.py` |

`PANINI-HARNESS/` (OpenMuse) was moved to the quarantine folder
(`../Jev-Rev-PANINI.removed-2026-09-24/`). Its useful patterns are reimplemented
natively; nothing ran it.

### Third pass (2026-09-25)

| Area | Built | Where |
| --- | --- | --- |
| Sandbox | Docker Desktop installed and verified live: no network, host disk invisible, capabilities dropped, timeouts enforced, no leftover containers. PANINI finds Docker outside `PATH`, gives it its helper tools, and pulls the image on first use. | `panini-core/capabilities/cli/.../sandbox.py` |
| Hooks | `SessionStart`, `PreToolUse` (exit 2 blocks), `PostToolUse`, `TurnEnd`. User hooks always run; project hooks only after you trust the exact file. | `panini-core/capabilities/cli/.../hooks.py` |
| Terminal sessions | Start, send, read and stop long-running processes. Each is approved like a command, contained in a kill-on-close job, and cleaned up on exit. | `panini-core/capabilities/cli/.../terminals.py` |
| A2A | Inbound JSON-RPC `/a2a` with an agent card and per-agent tokens (stored hashed). Every request waits for your approval and runs without your memories. Outbound `ask_agent` confirms with you first. | `backend/harness/a2a.py` |
| Sign-in connectors | Remote MCP over Streamable HTTP; OAuth discovery, dynamic registration, PKCE, refresh; tokens encrypted with DPAPI | `backend/harness/mcp_http.py`, `secrets_store.py` |
| Phone companion | Opt-in home-network listener, private-address guard, one-time pairing codes, revocable devices; inbox, approvals and requests from the phone | `backend/harness/mobile.py`, `mobile_app/` |

The Windows-native sandbox tier was dropped in favour of Docker. Its reasons are in
`docs/capabilities/panini-cli.md`.

Still open:

- HTTPS for the phone companion (it needs a certificate your phone trusts);
- push notifications to the phone;
- A2A streaming (`message/stream`).


