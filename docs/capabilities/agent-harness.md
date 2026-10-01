# Agent harness (memory, skills, background work)

The harness turns PANINI from a set of capabilities into an agent. It adds long-term
memory, reusable skills, saved conversations with compaction, a planning agent loop
over PANINI's capabilities, and durable background tasks. Everything is stored locally
in one SQLite database, `harness.sqlite3`, in PANINI's data directory
(`%LOCALAPPDATA%\PANINI` or `PANINI_DATA_DIR`).

## How a request reaches the agent

The coordinator's planner still routes every request. Simple, well-specified requests
go straight to one capability, as before. The planner picks the **agent** capability
for these kinds of request:

- open-ended goals that need judgement across capabilities, or more than three
  adaptive steps;
- anything to remember or recall about you;
- saving a procedure as a skill;
- doing something later or repeatedly.

You can also force it with the capability hint `agent`.

The agent then runs a tool loop (`backend/harness/agent.py`):

| Tool | Purpose |
| --- | --- |
| `update_plan` | Shows and maintains a step plan. |
| `use_capability` | Runs one step with `search`, `browser`, `desktop` or `code`, under the **same task context**. Chrome-first routing, confirmations, Stop and evidence all still apply. |
| `recall` / `remember` / `forget` | Long-term memory. |
| `load_skill` / `create_skill` | Skills. |
| `schedule_task` | Runs a goal later or every N minutes in the background. |
| `ask_user` | Asks one question and stops. |
| `finish` | Reports an honest outcome. |

**Honesty rule:** `verified_complete` is accepted only when a delegated step returned
verification evidence, or a local effect such as a saved memory was recorded.
Otherwise the outcome is downgraded to `partial`.

## Memory

- **Kinds:** `profile`, `preference`, `episode` and `fact`.
- **Retrieval is hybrid.** Each memory's score is 70% cosine similarity of free, local
  hashed n-gram embeddings (no model download, no API, nothing leaves the PC) and 30%
  SQLite FTS5 BM25 keyword rank. Results must score above a relevance threshold and
  fit a character budget.
- **In the prompt,** only memories relevant to the current goal are injected, labelled
  as *data, not instructions*.
- **Near-duplicates are merged.** Text that looks like a secret (keys, passwords,
  tokens, card numbers) is refused.
- **You stay in control.** You can list, search, edit, forget one, forget everything
  or export, from the web console API (`/api/harness/memories`) or the shell API.

## Skills

Skills use the Agent Skills format: a folder containing `SKILL.md`, with `name` and
`description` in its frontmatter. They are disclosed progressively: only names and
descriptions are in the prompt, and the agent loads the full instructions when a
skill fits.

Sources, from highest priority:

1. `<workspace>/.panini/skills`
2. `%LOCALAPPDATA%\PANINI\skills` (your own)
3. `backend/harness/builtin_skills`

The built-in skills are:

- `research-brief`;
- `save-a-skill`, which turns a successful task into a skill after you approve the
  draft.

## Conversations and compaction

Agent conversations persist per PANINI session. When a conversation exceeds its
budget, the oldest turns are summarised by the model into a running summary. The
cut never falls between a tool call and its result, and the most recent 16 items
are kept verbatim.

## Background tasks

Durable tasks live in `backend/harness/tasks.py`:

- **Leases.** A worker in the backend claims a due task with a 90-second lease and
  renews it with heartbeats.
- **Checkpoints and effects.** Progress is checkpointed, and any dispatched effect
  is counted.
- **Crash recovery.** If PANINI dies or the PC sleeps mid-run, the lease expires and
  the task is recovered:
  - it is requeued only if nothing was dispatched;
  - otherwise it becomes **needs review · outcome unknown**, so it is never blindly
    replayed.
- **Retries** back off at 30 s, 60 s and 120 s, for up to three attempts.
- **Recurring tasks** (every 5 minutes to 7 days) are rescheduled after each run.
- **Reviews** hold questions and unknown outcomes. They expire after 24 hours, and
  you can rerun (optionally with an answer) or dismiss them.
- **Least privilege.** Nobody is watching a background run, so it gets read-only
  capabilities (`search`). Any step that needs a confirmation stops and asks you to
  run it in the foreground.

## Avatar on, or background mode

Choose in the avatar's settings ("When PANINI is working") or from the tray menu:

- **Avatar on** (the default). The avatar stays on screen. A pulsing indicator under
  the status line shows background work, queued tasks and reviews.
- **Background mode.** The avatar starts hidden and PANINI runs from the tray. The
  tray tooltip and menu show what it is working on, plus queued tasks and reviews.
  Windows notifications tell you when a background task finishes or needs review.
  The avatar still appears by itself when it needs you (a confirmation or a
  question) and when you press Ctrl+Shift+Space.

## Watches, inbox and suggestions

- **Watches.** A watch is a recurring background check with a condition, such as
  "the price drops below ₹50,000".
  - Each run ends with `NOTIFY:` or `QUIET:`.
  - You get a notice only for `NOTIFY:`, and only once for each distinct finding.
  - Create one by asking PANINI, or on the console's *Tasks & watches* tab.
- **Inbox.** Notices collect in the inbox:
  - results of one-off background tasks;
  - watch alerts;
  - suggestions.

  The console's *Agent* page shows them next to the reviews waiting for you, and the
  tray reports unread items.
- **Suggestions.** They are computed locally, without a model call, at most every
  10 minutes:
  - a goal you asked for 3 or more times in two weeks becomes "make it a routine?";
  - a review about to expire gets a warning;
  - a foreground run with 3 or more verified steps becomes "save it as a skill?".

  Accepting a suggestion only creates a task; nothing acts by itself.

## Connectors (MCP)

List Model Context Protocol servers in `connectors.json` in the data directory:

```json
{
  "servers": {
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/notes"]
    }
  }
}
```

- **Startup.** Servers start on first use, and their tools appear in the agent's
  prompt.
- **Permissions:**
  - tools annotated read-only run without asking;
  - any other tool needs your confirmation in the avatar;
  - background runs accept a read-only tool only when its server entry also names
    it in `background_read_only`, for example
    `{"servers":{"issues":{"url":"https://example.com/mcp","background_read_only":["list_issues"]}}}`.
    In PANINI Admin → Connectors, use **Allow background** beside an available
    read-only tool to set this grant. Other connector tools are refused in background.
- **Receipts.** Successful connector calls record a local receipt with the server,
  tool, read-only flag, success flag and hashes of arguments and result. The
  receipt ID can support a verified agent result without storing connector data
  in the receipt table.
- **Management.** The console's *Connectors* tab shows each server's state and tools,
  and reloads the file after you edit it.

### Connectors that need sign-in

Remote servers are listed with a `url`:
`{"servers": {"linear": {"url": "https://mcp.linear.app/mcp"}}}`.

1. When the server asks for authorization, the *Connectors* tab shows
   **Sign in**.
2. PANINI discovers the provider from the server's protected-resource metadata and
   registers itself as a public client. It then opens the provider's page with
   PKCE, and the provider sends you back to PANINI's local callback.
3. The tokens are encrypted for your Windows account (DPAPI, in
   `connector-secrets.bin`) and refreshed automatically. **Sign out** deletes them.

A few details:

- Only `https` servers are accepted; `http` is allowed for `localhost` only.
- Servers that don't allow automatic registration take a `client_id` in their
  entry.
- Servers that use a fixed API key take a `headers` entry instead.

## Agent-to-agent (A2A)

**Inbound.** Other agents can send PANINI tasks over A2A: JSON-RPC at
`http://localhost:3200/a2a`, with the agent card at
`/.well-known/agent-card.json`.

- **Tokens.** Create a token for each calling agent on the console's *Agents* tab.
  It is shown once and stored only as a hash, and you can revoke it at any time.
- **Approval.** `message/send` never acts directly. The request waits in your
  inbox as "Another agent asks PANINI — approve?", and the caller sees
  `input-required`.
- **Running.** Once you approve, the task runs in the background with read-only
  research. Your memories and memory tools are withheld, and the agent is told not
  to share personal information.
- **Results and scope.** Callers poll `tasks/get` for the result, and `tasks/cancel`
  also works. A caller can see only its own tasks.
- **Reach.** The endpoint listens on this PC only. Reaching it from another machine
  needs a tunnel you set up yourself.

**Outbound.** PANINI's agent can ask other agents, listed in `agents.json` in the
data directory: `{"agents": {"helper": {"url": "…/a2a", "token": "…"}}}`. Each
message needs your confirmation, and background runs refuse to send.

## Phone companion

Turn it on in the console's *Phone* tab. PANINI then listens on your home network
at port 3210, and shows the address to open on your phone with the Wi-Fi address
first.

- **Pairing.** Pair with a one-time code, valid for 5 minutes, with at most 5
  attempts a minute. The phone keeps an HttpOnly device cookie, which you can
  revoke from the *Phone* tab.
- **Who can connect.** Only private-network addresses may connect. The console,
  the shell API and the extension stay on localhost.
- **What the phone can do:**
  - see status, the inbox and recent tasks;
  - approve or decline decisions, including requests from other agents;
  - dismiss notices;
  - send PANINI a request, which runs as a background task.
- **Security.** Traffic is plain HTTP on your LAN, so turn it on only on Wi-Fi you
  trust. The setting persists across restarts.

## Sub-agents

For independent questions, the agent can call `run_subagents`, with up to four tasks:

- **Fresh context.** Each sub-agent starts with a fresh context and one capability.
  Sub-agents cannot spawn more sub-agents or schedule work.
- **Scheduling.** Search sub-agents run in parallel. Browser, desktop and code share
  one executor each, so those run one after another.
- **Evidence.** The parent receives each sub-agent's outcome and message, and its
  evidence counts toward verification.

## Console

Open the *Agent* page at `http://localhost:3200/agent` (signed in). It has five tabs:

- **Inbox:** reviews and notices.
- **Tasks & watches:** add, cancel, and see each run's result.
- **Memories:** search, add, edit, forget, forget everything, export.
- **Skills:** view each skill's steps.
- **Connectors.**

## API

| Surface | Prefix | Access |
| --- | --- | --- |
| Web console | `/api/harness/…` | Console sign-in (the gateway forwards these). |
| PANINI shell | `/api/panini/harness/…` | The shell's runtime credential. |

Resources:

- `status`
- `memories`: `GET ?q=`, `POST`, `PATCH /{id}`, `DELETE /{id}`, `DELETE` (all), `GET export`
- `skills`: `GET`, `GET /{name}`, `POST`
- `tasks`: `GET`, `POST`, `GET /{id}` (with events), `POST /{id}/cancel`
- `reviews`: `GET`, `POST /{id}` with `rerun` or `dismiss`
- `notices`: `GET`, `POST /{id}` (`read` or `dismissed`), `POST /{id}/accept`
- `suggestions/refresh`: `POST`
- `connectors`: `GET`, `POST reload`
- `tasks` also accepts a watch: `POST` with `every_minutes` and `condition`

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PANINI_HARNESS_ENABLED` | `1` | Set to `0` to turn the harness off. |
| `PANINI_AGENT_MODEL` | `PANINI_CLI_MODEL` | Model for the agent loop. |
| `PANINI_CLI_SCRIPTED_MODEL` | | Replays a scripted model, for tests and offline demos only. |

## Verification

- `backend/tests/harness/test_harness.py` covers:
  - memory ranking, deduplication and secret refusal;
  - skill precedence;
  - lease recovery with and without effects;
  - retries and recurrence;
  - review expiry;
  - the agent's evidence rule, memory injection and compaction;
  - the background worker;
  - the coordinator adapter;
  - both APIs.
- It was also checked with a live run against the real model:
  - it remembered a preference;
  - it loaded `research-brief`, planned, delegated to search and verified;
  - for "every morning…", it asked for the time and time zone.

