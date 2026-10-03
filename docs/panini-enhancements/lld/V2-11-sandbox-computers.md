# V2-11: Sandbox computers (OpenBot on Docker)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 2 · P1 · L (1–1.5 weeks) |
| Depends on | V2-02, V2-03, V2-04; Docker Desktop (register D4) |
| Flag | `PANINI_SANDBOX` (default `false`) |
| Status | Built 2026-10-03; live checks pending Docker |

## 1. Outcome

PANINI (and later each persona) gets its own isolated, persistent computer: a container with a browser,
a workspace folder and a shell. Untrusted research, unknown downloads and experimental code run there
instead of on your PC. Files and browser logins survive stop/start; you can watch it and take control; and
revoking a permission cancels in-flight work. Your real PC stays the trusted hand.

## 2. Scope and non-goals

- In scope: building and running the OpenBot supervisor and computer image from the revision OpenDots
  pins, the hardened supervisor patch, a Python port of OpenDots' `ComputerService`, sandbox tools in the
  registry, per-persona computers and permissions, file exchange with the host, takeover/handback control,
  routing rules (when PANINI uses the sandbox), Admin "Computers" page, isolation and persistence tests.
- Not in scope: live frames and the takeover UI (V2-12 renders them), an egress allow-list proxy (open
  question), gVisor (not available on Docker Desktop for Windows; `COMPUTER_RUNTIME` stays configurable).

## 3. Current state

- Host execution: `harness/computer.py` (`PANINI_COMPUTER_ACCESS=full` by default per project memory) for
  files, commands and opening; PANINI-CLI has a separate Docker command sandbox (`python:3.12-slim`, no
  network, verified live per `PANINI-AGENTIC-DESIGN.md` §10 third pass) for CLI commands only.
- Docker Desktop is installed on this PC; PANINI-CLI already finds Docker outside `PATH`.
- No persistent agent computer, no sandbox browser.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `compose.computers.yml` | Build `computer-image` from `https://github.com/CopilotKit/OpenBot.git#b6932d31…` (`agent-computer/Dockerfile`); supervisor on `127.0.0.1:4312:4300`; `SUPERVISOR_TOKEN`, `COMPUTER_TOKEN`, `COMPUTER_NAMESPACE`, `COMPUTER_IMAGE`, `COMPUTER_BROWSER_MODE=headless`, `COMPUTER_MEMORY_BYTES`, `COMPUTER_RUNTIME`; Docker socket only in the supervisor; `no-new-privileges`, `cap_drop: [ALL]`, `init`, health check | Copied to `deployment/computers/` with namespace `panini` |
| OpenDots `deployment/computers/supervisor.Dockerfile` + `harden-supervisor.mjs` | Fail-closed patch: each computer gets `HMAC-SHA256(COMPUTER_TOKEN, "<prefix>" + botId)` instead of the master; refuses to build if the pinned source changed | Prefix `panini-computer:`; same fail-closed marker check |
| OpenDots `src/server/computer-service.ts` | Supervisor `GET /computers`, `POST /computers/{id}/ensure|stop`; computer API on `:4100` (`/control`, `/control/request`, `/control/take|release`, `/navigate`, `/read`, `/snapshot`, `/screenshot`, `/click`, `/type`, `/key`, `/scroll`, `/files/list|read|write`, `/exec`, `/human/click|type|key|scroll`); header `x-openbot-bot-id`; identity check (`botId` and container name `<ns>-computer-<id>`); endpoint binding (loopback port or service DNS only); 70 s deadline; 4 MB response cap; secret redaction; 409 → "refresh with computer_snapshot … do not bypass takeover"; permission re-check before, during (50 ms watcher) and after; `exec` results drop the command | Python port `backend/hands/sandbox.py`; watcher replaced by grant epochs (V2-03) plus a cancel token |
| OpenDots `src/shared/computer-types.ts` | Strict input schemas (relative paths, no traversal, `exec.timeoutMs` 1–60 s, `type.text` ≤ 16,000, `files_write.contents` ≤ 100,000) | Tool input schemas in the sandbox manifest |
| OpenDots `tests/computer.test.ts` (10 cases) and `tests/computer-deployment.test.js` (2 cases) | Behaviours worth proving | Ported test list (§7) |
| OpenMuse hardened computer profile (read-only root, `cap-drop ALL`, no network, resource limits, non-root) | Stronger defaults | "Offline" computers: `COMPUTER_NETWORK` set to an internal network; documented limits |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `deployment/computers/compose.yml` | Supervisor + image build (from OpenDots, namespace `panini`) |
| `deployment/computers/supervisor.Dockerfile`, `harden-supervisor.mjs`, `LICENSE.openbot`, `README.md` | Pinned, patched build |
| `backend/hands/sandbox.py` | `SandboxService` (port of `ComputerService`) and `SandboxHand` |
| `panini-core/capabilities/sandbox/panini.capability.json` | Manifest: `sandbox` capability, hands per computer |
| `backend/harness/migrations/0008_sandbox.sql` | `sandbox_computers` |
| `backend/routes/harness.py` | `/api/harness/computers/*` |
| Admin `src/pages/Computers.tsx` | Status, start/stop, permissions, files, terminal, activity |
| `scripts/panini-sandbox.ps1` | `build`, `up`, `down`, `status` wrappers around `docker compose` |

### 5.2 Secrets and configuration

| Setting | Stored | Notes |
| --- | --- | --- |
| `PANINI_SANDBOX_SUPERVISOR_URL` | `.env` | `http://127.0.0.1:4312` |
| `PANINI_SANDBOX_SUPERVISOR_TOKEN` | DPAPI secrets store | ≥ 24 random characters, generated by Admin |
| `PANINI_SANDBOX_COMPUTER_TOKEN` | DPAPI secrets store | Master for per-computer derivation; never sent to computers |
| `PANINI_SANDBOX_NAMESPACE` | `.env` | `panini`; stable (it selects containers and volumes) |
| `PANINI_SANDBOX_MEMORY_BYTES` | `.env` | default 2 GiB |
| `PANINI_UNTRUSTED_BROWSING` | `.env` | `sandbox` · `isolated` (default) · `off` (§5.6) |

Compose receives the tokens through environment variables set by `scripts/panini-sandbox.ps1` from the
secrets store, never from a committed file.

### 5.3 Service

```python
class SandboxService:
    def __init__(self, store, secrets, policy, ledger, http: httpx.AsyncClient, deadline_s=70): ...
    @property
    def configured(self) -> bool
    def token(self, computer_id: str) -> str          # HMAC-SHA256(master, "panini-computer:" + id).hexdigest()
    async def status(self, computer_id) -> dict        # never provisions (OpenDots rule)
    async def start(self, computer_id) -> dict         # supervisor ensure; validates identity and endpoint
    async def stop(self, computer_id) -> dict
    async def control(self, computer_id, verb: Literal["take", "release"]) -> dict   # owner only
    async def action(self, ctx, computer_id, action: str, args: dict, actor: Literal["agent", "owner"]) -> dict
```

Rules ported one to one:

- Computer ids `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; container must be `<ns>-computer-<id>`; endpoint must be
  `http://127.0.0.1:<published port>` (host-run) or `http://<container>:4100` (app-in-container), no
  credentials, path, query or fragment.
- `human_*` actions are owner-only; agents get "Human controls are owner-only."
- Response bodies are streamed with a 4,000,000-byte cap; any reflected token is replaced with `[redacted]`.
- HTTP 409 → "The sandbox browser changed or the owner has control. Take `sandbox_snapshot` again; if the owner
  has control, wait for them to release it."
- `exec` results omit the command; receipts store only its digest and redacted preview (V2-04).

### 5.4 Tools

| Tool | Effect | Group | Notes |
| --- | --- | --- | --- |
| `sandbox_status`, `sandbox_start`, `sandbox_stop` | observe / local_exec / local_exec | lifecycle | start/stop also from Admin |
| `sandbox_navigate(url)` | navigate | browser | http(s), no credentials in URL |
| `sandbox_read`, `sandbox_snapshot`, `sandbox_screenshot` | observe | browser | taint `web` |
| `sandbox_click(ref, snapshotId)`, `sandbox_type(ref, snapshotId, text, submit?)`, `sandbox_key(key)`, `sandbox_scroll(deltaY)` | submit dyn / input / input / navigate | browser | Fresh snapshot required (OpenBot enforces `snapshotId`) |
| `sandbox_files_list(path)`, `sandbox_files_read(path)` | observe | files | Relative paths only |
| `sandbox_files_write(path, contents, append?)` | local_write (sandbox) | files | |
| `sandbox_exec(command, timeoutMs)` | local_exec (sandbox) | shell | 1–60 s |
| `sandbox_import(host_path, dest)` | share_out when the computer has network, else local_write | files | Host path inside allowed roots, not secret-looking; approval shows file and size |
| `sandbox_export(path, host_dest)` | local_write (host) | files | Host destination rules from V2-03 (outside roots → tap); taint `download` |

Policy notes (V2-03 matrix with the sandbox as the target):

- Writes and commands **inside** the sandbox are `local_write`/`local_exec` scoped to the sandbox and default
  to `none` locally; they still raise to `tap` on remote surfaces (balanced profile) because they consume
  resources and may reach the network.
- Browser effects that reach other people (`communicate`, `transact`, `account`) are unchanged: a sandbox
  browser can still post or buy on the internet.

### 5.5 Persona computers

- One computer per persona id (`panini` by default); V2-18 assigns `computer_id` per persona.
- Per-computer permission switches (OpenDots' `enabled / browser / files / shell`) are grants on the tool
  groups `sandbox.browser`, `sandbox.files`, `sandbox.shell`; all default off; Admin shows them as switches.
- Isolation proof (§7): a second computer cannot list the first's files.

### 5.6 Routing

| Situation | Behaviour |
| --- | --- |
| Persona with a computer and the browser grant | Its browser work defaults to `sandbox_*` unless the person asks for their Chrome |
| Link from a tainted source (email, channel, A2A, MCP result) and `PANINI_UNTRUSTED_BROWSING=sandbox` | `browser_select("sandbox")` is the default suggestion; the model may still ask to use your Chrome (tap) |
| Host command classified risky and not needing the host (`RISKY_COMMAND` + no host paths) | Policy adds a `choice` interrupt: "Run on your PC" or "Run in PANINI's sandbox" |
| Docker not running | Readiness `needs_setup`: "Start Docker Desktop"; nothing falls back to the host (OpenDots rule) |

### 5.7 Storage

```sql
CREATE TABLE IF NOT EXISTS sandbox_computers(id TEXT PRIMARY KEY, persona_id TEXT, created REAL NOT NULL,
  network TEXT NOT NULL DEFAULT 'default', last_state TEXT, last_seen REAL);
```

Audit is the ledger (V2-04): every action has begin/end entries with actor `owner` or `agent`; OpenDots'
"latest 1,000 completed records per Dot" becomes ledger retention.

### 5.8 Failure modes

| Failure | Behaviour |
| --- | --- |
| Supervisor unreachable | `status: unavailable` with "Check Docker Desktop and the PANINI sandbox service" |
| Identity or endpoint mismatch | Refuse ("Computer endpoint is not bound to this persona"); log |
| Revocation mid-action | Cancel token aborts the HTTP request; the receipt is `cancelled` or `unknown_effect` after dispatch; OpenDots note kept: an upstream browser operation may finish after cancellation |
| Image or master token changed | Supervisor replaces owned containers on the next ensure, keeping volumes (OpenDots behaviour); Admin warns that in-flight work ends |

### 5.9 As built (2026-10-03)

- **Deployment** (`deployment/computers/`): `compose.yml` (project `panini-sandbox`, images
  `panini-computer:b6932d3` and `panini-supervisor:b6932d3-panini-auth`, OpenBot pinned at
  `b6932d31a8d6e7896c15139dfc27a6c6911deb27`, supervisor only on `127.0.0.1:4312`, Docker socket only in the
  supervisor, `no-new-privileges`, `cap_drop: [ALL]`, `init`, health check), `supervisor.Dockerfile`,
  `harden-supervisor.mjs` (per-computer credential `HMAC-SHA256(master, "panini-computer:" + id)`; refuses to
  build if the pinned upstream line changed), `LICENSE.openbot`, `README.md`. Tokens are generated into the DPAPI
  secret store (`sandbox-secrets.bin`) by Admin's **Set up the sandbox** or `scripts/panini-sandbox.py setup`;
  `scripts/panini-sandbox.py build|up|down|status` (plus a `.ps1` wrapper) passes them to `docker compose` through
  the environment only. `down` stops the supervisor and never removes volumes.
- **Service** (`backend/hands/sandbox.py` `SandboxService`): Python port of OpenDots' `ComputerService` and input
  schemas: identity and endpoint binding, status never provisions, per-computer credentials, owner-only human
  controls, permissions checked before, during (50 ms watcher that cancels the HTTP request) and after,
  4,000,000-byte response cap, secret redaction, 409 recovery text, redirects refused, `exec` without the command.
  The agents-paused check uses the policy engine's pause (V2-03).
- **Storage:** migration `0007_sandbox.sql` (`sandbox_computers` with the permission switches; the design said
  0008). Every agent action is a registry receipt; every owner action through the routes is a ledger receipt
  with actor owner.
- **Tools** (`panini-core/capabilities/sandbox/manifests/30-sandbox.json`, `SandboxHand`): 17 tools (status,
  start, stop, navigate, read, snapshot, screenshot, click, type, key, scroll, files list/read/write, exec,
  import, export), offered with `PANINI_SANDBOX`, also in background runs (they never touch this PC). Evidence:
  exit codes for commands, content hashes for written, imported and exported files, page observations.
  `sandbox_import` copies a text file (≤100 KB, never secret-looking) from this PC; `sandbox_export` saves to
  `Downloads\PANINI sandbox` without overwriting. OpenBot's file API is text-only, so binary files do not move
  in this version.
- **Persona computers:** one computer, `panini`, created with every permission off; `SandboxHand` picks the
  principal's persona when V2-18 adds them.
- **Routing:** with `PANINI_UNTRUSTED_BROWSING=sandbox`, a run that read untrusted content (email, channel, call,
  MCP, A2A, shared) gets `browser_navigate` refused in your Chrome and the managed browser, with an instruction
  to use `sandbox_navigate` (or ask the person). The "run on your PC or in the sandbox" choice for risky host
  commands needs choice cards and is left to the interrupts UI work.
- **Admin:** Computers page (`/build/computers`): setup, start/stop, the four permission switches, take control /
  hand back, an owner terminal. Routes `/api/harness/computers*`. Settings: supervisor URL, namespace, memory,
  untrusted browsing.
- **Tests:** 15 in `backend/tests/hands/test_sandbox.py` (the 10 OpenDots behaviours, including revocation
  cancelling a hanging request in under 1 s, plus tools, file exchange, Docker-down status, routing, flag and
  background offering, receipted owner routes) and 2 in `frontend/test/sandbox-harden.test.mjs` (the supervisor
  patch).
- **Live checks not yet run:** Docker Desktop was not running, and building the images downloads the pinned
  OpenBot source and base images, which needs the owner's go-ahead. `scripts/panini-sandbox-live.py` runs the four
  live checks (isolation between two computers, persistence across stop/start, revocation within 1 s, browsing
  inside the sandbox) once the images are built and the supervisor is up.

## 6. Implementation steps

1. Copy and adapt `deployment/computers/` (namespace, prefix); `scripts/panini-sandbox.ps1`; build once and
   record image digests in the evidence log.
2. `SandboxService` port with an httpx `MockTransport` test double for supervisor and computer.
3. Manifest and tools; policy groups; ledger integration.
4. Persona computers (single `panini` computer until V2-18), permissions as grants.
5. Import/export with host rules; routing rules; choice interrupt for risky host commands.
6. Admin Computers page (status, start/stop, permissions, files, owner terminal, activity).
7. Live verification script: two computers, isolation, persistence, revocation, Docker down.

## 7. Tests

Ported from OpenDots and extended:

| Test | File |
| --- | --- |
| Permissions default off; policy and audit persist across restart (metadata only) | `backend/tests/hands/test_sandbox_service.py` |
| Status never provisions; actions use per-computer derived credentials; receipt begins before dispatch | same |
| Rejects foreign targets, unknown computers, traversal, unexpected inputs, and agent `human_*` | same |
| Checks current permissions and pause; records failures without sensitive inputs | same |
| Cancels in-flight actions on revocation; bounds upstream deadlines | same |
| Handback still works after permissions are revoked; upstream failures sanitized | same |
| Tools bound to the current persona, without human or policy controls | `backend/tests/hands/test_sandbox_tools.py` |
| Namespace case handling preserves exact container identity | same |
| Safe recovery instruction for stale browser or control conflicts (409) | same |
| Hardened supervisor: child gets its own credential; patch refuses changed upstream | `deployment/computers/test/harden.test.mjs` |
| Live: second computer cannot see the first's files; files and browser profile persist across stop/start; Docker stopped → clear unavailable status | `scripts/panini-sandbox-live.py` |

## 8. Acceptance criteria

- [ ] Images build from the pinned revision; the patch check passes; digests recorded.
- [ ] All ported tests green; live isolation and persistence checks pass.
- [ ] An untrusted link opens in the sandbox when `PANINI_UNTRUSTED_BROWSING=sandbox`, and the agent reads it
      there.
- [ ] Revoking the shell permission during a 30 s command cancels it within 1 s.

## 9. Rollout and rollback

Opt-in: Admin "Set up the sandbox" (generates secrets, builds, starts). Flag on after the live checks.
Rollback: flag off; containers and volumes are left untouched (never delete volumes as routine cleanup).

## 10. Open questions

- Egress control: add an optional proxy container with a domain allow-list per computer? Recommendation:
  Phase 6 (frontier); until then offer per-computer `network: none` for offline work.
