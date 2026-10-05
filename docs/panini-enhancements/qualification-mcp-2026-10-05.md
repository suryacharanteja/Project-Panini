# V2-23 MCP development qualification — 2026-10-05

## Delivered implementation

- The official pinned MCP SDK 2.2.0 serves the exact loopback `/mcp` endpoint through the gateway.
  Every request authenticates with the calling client's bearer token, independently of the owner
  console. Origin headers, non-loopback hosts/peers and borrowed transport sessions are refused.
- Admin registers named clients and explicit scopes, displays setup once, creates temporary exact-tool
  browser permissions, removes permissions and revokes clients. Database records contain token hashes;
  stdio token files are created atomically in a directory with a protected current-user Windows ACL.
- Curated registry tools provide status, permitted Windows observations, approved coarse desktop tasks,
  isolated browser workflows, search, explicitly granted notebook reads and the client's own receipts.
  The external task runner receives neither owner memories nor owner skill/connector catalogs. Hidden
  shell, memory, connector, messaging and telephony tools cannot be called by name.
- Every effect requires owner approval even when the general policy mode is off or shadow. Temporary
  grants last at most one hour and cover exact low-risk browser tools; submissions and stronger effects
  still ask. A client cannot approve its own request. Grounded browser targets are refreshed after review.
- Results include receipts and size-limited screenshot images. Progress uses the official SDK session API.
  Revocation, feature shutdown, owner pause and disconnected effect streams cancel the running hand;
  interrupted or failed receipts retain uncertainty after dispatch. Idle cleanup closes only that session.
- The stdio bridge is packaged as `runtime/cli/panini-mcp.exe`. Admin's Codex setup passes the generated
  UUID and explicit protected token-file path. The avatar receives the active client/tool summary.

## Source evidence

`backend/tests/mcp_server` contains 34 passing tests using official SDK clients and actual loopback HTTP
and stdio transports. Desktop/browser contents are fixtures; the task-planning model is scripted.
Checks include token protection, scopes, session identity, images/progress, owner-only approvals and
grants, repeated effects, decline and changed targets, revocation/disconnection, private-data isolation,
explicit notebook reads, independent sibling-session cleanup and conservative failure receipts.

The whole-project gate covers the backend, offline scorecard, CLI, web, phone, Admin and shell suites,
formatting and typechecks. Final counts and packaged checks are recorded under `artifacts/mcp-server/`. The initial failed full-project run is retained as
`first-full-check.log`; legacy browser context compatibility and SSE test-server shutdown isolation were
fixed before the final gate.

The built Admin passed headless Chrome interaction checks at desktop and phone widths: register,
one-time setup, temporary permission, revocation, no overflow and no browser errors.
Those UI checks use fixture API responses; they do not qualify a physical Android device.

Reproduction:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/mcp_server -q
node --test frontend/test/mcp-gateway.test.mjs
npm test
node scripts/panini-mcp-ui-smoke.mjs
npm run panini:package -- --unsigned
.venv\Scripts\python.exe scripts/panini-mcp-packaged-smoke.py
```

## Open requirements

V2-23 remains **In progress**. Live Claude Code and Codex sessions, real approved desktop/browser
actions and live-provider task qualification remain open. The four local implementation gaps from
the first checkpoint are implemented and tested in the follow-up below.

The unsigned local installer is a development release. Code signing, clean-machine installation,
production deployment and physical Android qualification are not completed. The full LLD program
remains active. Red-team work is disabled and deferred by owner instruction; it is future development.

## Compatibility fixes found during qualification

The gateway now forwards the modern SDK's method/name and annotated parameter headers as well as
legacy session headers; the bundled-runtime smoke verifies both protocol paths. Optional task-tool
fields are converted to required nullable fields only for the strict model schema, then omitted again
before the original registry validates execution. This follows the [official function-calling
requirements](https://developers.openai.com/api/docs/guides/function-calling), and a scripted model
regression verifies nullable arguments without changing the MCP wire or registry schemas. The bridge
packager excludes the SDK's optional upstream CLI module; no additional CLI dependency is installed.

## Follow-up: sandbox, personas, card grants and hand ownership

- Explicit `sandbox` scope exposes curated sandbox tools, excluding host import/export. Each client
  gets a separate AppContainer SID and persistent workspace. Initial opt-in enables that computer's
  permissions once; subsequent calls cannot restore revoked permissions. Client revocation disables
  its computer and closes it without affecting other clients.
- Native commands accept a thread-safe cancellation signal, poll it at 50 ms, terminate the Windows
  job tree and drain workers before releasing the hand. Repeated cancellation cannot abandon the
  OS worker. Cancelled, timed-out and unsuccessful commands no longer report a successful tool result.
- Admin explicitly shares individual personas, including their instructions. `panini_ask` advertises
  only authorized active ids. A task retains the MCP client principal, receives only the intersection
  of client scope and persona permissions, and cannot access owner memory, connectors or host shell.
  Notebook reads honor the persona's space access; persona revocation cancels active hands.
- Eligible navigation/input cards offer an exact browser tool for one hour. The permission descriptor
  belongs to the action digest, the phone signs the additional decision field, expiry is enforced and
  token revocation removes grants. Submission/stronger actions cannot use this exemption.
- Registry activity begins only after its hand slot is acquired, after approval, and clears in `finally`.
  A queued sibling cannot overwrite the active owner; child task hands carry their own run ids.

Source verification: **39 focused MCP tests**, including real AppContainer execution/isolation and a
continuous write command stopped on revocation; **65 combined MCP/sandbox tests**. Whole-project
gate: **1,212 backend tests**, **125 CLI tests plus one skip**, and **16/16 offline scorecard cases**,
with web, phone, Admin, shell, formatting and typecheck checks. Five backend warnings remain: four
dependency deprecations and a Windows Proactor pipe cleanup warning. These are retained in the log.

Built Admin was checked in real Chromium at desktop and 412 px widths with fixture API responses:
scope selection, temporary permission card, matching nonce/digest, secret removal and no overflow.
The Android-sized phone smoke uses real encrypted fabric and WebCrypto decisions, a virtual passkey,
fixture calls/live frames and notebook operations. It now covers signed temporary card permissions.
The preview harness was updated for the current outbound-call route. This is browser emulation,
**not physical Android or SIP qualification**.

## Native CLI connection follow-up

The packaged smoke now supports `--native-clients`. Against the frozen runtime it registers a
disposable client, configures Claude Code through its actual `mcp add-json` command in an isolated
`CLAUDE_CONFIG_DIR`, and observes `mcp list` report the stdio server as connected. The same run checks
that Codex CLI accepts an isolated stdio configuration. The official SDK still verifies HTTP and stdio
tool discovery and approved sandbox effects. Codex configuration parsing is **not** an active agent
connection or tool discovery. Neither native CLI made a model request or performed desktop/browser
work. Evidence: `artifacts/mcp-native/packaged-smoke.json`.

Admin now offers Claude Code PowerShell stdio and Codex stdio setup using the protected token file.
It no longer renders the raw bearer token in a shell command. The built Admin browser smoke checks
that the one-time setup contains the stdio command and omits the bearer token. Real Claude Code and
Codex model sessions, approved desktop/browser effects and live model planning remain open.

Follow-up evidence lives in `artifacts/mcp-completion/`. The packaged smoke executes approved UTF-8
file writes and a native AppContainer command using the frozen backend and shipped gateway. Its
result and the release hash verifier must pass before the follow-up installer is delivered. This is
an unsigned local development increment; the full LLD program remains active, with V2-24 next.
