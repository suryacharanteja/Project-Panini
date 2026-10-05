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

`backend/tests/mcp_server` contains 33 passing tests using official SDK clients and actual loopback HTTP
and stdio transports. Desktop/browser contents are fixtures; the task-planning model is scripted.
Checks include token protection, scopes, session identity, images/progress, owner-only approvals and
grants, repeated effects, decline and changed targets, revocation/disconnection, private-data isolation,
explicit notebook reads, independent sibling-session cleanup and conservative failure receipts.

The final whole-project gate includes 1,206 passing backend tests (five dependency/Windows transport
warnings), the offline scorecard, CLI, web, phone, Admin and shell suites, formatting and typechecks.
Release logs and packaged checks are recorded under `artifacts/mcp-server/`. The initial failed full-project run is retained as
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

V2-23 remains **In progress**. Sandbox opt-in tools, persona selection beyond PANINI, grants directly
from interrupt cards, actual hand-slot ownership presentation, live Claude Code and Codex sessions,
real approved desktop/browser actions and live-provider task qualification remain open.

The unsigned local installer is a development release. Code signing, clean-machine installation,
production deployment and physical Android qualification are not completed. The full LLD program
remains active. Red-team work is disabled and deferred by owner instruction; it is future development.
