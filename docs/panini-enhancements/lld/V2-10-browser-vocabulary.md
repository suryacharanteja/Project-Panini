# V2-10: Browser vocabulary v2, verification and traces

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 2 · P0 · M (5 days) |
| Depends on | V2-08 (sidecar), V2-09 (your Chrome) |
| Flag | Active with `PANINI_BROWSER_DRIVER=playwright` |
| Status | Not started |

## 1. Outcome

The agent gets Playwright's complete browser vocabulary (the tool names models already know from Playwright
MCP) on whichever browser the run uses: your Chrome, the managed profile, or an isolated one. Every tool has
an effect class and per-browser availability; risky targets are previewed with a highlighted screenshot;
stale targets are refused; password fields are never typed; and "done" requires an assertion
(`browser_verify_*`) after the last action. Managed runs leave a Playwright trace you can replay.

## 2. Scope and non-goals

- In scope: the active-browser model (`browser_select`), mapping of all 80 Playwright MCP tools, PANINI
  macros (`browser_media`, `browser_status`), privileged probes (field info, element at point, media state),
  stale-ref guard, highlight previews, snapshot budgeting, upload and download rules, network/header
  redaction, evidence rules, automatic tracing and optional video receipts, prompt principles, replacing the
  v1 direct `browser` tool and the extension planner loop.
- Not in scope: recording for teach-by-demo (V2-22 uses the recording tools listed here as owner-only),
  live frames (V2-12), sandbox computer browsers (V2-11 has its own OpenBot tools).

## 3. Current state

- Agent direct tool `browser` (`harness/agent.py:154-178`): `read, scroll_down, scroll_up, navigate, click,
  type, media_pause, media_play, skip_ad`, element ids from a controls list.
- Extension planner (`extension_planner.py`): immutable task plan, up to six grounded candidate actions per
  snapshot, Jev selects; completion requires evidence quotes and visible results.
- Approvals: `browser_action_needs_approval` on label/role/text/value/href with `RISKY_WORDS`.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| Playwright MCP tool catalogue (80 tools, verified in `coreBundle.js`) | Names, schemas, behaviour, capability groups | Exposed under the same names; effect and availability per browser (§5.3) |
| Playwright `ariaSnapshot({mode: "ai"})` and the `aria-ref=e15` selector | Snapshot with refs, including iframes; refs resolve through normal locators | Ref → `{role, name}` index parsed from each snapshot for classification and the stale-ref guard |
| Playwright `browser_verify_element_visible`, `browser_verify_list_visible`, `browser_verify_text_visible`, `browser_verify_value` | Deterministic assertions | Evidence kind `assertion` (I2) |
| Playwright `browser_highlight` (with `--style`), `browser_generate_locator` | Visible pointing and stable locators | Highlighted screenshot in approval previews; locators stored in receipts for replay (V2-22) |
| Playwright tracing (`browser_start_tracing/stop_tracing`) and video with chapters and action call-outs (`browser_start_video`, `browser_video_chapter`, `browser_video_show_actions`) | Replayable proof | Automatic trace per managed run; optional video receipts for effects |
| Playwright `lib/agents/playwright-test-healer.agent.md` | "Prefer robust locators; regular expressions for dynamic data; never wait for networkidle" | Prompt principles; healer reused in V2-22 |
| OpenDots `computer-types.ts` (`click/type` require `ref` and `snapshotId` from a fresh snapshot; 409 → "refresh with computer_snapshot before retrying") | Fresh-snapshot discipline | Stale-ref guard (§5.5) and the error text the model sees |
| v1 `content.js` media logic (playing, ad showing, skip button) | YouTube parity for reflexes | `browser_media` macro implemented with a privileged probe |

## 5. Design

### 5.1 Active browser

Each run has an **active browser**: `chrome` when Claw is paired, connected, attached and `cdp-v1` is
available; otherwise `managed`. The person's words still decide ("use a separate browser" → `managed`, as
v1's `MANAGED_REQUEST` regex). Tools act on the active browser; the model switches with:

| Tool | Effect | Notes |
| --- | --- | --- |
| `browser_select({browser: "chrome" | "managed" | "isolated"})` | navigate | `chrome` only when ready; switching never closes the previous browser |
| `browser_status()` | observe | Which browsers are ready, the active one, open tabs (titles, URLs) |

`hand_from_run: true` on these tools tells the registry (V2-02) to resolve the concrete hand and concurrency
slot (`browser:chrome`, `browser:managed`, `browser:isolated:<run>`) from the run's active browser.

### 5.2 Groups offered to the model

| Group | Default offered | Tools |
| --- | --- | --- |
| core | yes | snapshot, find, navigate, navigate_back, navigate_forward, reload, click, type, press_key, fill_form, select_option, check, uncheck, hover, wait_for, tabs, take_screenshot, verify_*, handle_dialog, file_upload, pdf_save, highlight, hide_highlight, generate_locator, press_sequentially, drag, drop, keydown, keyup |
| devtools | on demand (Jev selection) | console_messages, console_clear, network_requests, network_request, network_clear, evaluate |
| vision | vision tier or canvas pages | mouse_click_xy, mouse_move_xy, mouse_drag_xy, mouse_down, mouse_up, mouse_wheel |
| storage | managed/isolated only, on demand | cookie_*, localstorage_*, sessionstorage_*, storage_state, set_storage_state |
| network | managed/isolated only, on demand | route, route_list, unroute, network_state_set |
| owner-only (never offered) | — | start/stop_tracing, start/stop_video, video_chapter, video_show/hide_actions, start/stop_recording, annotate, resume, get_config, close, resize, run_code_unsafe (except isolated) |

### 5.3 Tool table

Effect classes are static minimums; `dyn` means V2-03 raises them from the target's role/name, dialog
message, key or fields. Availability: ✓ allowed, ✗ never offered on that browser, **T** tap approval
always required there.

| Playwright tool | Effect | chrome | managed | isolated | Notes |
| --- | --- | --- | --- | --- | --- |
| browser_snapshot | observe | ✓ | ✓ | ✓ | Truncated for the model; full snapshot stored as artifact (§5.6); taints `web` |
| browser_find | observe | ✓ | ✓ | ✓ | Preferred over re-reading large snapshots |
| browser_take_screenshot | observe | ✓ | ✓ | ✓ | Artifact; image to the model only in vision tier |
| browser_navigate | navigate | ✓ | ✓ | ✓ | Domain grants and blocked domains (V2-03); `isolated` also `net_guard` |
| browser_navigate_back / forward / reload | navigate | ✓ | ✓ | ✓ | |
| browser_tabs | navigate | ✓ | ✓ | ✓ | `close` of a tab PANINI did not open → **T** in chrome |
| browser_wait_for | observe | ✓ | ✓ | ✓ | |
| browser_click | submit dyn | ✓ | ✓ | ✓ | Stale-ref guard; highlight preview when asking |
| browser_type | input (submit dyn when `submit: true`) | ✓ | ✓ | ✓ | Password refusal probe |
| browser_press_sequentially | input | ✓ | ✓ | ✓ | Password refusal probe |
| browser_fill_form | input dyn | ✓ | ✓ | ✓ | Probe per field; any credential field → deny the whole call |
| browser_select_option | input | ✓ | ✓ | ✓ | |
| browser_check / browser_uncheck | input dyn | ✓ | ✓ | ✓ | "I agree / accept terms" → account |
| browser_hover | navigate | ✓ | ✓ | ✓ | |
| browser_drag | input dyn | ✓ | ✓ | ✓ | |
| browser_drop | input; files → share_out | ✓ | ✓ | ✓ | Files: same rules as upload |
| browser_press_key | input dyn | ✓ | ✓ | ✓ | `Enter` in a form field → submit dyn on the form's submit control |
| browser_keydown / keyup | input | ✓ | ✓ | ✓ | |
| browser_file_upload | share_out | **T** | **T** | **T** | Paths inside allowed roots, not secret-looking (`harness/computer.py:SECRET`); preview: file names, sizes, destination origin |
| browser_handle_dialog | accept: submit dyn (dialog text); dismiss: navigate | ✓ | ✓ | ✓ | "Delete…?" → destroy |
| browser_pdf_save | local_write | ✓ | ✓ | ✓ | Into artifacts only |
| browser_highlight / hide_highlight | observe | ✓ | ✓ | ✓ | Also used by the wrapper for previews |
| browser_generate_locator | observe | ✓ | ✓ | ✓ | Locators recorded in receipts |
| browser_verify_element_visible / list_visible / text_visible / value | observe | ✓ | ✓ | ✓ | Evidence `assertion` |
| browser_console_messages / console_clear | observe | ✓ | ✓ | ✓ | Taints `web` |
| browser_network_requests / network_request / network_clear | observe | ✓ | ✓ | ✓ | `cookie`, `authorization`, `set-cookie`, `x-api-key` headers stripped by the wrapper |
| browser_evaluate | local_exec | ✗ | **T** | **T** | Model JavaScript never runs in your Chrome |
| browser_run_code_unsafe | local_exec | ✗ | ✗ | **T** | |
| browser_mouse_click_xy / drag_xy / down / up | submit dyn | ✓ (vision) | ✓ | ✓ | Probe `element_at_point` classifies the target; unresolvable → **T** |
| browser_mouse_move_xy / wheel | navigate | ✓ (vision) | ✓ | ✓ | |
| browser_cookie_list / get, localstorage_* get/list, sessionstorage_* get/list | observe (sensitive) | ✗ | ✓ | ✓ | PANINI's own profiles only |
| browser_cookie_set / delete / clear, *storage set/delete/clear, browser_set_storage_state | account | ✗ | **T** | ✓ | |
| browser_storage_state | local_write (sensitive) | ✗ | **T** | ✓ | |
| browser_route / route_list / unroute / network_state_set | submit | ✗ | **T** | ✓ | Testing and mocking only |
| browser_resize, browser_close | navigate | ✗ | ✓ (wrapper) | ✓ (wrapper) | Owner/wrapper only |
| tracing, video, recording, annotate, resume, get_config | — | owner | owner | owner | Never offered to models (§5.8, V2-22) |

PANINI macro:

| Tool | Effect | Implementation |
| --- | --- | --- |
| `browser_media({action: "pause" | "play" | "skip_ad" | "state"})` | input (`state`: observe) | Privileged probe ported from `content.js` media logic: finds the main `<video>`, ad overlay and skip button; returns `{playing, ad_showing, ad_skippable_now}` (v1 fields) |

### 5.4 Privileged probes (V2-03 §5.7)

Fixed scripts the wrapper runs through Playwright's `browser_evaluate` with a `ref`. They are part of the
wrapper, reviewed, and never model-supplied.

| Probe | Returns | Used for |
| --- | --- | --- |
| `field_info(ref)` | `{tag, type, autocomplete, name, id, form_action, is_password}` | Credential refusal (I5), form submit detection |
| `element_info(ref)` | `{role, name, text (100 chars), href, disabled, in_viewport}` | Stale-ref guard, classification |
| `element_at_point(x, y)` | `element_info` of `document.elementFromPoint` (shadow-root aware, as `content.js:401`) | Coordinate actions |
| `media_state()` | v1 media fields | `browser_media` |
| `form_of(ref)` | submit control name and form action | `Enter` → submit classification |

### 5.5 Stale-ref guard

1. Every snapshot response is parsed into an index `{ref → (role, name)}` with a `snapshot_seq`.
2. An effect tool must use a ref from the latest snapshot of that page; otherwise the wrapper refuses with:
   "That element reference is from an older view. Take `browser_snapshot` again before acting."
3. When the decision is `ask`, the wrapper runs `element_info(ref)` again **after** approval and before
   dispatch. A different role or name, or a URL change, aborts with `PAGE_CHANGED` and nothing is sent.
4. Playwright itself fails when the DOM node behind a ref was removed; that error is returned as `failed`.

### 5.6 Snapshot budgeting

- Responses longer than 12,000 characters are truncated for the model with a note "Snapshot truncated. Use
  `browser_find` to search it." The full text is saved as a `snapshot` artifact linked from the receipt.
- `browser_find` runs over the stored full snapshot when the page has not changed (same `snapshot_seq`),
  saving a browser round trip.
- Mobile emulation (`--mobile`, lighter pages) is available for `isolated` research runs (Playwright CLI
  guidance: "mobile pages are usually lighter, so snapshots are smaller and cheaper").

### 5.7 Previews for approvals

When policy asks, the wrapper calls `browser_highlight(ref, style=copper)` → `browser_take_screenshot` →
`browser_hide_highlight`, stores the image as an artifact, and adds it to the interrupt preview with the URL,
the element's role and name, and (for `fill_form`/`type` with `submit`) the field names and values (values
are shown to the owner in the preview; receipts keep only digests, V2-04).

### 5.8 Evidence, traces and video

- After an effect, the wrapper records the post-action page state Playwright returns as an `observation`
  (weak evidence).
- `browser_verify_*` results are `assertion` evidence. `finish(verified_complete)` requires an assertion,
  file hash or postcondition after the last effect (I2, V2-06 §5.5). Manifest prompt principle: "After an
  action that should change the page, confirm the change with a `browser_verify_*` tool."
- Managed and isolated runs: the wrapper starts `browser_start_tracing` at the run's first browser call and
  stops it when the run ends; the trace zip is an artifact; Admin's receipt view has **Open trace**, which runs
  `node <playwright-core>/cli.js show-trace <file>` locally.
- Chrome runs: tracing captures DOM snapshots of your pages, so it is off unless `PANINI_CHROME_TRACING=on`.
- Optional video receipts (`PANINI_BROWSER_VIDEO=effects`): the wrapper records a short video with
  `browser_video_show_actions` call-outs around effect calls only.

### 5.9 Uploads and downloads

- Uploads: `browser_file_upload` and file drops need `tap`; files must be inside allowed roots, not match
  `SECRET`, and are listed with sizes and the destination origin in the preview; taint `sensitive_local`
  makes the approval mandatory even in `trusted` profiles.
- Downloads in your Chrome: Chrome downloads to your usual Downloads folder as it would for you; clicks
  classified as downloads are `local_write` with taint `download` (V2-03). The relay does not change Chrome's
  download behaviour. In managed/isolated, downloads land in `--output-dir` and are reported as artifacts.

### 5.10 Replacing v1 paths

| v1 | v2 |
| --- | --- |
| Agent tool `browser` (9 actions) | Removed when the flag is on; reflexes use `browser_media`, `browser_navigate` |
| `use_capability("browser", …)` → `ExtensionLoop` propose-6/Jev-select | Removed from the `use_capability` enum; the brain drives browser tools directly |
| `BrowserAdapter.open_in_chrome` | `browser_tabs({action: "new", url})` in chrome (workspace) or `browser_navigate` |
| Console Browser page (`/browser`) | Keeps working on the legacy loop until the cleanup PR, then calls the brain with `capability_hint` |

## 6. Implementation steps

1. Tool table as data (`backend/hands/browser_tools.py`): names, effects, groups, availability; manifest
   generated from it; contract test against `tools-1.63.0.json`.
2. Active browser state and `browser_select`/`browser_status`; registry `hand_from_run`.
3. Snapshot parser and ref index; truncation and artifact storage; `browser_find` local search.
4. Privileged probes; password refusal; stale-ref guard; header redaction.
5. Highlight previews for interrupts.
6. Evidence wiring (observation, assertion); I2 integration; prompt principles.
7. Automatic tracing for managed/isolated; Open trace in Admin; optional video receipts.
8. `browser_media` macro; reflex mapping; remove the v1 direct tool and the browser enum entry under the flag.
9. Extend browser golden tasks: upload (declined and approved), dialog, iframe form, hover menu, canvas click
   (vision), password refusal, stale-ref abort.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Tool table completeness (all 80 names mapped; unknown names hidden) | `backend/tests/hands/test_browser_tools.py` |
| Unit | Classification from role/name/dialog/keys; availability per browser | `backend/tests/hands/test_browser_policy.py` |
| Unit | Snapshot parser, truncation, local find | `backend/tests/hands/test_snapshot.py` |
| Invariant | I5: `browser_type` / `fill_form` into a password field always denied | `backend/tests/hands/test_password_refusal.py` |
| Integration | Stale ref after DOM change refused; approval then page change → `PAGE_CHANGED`, no click sent | `backend/tests/hands/test_stale_ref.py` (managed, demo page) |
| Integration | Trace artifact produced and opens with `show-trace --help` smoke | `backend/tests/hands/test_trace.py` |
| Eval | Browser cases (8 from V2-01 + 7 new) on chrome and managed | `scripts/panini-eval.py` |

## 8. Acceptance criteria

- [ ] All 80 Playwright tools are mapped; the model never sees owner-only tools.
- [ ] Browser golden tasks pass on chrome and managed with success ≥ v1 and fewer model calls per action.
- [ ] Password refusal, stale-ref abort and upload approval proven by tests.
- [ ] A managed run's receipt links a trace that opens in the Playwright trace viewer.

## 9. Rollout and rollback

Ships with the driver flag. Rollback: `PANINI_BROWSER_DRIVER=legacy` restores the v1 tool and loops.

## 10. Open questions

- Offer `browser_evaluate` in your Chrome behind an explicit, expiring grant for power users? Recommendation:
  no in v2; read-only probes cover the safe cases.
