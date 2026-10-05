# V2-23: PANINI as an MCP server

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · M (4–5 days) |
| Depends on | V2-02 (registry), V2-03 (policy), V2-05 (interrupts); V2-13 gate |
| Flag | `PANINI_MCP_SERVER` (default `false`) |
| Status | In progress |

## 1. Outcome

Other agents on your PC (Claude Code, Codex, any MCP client) can borrow PANINI's hands: see your Windows
desktop and attached Chrome tabs, ask PANINI to do things in Windows apps, search, read your Spaces, or run
a whole PANINI task. Observation works freely; every effect asks you first, on the avatar or your phone, and
every call is receipted.

## 2. Scope and non-goals

- In scope: an MCP server over Streamable HTTP on loopback (through the gateway) and a stdio bridge, client
  registration with tokens and grants, a curated tool set mapped from the registry, annotations, progress
  for long tasks, image results, approvals and receipts, Admin setup snippets.
- Not in scope: exposing PANINI to other machines (A2A covers cross-agent messaging), MCP sampling
  (PANINI does not run the client's model), exposing memory by default.

## 3. Current state

PANINI is an MCP **client** (`harness/connectors.py` stdio, `harness/mcp_http.py` Streamable HTTP with OAuth)
and an A2A server (`harness/a2a.py`: per-agent tokens stored hashed, every request waits for approval, runs
without the person's memories). The `mcp` Python SDK 2.2.0 is in the lock file.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| PANINI A2A server (`a2a.py`) | Tokens created in the console, stored as hashes, revocable; requests run without memories; replies are data | Same client model for MCP |
| Playwright MCP's host check (`installHttpTransport`: requests must target an allowed `Host`, default the bound host) and its `--allowed-hosts` | DNS-rebinding defence for local HTTP MCP | Gateway already enforces a host allow-list and Origin checks (`frontend/server/security.mjs`); the MCP route requires `Authorization: Bearer` and no `Origin` (non-browser clients) |
| MCP tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) | Telling clients what tools do | Generated from effect classes |
| Playwright MCP tool naming and snapshot-with-refs results | Familiar vocabulary for coding agents | Chrome/managed tools exposed with the same `browser_*` semantics |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/mcp_server/server.py` | MCP server (official `mcp` SDK, pinned 2.2.0) with tool handlers |
| `backend/mcp_server/tools.py` | Curated mapping from registry tools |
| `backend/mcp_server/clients.py` | Client tokens (hashed), grants, last use |
| `backend/routes/mcp.py` | `/mcp` Streamable HTTP endpoint (backend), proxied by the gateway |
| `scripts/panini-mcp.py` (+ packaged `panini-mcp.exe` via the CLI spec) | stdio ↔ HTTP bridge for stdio-only clients |
| `backend/harness/migrations/0016_mcp_clients.sql` | `mcp_clients` |
| Admin `src/pages/Agents.tsx` | MCP clients next to A2A clients: create, copy setup, grants, revoke |

### 5.2 Endpoint and auth

- `POST/GET http://127.0.0.1:3200/mcp` (gateway → backend), Streamable HTTP; `Authorization: Bearer <token>`.
- Requests with an `Origin` header are refused (browsers must not reach it); `Host` must be loopback.
- One token per client (`mcp_clients(id, name, token_hash, grants, created, last_used, revoked)`); principal
  `Principal(kind="mcp_client", surface="mcp", client_id=id)` → policy tier **external** (V2-03).
- `panini-mcp` (stdio) reads the token from `%LOCALAPPDATA%\PANINI\mcp\<client>.token` (created in Admin,
  user-only ACL) and forwards JSON-RPC to `/mcp`.

Setup snippets shown in Admin:

```bash
claude mcp add --transport http panini http://127.0.0.1:3200/mcp --header "Authorization: Bearer <token>"
```

```toml
# Codex ~/.codex/config.toml
[mcp_servers.panini]
command = "panini-mcp"
args = ["--client", "codex"]
```

### 5.3 Tools exposed

| MCP tool | Maps to | Effect | Annotations |
| --- | --- | --- | --- |
| `panini_status` | runtime status, hands readiness | observe | readOnly |
| `panini_ask(goal, persona?)` | a full background-mode run as the client principal; progress notifications with plan steps | dynamic (each inner effect asks) | openWorld |
| `desktop_windows`, `desktop_screenshot(window?)` | desktop observation (image content) | observe | readOnly |
| `desktop_do(instruction)` | coarse desktop tool | submit dyn | destructive (asks) |
| `browser_snapshot`, `browser_take_screenshot`, `browser_find`, `browser_verify_*`, `browser_navigate`, `browser_click`, `browser_type`, `browser_select_option`, `browser_press_key`, `browser_tabs`, `browser_wait_for` | V2-10 tools on the active browser (`browser_select` allowed: managed by default for MCP clients; your Chrome needs the client's grant) | as V2-10 | from effect |
| `sandbox_*` (when granted) | V2-11 | as V2-11 | from effect |
| `search_web(query)` | `search_run` | observe | readOnly, openWorld |
| `pages_search`, `pages_read` (when granted) | V2-19 | observe | readOnly |
| `receipts_search(query, since)` | `activity_search` | observe | readOnly |

Not exposed by default: memory tools, connectors, A2A, channels sends, telephony, PANINI-CLI (code agents
usually have their own), host `computer_run_command` (the client already has a shell).

### 5.4 Policy and approvals

- Default grants for a new client: observe tools on desktop, managed browser, search; nothing else.
- Every effect from an MCP client opens an interrupt titled "<client> wants to …" (for example "Claude Code
  wants to click 'Deploy' in Chrome"); `tap` at minimum, `verify` for classes the external tier requires; the
  tool call blocks until decided (with MCP progress notifications) or returns `isError` with "The owner
  declined." Clients can never resolve interrupts (I8).
- Per-client expiring grants (for example "allow Claude Code to click in the managed browser for 1 hour") are
  created from the interrupt card ("Allow for this session").
- The avatar shows "Claude Code is using your desktop" while a client holds a hand slot.

### 5.5 Results

Tool results are text plus images where useful (screenshots as MCP image content, size-limited to 1 MB);
results include receipt ids. `panini_ask` returns `{outcome, message, receipts[]}`.

## 6. Implementation steps

1. Clients store and Admin UI (tokens, grants, revoke); setup snippets.
2. `/mcp` endpoint with the SDK; auth, host and origin checks; gateway route.
3. Tool mapping with annotations; observe tools first; image results.
4. Effects with interrupts and expiring grants; slot ownership display.
5. `panini_ask` with progress notifications.
6. `panini-mcp` stdio bridge and packaging.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Token hashing, revoke, grants; origin refused; wrong host refused | `backend/tests/mcp_server/test_auth.py` |
| Unit | Annotations from effect classes; hidden tools absent from `tools/list` | `backend/tests/mcp_server/test_tools.py` |
| Integration | MCP client (SDK) lists tools, takes a desktop screenshot, requests a click → interrupt → approve → receipt | `backend/tests/mcp_server/test_flow.py` |
| Invariant | I8: an MCP client cannot resolve its own interrupt | same |
| Live | Claude Code connected over HTTP: screenshot of Notepad; click in the managed browser after approval | evidence log |

## 8. Acceptance criteria

- [ ] Claude Code and Codex can connect (HTTP and stdio) and list PANINI tools.
- [ ] Observation works without prompts; every effect asks the owner; receipts show the client.
- [ ] Revoking a client token stops further calls immediately.

## 9. Rollout and rollback

Opt-in per client in Admin, after the V2-13 gate. Rollback: flag off → `/mcp` returns 404; tokens remain.

## 10. Open questions

- Expose a `desktop_snapshot` with refs for coding agents (fine-grained desktop control)? That arrives with
  V2-26; until then `desktop_do` plus screenshots.

## Implementation checkpoint — 2026-10-05

The official SDK 2.2.0 HTTP endpoint, principal-bound browser contexts, client registration/revocation,
mandatory effect approvals, receipts, image/progress results, private-data-free task runner, Admin setup
and stdio bridge are implemented. Migration `0031_mcp_clients.sql` stores token hashes; one-time token
files are created atomically in a protected per-client directory. Admin provides the actual token file
path through `--token-file`; client ids are UUIDs, not display names. Temporary exact-tool browser grants
are available from Admin, for at most one hour. Submission and stronger effects still require approval.

Source tests use real loopback HTTP and official SDK clients, with fixture desktop/browser content and
a scripted task model. They do not qualify live Claude Code/Codex hands, live model planning, or hardware.
See [qualification record](../qualification-mcp-2026-10-05.md).

Still open: sandbox opt-in tools, persona selection beyond PANINI, grants created directly from the
interrupt card, activity displayed for actual slot ownership, live Claude Code/Codex connection and
approved browser/desktop task qualification, and production release qualification. The owner deferred
red-team work; it is disabled and is not a current enablement gate. Other policy, pairing and receipt
checks remain enforced. This LLD remains **In progress** until its remaining requirements are verified.
