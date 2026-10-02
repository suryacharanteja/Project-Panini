# V2-07: AG-UI adapter and inline cards

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 1 · P1 · M (4–5 days) |
| Depends on | V2-05 (interrupts), V2-06 (run engine events) |
| Flag | `PANINI_V2_AGUI` (default `false`) |
| Status | Done (2026-10-02) |

## 1. Outcome

PANINI speaks AG-UI, the open agent-to-UI protocol: any AG-UI client (CopilotKit React components,
`@ag-ui/client`, other agent UIs) can hold a conversation with PANINI, watch tool calls and computer
activity stream in, and answer approvals through AG-UI interrupts. PANINI's own shell and console gain the
inline tool, computer and review cards that make OpenDots' chat feel alive.

## 2. Scope and non-goals

- In scope: AG-UI endpoint (run, connect), event projection, interrupts ↔ resume, client-declared tools,
  gateway routing, a shared card library (ToolCard, InterruptCard, QuestionCard, ChoiceCard, TakeoverCard)
  used by the shell and an Admin "Conversations" page, conformance tests.
- Not in scope: the live view stream itself (V2-12 supplies frames that the cards display), Spaces UI
  (V2-19), switching the shell's transport from NDJSON to AG-UI (optional later).

## 3. Current state

- Shell transport: NDJSON stream `GET /api/panini/sessions/{id}/events?after=` (`apps/shell/src/main/api.ts`),
  events from `EventHub` (`backend/panini/events.py`); renderer components `ExecutionPanel.tsx`,
  `ComputerUsePanel.tsx` (with `PanelBoundary` and `describeActivity`, added after the "empty object as a
  React child" crash recorded in project memory).
- Console: plain pages in `frontend/public/` (agent inbox, browser, live voice) and the React Admin
  (`panini-core/apps/admin`, React 19, TanStack Router, Tailwind 4).
- No AG-UI; `PANINI-AGENTIC-DESIGN.md` §5 recorded AG-UI as an "optional adapter later".

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| AG-UI spec (events, interrupts) | Event types incl. `ACTIVITY_SNAPSHOT/DELTA`, `SUBAGENT_*`, `RUN_FINISHED{outcome: interrupt}`, `RunAgentInput.resume` | Direct mapping (§5.3); Python SDK `ag-ui-protocol` (`ag_ui.core`, `ag_ui.encoder.EventEncoder`) |
| OpenDots `src/client/ComputerToolCard.tsx` | Inline card per computer tool: icon by action kind, human labels ("Opening website", "Running terminal command"), state (Working / Finished / Needs attention / Interrupted), detail line, terminal output, live preview refreshed every 3 s only while visible (`document.hidden`) | `ToolCard` keyed by PANINI tool names and hands; preview from live frames (V2-12) instead of polling screenshots |
| OpenDots `src/client/PageReviewCard.tsx` | Review card: restore-before-decide, disabled buttons until restore completes, "Nothing is saved until you approve", retry restore, Approve & save / Decline, "Open page" after approval | Generic `InterruptCard` for approval and review kinds; edit-before-approve |
| OpenDots `src/client/TaskActions.tsx`, `TaskPresentation.tsx`, `ChatTranscript.tsx` | Send button turns into Stop in place keeping the draft; work updates and receipts in the timeline | Shell composer and timeline (also listed in `PANINI-AGENTIC-DESIGN.md` §5 "UX patterns worth borrowing") |
| OpenDots `src/server/runtime-scope.ts` | Strict route allow-list with exact path matching, method checks, agent/thread consistency checks ("the SDK accepts suffix matches, so accepting arbitrary prefixes would validate a different thread") | The AG-UI routes validate `threadId` ↔ binding ↔ persona the same way |
| OpenDots `src/server/dot-agent.ts` | Client `system`/`developer` messages are dropped before the model sees them | Same rule (§5.5) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/routes/agui.py` | `POST /api/agui/agents/{persona}/run`, `POST /api/agui/agents/{persona}/connect` |
| `backend/agui/projection.py` | Engine events → AG-UI events |
| `backend/agui/input.py` | `RunAgentInput` validation, message filtering, resume handling, client tools |
| `frontend/server/gateway.mjs` | Add `/api/agui/` to the console-authenticated prefixes; no buffering for `text/event-stream` |
| `panini-core/apps/shared-ui/` (new) | React card library + CSS tokens shared by shell and Admin |
| `panini-core/apps/shell/src/renderer/` | Timeline renders cards from `tool.*` and `interrupt.*` events |
| `panini-core/apps/admin/src/pages/Conversations.tsx` | AG-UI chat with `@ag-ui/client` `HttpAgent` |
| `backend/requirements.txt` | `ag-ui-protocol` (pinned) |

### 5.2 Endpoint contract

`POST /api/agui/agents/{persona}/run` with `RunAgentInput`:

| Field | Handling |
| --- | --- |
| `threadId` | Bound to a PANINI thread via `thread_bindings(surface="agui", external_id=threadId)`; created on first use |
| `runId` | Used as the AG-UI run id; the PANINI run id is mapped in `runs.task_id`-style metadata |
| `messages` | Only the **last user message** is a new turn; earlier messages are the client's copy and are ignored (PANINI's thread is the authority). Messages with role `system` or `developer` are dropped. |
| `tools` | Client tools (§5.4) |
| `state`, `context` | Stored as untrusted context for the run (tainted label `client_context`) |
| `forwardedProps` | Ignored except `{"panini": {"surface": "console"}}` |
| `resume[]` | Must address every open interrupt of the thread's parked run; each entry resolves one interrupt via V2-05 (`decided_by.surface="agui"`, `method="tap"`); then the parked run continues and streams under this `runId` with `parentRunId` = the interrupted run |

Response: `text/event-stream` encoded with `EventEncoder`. Auth: console session (gateway) or shell bearer;
`persona` must exist (V2-18; `panini` until then).

`POST /api/agui/agents/{persona}/connect {threadId}`: `MESSAGES_SNAPSHOT` + `STATE_SNAPSHOT` (plan, open
interrupts) and then live events of the thread's active run, if any.

### 5.3 Event projection

| Engine event | AG-UI events |
| --- | --- |
| `run.started` | `RUN_STARTED{threadId, runId, parentRunId?}` |
| `run.message.delta` (or a final message) | `TEXT_MESSAGE_START{messageId, role:"assistant"}` → `TEXT_MESSAGE_CONTENT{delta}` … → `TEXT_MESSAGE_END` |
| `run.plan` | `STATE_DELTA` JSON Patch on `/plan` (first time: `STATE_SNAPSHOT{plan, interrupts: []}`) |
| `tool.started` | `TOOL_CALL_START{toolCallId, toolCallName, parentMessageId}` → `TOOL_CALL_ARGS{delta: args_view JSON}` → `TOOL_CALL_END` |
| `tool.finished` | `TOOL_CALL_RESULT{messageId, toolCallId, content: result_view}` |
| `live.frame` | `ACTIVITY_SNAPSHOT{messageId: "computer:<hand>", activityType: "computer", content: {hand, frameUrl, url, title, phase}}` then `ACTIVITY_DELTA` patches |
| `subrun.started/finished` | `SUBAGENT_STARTED{subagentRunId, name}` / `SUBAGENT_FINISHED{subagentRunId, outcome}` |
| `receipt.recorded` | `CUSTOM{name: "panini.receipt", value: {receiptId, tool, effect, outcome}}` |
| `interrupt.opened` | `RUN_FINISHED{outcome: {type: "interrupt", interrupts: [{id, reason, message, toolCallId, responseSchema, expiresAt, metadata: {kind, title, preview, level, nonce, actionDigest}}]}}`; the SSE stream ends; the PANINI run stays parked |
| `run.finished` | `RUN_FINISHED{outcome: {type: "success"}, result: {outcome, message, evidenceIds, results}}` |
| fault | `RUN_ERROR{message: safe text, code}` |

`args_view` and `result_view` are the ledger's redacted views (V2-04), never raw arguments. Frames are
referenced by artifact URL (authenticated), never inlined in AG-UI events.

`nonce` and `actionDigest` travel in interrupt metadata because the resume must echo them (I3). A `resume`
entry carries them in `metadata: {nonce, actionDigest}`; `payload` is validated against `responseSchema`.

### 5.4 Client tools

Tools in `RunAgentInput.tools` are offered to the model for this run as `client_<name>` with effect
`observe` and results marked tainted. When the model calls one, the adapter emits the tool call events and
then `RUN_FINISHED{outcome: interrupt, reason: "client_tool", toolCallId}`; the client executes the tool and
resumes with `payload = {result}`. This is how CopilotKit frontend tools and generative-UI actions work, so
AG-UI frontends can add UI actions without PANINI code changes.

### 5.5 Security

- Client-supplied `system`/`developer` messages, `state` and `context` never become instructions.
- One active run per thread; a second `run` while one is active returns `RUN_ERROR{code: "THREAD_BUSY"}`
  (the console then calls `connect`).
- Resume entries are checked by V2-05 rules (digest, nonce, expiry, level). `verify` and `local` levels
  cannot be resolved through AG-UI from the console unless the console is on loopback (`local`) or carries a
  passkey assertion (V2-15).
- The endpoint is console/shell only; it is not exposed on the phone listener or the relay.

### 5.6 Shared card library

```text
panini-core/apps/shared-ui/
  cards/ToolCard.tsx         labels per tool family, state chip, detail, output, live preview slot
  cards/InterruptCard.tsx    approval + review (restore-before-decide, preview, edit, approve/decline)
  cards/QuestionCard.tsx     free-text answer with draft retention
  cards/ChoiceCard.tsx       numbered options (voice "the second one" still works through the thread)
  cards/TakeoverCard.tsx     "Your turn: sign in, then press Done"
  cards/ReceiptChip.tsx      receipt link with effect and outcome
  boundary.tsx               PanelBoundary (moved from the shell; every card renders inside one)
  text.ts                    describeActivity-style helpers: only strings reach the DOM
  tokens.css                 Obsidian/copper tokens (shared with docs/PRODUCT-DESIGN-STANDARDS.md)
```

Rules carried from the shell's crash fix: every card renders inside a boundary; props are normalized to
strings before rendering; a malformed event drops one card, never the interface (the heartbeat must keep
running). Accessibility: cards are `section`s with `aria-label`; state chips use text, not color alone.

### 5.7 Shell integration

The shell keeps its NDJSON stream and renders cards from `tool.started/finished`, `interrupt.opened/resolved`
and `live.frame` events. The confirmation panel becomes `InterruptCard` (same nonce/digest flow through
`/api/panini/interrupts/{id}/resolve`). The composer adopts send → Stop in place with the draft kept.

### 5.8 As built (2026-10-02)

- Backend: `backend/agui/input.py` (validation with `ag_ui.core.RunAgentInput`, last-user-message turn,
  `system`/`developer` dropped, client state/context appended as labelled untrusted data, client tools,
  resume coverage rule), `projection.py` (`Projector`: events per §5.3; tool arguments and results are the
  ledger's redacted views; interrupt metadata carries `nonce` and `actionDigest`), `runner.py` (`AguiRuns`).
  `ag-ui-protocol==1.0.0` pinned (requirements and Windows lock).
- Routes: console `/api/agui/agents/{persona}/run|connect|stop` (behind the console sign-in) and the same
  under `/api/panini/agui/` for the shell (controller bearer). 404 unless `PANINI_V2_AGUI` is on. The gateway
  routes `POST /api/agui/` with no proxy timeout (SSE).
- A conversation is a PANINI thread bound with `thread_bindings(surface="agui")`; one run per thread
  (`THREAD_BUSY`). Each run is an agent run with `durable=True` (run record and checkpoints even without
  `PANINI_V2_ONE_BRAIN`). Approvals from the agent, the policy hook or a v1 adapter open an approval interrupt
  and park the run (`RUN_FINISHED{outcome: interrupt}`); the resume request resolves it through V2-05
  (`decided_by surface=agui, method=tap`), pre-approves the action digest once, and continues **at the
  pending tool call** — the agent loop now checkpoints the rest of a parked round (`pending`) and runs it
  first on resume, so the model is not asked again. Declining ends the run: "Okay, I did not do that.
  Nothing was changed." A new message instead of an answer cancels the waiting step. A wrong nonce or digest
  is refused (`INTERRUPT_CHANGED`); answering twice is refused without repeating anything.
- The same pending-call resume now applies to parked background runs (V2-05/V2-06): with one brain on, the
  approved call runs directly from the checkpoint (`test_background_task_parks_resumes_and_does_not_repeat_effects`).
- Client tools (§5.4): offered to the model as `client_<name>`; a call parks the run on a `question`
  interrupt with `reason: client_tool` and `toolCallId`; the resume payload `{result}` becomes the tool output.
- Console runs get the hands that do not share the avatar's screen: `PANINI_AGUI_CAPABILITIES`
  (default `search,code`) plus the agent's own computer tools; Desktop and the person's Chrome stay with
  the avatar until the sandbox and Playwright hands (V2-08, V2-11) arrive.
- Event fan-out: every stream of a thread receives the run's events; `connect` sends `MESSAGES_SNAPSHOT`
  and `STATE_SNAPSHOT{plan, interrupts, running}` and then live events of an active run.
- UI: `panini-core/apps/shared-ui/` (`ToolCard`, `InterruptCard`, `QuestionCard`, `ChoiceCard`,
  `TakeoverCard`, `ReceiptChip`, `CardBoundary`, string-only `text.ts`, the `applyEvent` conversation
  reducer and plain-CSS `tokens.css`). Admin **Conversations** page (`/conversations`) on a small fetch/SSE
  client (`src/agui.ts`) rather than `@ag-ui/client`'s `HttpAgent`: PANINI needs explicit resume entries
  with nonce/digest and thread re-attach, and the official schemas are still enforced by the conformance test.
  The shell renders a `ToolCard` per agent tool call (task events with `agent_event: tool.started/finished`,
  redacted) inside `PanelBoundary`; confirmations stay on the shell's native dialog (it cannot be clicked by
  page content), so `InterruptCard` is used in the console only.
- Conformance: `scripts/agui-fixture.py` records real streams (tool call, approval interrupt, resume,
  connect) into `panini-core/apps/admin/test/fixtures/`; `admin/test/agui.test.tsx` validates every event
  with `@ag-ui/core/schemas` `EventSchemas` and renders every card with malformed props.
- Found and fixed while testing: ledger `mask()` let a `?token=` value through in command previews (V2-04);
  it now strips secret query values first everywhere. Newer Chromium returns a Promise from
  `scrollIntoView`, which crashed an effect that returned it (fixed with a block body).
- Live check (dev server with the v2 flags, signed-in console): a listing question streamed tool cards and
  the answer; "delete <scratch file>" showed the approval card, nothing ran until Approve, then the run
  resumed at the pending command and deleted the file; Decline left the file untouched and the card showed
  "Interrupted".

## 6. Implementation steps

1. Add `ag-ui-protocol`; `input.py` validation; `projection.py` with golden-event tests.
2. `run` endpoint for a simple turn (no tools) end to end; gateway prefix; SSE through the proxy.
3. Tool events, plan state, receipts, sub-runs.
4. Interrupt → `RUN_FINISHED{interrupt}` → resume round trip; client tools.
5. `connect` endpoint (snapshot + live).
6. `shared-ui` cards; shell timeline and InterruptCard; send → Stop composer.
7. Admin Conversations page on `@ag-ui/client` `HttpAgent`.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Projection mapping per event type; redaction preserved | `backend/tests/agui/test_projection.py` |
| Unit | Input filtering (system/developer dropped), resume coverage rule, expiry | `backend/tests/agui/test_input.py` |
| Conformance | Recorded SSE parsed by `@ag-ui/client` / `@ag-ui/core` schemas without errors | `frontend/test/agui-conformance.test.mjs` |
| Integration | Interrupt → resume → run completes; second resume is idempotent | `backend/tests/agui/test_resume.py` |
| UI | Cards render malformed props without throwing (the crash class from project memory) | `panini-core/apps/shared-ui/test/*.test.tsx` (vitest) |
| Live | Admin Conversations: approval round trip with a real tool | manual + `panini-core/apps/shell/test/unified.mjs` extension |

## 8. Acceptance criteria

- [x] `@ag-ui/client` streams a PANINI run with tool calls and an approval interrupt, and resumes it.
- [x] Shell shows ToolCard, InterruptCard and live preview; v1 confirmation tests still pass.
- [x] Malformed-event test proves one bad card cannot unmount the interface.

## 9. Rollout and rollback

Flag gates the endpoint and the new shell cards. Rollback: flag off; shell falls back to v1 panels.

## 10. Open questions

- Should PANINI also accept AG-UI runs from other machines (for example a teammate's CopilotKit app)? Not in
  v2: that is an external principal and belongs behind the MCP/A2A boundary (V2-23) with its own grants.
