# V2-22: Teach by demonstration and self-healing skills

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 5 · P2 · L (2 weeks) |
| Depends on | V2-10 (browser vocabulary), V2-04 (receipts), V2-05 (reviews); desktop broker changes |
| Flag | `PANINI_TEACH` (default `false`) |
| Status | In progress |

## 1. Outcome

"Watch me do this." You do a task once, in the browser or a Windows app, and PANINI turns it into a reusable
skill with inputs ("search term", "date"), expected results, and steps it can replay reliably. When a site or
app changes, PANINI repairs the step itself, proves the repaired run worked, and asks you to approve the
updated skill. Skills never grant permissions: every effect in a replay still goes through policy.

## 2. Scope and non-goals

- In scope: browser recording through Playwright's recorder, desktop recording in the .NET worker, step
  normalization, parameterization, expected-state assertions, skill bundles (Agent Skills format + replay
  data), deterministic replay through registry tools, the healer loop, skill review and versioning, metrics.
- Not in scope: Android recording (later, on V2-21/V2-26), recording passwords or payment entry (never).

## 3. Current state

- Skills: `harness/skills.py` (`SKILL.md` with frontmatter; project > user > built-in; `create_skill` tool;
  "save as skill?" suggestions after multi-step runs in `harness/proactive.py`). Skills are instructions for the
  model, not replayable steps.
- Desktop worker (`panini-core/capabilities/desktop/Panini.Desktop.Worker/`): grounded input, element anchors,
  window capture; project memory notes that UIA `FromPoint` cannot resolve inside WinUI apps.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| Playwright MCP `browser_start_recording` / `browser_stop_recording` and CLI `recording-start/stop` ("record user actions in the browser, print them as Playwright code on stop") | Recording human actions with Playwright's locator generation | Owner-only tools; output parsed into steps (§5.3) |
| Playwright `browser_generate_locator`, `getByRole/getByTestId/getByText` locator grammar | Stable targets | Steps store locators plus role/name |
| Playwright `--codegen python|typescript` | Code generation for replay | Optional `replay.py` export for power users (not used by PANINI's own replay) |
| Playwright `lib/agents/playwright-test-healer.agent.md` | Healing workflow: run → debug the failing step → snapshot → analyze selectors, timing, data → repair → re-run → iterate; prefer robust locators; regular expressions for dynamic data; never wait for networkidle | Healer loop (§5.6) with PANINI's approval gate on the patch |
| Playwright `lib/agents/playwright-test-planner.agent.md` | Exploration discipline (snapshot over screenshots) | Recording review screenshots are artifacts; the model works from snapshots |
| PANINI skills format | Progressive disclosure | Bundle keeps `SKILL.md` as the entry point |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/teach/recorder.py` | Starts/stops recording sessions for browser or desktop; collects raw events |
| `backend/teach/codegen_parser.py` | Playwright code → normalized steps |
| `backend/teach/desktop_events.py` | Desktop raw events → normalized steps |
| `backend/teach/parameterize.py` | Inputs and defaults (fast model + rules) |
| `backend/teach/bundle.py` | Skill bundle read/write and versions |
| `backend/teach/replay.py` | Deterministic replay through the registry |
| `backend/teach/healer.py` | Repair loop |
| `panini-core/capabilities/desktop/Panini.Desktop.Worker/Recorder.cs` | Low-level hooks + UIA capture |
| Broker ops `record_start` / `record_stop` | Added to the HMAC-signed pipe protocol |
| `backend/harness/migrations/0030_teach.sql` | recordings, reviewed versions and `skill_runs` |

### 5.2 Recording

Browser (managed or your Chrome):

```text
"Watch me" → recorder.start(kind="browser", session) → Playwright browser_start_recording (owner-only)
person works → "Done" → browser_stop_recording → Playwright code → codegen_parser → steps
also captured: snapshot at start and end (artifacts), URLs, a screenshot per step (from the live hub)
```

Desktop:

```text
recorder.start(kind="desktop", window) → broker record_start → Worker hooks WH_MOUSE_LL + WH_KEYBOARD_LL
each click: UIA element (AutomationId, Name, ControlType, ancestry path, bounding rect) via tree hit-testing
            by bounds (FromPoint is unreliable in WinUI apps) + window-relative point + small crop screenshot
typing:     aggregated per focused element; skipped entirely when the element IsPassword (never recorded)
keys:       shortcuts as key chords; scroll and drag as gestures
record_stop → events → desktop_events → steps
```

Recording shows a red "Recording" chip on the avatar and in the page (managed/Chrome via the overlay); Stop
is always available.

### 5.3 Normalized steps

```json
{"version": 1, "kind": "browser",
 "steps": [
   {"id": "s1", "tool": "browser_navigate", "args": {"url": "https://shop.example/"}},
   {"id": "s2", "tool": "browser_click", "target": {"locator": "getByRole('searchbox', { name: 'Search' })", "role": "searchbox", "name": "Search"}},
   {"id": "s3", "tool": "browser_type", "target": {"role": "searchbox", "name": "Search"}, "args": {"text": "{{query}}", "submit": true}},
   {"id": "s4", "tool": "browser_click", "target": {"role": "link", "name_regex": "^{{product}}"}}
 ],
 "expect": [{"tool": "browser_verify_text_visible", "args": {"text": "Add to cart"}}]}
```

Parser coverage: `goto`, `click`, `dblclick`, `fill`, `press`, `selectOption`, `check`, `uncheck`, `hover`,
`setInputFiles` (kept as a step needing a parameterized file and approval), frame locators. Unparsed lines
become `{"tool": "manual", "note": …}` steps that the review shows for the person to fix or delete.

### 5.4 Parameterization and expectations

- Typed values and selected options become candidate inputs. A fast-model pass with the person's one-line
  description proposes names, types and which values are constants (rules first: dates, emails, numbers,
  quoted search text are inputs).
- PANINI proposes 1–3 expectations from the final snapshot (text or element that proves success); the person
  confirms in review.
- Secrets: password fields are never recorded; values matching secret patterns (`harness/memory.py:_SECRET`)
  are dropped and the step is marked "you will be asked to type this yourself" (takeover).

### 5.5 Skill bundle

```text
<skills>/<name>/SKILL.md          frontmatter: name, description, inputs (JSON Schema), kind, version
<skills>/<name>/replay.json       normalized steps + expectations
<skills>/<name>/recording/        step screenshots (artifacts copied), start/end snapshots
<skills>/<name>/CHANGELOG.md      versions with healer patches and approvals
```

The review interrupt shows each step with its screenshot, the inputs, the expectations, and which steps are
effects (with their classes). Saving requires approval (V2-13 skill-poisoning rule).

### 5.6 Replay and healing

Replay (`use_skill(name, inputs)` tool, or the agent loading the skill):

```text
for each step: resolve target → snapshot → find node by role + name (exact, then regex) → ref
               → ctx.call_tool(step.tool, args with ref)  (policy, interrupts, receipts apply)
               → settle → next
then: run expectations (assertions = evidence for I2)
```

Healer (when a target is not found uniquely, an action fails, or an expectation fails):

1. Take a fresh snapshot (and screenshot for the vision tier).
2. Ask the model: given the step's intent (tool, original role/name/locator, screenshot from the recording)
   and the current snapshot, which ref matches, or is the page in an unexpected state?
3. Try the repaired step; continue; run the expectations.
4. If the run passes, write a **proposed patch** (new role/name/regex) to the skill as `pending` and open a
   review interrupt "Update skill <name>: step s4 now matches 'Buy now' instead of 'Add to cart'".
5. Never more than 2 repairs per replay; otherwise stop with `partial` and the evidence.

Metrics per skill (`skill_runs`): runs, passes, heals, last failure; a skill with 3 consecutive failures is
marked "needs attention" in Admin.

## 6. Implementation steps

1. Browser recording (owner-only tools), codegen parser with fixtures from real recordings.
2. Bundle format, review interrupt, save and versions.
3. Replay engine through the registry; expectations as evidence.
4. Healer loop and patch review.
5. Desktop recorder in the worker (hooks, UIA by bounds, password skip), broker ops, event normalizer.
6. Parameterization; Admin skill page with runs, heals and changelog.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Codegen parser on recorded Playwright snippets (all supported statements, unparsed lines) | `backend/tests/teach/test_codegen_parser.py` |
| Unit | Parameterization rules; secrets dropped | `backend/tests/teach/test_parameterize.py` |
| Integration | Record on the demo shop page (managed), replay with new inputs, expectations pass | `backend/tests/teach/test_replay_demo.py` (local) |
| Integration | Change the demo page's button label → healer repairs → patch review opened | `backend/tests/teach/test_healer.py` (local) |
| Live | Desktop: record "open Notepad, type a line, save to a file"; replay with a new line | `npm run panini:test:desktop-live` (extended) |

## 8. Acceptance criteria

- [x] A browser recording becomes a reviewed skill that replays with different inputs.
- [ ] The healer repairs a changed label and proposes a patch; nothing changes without approval.
- [x] A desktop recording replays in Notepad; passwords never appear in recordings.

The [browser checkpoint report](../qualification-teach-browser-2026-10-05.md) records real pinned-recorder
and registry replay evidence. The changed-label repair test uses a deterministic model; live model
qualification remains pending. Named iframe replay resolves descendants of a uniquely selected iframe
from each fresh snapshot; other frame selectors remain manual. A pinned-recorder run in a real managed
browser recorded, reviewed and replayed a changed input in a named iframe. Multiple top-level iframes
are now permitted when the current DOM and snapshot frame counts agree, the reviewed selector matches
exactly one DOM iframe, and its label is unique. The binding is rechecked before effects. The [Windows teaching checkpoint](../qualification-teach-desktop-2026-10-05.md)
adds selected-window recording, normalization, private-field suppression, genuine WPF replay and installed
Notepad record/type/save replay with a different Unicode input and exact disk verification. Opening an app
as a recorded step now has a catalog-only, owner-reviewed open-and-record path. The launch is stored as
the first replay step; replay opens the app once and binds its newly verified window before acting.
This path has fixture integration and built-Admin browser coverage, but has not yet passed a live
native Teach recording/replay qualification. Save As dialogs, per-step crops, gestures and the remaining
requirements are not qualified yet. A real managed-browser replay also passes with a reviewed iframe served from a second
loopback origin; arbitrary external origins and nested frames remain unqualified. The LLD remains in progress.

## 9. Rollout and rollback

Flag on after step 4 for browsers; desktop recording follows. Rollback: flag off; skills remain usable as plain
`SKILL.md` instructions.

## 10. Open questions

- Share skills between PANINI installations (export/import with signatures)? Recommendation: later; skills
  can contain sensitive URLs and steps.


## Per-step crops — 2026-10-06

Windows recordings keep a 160×160 PNG crop around each grounded click:
- The worker (`WindowCapture.Crop`/`CropWithin`) captures the target window with PrintWindow. Hung windows are
  skipped and a capture is abandoned after 500 ms, so a hung or suspended app never stalls recording (found and
  fixed with the smoke's suspended-provider case).
- Password and ungrounded targets get no crop.
- The recorder checks each crop is a small PNG, stores it as a `screenshot` artifact, and the step carries only
  `crop_artifact`; images never live inside the skill.
- Tests: `backend/tests/teach/test_desktop.py` (PNG kept, non-PNG dropped, clicks only).
- Live: `scripts/panini-teach-desktop-smoke.py` with real hooks on the WPF fixture asserts every recorded click
  has a stored PNG crop (`artifacts/teach-desktop/click-crop.png`) and still passes all its existing checks.

Still open: Save As dialogs (owned dialog windows during recording and replay), gestures, and live healer
qualification with a real model.
