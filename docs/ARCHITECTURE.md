# Architecture

PANINI is a modular monolith for one user on one Windows computer. One runtime
process tree serves every surface; capabilities are shared services behind one
coordinator, not separate applications or modes.

## Unified runtime

```text
PANINI avatar (Electron main + sandboxed renderer)
  |  controller: bootstrap token -> lease (fenced epochs)
  |                                        Web console (any local browser)
  |                                          |  one-time sign-in -> HttpOnly session
  v                                          v
Gateway :3200  frontend/server/gateway.mjs  <---- JevRev Claw (extension)
  |-- /api/panini/*   -> coordinator API (own credentials)      /ws/extension
  |-- /api/*          -> console routes (+ per-launch service token)
  |-- /ws/compare     -> comparison (signed-in console only)
  |-- /ws/extension   -> relay (mutual pairing handshake in backend)
  `-- /search/*       -> TanStack Start (Vite :3230 in development)
          |
Backend :8200  backend/app.py (FastAPI)
  |-- panini/coordinator  sessions, leases, confirmations, events, evidence
  |-- capabilities/       search | browser | desktop | code | agent adapters
  |-- harness/            memory, skills, sessions, agent loop, durable tasks (SQLite)
  |-- routes/             comparison, browser, speech, voice, console, panini, harness
  `-- extension_pairing   device secrets in %LOCALAPPDATA%\PANINI
          |
          +-- JevBrowserLoop (managed Chromium)   ExtensionLoop -> bridge -> Chrome tab
          +-- Windows companion: RuntimeHost -> Broker (named pipe, HMAC) -> Worker
          `-- PANINI-CLI (panini-core/capabilities/cli app-server over stdio) in approved workspaces
```

`frontend/server/main.mjs` is the only composition root. `npm run dev`/`npm start`
run it directly; the Electron shell starts it with `--managed` through
`Panini.RuntimeHost` (a Windows Job object with kill-on-close), plus the desktop
broker. It generates or accepts the per-launch identities (instance ID,
bootstrap token, console secret) and a gateway-to-backend service token that
never leaves the process tree, always enables the coordinator, and writes a
user-private discovery file so `npm run console` can mint a sign-in link.
Ports come from `FRONTEND_PORT`/`BACKEND_PORT`/`SEARCH_PORT` (3200/8200/3230);
the shell reads the same configuration and refuses occupied ports.

## Trust boundaries

| Boundary                        | Mechanism                                                                                                                | Code                                                                                               |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| Websites → runtime              | Host allow-list (DNS rebinding), Origin and `Sec-Fetch-Site` checks at gateway and backend                               | `security.mjs`, `backend/http.py`                                                                  |
| Console user → gateway          | Signed single-use ticket (HMAC, 10–30 min) → `SameSite=Strict` HttpOnly cookie, 12 h sliding                             | `console-auth.mjs`                                                                                 |
| Other local processes → backend | Console routes require the gateway's per-launch token                                                                    | `backend/console.py`                                                                               |
| Shell → coordinator             | Bootstrap token → controller token; lease epochs fence stale controllers; idempotent command IDs                         | `backend/panini/auth.py`, `coordinator.py`                                                         |
| Extension ↔ runtime             | One-time pairing code → device secret; HMAC challenge-response both ways on each connection; origin bound to the pairing | `extension_pairing.py`, `routes/browser.py`, `panini-core/capabilities/browser/extension/background.js` |
| Backend → Windows companion     | HMAC-signed, deadline-bound, replay-protected pipe (`CurrentUserOnly`); elevated targets and secure desktops refused     | `capabilities/desktop.py`, `Panini.Desktop.Broker`                                                 |
| Effects → the world             | Confirmation bound to a nonce and action digest, approved in the shell; evidence required for "complete"                 | `TaskContext.confirm`, adapters                                                                    |

The web console can **observe** a PANINI conversation (a one-time observer
ticket via `/api/console/panini-observer`) but cannot control it; control stays
with the shell's lease.

## PANINI coordinator

`backend/panini/coordinator.py` owns one conversation at a time: controller
leases, a bounded event hub, task state, confirmations, evidence and results.
A planner (`planning.py`) turns an instruction into up to six capability steps
or a clarification; history, window titles and observations are untrusted data.
Each step resolves a target (`targets.py`), checks capability readiness, runs the
adapter under a deadline, and must return valid evidence to be
`verified_complete`. Observations from earlier steps are passed forward as
untrusted data. A background watchdog enforces lease expiry and retention; it
logs and survives failures in any one session, and `end()` performs every
cleanup step even when one fails.

The **agent** capability (`capabilities/agent.py`) runs the harness agent loop
for open-ended goals. It delegates each step to the other adapters under the same
task context, so leases, confirmations, Stop and evidence rules are unchanged.
The harness (`backend/harness/`) adds local memory, skills, compacted sessions
and a leased, checkpointed background-task worker that sends interrupted runs
with effects to review instead of replaying them. See
[capabilities/agent-harness.md](capabilities/agent-harness.md).

Adapters own their defaults. Browser prefers the user's attached Chrome tab and
uses a managed browser only when explicitly requested; when Chrome is not
paired, connected or attached it returns a specific next step instead of
falling back.

## Browser ownership and cancellation

The console's browser runs, live voice, and PANINI browser tasks share the same
two loops, and only one instruction runs at a time. A busy loop reports its
owner ("PANINI is using the browser").

**`chromium`.** One `JevBrowserLoop` owns a persistent Browser Use session. It
reserves its slot before startup, runs the agent in an owned task, and releases
the slot only after the task has stopped. `ChatJev` validates proposed actions
against Browser Use tool schemas and asks Jev to choose. Under PANINI, a guarded
tool registry admits one action at a time, refuses password fields and
coordinate clicks, and asks for confirmation of effects. A visible Chromium
window is launched by default (`BROWSER_HEADLESS=false`); it does not use the
user's profile.

**`extension`.** Drives the user's own Chrome through JevRev Claw.
`extension_planner.py` builds an immutable task plan (requested search queries
and domains) before reading the page, then proposes up to six grounded actions
per snapshot; Jev selects among them. Completion requires validated evidence
quotes, every planned query and domain, and visible results. A context-local run
binding checks the socket and attachment grant before every action; the
extension re-checks the expected tab and grant before dispatch, so detaching,
switching or reconnecting can never redirect a running instruction. Stale
controls and slow navigation are re-observed; repeated failures or unchanged
states stop the loop. Human-verification pages stop for the user.

## Extension protocol 4

1. The popup pairs once: the user types a code shown by the shell or console;
   the extension redeems it at `/api/extension/pair` and stores the device
   secret in `chrome.storage.local`.
2. On connect the extension sends `hello` with its device ID and a nonce, never
   the secret. The server answers with its own nonce and an HMAC proof; the
   extension verifies it before sending its own proof. Only then does the server
   adopt the socket and send `connected`. Actions from an unverified server are
   ignored.
3. `hello` frames after authentication report tab/workspace changes. Action
   frames carry the expected tab, grant and workspace revision.

The extension reconnects with exponential backoff (1–30 s). The content script
is injected on demand into attached tabs only. Popup commands are rejected when
sent from a content script. Tab workspaces add explicit membership, per-tab
grants and a scope revision; agent tab switches keep the run's target, while
manual scope changes revoke it.

## Comparison and Jev transport

Both providers evaluate the same named boolean questions from
`backend/tasks.json`; add tasks there without changing provider or UI code.
`jev_client.py` owns one warm Node sidecar because the Vercel AI SDK exposes the
Jev evaluation API. Each RPC has a unique ID and future; a dedicated reader
matches responses, skips stray non-protocol output, and a dead sidecar restarts.
Provider errors are surfaced rather than replaced by simulated answers.

## Search pipeline

`panini-core/capabilities/search/src/routes/api/ask.ts` validates requests and streams pipeline
events. `lib/pipeline.ts` orchestrates intent, concurrent engine lanes, ranking,
caching and partial failures. `lib/web-search.ts` retrieves cited sources through
the OpenAI key; `lib/jev.ts` uses the AI Gateway key for source, query, time and
relevance judgments. Only tool-returned URLs are admitted; platform filters use
site operators and hostname filtering. The memory cache holds at most 500
entries. In production the runtime snapshots the built handler and assets per
process so a rebuild cannot break a running instance; an installed runtime uses
its immutable files in place.

## Limits

Provider calls have deadlines. Browser runs default to `run_policy: continuous`
(no overall step/time cap; Stop, disconnect, completion or a stall ends them);
bounded runs accept 1–1000 steps and 5–3600 seconds. Concurrency: four
comparisons, two transcriptions, four search streams, one browser instruction;
excess work gets HTTP 429 with `Retry-After`. Request bodies are capped at 15 MiB,
audio at 10 MiB, comparison text at 20,000 characters and browser instructions at
5,000. A shared 60 paid-requests/minute limiter covers HTTP and comparison
sockets. Request IDs flow from gateway to backend; logs contain route templates,
status and duration, never bodies, query strings, audio or credentials.

## PANINI live conversation

`panini.live` owns the OpenAI Live connection, transcript context and delegation
IDs; `panini.conversation` owns task revisions, numbered results, follow-ups,
required questions and cancellation. Speech fragments are claimed exactly once
across native delegation, explicit controls and a settled-speech fallback.
Assistant speech is never execution evidence or authorization. Acknowledgements
cannot replace active work unless they answer a pending question. Event history,
conversation turns and captions are bounded; sessions are not persisted or
resumed automatically. The shell's voice uses WebRTC media negotiated through the
coordinator (`backend/panini/voice.py`); the console's live conversation uses the
same package through `backend/voice_service.py`.

## Engineering principles

Separation of concerns, explicit composition and dependency injection at real
external boundaries, following
[Microsoft's Clean Architecture guidance](https://learn.microsoft.com/en-us/dotnet/architecture/modern-web-apps-azure/common-web-application-architectures);
small, reviewable changes following
[Google's engineering practices](https://google.github.io/eng-practices/review/developer/small-cls.html).
Prettier and Ruff enforce style; `npm test` runs behavior tests and format checks
without provider calls; `npm run test:runtime` and `npm run panini:test:unified`
exercise the real runtime and shell. Human-AI interaction follows
[Microsoft's guidelines](https://www.microsoft.com/en-us/research/project/guidelines-for-human-ai-interaction/)
and [Google PAIR](https://pair.withgoogle.com/guidebook/): clear feedback,
limited authority, easy correction and graceful failure.
