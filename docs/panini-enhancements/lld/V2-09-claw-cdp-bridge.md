# V2-09: Claw CDP bridge (your Chrome through Playwright)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 2 · P0 · L (1.5 weeks) |
| Depends on | V2-08 |
| Flag | `PANINI_CLAW_CDP` (default `false`) |
| Status | Not started |
| Decision | Register D3 (Claw requires the `debugger` permission) |

## 1. Outcome

PANINI acts in the Chrome tabs you attached with real, trusted input, using Playwright's full engine, while
keeping Claw's guarantees: only attached tabs, only while attached, re-checked on every command, and no
access to the rest of your browser (other tabs, all cookies, extensions, browser settings). Uploads,
downloads, dialogs, cross-origin iframes, canvas apps, hover menus and network evidence start working on
your logged-in sites.

## 2. Scope and non-goals

- In scope: Claw v4 (`debugger` permission, protocol 5 capability `cdp-v1`), the CDP relay in the backend
  (port of Playwright's relay model), the CDP method policy in both places, run binding and revocation,
  tab creation inside workspaces, idle detach, fallback to v1 actions, the trusted-input demo page.
- Not in scope: the tool vocabulary and its effect mapping (V2-10), live frames (V2-12), whole-browser
  "remote debugging" attach (documented as an unsupported alternative in §5.9).

## 3. Current state

- Claw (`panini-core/capabilities/browser/extension/`, MV3, `manifest.json` v3.2.0): permissions `tabs`,
  `activeTab`, `scripting`, `storage`, optional `tabGroups`; host permissions for all http(s).
- Protocol 4 (`docs/ARCHITECTURE.md`): pairing code → device secret; mutual HMAC challenge on every connect;
  `hello`/`connected`; action frames `{id, action: {kind, expected_tab, expected_attachment,
  expected_workspace, …}}` → `{type: "result", id, ok, result|error}`; `cancel {id}`.
- `background.js:target()` re-checks the attached tab and grant before each action; workspaces validate tab
  membership and revision.
- `content.js` performs actions with `el.click()`, `dispatchEvent(new KeyboardEvent/MouseEvent)` and value
  setters (`content.js:506-600`): events are untrusted (`isTrusted=false`).
- Backend: `extension_bridge.py` owns the socket; `extension_loop.py` runs planner-driven instructions; the
  agent's direct tool uses `BrowserAdapter.direct`.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| Playwright `src/tools/mcp/cdpRelay.ts` (`CDPRelayServer`, bundled in `coreBundle.js`) | A WebSocket server exposing a CDP endpoint (`/cdp/<uuid>`) to Playwright and an extension endpoint (`/extension/<uuid>`); one CDP client; synthetic `Browser.getVersion` ("Chrome/Extension-Bridge") and `Browser.setDownloadBehavior`; everything else to the protocol handler | Python port in `backend/hands/cdp_relay.py`. The extension leg is **not** a new socket: it is multiplexed over Claw's already-authenticated connection |
| Playwright `src/tools/mcp/browserModel.ts` (`BrowserModel`) | Tab model: known tabs, tab sessions with synthetic `pw-tab-N` session ids, child sessions for OOPIFs/workers, `Target.attachedToTarget/detachedFromTarget` emission, auto-attach on `Target.setAutoAttach`, `createTarget` via `chrome.tabs.create`, `closeTarget` via `chrome.tabs.remove`, browser-level commands routed through any attached tab | Ported, with **scoping**: known tabs = attached tab or workspace members only; browser-level commands are policy-filtered, not blindly routed |
| Playwright `src/tools/mcp/cdpRelayV2.ts` (`ExtensionProtocolV2`) | Extension messages: `chrome.debugger.attach [{tabId}, "1.3"]`, `chrome.debugger.sendCommand [{tabId, sessionId?}, method, params]`, `chrome.tabs.create [{url}]`, `chrome.tabs.remove [tabId]`; events `chrome.debugger.onEvent`, `chrome.debugger.onDetach`, `chrome.tabs.onCreated`, `chrome.tabs.onRemoved`, `extension.initialized` | Same primitives as protocol-5 frames, each carrying the attachment grant and workspace revision |
| Playwright extension pairing (`connect.html?mcpRelayUrl=…&token=…`, `PLAYWRIGHT_MCP_EXTENSION_TOKEN`) | Token-authenticated relay URL handed to the extension | Not needed: Claw already has mutual HMAC pairing; the relay token protects only the Playwright leg |
| Playwright CLI `attach --cdp=chrome|msedge` (Chrome's "Allow remote debugging for this browser instance") | Whole-browser attach | Documented alternative, off (§5.9) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `panini-core/capabilities/browser/extension/manifest.json` | v4.0.0; adds `"debugger"` to `permissions` (cannot be optional in Chrome) |
| `…/extension/cdp.js` | Debugger attach/send/detach, event forwarding, tab tracking |
| `…/extension/cdp-policy.js` | Pure method policy (same table as the backend; unit-tested with `node --test`) |
| `…/extension/background.js` | Advertises `cdp-v1`; routes `cdp` frames; enforces `target()` grant checks for CDP too |
| `…/extension/popup.*` | Shows "Full control on" per attached tab; explains the Chrome debugging bar |
| `backend/hands/cdp_relay.py` | Relay server + scoped browser model (Python port, Apache-2.0 attribution header) |
| `backend/hands/cdp_policy.py` | Method policy (mirror of `cdp-policy.js`; a test asserts both tables are identical) |
| `backend/extension_bridge.py` / `routes/browser.py` | Multiplex `cdp_event`, `cdp_detached`, `cdp_tab` frames to the relay |
| `backend/hands/browser.py` | `chrome` hand: relay + `PlaywrightSession(preset="chrome")` |
| `frontend/public/trusted-input-demo.html` | Records `event.isTrusted` for click, keydown, input; shows "trusted: yes/no" |
| `panini-core/capabilities/browser/playwright/cdp-methods-1.63.0.json` | Methods Playwright 1.63 issues in chrome mode (from the spike, §6 step 1) |

### 5.2 Protocol 5 frames (over the existing authenticated socket)

Backend → extension (request; result framing unchanged):

```json
{"id": "op-17", "action": {"kind": "cdp", "op": "send",
  "tab_id": 412, "session_id": null, "method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": 210, "y": 344, "button": "left", "clickCount": 1},
  "expected_tab": 412, "expected_attachment": "att-9f…", "expected_workspace": {"id": "ws-2", "revision": 7}}}
```

`op` ∈ `attach` (`{tab_id}`), `send` (`{tab_id, session_id?, method, params}`), `detach` (`{tab_id}`),
`create_tab` (`{url}`; workspace mode only), `close_tab` (`{tab_id}`; PANINI-created tabs only).

Extension → backend (unsolicited, only for tabs this run is attached to):

```json
{"type": "cdp_event", "tab_id": 412, "session_id": "child-…", "method": "Page.frameNavigated", "params": {…}}
{"type": "cdp_detached", "tab_id": 412, "reason": "canceled_by_user" | "target_closed" | "replaced_with_devtools"}
{"type": "cdp_tab", "event": "created" | "removed", "tab": {"id": 455, "opener_tab_id": 412, "url": "…"}}
```

`connected.capabilities` includes `cdp-v1` only when the extension has the `debugger` permission. Max frame
16 MiB (screenshots); events are coalesced per 16 ms per method family when the queue exceeds 512 frames.

### 5.3 Scoped browser model (relay)

```python
class ScopedBrowserModel:   # port of Playwright BrowserModel with PANINI scoping
    known_tabs: dict[int, dict]          # only attached tab / workspace members, from the extension
    tab_sessions: dict[int, TabSession]  # tab_id → {session_id "pw-tab-N", target_info, child_sessions}
    auto_attach: bool
    async def on_tab_created(self, tab)           # only if opener ∈ workspace or created by PANINI
    async def on_tab_removed(self, tab_id)
    async def on_debugger_event(self, tab_id, session_id, method, params)   # track child sessions, emit
    async def on_debugger_detach(self, tab_id, reason)                       # emit detachedFromTarget; revoke
    async def enable_auto_attach(self)            # Target.setAutoAttach on the root session
    async def create_target(self, url)            # extension create_tab (policy: workspace allows new tabs)
    async def close_target(self, target_id)       # only tabs PANINI created; else error
    def target_info(self, session_id)
    async def send(self, session_id, method, params)   # policy → extension send
```

Relay server:

- Binds `127.0.0.1:0`; path `/cdp/<32-byte token>`; requires `Authorization: Bearer <token>` (Playwright MCP
  `--cdp-header`); accepts exactly one client; rejects a second with close code 1008.
- Handles `Browser.getVersion` (synthetic), `Browser.setDownloadBehavior` (→ `deny` by default; V2-10 opens a
  short download window into `%LOCALAPPDATA%\PANINI\downloads` after approval), `Target.setAutoAttach`
  (root), `Target.createTarget`, `Target.closeTarget`, `Target.getTargetInfo`; everything else goes through
  `cdp_policy.decide(method, params, scope)` then to the extension.
- **Run binding:** a relay instance is bound to `(bridge_connection_id, attachment_grant, workspace_id,
  workspace_revision)`. Any change (detach, re-attach, tab switch outside the workspace, reconnect, revision
  bump by the person) closes the Playwright connection with reason `scope_changed`; the hand returns
  `blocked: "The attached tab changed. Attach it again to continue."` (v1 semantics).
- **Idle detach:** 120 s after the last command the relay detaches (the Chrome "is debugging this browser"
  bar disappears); the next chrome tool re-attaches.

### 5.4 CDP method policy (I9)

The same table is enforced in the relay and in the extension (defence in depth). Unknown methods are denied.

| Domain / method | Rule |
| --- | --- |
| `Page.*`, `Runtime.*`, `DOM.*`, `DOMSnapshot.*`, `Input.*`, `Accessibility.*`, `CSS.*`, `Overlay.*`, `Emulation.*`, `Log.*`, `Performance.*`, `IO.read`, `IO.close`, `Inspector.*` | allow (tab-scoped by the debugger session) |
| `Page.navigate` | allow only `http:`, `https:`, `about:blank`; deny `javascript:`, `file:`, `chrome:`, `chrome-extension:`, `data:`, `devtools:` |
| `Page.setBypassCSP`, `Page.setDownloadBehavior`, `Page.addScriptToEvaluateOnNewDocument` with `runImmediately` across all frames | `setBypassCSP` deny; download behaviour handled by the relay; init scripts allow (Playwright needs them) |
| `Network.enable/disable`, `Network.setCacheDisabled`, `Network.setExtraHTTPHeaders`, `Network.emulateNetworkConditions`, `Network.getResponseBody`, `Network.setBypassServiceWorker` | allow (this tab's own traffic) |
| `Network.getAllCookies`, `Network.clearBrowserCookies`, `Network.clearBrowserCache` | **deny** |
| `Network.getCookies` | allow only with `urls` all on the attached tab's current origin |
| `Network.setCookie(s)`, `Network.deleteCookies` | deny (account-class actions are refused in your Chrome) |
| `Storage.*` | deny, except `Storage.getStorageKeyForFrame` |
| `Fetch.*` | deny unless the `chrome` grant enables the `network` tool group (default off) |
| `Target.setAutoAttach` (child sessions), `Target.setDiscoverTargets` (filtered to scope), `Target.getTargetInfo` | allow / handled by the relay |
| `Target.createBrowserContext`, `Target.disposeBrowserContext`, `Target.exposeDevToolsProtocol`, `Target.createTarget` (direct), `Target.attachToBrowserTarget` | deny (the relay owns tab creation) |
| `Browser.*` (except the two synthetic ones) | deny |
| `Debugger.*`, `HeapProfiler.*`, `Profiler.*`, `Tracing.*`, `Memory.*`, `SystemInfo.*`, `Tethering.*`, `ServiceWorker.*`, `Security.*`, `Extensions.*`, `PWA.*`, `BackgroundService.*`, `Cast.*`, `WebAuthn.*` | deny |

The final allow-list is the intersection of this table and `cdp-methods-1.63.0.json` from the spike: if
Playwright needs a method not listed, the contract test fails and the method is reviewed explicitly.

Arbitrary page JavaScript requested by the **model** (`browser_evaluate`, `browser_run_code_unsafe`) is
blocked at the tool layer (V2-10), because CDP cannot tell Playwright's internal `Runtime.evaluate` from a
model-requested one.

### 5.5 Extension behaviour

- `cdp attach` is refused unless `target()` passes (attached tab or workspace member, matching grant and
  revision) and the tab is a website (`website(url)`; Web Store and `chrome://` pages are excluded, as today).
- `chrome.debugger.onDetach` (including the person pressing **Cancel** on Chrome's debugging bar) →
  `cdp_detached` → the relay revokes → PANINI stops the run and says "You stopped browser control."
- "Another debugger is already attached" (DevTools open on that tab) → error `DEVTOOLS_OPEN`: "Close DevTools
  on this tab, or PANINI will use basic controls."
- A content-script fallback stays: when `cdp-v1` is missing (old Claw, or the permission was not granted),
  the chrome hand offers v1 actions only and readiness says "Update JevRev Claw to v4 for full control".

### 5.6 Flows

```text
attach:  hand → relay.start(scope) → session(preset=chrome, --cdp-endpoint relay) → Playwright connects
         → Browser.getVersion (synthetic) → Target.setAutoAttach → relay: extension cdp attach(tab)
         → Target.attachedToTarget(pw-tab-1) → Playwright page ready
action:  model chrome_click{ref} → V2-10 checks → Playwright browser_click → Input.dispatchMouseEvent …
         → relay policy allow → extension: target() check → chrome.debugger.sendCommand → trusted click
revoke:  person detaches the tab in the popup → extension cdp detach + workspace revision → relay closes
         Playwright socket → in-flight call fails → hand returns blocked (scope_changed)
```

### 5.7 Security summary

| Threat | Control |
| --- | --- |
| Reading all cookies or other sites' storage | `Network.getAllCookies`, `Storage.*`, cookie setters denied in relay and extension |
| Driving tabs the person did not attach | Known tabs come only from the extension's attached/workspace set; `create_tab` only inside workspaces; `close_tab` only for PANINI-created tabs |
| Another local process using the relay | Loopback, 32-byte path token + bearer header, single client, token rotates per relay start |
| Stale scope after the person changes tabs | Binding tuple check on every frame in the extension and on every command in the relay |
| Silent long-lived debugging | Chrome's debugging bar while attached; idle detach after 120 s |

### 5.8 Failure modes

| Failure | Behaviour |
| --- | --- |
| Extension disconnects mid-action | Relay closes; effect calls after `dispatch` → `unknown_effect`; others `failed` |
| Debugger attach fails (policy pages, DevTools) | `blocked` with the reason; v1 actions remain available for reading |
| Event storm | Coalescing (§5.2); if the queue still exceeds 4,096 frames, the relay detaches with `overloaded` |

### 5.9 Alternative not adopted by default: whole-browser remote debugging

Chrome can expose the whole browser to local CDP clients after the person enables "Allow remote debugging
for this browser instance" (`chrome://inspect/#remote-debugging`); Playwright attaches with
`attach --cdp=chrome`. That gives every tab, every cookie and every extension to the client, which breaks
I9. PANINI does not offer it in v2.

## 6. Implementation steps

1. **Spike (½ day):** run Playwright MCP against a stock Chrome with a logging CDP proxy; exercise the core
   tool set (snapshot, click, type, fill_form, select, press, tabs, dialog, upload, wait, verify, screenshot,
   PDF); record methods → `cdp-methods-1.63.0.json`.
2. `cdp_policy.py` + `cdp-policy.js` from the table ∩ spike; identical-table test; I9 tests.
3. Relay server + scoped model (port with attribution); unit tests with a fake extension and a scripted CDP
   client.
4. Claw v4: manifest, `cdp.js`, frames, `target()` checks for CDP, popup text; extension unit tests.
5. Bridge multiplexing in `routes/browser.py`/`extension_bridge.py`; run binding and revocation.
6. `chrome` hand with `PlaywrightSession(preset=chrome)`; idle detach; fallback to v1 actions.
7. Trusted-input demo page; extend `scripts/verify-extension.py` to attach it and drive it through PANINI.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Policy table: every deny row, URL scheme rules, cookie origin rule, unknown → deny | `backend/tests/hands/test_cdp_policy.py`, `panini-core/capabilities/browser/extension/test/cdp-policy.test.mjs` |
| Unit | Tables identical in Python and JS | `backend/tests/hands/test_cdp_policy_parity.py` |
| Unit | Scoped model: unknown tab events dropped, child sessions, create/close rules | `backend/tests/hands/test_cdp_relay.py` |
| Invariant | I9 end to end: Playwright `context.cookies()` in chrome mode fails; `Storage.getCookies` never reaches the extension | `backend/tests/hands/test_i9.py` |
| Integration | Revocation: detach during a long `browser_wait_for` stops within 1 s with `blocked` | `backend/tests/hands/test_chrome_revoke.py` |
| Live | `scripts/verify-extension.py --cdp`: trusted-input page shows "trusted: yes" for click and keys; upload fixture after approval; dialog accept | `scripts/verify-extension.py` |
| Eval | Browser golden tasks in your Chrome (attached demo tab) ≥ v1 extension baseline | `scripts/panini-eval.py` |

## 8. Acceptance criteria

- [ ] Trusted-input demo reports `isTrusted=true` for clicks and key presses in an attached tab.
- [ ] I9 test green; the policy tables match; unknown methods are denied in both places.
- [ ] Detaching the tab, pressing Cancel on Chrome's bar, or switching tabs outside the workspace stops the
      run with a clear message.
- [ ] Browser golden tasks in your Chrome: success ≥ v1 extension baseline with fewer model calls per action
      (no propose-6/Jev-select round trip).

## 9. Rollout and rollback

Ship Claw v4 with the permission change and clear release notes (Chrome asks you to re-approve the
extension once). `PANINI_CLAW_CDP=true` in development after step 6; default at the Phase 2 gate. Rollback:
flag off → the chrome hand uses v1 actions; Claw v4 still works with v1 frames.

## 10. Open questions

- Should PANINI keep the debugger attached for the whole run to avoid the bar flickering between steps?
  Recommendation: attached for the run, detached 120 s after the last command.
