# Jev-Rev / PANINI: Project and Code Audit

## Resolution status (updated 2026-09-24, same day)

| ID | Status | Resolution |
| --- | --- | --- |
| H1 | Open (owner) | No version control yet. The owner will initialize and push the repository. |
| H2 | Fixed | `artifacts/` (with both key copies) moved out of the project to a quarantine folder. Rotate the keys if it was ever shared. |
| M1 | Fixed | Extension device pairing plus a mutual HMAC handshake. Unpaired clients, other extension origins and wrong secrets are refused (unit tests plus `npm run test:runtime`). |
| M2 | Fixed | The extension verifies the server's proof before accepting anything, backs off 1–30 s, and never sends its secret. |
| M3 | Fixed | The watchdog catches and logs every per-session error; `end()` runs every cleanup step. Regression tests reproduce the original failure. |
| M4 | Fixed | The coordinator logs task failures, cleanup failures and watchdog errors. |
| L1 | Fixed | Every popup command rejects content-script senders. |
| L2 | Fixed | The global content script is removed; it is injected on demand into attached tabs. |
| L3 | Fixed | One port configuration (3200/8200/3230) shared by the runtime and shell; a clear message when a runtime is already running. |
| L4 | Fixed | The sidecar reader skips non-protocol lines and never reuses an unread process (regression test). |
| L5 | Fixed | `.env.example` documents the variable the launcher actually reads; `find_codex` leftovers removed. |
| Q1 | Fixed | `panini-core/capabilities/cli/tests`, `scripts/` and `panini-core/capabilities/desktop/tests` are in `npm test`; the runtime smoke is in CI. |
| Q2 | Fixed | Moved to quarantine with the other dead code (the project went from ~9,200 source files to ~400). |
| Q3 | Fixed | The unused 91 MB Rust fork was removed; the launcher runs the installed Codex CLI, and no Codex source ships. |
| Q4 | Open | `Coordinator._run` and the shell's large files remain candidates for extraction. |
| Q5 | Corrected | `.gitignore` has no duplicate. The duplicate was in `.prettierignore`, printed next to it. |

The architecture was also unified: one runtime now serves the PANINI avatar, the
signed-in web console and the extension at the same time. See
[ARCHITECTURE.md](ARCHITECTURE.md).

---

**Date:** 2026-09-24
**Scope:** `D:\AI\Jev-Rev-PANINI` (whole tree, with first-party code reviewed by hand)
**Method:** automated checks (tests, lint, typecheck, dependency audit, secret scan), manual review of security-critical paths, and two scripted reproductions of suspected defects.

---

## 1. Summary

The core code is in good shape. The security design is deliberate and layered: per-launch secrets, fenced controller leases, nonce-bound confirmations, HMAC-signed desktop IPC, and a hardened Electron shell. Every automated check passes, and both dependency audits come back clean.

The main risks are at the **edges of the system and in project hygiene**, not in the core logic:

1. **The project isn't under version control.** There is no `.git` anywhere in the tree.
2. **Live API keys are copied outside `.env`**, into `artifacts/removed-source/`.
3. **The browser-extension link isn't authenticated in the default profile.** The extension also never authenticates the server it connects to.
4. **The coordinator's watchdog can die silently.** Both this and #3 were confirmed by scripted reproduction.

| Severity | Count |
| --- | --- |
| High | 2 |
| Medium | 4 |
| Low | 5 |
| Quality / process | 5 |

---

## 2. What was checked

### 2.1 Inventory

| Area | Language | Approx. size | Role |
| --- | --- | --- | --- |
| `backend/` | Python (FastAPI) | ~7k LOC | API, PANINI coordinator, capability adapters |
| `frontend/` | Node (Express) + vanilla JS | ~5k LOC | Gateway, legacy UI, sockets |
| `extension/` | JS (Chrome MV3) | ~1.7k LOC | Browser executor ("Claw") |
| `panini-core/apps/shell/` | TypeScript (Electron) | ~2.5k LOC | Desktop shell, confirmations |
| `panini-core/capabilities/search/` | TypeScript (TanStack Start) | ~3k LOC | Ranked web search |
| `panini-core/capabilities/cli/src` | Python | ~1k LOC | Supervision layer for the coding engine |
| `panini-core/capabilities/desktop/` | C# (.NET 10) | ~1.3k LOC | UIA broker/worker, input |
| `panini-core/voice/live-spoken-conversation/` | Python | ~0.8k LOC | Voice conversation |
| `panini-core/capabilities/cli/panini-core/capabilities/cli-rs` | Rust | 91 MB, ~7.8k files | **Vendored**, rebranded OpenAI Codex snapshot (Apache-2.0) |

In total there are about **33k lines of first-party code across ~230 files**, plus the vendored fork. `artifacts/` holds 2.7 GB across ~20k files.

### 2.2 Automated checks

| Check | Result |
| --- | --- |
| `npm test` (full `scripts/check.mjs` suite) | **Pass** |
| Prettier (configured paths) | Pass |
| Ruff lint + format (`backend`, voice package) | Pass |
| pytest `backend/tests` | **210 passed** |
| Node test runner (`frontend/test`) | **96 passed** |
| Vitest (`panini-core/capabilities/search`) | **95 passed** |
| `tsc --noEmit` (search, panini-core/apps/shell) | Pass |
| pytest `panini-core/capabilities/cli/tests` (**not wired into `npm test` or CI**) | 18 passed |
| Ruff on `scripts/` (**not in the check script**) | 6 issues, all auto-fixable |
| `npm audit` | **0 vulnerabilities** |
| `pip-audit` | **No known vulnerabilities** (local editable package skipped) |
| `pip check` | No broken requirements |
| Secret scan | **Live keys found in 2 files outside `.env`** (see H2) |

### 2.3 Manual review coverage

- **In depth:**
  - Gateway and security middleware
  - Both WebSocket bridges
  - PANINI auth, routes, and coordinator
  - Browser, code, and desktop adapters
  - Coding engine, process ownership, and verification
  - Desktop broker and worker (C#)
  - Extension background, content, and manifest
  - Electron main process (security settings, IPC, protocol, credential handling)
  - Jev sidecar client
  - `innerHTML` sinks in the legacy UI
  - Search API route
- **Skimmed:** voice routes and services, extension planner/loop, search pipeline, renderer UI.
- **Not reviewed:**
  - The vendored Rust fork (`panini-core/capabilities/cli-rs`)
  - `panini-core/capabilities/desktop/skill-recorder`
  - `panini-core/capabilities/desktop/computer-use-reference`
  - `integrations/panini-core/capabilities/desktop-reference`
  - Reference repos under `artifacts/`

---

## 3. Findings

### High

#### H1. The project isn't under version control
- **Where:** the whole tree. There is no `.git` at the root or in any subdirectory.
- **Impact:**
  - No history, rollback, blame, or review.
  - No way to tell which of the 210 artifact entries correspond to which code state.
  - `.github/workflows/ci.yml` is well-built (Linux and Windows jobs, audits) but **can never run**.
  - A disk failure or a bad bulk edit (e.g. `artifacts/apply-rebrand.py`) is unrecoverable.
- **Fix:** run `git init` and make a first commit. `.gitignore` already excludes `.env`, `.env.*`, `artifacts/`, `node_modules/`, and `.venv/`, so this is safe. Before the first push, run `git status` to confirm no secrets are staged. Then push to a private remote so CI starts running.

#### H2. Live API keys duplicated outside `.env`
- **Where:** `artifacts/removed-source/backend.env` and `artifacts/removed-source/frontend.env`.
- **What:** both files contain the **same live** `OPENAI_API_KEY`, `AI_GATEWAY_API_KEY`, and `ASSEMBLYAI_API_KEY` as the root `.env`.
- **Scope checked:** the exact key prefixes were **not** found in the packaged build (`artifacts/panini-package/`) or anywhere else. The packaged app correctly loads keys from DPAPI-encrypted `credentials.bin` instead.
- **Why it matters:** `artifacts/` is the directory most likely to be zipped, shared, or uploaded. It holds build receipts, logs, and reference repos. It is gitignored, but only once a repo exists (see H1).
- **Fix:**
  - Delete both copies.
  - If `artifacts/` has ever left this machine, rotate all three keys.
  - Consider scoping the OpenAI key to a project with a spend limit.

### Medium

#### M1. Default profile: `/ws/extension` accepts any local process or any extension (verified)
- **Where:**
  - `backend/routes/browser.py:61-108`
  - `frontend/server/extension-socket.mjs:31-38`
- **What:** in the default (non-PANINI) profile, the socket rejects web-page origins but accepts two other kinds of client:
  - **No Origin at all:** any local process.
  - **Any** `chrome-extension://` or `moz-extension://` origin: any installed extension.

  The only "identity" check is the public string `source: "jev-rev-browxtension"`.
- **Reproduction:** a script with no Origin and no token got `{"type":"connected"}`. The bridge then reported `connected: True, compatible: True`, an attached tab, and a fresh `target_id`, so extension-mode browser runs are routed to it. A connection with `Origin: chrome-extension://someotherextensionid` was also accepted.
- **Impact:** the impostor gets three things:
  - It receives the user's browser actions, including text to type into pages.
  - It can return forged page snapshots that steer the planner.
  - It holds the single slot. The genuine extension is then refused with "Another Jev extension is connected".
- **Note:** the comment in `extension-socket.mjs:15-18` ("only an extension the user installed and attached to a tab can reach it") is inaccurate. PANINI mode is **not** affected, because it requires a one-time pairing token (`routes/browser.py:84-97`).
- **Fix:** use the pairing-ticket flow in the default profile too. At minimum, pin the extension's own ID (allow-list exactly `chrome-extension://<your-id>`) and reject connections with no Origin.

#### M2. The extension never authenticates the server and reconnects forever to a common port
- **Where:**
  - `extension/background.js:3`: the default is `ws://127.0.0.1:3000`
  - `extension/background.js:137-215`
  - `extension/background.js:208`: retry every 3 s
  - `extension/background.js:121-134`: sends the PANINI token in `hello`
- **What:** the extension trusts whichever process answers on the configured localhost port. Once a tab or workspace is attached (the grant persists in `chrome.storage.session`), it keeps retrying every 3 s even while Jev-Rev is closed. Port 3000 is the default for many dev servers.
- **Impact:** any local process listening there can drive the attached tab in the user's **logged-in** Chrome profile (click, type, navigate, read text, take screenshots). In PANINI mode, that process is also handed the pairing token. Chrome specifically protects session cookies from other same-user processes (app-bound encryption), so this bridge is a real privilege step-up, not a no-op.
- **Fix:**
  - Add mutual authentication. The server should prove it holds the pairing secret (challenge-response) before the extension executes anything.
  - Stop auto-reconnecting when no pairing exists or after repeated failures.
  - Drop the attachment grant when the app has been unreachable for a while.

#### M3. The coordinator watchdog dies permanently on any non-`Fault` exception (verified)
- **Where:**
  - `backend/panini/coordinator.py:793-808`: `_watch` only catches `Fault`
  - `backend/panini/coordinator.py:774-791`: `end()`
  - `backend/capabilities/browser.py:259-266`: `session_end` calls `socket.close()`
- **What:** `end()` awaits each adapter's `session_end` inside `asyncio.wait_for(..., 4)`, and awaits `media.close()`. Any `TimeoutError`, or any `RuntimeError` from closing an already-closed WebSocket, escapes `_watch`. That ends the watchdog task silently.
- **Reproduction:** with an adapter whose `session_end` raises `RuntimeError`, the watchdog finished with that exception. The closed session was **still in the registry 90 simulated seconds later**.
- **Impact once the watchdog is dead:**
  - Closed sessions are never purged. After 8, `create_session` returns `SESSION_LIMIT` until PANINI restarts.
  - Tokens are never pruned.
  - Expired leases no longer proactively stop running tasks or close the live voice/media session, which is billed per minute.
  - `end()` is also non-atomic. The state is already `closed` when an adapter raises, so the remaining adapters aren't ended, `extension_transport` tokens aren't revoked, and targets aren't cleared.
- **Fix:**
  - In `_watch`, catch `Exception` per session and log it; never let the loop exit.
  - In `end()`, wrap each adapter call separately and always run the token revocation and cleanup (e.g. in `finally`).
  - Add a regression test based on the reproduction in the Appendix.

#### M4. Executor failures are swallowed with no logging
- **Where:** `backend/panini/coordinator.py:626-631` (`except Exception:` becomes the generic message "The executor failed"). There is no `logging` import anywhere in `backend/panini/`.
- **Impact:** every unexpected adapter error, including planner bugs, a `KeyError` in an adapter, or provider schema drift, reaches the user as the same message with zero diagnostic trail. Combined with M3, a dead watchdog also leaves no trace.
- **Fix:** add `logger.exception("task failed", extra={"task_id": ...})` in the generic handler and in `_watch`, and keep the user-facing text generic. The HTTP layer already has a structured JSON logger to mirror (`backend/http.py`).

### Low

#### L1. Inconsistent sender checks in the extension's message handler
- **Where:** `extension/background.js:338` onward.
- **What:** `pairPanini` rejects content-script senders (`:421`). `attach` (`:396`), `setUrl`/`reconnect` (`:445`), and the `workspace*` messages (`:365`+) do not. Content scripts run inside every http(s) page's renderer, and Chrome's guidance is to treat their messages as potentially compromised.
- **Fix:** apply the same `if (_sender.tab) throw` check to all popup-only commands.

#### L2. The extension requests more reach than it uses
- **Where:** `extension/manifest.json:22` (`content_scripts` on `http://*/*, https://*/*`) plus host permissions for all sites.
- **What:** `content.js` is injected into every page the user visits, but actions only ever target explicitly attached tabs. `ensureContent()` (`background.js:43-57`) already injects on demand via `chrome.scripting`.
- **Fix:** remove the static `content_scripts` entry and rely on on-demand injection. Consider optional host permissions requested at attach time. This shrinks the attack surface and eases Web Store review.

#### L3. Ports are hardcoded in several places that disagree
- **Where:**
  - Extension default: 3000 (`background.js:3`)
  - Pairing: hardcoded `127.0.0.1:3200` (`background.js:425,433`)
  - Gateway defaults: 3000/8000/3030 (`frontend/server/config.mjs:36`)
  - `.env` / `.env.example`: 3200/8200/3230
  - PANINI: hardcoded 8200/3200/3230 (`panini-core/apps/shell/src/main/main.ts:48-49,232-234`, `scripts/panini-gateway.mjs:9`)
- **Impact:** with the current `.env`, running `npm run dev` and `npm run panini` at the same time collides on the same ports. PANINI then fails after about 60 s with a generic "could not start its backend". The identity check in `main.ts:269` correctly refuses the wrong backend, but the failure is slow and opaque.
- **Fix:** use one source of truth for ports, and detect ports already in use at startup with a specific error.

#### L4. The Jev sidecar can wedge on a single malformed stdout line
- **Where:** `backend/jev_client.py:27-47` and `:54-78`.
- **What:** a non-JSON line on the sidecar's stdout (e.g. a dependency calling `console.log`) raises `ValueError`, and the reader thread exits. The process is still alive, so `start()` keeps reusing it and every later `ask()` waits the full ~46 s timeout. This fails closed, which is good: desktop admission rejects the action. But desktop actions and comparisons are unusable until restart.
- **Fix:** skip malformed lines (`continue`), or kill the process when the reader exits so the next call respawns it.

#### L5. The documented executable override does nothing
- **Where:** `.env.example:33` documents `PANINI_CLIX_EXECUTABLE`, but `panini-core/capabilities/cli/src/panini_cli/engine.py:15-17` reads `PANINI_CLI_EXECUTABLE` / `JEV_REV_PANINI_CLI_EXECUTABLE`. Nothing reads the documented name.
- **Fix:** correct `.env.example`. While there, finish the rebrand leftovers in `backend/capabilities/code.py:24,60-62` ("install Codex", `find_codex`).

**Minor notes (no action needed unless convenient):**
- The PANINI `/api/panini` proxy is mounted before the rate limiter (`gateway.mjs:55-64` vs `:75`). It is authenticated anyway.
- The broker redirects the worker's stderr but never drains it (`Panini.Desktop.Broker/Program.cs:116`). A chatty worker would block until the deadline.
- The Windows Job is assigned just after spawn (`panini_cli/process.py:23-46`), so a grandchild spawned in the first few milliseconds could escape the kill-on-close tree.

### Quality and process

#### Q1. Test and lint gaps in CI
- `panini-core/capabilities/cli/tests` has 18 passing tests, but they never run. `pyproject.toml:2` sets `testpaths = ["backend/tests"]`, and neither `scripts/check.mjs` nor CI invokes them.
- `scripts/` isn't linted (6 auto-fixable Ruff issues).
- There is no coverage measurement.
- No test exercises the default-profile extension-socket authentication, which is why M1 went unnoticed.

#### Q2. `artifacts/` is a hazard
The directory holds 2.7 GB and about 20k files: logs, a complete inherited venv, packaged builds, third-party reference repos, and the key copies from H2. Move durable evidence (validation receipts) into a small tracked folder and delete the rest, or move it outside the project.

#### Q3. The vendored fork has no update path
`panini-core/capabilities/cli/panini-core/capabilities/cli-rs` is a rebranded snapshot of OpenAI Codex. The Apache-2.0 `LICENSE`/`NOTICE` are preserved and provenance is recorded in `panini-core/capabilities/cli/licenses/source-provenance.json`, which is good. Two things need attention:
- **No path for upstream security fixes.** Managed coding relies on the upstream sandbox (workspace-write, network off), so record the upstream commit and set a cadence for pulling sandbox and security fixes.
- **Licence obligation (not legal advice):** Apache-2.0 §4(b) requires modified files to carry prominent change notices. A bulk rename touches thousands of files, so confirm how that obligation is being met.

#### Q4. Some functions are very large
- `Coordinator._run` is about 215 lines (`coordinator.py:454-668`). It mixes clarification resume, routing, step execution, evidence validation, and cleanup. Extracting the per-step body would make M3/M4-style edge cases testable in isolation.
- `panini-core/apps/shell/src/renderer/main.tsx` (729 lines) and `src/main/main.ts` (676 lines) are candidates for the same treatment.

#### Q5. Small repo-config nits
- `.gitignore` lists `artifacts/` twice.
- `panini-core/voice/live-spoken-conversation/*.egg-info` is build output sitting in the source tree.

---

## 4. Strengths worth keeping

- **Local attack surface:**
  - A Host allow-list on both gateway and backend blocks DNS rebinding.
  - Origin and `Sec-Fetch-Site` checks run at two layers.
  - Body-size limits apply at both layers.
  - Responses are `no-store`.
  - Logs deliberately contain no bodies or credentials.
- **PANINI authority model:**
  - A 256-bit per-launch bootstrap token.
  - Opaque tokens, stored hashed and compared with `compare_digest`.
  - Single-use tickets with a 30–60 s TTL.
  - Separate controller, observer, and extension-transport roles.
  - Lease epochs that fence stale controllers.
  - Idempotent command IDs with digest checks.
- **Human in the loop:**
  - Every mutating browser, desktop, and code action waits for a confirmation bound to a nonce and a digest, rendered in the Electron main process.
  - Content from the page or app is labelled untrusted in prompts.
  - Password fields are refused.
  - `verified_complete` requires evidence records the coordinator can validate.
- **Desktop IPC:**
  - HMAC-SHA256 with a constant-time compare, a 12 s deadline window, and a replay set.
  - The pipe is `CurrentUserOnly`.
  - Elevated targets and secure desktops are refused.
  - Each request runs in a disposable worker with a scrubbed environment.
- **Coding:**
  - The sandbox, approval, and cwd policy is **re-verified after thread start**.
  - `grantRoot` is declined.
  - Network, MCP, apps, and plugins are disabled.
  - A Windows Job ties the process tree's lifetime to PANINI.
- **Electron:**
  - `sandbox`, `contextIsolation`, and no `nodeIntegration`.
  - Navigation and `window.open` are blocked.
  - The permission handler allows audio only.
  - The custom protocol handler is path-contained.
  - IPC senders are validated.
  - Runtime hashes are verified in packaged builds.
  - Keys are stored with DPAPI (`safeStorage`) and scrubbed from companion processes.
- **Web:** all `innerHTML` sinks escape dynamic data, and search has no SSRF surface (it only calls OpenAI).

---

## 5. Recommended order of work

1. **Today:** `git init` and first commit (H1). Delete the key copies, and rotate if `artifacts/` was ever shared (H2).
2. **This week:**
   - Harden the watchdog and make `end()` best-effort, with a regression test (M3).
   - Add logging to the coordinator (M4).
   - Wire `panini-core/capabilities/cli/tests` and `scripts/` linting into `npm test` (Q1).
3. **Before the extension is used outside development:**
   - Authenticate the default-profile socket (M1) and add mutual authentication with a reconnect policy (M2).
   - Tighten the message sender checks (L1) and drop the global content script (L2).
4. **Housekeeping:** consolidate ports (L3), fix the sidecar reader (L4), correct `.env.example` (L5), prune `artifacts/` (Q2), and plan upstream sync for the vendored fork (Q3).

---

## Appendix: reproductions

Both scripts run against the unmodified code (`PYTHONPATH=<repo>`, repo `.venv`). Each is a ready-made regression test once the fix lands.

**M3 (watchdog):**

```python
class FlakyAdapter(UnavailableAdapter):
    def __init__(self): super().__init__("search", "")
    async def session_end(self, session_id): raise RuntimeError("socket already closed")

settings = Settings(enabled=True, bootstrap_token="x"*40, lease_seconds=1, retention_seconds=1)
c = Coordinator(settings, {"search": FlakyAdapter()}, clock=clock)   # injectable clock
# create + acquire a session, c.start(), advance clock past lease, then past retention
# Observed: c.watchdog.done() == True, exception RuntimeError('socket already closed');
#           session never purged afterwards.
```

**M1 (extension socket, default profile):**

```python
app = create_app(panini_settings=Settings())          # PANINI disabled
with TestClient(app) as client, client.websocket_connect("ws://127.0.0.1/ws/extension") as ws:
    ws.send_json({"type": "hello", "source": "jev-rev-browxtension", "protocol": 3,
                  "version": "2.0.2", "tab": 7, "attachment_id": "x"})
    ws.receive_json()   # -> {'type': 'connected', ...}; bridge: connected/compatible/target_id set
```


