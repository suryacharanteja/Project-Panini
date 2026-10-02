# V2-08: Playwright sidecar and managed browser

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 2 · P0 · M (4–5 days) |
| Depends on | V2-02, V2-03, V2-04 |
| Flag | `PANINI_BROWSER_DRIVER` = `legacy` (default) · `playwright` |
| Status | Done (2026-10-02) |

## 1. Outcome

PANINI drives browsers with Playwright 1.63's own agent-grade engine, the Playwright MCP server that ships
inside `playwright-core`: accessibility snapshots with element refs, auto-waiting actions, tabs, dialogs,
uploads, downloads, frames, console and network inspection, verification tools, tracing and video. The
managed browser becomes a persistent, visible PANINI profile on the installed Edge or Chrome (no bundled
browser download), replacing the Browser Use loop. The same engine serves your Chrome (V2-09) and sandboxes.

## 2. Scope and non-goals

- In scope: sidecar process management, browser sessions and profiles (managed, isolated, later chrome and
  sandbox), MCP client wiring, tool catalogue pinning, environment hygiene, artifacts directory, packaging,
  a minimal tool set for the managed hand, demo-page golden tasks, Browser Use retirement plan.
- Not in scope: effect mapping and policy for all 80 tools, stale-ref guard, password refusal, tracing
  receipts (V2-10); the CDP relay to your Chrome (V2-09); live frames (V2-12).

## 3. Current state

- Managed mode: `JevBrowserLoop` (`panini-core/capabilities/browser/runtime/panini_browser/jev_browser_loop.py`)
  runs `browser_use` 0.11.13 (CDP via `cdp_use`); `ChatJev` validates proposed actions and asks Jev to
  choose; a guarded registry admits one action at a time, refuses password fields and coordinate clicks;
  visible Chromium by default; **not** the user's profile and **not persistent**.
- The extension path has its own proposer (`extension_planner.py`) and loop (`extension_loop.py`).
- `node_modules/playwright-core` 1.63.0 is installed (shell devDependency) and contains:
  `lib/entry/mcp.js` (Playwright MCP server), `lib/tools/` (CLI client, skills), `lib/coreBundle.js`
  (`CDPRelayServer`, `BrowserModel`, `ExtensionProtocolV2`, 80 `browser_*` tools).
- Packaging copies `node.exe` into `artifacts/panini-runtime/node/` (`scripts/panini-package.mjs:214`).
- The harness already has an MCP stdio client (`backend/harness/connectors.py:Server`, protocol `2025-06-18`).

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| `playwright-core/lib/entry/mcp.js` (Apache-2.0) | The whole MCP server, unmodified | Spawned per browser session with PANINI-chosen flags; called through the PANINI policy wrapper |
| Playwright MCP options (verified in `coreBundle.js`): `--browser`, `--user-data-dir`, `--isolated`, `--headless`, `--cdp-endpoint`, `--cdp-header`, `--caps vision,pdf,devtools`, `--output-dir`, `--output-max-size`, `--image-responses allow|omit`, `--codegen none|typescript|python`, `--timeout-action/-navigation/-settle`, `--allowed-origins`, `--blocked-origins`, `--block-service-workers`, `--init-script`, `--init-page`, `--snapshot-boxes`, `--secrets`, `--save-session`, `--storage-state` | Profile presets (§5.3) |
| Playwright CLI skill `lib/tools/skills/playwright-cli/SKILL.md` | Named sessions (`-s=name`), `--persistent` profiles, `attach --cdp=msedge`, `--mobile` "lighter pages save tokens" | Session naming, persistent managed profile, optional mobile emulation for research |
| OpenDots `src/browser/index.ts` | Isolated read-only capture service: bearer secret, one request at a time, 40 s deadline, abort on client disconnect, JS disabled, only GET of document/stylesheet/image/font, 50 requests, public-IP check before and after navigation | The `isolated` preset for untrusted pages: `--isolated --headless --block-service-workers`, blocked private origins, request budget enforced in the hand; OpenDots' `security.ts` `isPublicAddress` ported to `backend/hands/net_guard.py` |
| OpenDots `src/browser/security.ts` | Public-address validation (IPv4/IPv6 special ranges, `.local`, `.localhost`, non-standard ports) | Port to Python for navigate checks in `isolated` and sandbox presets |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `panini-core/capabilities/browser/playwright/package.json` | Private package; dependency `playwright-core: 1.63.0` (exact) |
| `panini-core/capabilities/browser/playwright/init-overlay.js` | `--init-script`: PANINI badge and copper action ring (shown by V2-10 probes) |
| `panini-core/capabilities/browser/playwright/init-live.mjs` | `--init-page`: starts `page.screencast` and posts frames to the live endpoint (V2-12) |
| `panini-core/capabilities/browser/playwright/tools-1.63.0.json` | Pinned tool catalogue (names + input schemas) for the contract test |
| `backend/hands/playwright_mcp.py` | `PlaywrightSession`, `PlaywrightPool` |
| `backend/hands/net_guard.py` | Public-address checks (port of OpenDots `security.ts`) |
| `backend/hands/browser.py` | `BrowserHand` (V2-10 completes the vocabulary); this item: managed + isolated hands, minimal tools |
| `panini-core/capabilities/browser/panini.capability.json` | Adds hands `managed`, `isolated` and their tools |
| `scripts/panini-package.mjs` | Copies the sidecar and `playwright-core` (lib, package.json, LICENSE, NOTICE) into the runtime |
| `scripts/check-pins.mjs` (V2-00) | Fails when `playwright-core` ≠ 1.63.0 or the tool catalogue changed |

### 5.2 Process model

```python
class PlaywrightSession:
    """One Playwright MCP server process bound to one browser profile."""
    key: str                      # "managed" | "isolated:<run>" | "chrome" | "sandbox:<id>"
    def __init__(self, preset: "Preset", *, node: Path, entry: Path, env: dict): ...
    async def start(self) -> None          # spawn, MCP initialize, tools/list, verify catalogue
    async def call(self, tool: str, args: dict, *, timeout_s: float) -> dict   # MCP tools/call
    async def close(self) -> None          # browser_close, then terminate; kill after 5 s
    @property
    def healthy(self) -> bool

class PlaywrightPool:
    async def session(self, key: str, preset: "Preset") -> PlaywrightSession   # start on first use
    async def close_idle(self, idle_s: float = 600) -> None
    async def close_all(self) -> None
```

- Spawned with `asyncio.create_subprocess_exec(node, entry, *flags)`; stdio MCP; one process per session key.
  The backend runs inside `Panini.RuntimeHost`'s kill-on-close job object, so children end with PANINI.
- `node` is `artifacts/panini-runtime/node/node.exe` when packaged, else the `node` on `PATH` (≥ 22.19).
- `entry` is `<playwright-core>/lib/entry/mcp.js` resolved from the sidecar package.
- Environment: a minimal allow-list (`SYSTEMROOT`, `PATH`, `TEMP`, `TMP`, `LOCALAPPDATA`, `USERPROFILE`,
  `PROGRAMFILES*`); every PANINI and provider secret is stripped (the PANINI-CLI rule "credentials stripped
  from command environments"); `PLAYWRIGHT_BROWSERS_PATH` unset; `NODE_OPTIONS` empty.
- Restart policy: on unexpected exit, mark the session unhealthy, fail in-flight calls with
  `outcome=unknown_effect` when an effect tool was running (else `failed`), and restart on the next call; after
  3 restarts in 10 minutes the hand reports `unavailable`.
- The MCP client reuses `harness/connectors.Server`'s JSON-RPC framing (extracted into
  `backend/harness/mcp_stdio.py` so connectors and hands share it).

### 5.3 Presets

| Preset | Flags | Use |
| --- | --- | --- |
| `managed` | `--browser msedge` (or `chrome` when `PANINI_MANAGED_BROWSER=chrome`), `--user-data-dir %LOCALAPPDATA%\PANINI\browser\managed`, headed, `--caps pdf,devtools,vision`, `--output-dir <artifacts>/playwright/managed`, `--image-responses omit`, `--codegen none`, `--init-script init-overlay.js`, `--blocked-origins <policy>` | "Use a separate browser"; PANINI's own logins persist; background read-only research |
| `isolated` | `--browser msedge --isolated --headless --block-service-workers`, `--blocked-origins` private ranges, `--output-dir <artifacts>/playwright/isolated/<run>` | Untrusted links (email, channel), when the sandbox tier is off |
| `chrome` (V2-09) | `--cdp-endpoint ws://127.0.0.1:<port>/cdp/<token>`, `--cdp-timeout 15000`, `--caps pdf,devtools`, `--output-dir …/chrome` | Your Chrome tabs through Claw |
| `sandbox:<id>` (V2-11) | `--cdp-endpoint` of the sandbox computer's browser when exposed, else not used (sandbox tools go through OpenBot) | Persona computers |

Notes:

- `--allowed-origins` is documented by Playwright as **not** a security boundary; PANINI policy enforces
  domain grants itself (V2-03) and only passes the lists to Playwright as defence in depth.
- `--secrets` is used only to **mask** known PANINI values in outputs. Typing secret placeholders is refused by
  the policy wrapper (`credential` is always denied, I5).
- Edge is always present on Windows 11, so no browser download is needed; `--browser chrome` uses the
  installed Chrome.

### 5.4 Managed hand: minimal tools in this item

| PANINI tool | Playwright tool | Effect |
| --- | --- | --- |
| `managed_navigate` | `browser_navigate` | navigate |
| `managed_snapshot` | `browser_snapshot` | observe |
| `managed_click` | `browser_click` | submit (dynamic) |
| `managed_type` | `browser_type` | input (`submit: true` → submit, dynamic) |
| `managed_select` | `browser_select_option` | input |
| `managed_press_key` | `browser_press_key` | input (Enter → submit, dynamic) |
| `managed_wait_for` | `browser_wait_for` | observe |
| `managed_tabs` | `browser_tabs` | navigate |
| `managed_back` | `browser_navigate_back` | navigate |
| `managed_screenshot` | `browser_take_screenshot` | observe (artifact) |
| `managed_verify_text` | `browser_verify_text_visible` | observe (evidence: assertion) |

V2-10 adds the remaining tools, the stale-ref guard, password refusal and evidence rules. Until then the
managed hand uses V2-03's dynamic classification on the ref's accessible name taken from the last snapshot.

### 5.5 Browser Use retirement

1. With `PANINI_BROWSER_DRIVER=playwright`, "use a separate browser" maps to the `managed` hand; the
   coordinator v1 `chromium` mode (`JevBrowserLoop`) is not started.
2. The legacy `browser_run` coarse tool remains for the extension path until V2-09/V2-10 pass their gates.
3. After the Phase 2 gate: `browser_use`, `browser_use_sdk`, `cdp_use` move to an optional extra and are
   removed from the packaged runtime (`scripts/panini-backend.spec:27` lists them for PyInstaller today).

### 5.6 Packaging

| Change | Detail |
| --- | --- |
| Runtime layout | `runtime/browser-driver/` (sidecar files) and `runtime/browser-driver/node_modules/playwright-core/` |
| Size | `playwright-core` without browsers is about 10 MB; measured and recorded in the evidence log (budget ≤ 40 MB for Phase 1–2) |
| Integrity | Added to the release manifest hashed by `scripts/panini-release-gate.ps1` |
| Licences | Apache-2.0 LICENSE and NOTICE copied to `runtime/licenses/npm` |

### 5.7 Failure modes

| Failure | Behaviour |
| --- | --- |
| Edge/Chrome not found | Hand readiness `needs_setup`: "Install Microsoft Edge or Chrome" |
| Profile locked (another Edge using the folder) | `managed` uses a PANINI-only folder, so this only happens with two PANINI instances; readiness reports it |
| Tool catalogue differs from the pin | Session refuses to start (`R6`), readiness `unavailable` "Browser driver version mismatch" |
| MCP call timeout | `outcome=failed` (or `unknown_effect` for effect tools after `dispatch`), session health re-checked |

### 5.8 As built (2026-10-02)

- `backend/harness/mcp_stdio.py`: the JSON-RPC stdio client extracted from `connectors.Server` (which now
  subclasses it; connector tests unchanged).
- `backend/hands/playwright_mcp.py`: `PlaywrightSession` (spawn `node <playwright-core>/lib/entry/mcp.js`,
  initialize, `tools/list`, refuse to start when an offered tool is missing from the pin or its schema
  changed; allow-listed environment with every PANINI/provider secret, `PLAYWRIGHT_BROWSERS_PATH` and
  `NODE_OPTIONS` removed; a crash during an effect tool raises `SessionCrashed` → `unknown_effect`, otherwise a
  plain failure; restart on the next call; after the first start plus three restarts in ten minutes the driver
  pauses), `PlaywrightPool` (one session per preset key, idle shutdown from the harness background cycle,
  closed with the harness), presets `managed` (persistent profile `<data>/browser/managed`, headed unless
  `PANINI_MANAGED_HEADLESS`, `--caps pdf,devtools,vision,testing`, `--image-responses omit`,
  `--codegen none`, the badge init script, action/navigation timeouts) and `isolated` (in-memory, headless,
  service workers blocked, private origins blocked as defence in depth). Paths follow `backend.config.ROOT`
  (the runtime folder when packaged).
- Catalogue: `scripts/playwright-catalogue.py` pins all 70 tools of Playwright MCP 1.63.0 with every
  capability (`vision,pdf,devtools,testing,storage,network`) in
  `panini-core/capabilities/browser/playwright/tools-1.63.0.json` (name, read-only hint, schema sha256);
  presets offer subsets. `--check` runs in the backend suite against the installed playwright-core;
  `check-pins.mjs` also checks the sidecar's pin. Finding: the verify tools (`browser_verify_*`) need the
  undocumented `testing` capability, and cookie/storage tools come with `storage`/`network` (left off: they
  are credential-adjacent).
- Finding: Playwright 1.63 writes the page snapshot that follows an action to a `.yml` file and returns only a
  link (an explicit `browser_snapshot` returns it inline). The hand inlines the linked snapshot, reading only
  files inside PANINI's own driver output folder, so one call per action still gives the model the page.
- `backend/hands/net_guard.py`: port of OpenDots `security.ts` (`is_public_address`, `check_url` with
  resolution and DNS-rebinding refusal), with OpenDots' test cases plus CGNAT and IPv4-mapped IPv6.
- `backend/hands/browser.py` `ManagedBrowserHand` and the capability manifest
  `panini-core/capabilities/browser/manifests/20-managed-browser.json` (11 tools, §5.4; offered only with
  `PANINI_BROWSER_DRIVER=playwright` and in the foreground via the new `managed_browser` view requirement).
  Safety in this item: http(s) only; never types into password/PIN/CVV/security-code fields (project rule);
  the element description must match the ref's accessible name from the latest snapshot, so the policy engine
  classifies the real control. Every result that carries page state is `page` evidence; `managed_verify_text`
  is an `assertion`. Native hand evidence now feeds the agent's evidence rule (`Agent._call`).
- Browser Use retirement step 1: with the Playwright driver, Browser's separate-browser target
  (`chromium`) runs a focused agent (`Agent.only_tools`) on the managed tools instead of `JevBrowserLoop`;
  every step goes through the registry (policy, ledger). It needs `PANINI_V2_REGISTRY`.
- Packaging: the runtime gets `panini-core/capabilities/browser/{playwright,manifests}` and
  `playwright-core` (`lib`, `package.json`, `browsers.json`, `index.js`, licences): 10.95 MB (budget 15 MB);
  licences copied to `licenses/npm`; the existing release inventory hashes every file. Capability manifests
  are read from the runtime folder. A packaged smoke run was not part of this item (it needs the full
  .NET/Electron build); it is listed for the release gate.
- Evidence: 35 tests in `backend/tests/hands/test_playwright.py`, including real headless Edge through the real
  Playwright MCP server (demo page read → type → submit → verify in 1.7 s) and the acceptance check that a
  persistent cookie survives a driver/browser restart on the managed profile. Live (`PANINI_BROWSER_DRIVER=
  playwright`, headless): "Use a separate browser to open https://example.com and tell me the main heading"
  passes in 6.2 s (v1 Browser Use: partial after 22.6 s); "Open youtube.com." passes in 10.9 s (v1: 34.1 s).

## 6. Implementation steps

1. Sidecar package and pinned catalogue; `check-pins.mjs` extension.
2. Extract `mcp_stdio.py` from `connectors.py` (no behaviour change; connector tests pass).
3. `PlaywrightSession`/`Pool` with environment hygiene, restart policy, idle shutdown.
4. Presets `managed` and `isolated`; `net_guard.py` port with tests.
5. Managed hand with the minimal tools; manifest entries; registry wiring.
6. Demo-page golden tasks (V2-01 browser cases) on the managed hand; flag-gated replacement of `chromium`.
7. Packaging, integrity manifest, licences; packaged smoke (`scripts/panini-runtime-smoke.py` extension).

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Session lifecycle with `backend/tests/harness/fake_mcp.py`; restart policy; env stripping | `backend/tests/hands/test_playwright_session.py` |
| Unit | `net_guard` ranges (ported from OpenDots `tests/security.test.ts` cases) | `backend/tests/hands/test_net_guard.py` |
| Contract | Real `mcp.js` `tools/list` equals `tools-1.63.0.json` | `backend/tests/hands/test_playwright_contract.py` (local integration) |
| Integration | Managed hand reads, fills and verifies `frontend/public/browser-demo.html` | `backend/tests/hands/test_managed_demo.py` (local integration) |
| Eval | Browser golden tasks on `managed` ≥ v1 `chromium` | `scripts/panini-eval.py` |
| Live | Packaged runtime starts the sidecar and completes one task | `scripts/panini-runtime-smoke.py` |

## 8. Acceptance criteria

- [x] Managed sessions persist logins across PANINI restarts (sign in once to a test site, restart, still
      signed in).
- [x] Browser golden tasks on `managed` ≥ the v1 `chromium` baseline; median latency per action ≤ v1.
- [ ] Packaged runtime grows ≤ 15 MB for this item; integrity gate passes.
- [x] Killing the sidecar mid-action yields `unknown_effect` (effect tools) or `failed` (observe tools),
      never a false success.

## 9. Rollout and rollback

`PANINI_BROWSER_DRIVER=playwright` in development after step 6; default at the Phase 2 gate. Rollback:
`legacy`. The managed profile folder is kept.

## 10. Open questions

- Use `--image-responses allow` for the vision tier so the agent can look at screenshots directly?
  Recommendation: `omit` by default; the vision tier (V2-06) requests screenshots explicitly as artifacts.
