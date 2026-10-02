# PANINI v2 harvest map

**Date:** 2026-10-02 · **Register:** [live_panini_version2_implementation_register.md](live_panini_version2_implementation_register.md)

Every source PANINI v2 learns from, what we take, how, and which item uses it. Status values:

- **Use as-is**: run the upstream component unmodified (pinned).
- **Port code**: copy and adapt code, with attribution (licence allows it).
- **Port pattern**: reimplement the design in PANINI's stack (no code copied).
- **Reference**: read for ideas; nothing taken directly.
- **Skip**: not taken, with the reason.

---

## 1. CopilotKit OpenDots (MIT)

Snapshot of 2026-10-01 in `panini-core/capabilities/new-capablities/`, moved to `references/opendots/` by
V2-00. TypeScript (Hono, CopilotKit runtime, TanStack AI, AG-UI, Tiptap). Requires CopilotKit's hosted
Intelligence service for threads, so it cannot be adopted as a runtime (ADR-001).

### 1.1 Server

| File | Status | What PANINI takes | Item |
| --- | --- | --- | --- |
| `src/server/computer-service.ts` | Port code (to Python) | Supervisor/computer API, per-Dot HMAC credential, identity and endpoint binding, 70 s deadline, 4 MB cap, secret redaction, 409 recovery message, permission checks before/during/after, `exec` result without the command | V2-11 |
| `src/server/computer-tools.ts` | Port pattern | Tools generated from schemas, `check()` first, owner-only `human_*` hidden from agents | V2-02, V2-11 |
| `src/server/computer-store.ts`, `computer-routes.ts` | Port pattern | Begin/finish audit with actor and outcome; routes for status, permissions, start/stop, control, actions | V2-04, V2-11 |
| `src/server/dot-agent.ts` | Port pattern | One agent class for every surface; permission snapshot + 100 ms watcher that aborts on change; client system/developer messages dropped; channel errors replaced by a generic message; untrusted-data prompt rules | V2-03, V2-06, V2-13, V2-18 |
| `src/server/headless.ts` | Port pattern | Server-initiated turns in persistent threads; clean detach | V2-06 |
| `src/server/voice.ts` | Port pattern | Call lifecycle (one call, activation deadline, 15 min cap), compute delegation idempotent per tool call and limited per call, hangup on every failure, provider errors without bodies, **call receipt** written into the thread ("summarize only confirmed decisions; transcript is untrusted"), late transcripts, paused sync resumed once | V2-16, V2-20 |
| `src/server/slack-channel.ts` | Port pattern | Tenant + user allow-lists, human actors only, created messages only, mention → subscribe → follow-ups, pause notice, `safeFailure` (class name and HTTP status only) | V2-17, V2-13 |
| `src/server/pages.ts`, `page-service.ts`, `page-tools.ts`, `page-routes.ts` | Port pattern | Pages with revisions, nested parents, page threads per page and Dot (reserved, recovered after restart), review records keyed by tool call, page tools scoped to Spaces, CAS on edits | V2-19 |
| `src/server/workspace.ts`, `workspace-routes.ts` | Port pattern | Dots (name, instructions, default Space, Space access list, research/memory permissions), thread bindings, calls table, captures | V2-18, V2-19, V2-06 |
| `src/server/runner.ts` | Port pattern | Lease ownership checked every 100 ms, 90 s limit, release on shutdown "queued for restart" | V2-25 (PANINI's `tasks.py` already has the core) |
| `src/server/runtime-scope.ts` | Port pattern | Exact route allow-list with method checks and agent/thread consistency ("suffix matches would validate a different thread") | V2-07 |
| `src/server/tanstack-tools.ts` | Reference | Tool definitions → model SDK tools | V2-02 |
| `src/server/research.ts` | Reference | Clearly fictional **sample mode** separated from live mode | V2-01 (fixtures are labelled, never mistaken for live evidence) |
| `src/server/app.ts`, `platform.ts`, `platform-config.ts` | Reference | Owner token required for non-loopback binding; setup state when unconfigured; exact app origin | V2-14, V2-00 |
| `src/server/shutdown.ts` | Reference | Ordered graceful shutdown | V2-08 (sidecar close) |
| `src/server/store.ts` | Skip | PANINI's harness store already covers tasks, runs, events, memories | — |
| `src/server/learning.ts`, `src/shared/learning.ts` | Skip | Depends on hosted Intelligence learning containers; PANINI has local learning | — |
| `src/server/index.ts` | Skip | Composition specific to OpenDots | — |

### 1.2 Browser service

| File | Status | What PANINI takes | Item |
| --- | --- | --- | --- |
| `src/browser/index.ts` | Port pattern | Isolated read-only capture: bearer secret, one request at a time, 40 s deadline, abort on disconnect, JS off, GET of document/stylesheet/image/font only, 50-request budget | V2-08 (`isolated` preset) |
| `src/browser/security.ts` | Port code (to Python) | Public-address validation (IPv4/IPv6 special ranges, `.local`, `.localhost`, standard ports only, no credentials) | V2-08 (`net_guard.py`) |
| `src/browser/transport.ts` | Port pattern | Resource fetch pinned to the validated address | V2-08 |

### 1.3 Client

| File | Status | What PANINI takes | Item |
| --- | --- | --- | --- |
| `src/client/ComputerToolCard.tsx` | Port pattern | Inline tool card: icons, human labels, states, detail line, terminal output, live preview only while visible | V2-07 |
| `src/client/PageReviewCard.tsx`, `page-review-decision.ts`, `src/shared/page-review.ts` | Port code | Restore-before-decide, idempotent save per tool call, review tool contract | V2-05, V2-07, V2-19 |
| `src/client/ComputerPanel.tsx` | Port pattern | Browser/Files/Terminal/Activity tabs, polling only while visible and running, revision counters, owner controls | V2-11, V2-12 |
| `src/client/editor/*` (`RichEditor.tsx`, `autosave.ts`, `use-page-autosave.ts`, `markdown.ts`, `slash-commands.ts`, `DocumentMenu.tsx`), `editor.css` | Port code | Tiptap 3 editor, slash commands, Markdown source mode, autosave with progress and failure-safe drafts | V2-19 |
| `src/client/PageDocument.tsx`, `PageOutline.tsx`, `PageConversation.tsx`, `SpaceLibrary.tsx`, `SpaceNav.tsx`, `SpaceWorkspace.tsx`, `page-context.ts`, `page-navigation.ts`, `page-snapshots.ts`, `page-chat-requests.ts` | Port code | Library (grid/list, search, nested pages), page document, outline, page conversation | V2-19 |
| `src/client/TaskActions.tsx`, `TaskPresentation.tsx`, `ChatTranscript.tsx`, `ResultPane.tsx`, `ThreadList.tsx`, `Chat.tsx` | Port pattern | Send → Stop in place keeping the draft; timeline with work updates, sources and receipts; thread list | V2-07, V2-15 |
| `src/client/CallView.tsx`, `useVoice.ts` | Port pattern | Call screen: timer, separate captions, mute, speaker mute, minimize into chat | V2-15 |
| `src/client/WorkspaceDialog.tsx` | Port pattern | Specialist settings UI (role, instructions, permissions, Spaces) | V2-18 |
| `src/client/App.tsx`, `style.css`, `api.ts`, `main.tsx`, `Mascot.tsx` | Reference / Skip | Layout ideas; PANINI has its own avatar and design system | — |

### 1.4 Deployment, tests, CI

| File | Status | What PANINI takes | Item |
| --- | --- | --- | --- |
| `compose.computers.yml`, `compose.computers-app.yml` | Port code | Supervisor and image built from the pinned OpenBot revision, loopback port, security options, health check | V2-11 |
| `deployment/computers/supervisor.Dockerfile`, `harden-supervisor.mjs`, `LICENSE.openbot`, `README.md` | Port code | Fail-closed per-computer credential patch | V2-11 |
| `compose.yml`, `Dockerfile`, `.dockerignore` | Skip | PANINI runs on Windows, not in containers | — |
| `tests/computer.test.ts`, `computer-deployment.test.js` | Port test cases | 12 behaviours | V2-11 |
| `tests/voice.test.ts` | Port test cases | 10 behaviours | V2-16 |
| `tests/slack-channel.test.ts` | Port test cases | 7 behaviours | V2-17 |
| `tests/page-service.test.ts`, `page-routes.test.ts`, `pages.test.ts`, `autosave.test.ts`, `page-review.test.tsx` | Port test cases | Page behaviours | V2-19 |
| `tests/security.test.ts` | Port test cases | URL guard cases | V2-08 |
| `tests/fixtures/model-stream.ts`, `headless-runtime.test.ts` | Port pattern | Scripted model streams | V2-01 |
| `.github/workflows/ci.yml` | Reference | CI layout (format, lint, typecheck, test) | V2-00 |

---

## 2. CopilotKit OpenBot (MIT, revision `b6932d31a8d6e7896c15139dfc27a6c6911deb27`)

| Component | Status | Item |
| --- | --- | --- |
| `agent-computer` image (browser, files, shell, takeover API) | Use as-is (built from the pin) | V2-11 |
| Supervisor (ensure/stop/list, ownership checks, volumes) | Use as-is with the hardening patch | V2-11 |

---

## 3. CopilotKit OpenMuse (MIT; reviewed in `docs/PANINI-AGENTIC-DESIGN.md` §5, quarantined)

| Module | Status | Item |
| --- | --- | --- |
| Durable worker (CAS leases, heartbeats, fenced checkpoints) | Already ported in v1 (`harness/tasks.py`) | — |
| Idempotent tool replay via operation hashes | Port pattern | V2-04 |
| Terminal tools (`finish_task`, `ask_user`, `prepare_*`) | Port pattern | V2-06, V2-05 |
| Reviewed actions (content hash, expiry, atomic claim, outcome unknown) | Port pattern | V2-05 |
| Goals and page watches with backoff | Port pattern | V2-25 (v1 has watches) |
| Identity (name, tone, avatar) | Port pattern | V2-18 |
| Hardened Docker computer (read-only root, `cap-drop ALL`, no network, limits, non-root) | Port pattern | V2-11 |
| Playwright worker with takeover console | Port pattern (via Playwright directly) | V2-08, V2-12 |
| Expo mobile and web client | Reference (native app later) | V2-15 §5.7 |
| Gmail and Calendar adapters | Prefer MCP connectors | V2-25 sources |
| CopilotKit runtime and Intelligence | Skip (hosted, lock-in) | — |
| UX patterns (send → Stop, follow-up queue, background updates linking to tasks, review screens with exact recipient and effect, editable identity and memory) | Port pattern | V2-07, V2-15, V2-05 |

---

## 4. Playwright 1.63.0 (Apache-2.0; installed at `node_modules/playwright-core`)

| Feature (where verified) | Status | Item |
| --- | --- | --- |
| Playwright MCP server (`lib/entry/mcp.js`, options in `coreBundle.js`) | Use as-is | V2-08 |
| 80 `browser_*` tools in groups core, core-input, core-navigation, core-tabs, devtools, network, pdf, storage, testing, vision | Use as-is behind PANINI policy | V2-10 |
| CDP relay: `CDPRelayServer`, `BrowserModel`, `ExtensionProtocolV2` (`src/tools/mcp/cdpRelay*.ts`, `browserModel.ts`) | Port code (to Python, scoped) | V2-09 |
| Extension relay protocol messages (`chrome.debugger.attach/sendCommand`, `chrome.tabs.create/remove`, `onEvent/onDetach/onCreated/onRemoved`, `extension.initialized`) | Port pattern (in Claw v4) | V2-09 |
| `page.ariaSnapshot({mode: "ai", boxes, depth})` and `aria-ref=` selectors | Use | V2-10, V2-26 |
| `browser_verify_*` assertions | Use (evidence) | V2-10, V2-26 |
| `browser_highlight`, `browser_generate_locator` | Use | V2-10, V2-22 |
| Tracing and the trace viewer (`show-trace`) | Use | V2-10 |
| Video with chapters and action call-outs | Use | V2-10, V2-12 |
| `page.screencast.start({onFrame})`, `showActions({cursor: "pointer"})` | Use | V2-12 |
| Recording user actions → code (`browser_start/stop_recording`, CLI `recording-start/stop`) | Use | V2-22 |
| `--codegen python|typescript` | Use (optional export) | V2-22 |
| Persistent profiles (`--user-data-dir`), isolated sessions, `--storage-state`, named sessions | Use | V2-08 |
| `--browser msedge|chrome` (installed browsers, no download) | Use | V2-08 |
| `--allowed-origins`, `--blocked-origins`, `--block-service-workers` | Use (defence in depth) | V2-03, V2-08 |
| `--init-script`, `--init-page` | Use | V2-08, V2-12 |
| `--secrets` masking | Use (masking only) | V2-08 |
| `--mobile` / devices | Use (isolated research) | V2-10 |
| `attach --cdp=chrome|msedge` (whole-browser remote debugging) | Skip by default (breaks I9) | V2-09 §5.9 |
| `_android` (`devices`, `AndroidDevice` actions, `AndroidInput`, `launchBrowser`, `webViews`) | Use | V2-21 |
| Agents: planner, generator, healer (`lib/agents/*.agent.md`) | Port pattern | V2-22, V2-01 |
| Playwright CLI skill docs (`lib/tools/skills/playwright-cli/`) | Reference | V2-08, V2-10 |
| `show --annotate` dashboard (person annotates a page; agent receives screenshot, snapshot and notes) | Reference (future "point at it" feature) | V2-12 open item |
| `_electron` automation | Already used by shell live tests | V2-01 |
| `page.consoleMessages()`, `pageErrors()`, `requests()` | Use through devtools tools | V2-10 |

---

## 5. Protocols and platform APIs

| Source | Use | Item |
| --- | --- | --- |
| AG-UI events and interrupts (`docs.ag-ui.com`), `ag-ui-protocol` (Python), `@ag-ui/client` | Event projection, interrupts/resume, client tools | V2-07 |
| OpenAI GPT-Live SIP (`live.transport.incoming`, accept/reject/refer/hangup, outbound `transport.type: "sip"`, sideband, DTMF, limits) | Phone line in and out | V2-16, V2-20 |
| Standard Webhooks (OpenAI webhook headers) | Ingress verification | V2-14 |
| Meta WhatsApp Cloud API (verify challenge, `X-Hub-Signature-256`, templates, 24-hour window) | WhatsApp channel | V2-17 |
| Slack Socket Mode (Bolt for Python) | Slack channel without a public URL | V2-17 |
| Telegram Bot API long polling | Telegram channel without a public URL | V2-17 |
| Web Push (VAPID, RFC 8291), WebAuthn, Web Share Target | Phone app | V2-14, V2-15 |
| Chrome `chrome.debugger` (`debugger` cannot be an optional permission) | Claw v4 | V2-09 |
| Electron `powerMonitor` | Presence | V2-24 |
| MCP spec (server side, annotations) and the `mcp` Python SDK 2.2.0 (in the lock) | PANINI as an MCP server | V2-23 |
| Tailscale Serve and Funnel | Private transport; webhooks with TLS ending on the PC | V2-14 |
