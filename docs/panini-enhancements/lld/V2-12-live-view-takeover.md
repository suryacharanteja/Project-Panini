# V2-12: Live view and human takeover

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 2 · P1 · M (4–5 days) |
| Depends on | V2-07 (cards), V2-08 (Playwright sessions), V2-11 (sandbox) |
| Flag | `PANINI_LIVE_VIEW` (default `false`) |
| Status | Done (2026-10-03) |

## 1. Outcome

Whatever hand PANINI is using (your Chrome, the managed browser, a sandbox computer, the Windows desktop,
later Android), you can watch it live in one panel on the avatar, the console or your phone, see a visible
pointer move in the browser as it acts, and take control with one button. When you give control back, PANINI
looks again before it acts.

## 2. Scope and non-goals

- In scope: frame sources per hand, the live hub, delivery to shell/console/AG-UI (and to the phone through
  V2-14), the unified takeover state machine, owner input for sandbox and managed browsers, the visible
  pointer in browsers, the `LiveView` card, privacy rules.
- Not in scope: remote transport and E2E encryption (V2-14), Android frames (V2-21 plugs in), recording
  demonstrations (V2-22).

## 3. Current state

- `TaskContext.computer_activity` publishes `computer.activity` with an optional JPEG thumbnail ≤ 46 KB
  (`coordinator.py:173-196`); the shell shows it in `ComputerUsePanel` with a timeline; full screenshots stay
  broker-side.
- Desktop shows a large copper cursor; the run stops if you move the mouse (README "A visible hand on your PC").
- Browsers: no live view in v1 beyond extension screenshots used by the planner.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| Playwright 1.63 `page.screencast.start({onFrame, size, quality})` | JPEG frames with viewport size and timestamp | `init-live.mjs` (`--init-page`) streams frames to the backend for Playwright sessions (managed, isolated, chrome) |
| Playwright `page.screencast.showActions({cursor: "pointer", duration, position})` | Animated pointer from the previous action point to the next, with an action title | The browser equivalent of the desktop copper cursor; on by default while PANINI acts |
| OpenBot control API via OpenDots (`/control` → `{holder: bot|human, requested, transitioning, resumeSnapshotRequired, request: {id, status}}`, `/control/request`, `/control/take`, `/control/release`, `/human/click|type|key|scroll`) | Takeover state machine and owner input | Generalized to every hand (§5.4) |
| OpenDots `ComputerPanel.tsx` | Browser/Files/Terminal/Activity tabs, polling only while visible and running, owner controls, revision counters to drop stale responses | `LiveView` card and Admin Computers page |
| OpenDots `ComputerToolCard.tsx` | Inline preview "Dot's computer · Current browser view" with URL line and live indicator | Inline preview in ToolCard (V2-07) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/live/hub.py` | `LiveHub`: latest frame per (run, hand, target), throttled fan-out, viewer counts |
| `backend/live/control.py` | Takeover state per hand; tool-call gating |
| `backend/routes/live.py` | Ingest (loopback), stream (SSE), latest frame, control, owner input |
| `panini-core/capabilities/browser/playwright/init-live.mjs` | Screencast + showActions inside each Playwright MCP process |
| `backend/hands/sandbox.py` | Screenshot polling while watched; `human_*` input |
| `backend/capabilities/desktop.py` | Forwards existing activity frames; optional low-rate capture while watched |
| `panini-core/apps/shared-ui/cards/LiveView.tsx` | Frame, chips, Take/Give back, tap-to-click, key input, timeline |

### 5.2 Frame sources

| Hand | Source | Rate (watched / unwatched) |
| --- | --- | --- |
| Playwright browsers | `init-live.mjs`: `page.screencast.start({size: {width: 960, height: 600}, quality: 60, onFrame})` posts to `POST /api/live/ingest/<session-token>` | ≤ 4 fps / off (started only when a viewer subscribes or an effect is pending approval) |
| Sandbox | `GET /screenshot` on the computer | 1 frame / 2 s while watched (OpenDots polls every 3–4 s when visible) |
| Desktop | `computer.activity` thumbnails (existing); a new broker op `frame` (Worker `WindowCapture.cs`) for the target window | per action / 1 fps while watched |
| Android (V2-21) | `device.screenshot()` | 1 fps while watched |

`init-live.mjs` receives `PANINI_LIVE_URL` and a per-session `PANINI_LIVE_TOKEN` through the sidecar
environment (the only PANINI values passed to it). Frames are posted, not pulled, so no port is opened in
the sidecar.

### 5.3 Hub and delivery

```python
class LiveHub:
    def publish(self, run_id: str | None, hand: str, target: str, jpeg: bytes, meta: dict) -> None
    def latest(self, hand: str, target: str) -> tuple[bytes, dict] | None
    async def subscribe(self, hand: str, target: str, *, max_fps: float) -> AsyncIterator[dict]
    def viewers(self, hand: str, target: str) -> int
```

- In memory only; the latest frame per target plus the last 20 metadata records. Frames are never written to
  disk except effect-point snapshots that become artifacts (V2-04, linked from receipts).
- `GET /api/live/{hand}/{target}/stream` (SSE): `{seq, ts, url, title, width, height, frame: <base64 JPEG>}`;
  shell and console at ≤ 4 fps; remote surfaces (V2-14) at ≤ 1 fps and 480 px wide.
- `GET /api/live/{hand}/{target}/latest.jpg` for cards; `live.frame` events carry `{hand, target, seq}` and the
  URL, never the image, except the existing ≤ 46 KB thumbnails for the shell.

### 5.4 Takeover

State per (hand, target): `holder ∈ {agent, human}`, `requested`, `transitioning`,
`resume_snapshot_required`, `request {id, status}` (OpenBot's fields).

| Action | Who | Effect |
| --- | --- | --- |
| `take(hand, target)` | owner (any surface with the live grant) | `holder=human`; agent tool calls on that target return `blocked`: "You have control of <hand>. PANINI is waiting." The run keeps its lease; Stop still works |
| `release(hand, target)` | owner | `holder=agent`, `resume_snapshot_required=true`; the next effect tool on that target is refused until a snapshot/observe tool ran (enforced with V2-10's stale-ref guard) |
| `request_takeover(reason)` | agent (V2-05 tool) | Opens a `takeover` interrupt; resolving it "Done" performs `release` |

Per-hand behaviour:

| Hand | Owner input while holding control |
| --- | --- |
| Sandbox | `human_click/type/key/scroll` (OpenBot), from tap-to-click on the live frame (coordinates scaled from the displayed image) |
| Managed browser | On the PC: the visible window. Remotely: owner-only `browser_mouse_click_xy`/`browser_type`/`browser_press_key` through the Playwright session |
| Your Chrome | On the PC: your tab as usual. Remotely: **off by default**; when enabled it is `verify` level (passkey) per takeover, attached tabs only |
| Desktop | On the PC: your mouse and keyboard; v1 "moving the mouse stops the task" becomes "moving the mouse takes control" (pause, not stop) with an on-screen "Give back" chip; voice "carry on" releases (release resumes the agent; every later effect still passes policy) |

### 5.5 Visible pointer in browsers

`init-live.mjs` calls `page.screencast.showActions({cursor: "pointer", duration: 500, position: "top-right"})`
for managed and chrome sessions while a run is active (disposed when the run ends), so you see where PANINI
is clicking and what it is doing in the page, matching the desktop's visible cursor.
`PANINI_BROWSER_SHOW_ACTIONS=on|off` (default on).

### 5.6 Privacy

- Live frames of your Chrome and desktop are personal. Remote viewing (phone, relay) needs the
  `live.remote` grant for that hand, default **on** for sandbox and managed, **off** for chrome and desktop.
- Frames over the relay are E2E-encrypted (V2-14). The relay never stores frames.
- Viewer presence is shown on the PC ("Watching from your phone") when a remote surface subscribes to chrome
  or desktop frames.

### 5.7 Failure modes

| Failure | Behaviour |
| --- | --- |
| Screencast unsupported in a page (for example a PDF viewer) | Fall back to `browser_take_screenshot` once per action |
| Frame ingest flood | Hub keeps only the latest frame; older frames are dropped |
| Viewer disconnects | Rate drops to unwatched levels within 5 s |
| Takeover never released | The run waits up to the takeover interrupt TTL (30 min), then ends `partial`: "You still have control; I stopped." |

## 6. Implementation steps

1. `LiveHub` and routes (ingest, stream, latest); tests with synthetic frames.
2. `init-live.mjs` with screencast and showActions; sidecar environment wiring; managed demo.
3. Sandbox polling while watched; desktop frames from activity and the new broker `frame` op.
4. Takeover state machine and tool gating; release → resume-snapshot rule.
5. Owner input paths (sandbox `human_*`, managed owner-only tools); desktop "take control instead of stop".
6. `LiveView` card in shell, Admin and (V2-15) phone.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Hub throttling, latest-frame semantics, viewer counts | `backend/tests/live/test_hub.py` |
| Unit | Takeover gating: tool blocked while human holds; refused after release until a snapshot | `backend/tests/live/test_control.py` |
| Integration | Managed demo page: frames arrive at ≤ 4 fps when watched, none when unwatched | `backend/tests/live/test_screencast.py` (local) |
| Live | Desktop: move the mouse during a Notepad task → paused, chip shown, "carry on" resumes, task completes | `npm run panini:test:desktop-live` (extended) |
| Live | Sandbox: take control from the console, type into the sandbox browser, give back, agent re-snapshots | `scripts/panini-sandbox-live.py` (extended) |

## 8. Acceptance criteria

- [ ] One LiveView shows chrome, managed, sandbox and desktop frames for a run that uses them.
- [ ] Take/give back works on each hand with the resume-snapshot rule enforced.
- [ ] The animated pointer appears in the managed browser and your Chrome during actions.
- [ ] Remote viewing of chrome/desktop is off until granted.

## 9. Rollout and rollback

Flag on in development after step 4. Rollback: flag off; v1 thumbnails remain.

## 10. Open questions

- Should desktop takeover pause only the desktop hand or the whole run? Recommendation: only the hand; other
  hands (search, managed browser) may continue if the plan allows.

## 11. As built (2026-10-03)

| Part | Where | Notes |
| --- | --- | --- |
| Hub | `backend/live/hub.py` | Newest frame per (hand, target), never queued; viewer counts; a 5 s grace before sources stop; pull sources (`register_poller`) run only while watched; polling viewers (`touch`) count as watching. Viewers are woken on their own event loop, so frames may be published from any thread. |
| Takeover | `backend/live/control.py` | OpenBot's fields (holder, requested, transitioning, resumeSnapshotRequired, request). `gate()` blocks agent calls while you hold a hand and refuses effects after hand-back until an observe tool ran. Listeners mirror the state into hands (OpenBot's own `/control`). |
| Routes | `backend/routes/live.py` | `/api/harness/live` and `/api/panini/harness/live`: list, SSE stream (≤ 4 fps), `latest.jpg` (with URL, title and size headers), control, take / release, owner input. Ingest at `/api/live/ingest/<token>` from loopback only, with a per-session token; `console.py` treats it as a non-console route. |
| Browser frames and pointer | `panini-core/capabilities/browser/playwright/init-live.cjs`, `playwright_mcp.py` | Loaded with `--init-page` in sessions that have a live identity (managed, your Chrome, a native sandbox computer's browser). It asks PANINI every second whether anyone watches and starts or stops `page.screencast` (960×600, quality 60, ≤ 4 fps). `showActions` draws the pointer and the action title (`PANINI_BROWSER_SHOW_ACTIONS`). The token and backend address go through the sidecar environment (and through the box's allow-list). |
| Sandbox | `backend/hands/sandbox.py` | Container computers: `GET /screenshot` every 2 s while watched. Native computers stream through their browser's screencast. Owner input maps to `human_*`. Admin, the live view and the agent all share one takeover state. |
| Managed browser | `backend/hands/browser_v2.py` | Every agent tool on chrome/managed passes the gate. Owner input on the managed browser uses coordinate and key tools the agent is never offered. Your Chrome takes input on the PC itself (remote input stays off). |
| Desktop | `backend/capabilities/desktop_visual.py`, `backend/panini/coordinator.py` | Action frames go to the hub. With live view on, `USER_INPUT_ACTIVE` (you moved the mouse) takes control instead of stopping: the task waits (Stop still works; 30 min TTL ends it), then looks again. Saying "carry on" (and similar) gives back every hand you hold. With live view off, the v1 stop remains. |
| Cards | `panini-core/apps/shared-ui/live.tsx`, Admin `pages/Live.tsx`, shell `ShellLive.tsx` | One `LiveView` card: frames, URL and title, live indicator, Take / Give back, tap-to-click scaled to page coordinates, wheel, typing and keys. Pluggable transport: SSE in browsers; the shell bridges through its main process (`live` operation, path allow-list, bearer token). |

Verification:
- Unit and route tests: `backend/tests/live/` (hub, takeover, routes, desktop takeover, "carry on"), plus takeover tests in `test_sandbox.py` and `test_browser_v2.py`.
- Integration (local, real browser): `backend/tests/live/test_screencast.py`: no frames while unwatched, ≤ 4 fps while watched, frames stop after the grace period.
- Live in Admin (native sandbox browser running as `panini-sandbox`): the Live page streamed the agent's page and followed it to a new page. Take control showed "you have control". Tab and Enter from the card followed a link in the sandbox browser, and the frame showed the new page. Give back showed the look-again notice.
- Pointer: frames captured during an agent click show the pointer on the link and the title "Click Learn more link".

Not built in this item:
- the `request_takeover` agent tool (the state machine supports it; the V2-05 tool and interrupt wiring remain);
- remote grants and viewer presence for chrome/desktop (they belong with the remote transport, V2-14);
- the live desktop run that moves the real mouse during a Notepad task.
