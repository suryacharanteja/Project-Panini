# V2-26: Universal hands protocol (one snapshot/ref vocabulary)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 6 · P2 · L (2 weeks) |
| Depends on | V2-10 (browser vocabulary), V2-21 (Android tree); desktop worker |
| Flag | `PANINI_DESKTOP_TOOLS` = `coarse` (default) · `fine` |
| Status | In progress: shared grammar, fine Windows registry hand, browser/Android/sandbox normalization built; macros and comparison gate pending |

## 1. Outcome

Every hand speaks the same language: take a snapshot, get a tree of roles and names with refs, act on a ref,
verify the result. The Windows desktop gains fine-grained, verifiable tools (as browsers have with
Playwright), so the brain, teach-by-demonstration, previews and external MCP clients treat web pages,
Windows apps, Android apps and sandbox computers the same way. Neither OpenDots nor OpenMuse has a
cross-surface observation model.

## 2. Scope and non-goals

- In scope: the common snapshot grammar and role mapping, a desktop UIA tree snapshot with refs and boxes,
  fine-grained desktop tools (click/type/select/toggle/scroll/press/verify), desktop highlight previews with
  the copper cursor, normalization adapters for Android and OpenBot sandbox snapshots, evaluation against
  the coarse desktop planner.
- Not in scope: replacing `desktop_draw`/visual planning for canvas apps (stays a macro), OCR-only apps
  (vision tier fallback).

## 3. Current state

- Desktop `observe` (broker op) returns `{observation_id, observed_at, foreground, elements: [{name, type,
  automation_id, value, enabled, patterns, bounds, …}]}`, a flat list; `DesktopAdapter.run` builds
  observation-scoped handles (`automation_id` when unique, else `control_<n>`) and compacts elements
  (`compact_element`, `backend/capabilities/desktop.py:56`) for its internal planner, which chooses one UIA
  action per step with a fixed postcondition.
- Visual planning (`desktop_visual.py`) handles drawing and screenshot-aware actions.
- Project memory: UIA `FromPoint` cannot resolve inside WinUI apps; drags must be paced.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| Playwright `ariaSnapshot({mode: "ai", boxes: true, depth})` grammar (`- role "name" [ref=e5] [box=x,y,w,h]`, states like `[checked]`, `[disabled]`) | A compact, model-friendly tree | The common grammar for all hands |
| Playwright verify tools (`verify_element_visible`, `verify_text_visible`, `verify_value`, `verify_list_visible`) | Deterministic assertions | `<hand>_verify_*` for every hand |
| PANINI `compact_element` and observation-scoped handles | Token-efficient UIA elements | Desktop refs and snapshot lines |
| Playwright `browser_highlight` | Show what will be acted on | Desktop highlight with the copper cursor ring (broker `overlay` op exists) |

## 5. Design

### 5.1 Common grammar

```text
- window "Untitled - Notepad" [ref=w1]
  - menubar [ref=w2]
    - menuitem "File" [ref=w3]
  - document "Text editor" [ref=w4] [value="Hello"] [box=0,80,1200,700]
  - button "Close" [ref=w5]
```

Rules: one line per node; `role "name"`; optional `[value=…]` (truncated to 100 characters),
`[checked]`, `[selected]`, `[expanded]`, `[disabled]`, `[password]` (value never shown), `[box=x,y,w,h]` in the
hand's coordinate space; refs are prefixed by hand (`e` web, `w` Windows, `a` Android, `s` sandbox) and valid
until that hand's next snapshot.

### 5.2 Windows role mapping

| UIA ControlType | Role |
| --- | --- |
| Button, SplitButton | button |
| Edit | textbox (`[password]` when `IsPassword`) |
| Document | document |
| CheckBox | checkbox |
| RadioButton | radio |
| ComboBox | combobox |
| List / ListItem | list / listitem |
| Menu, MenuBar / MenuItem | menu, menubar / menuitem |
| Tab / TabItem | tablist / tab |
| Tree / TreeItem | tree / treeitem |
| DataGrid, Table / DataItem | grid / row |
| Hyperlink | link |
| Text | text |
| Slider, Spinner, ScrollBar | slider, spinbutton, scrollbar |
| Window, Pane, Group, Custom | window, group, group, generic |

### 5.3 Desktop snapshot

- New broker op `snapshot {target, depth, max_nodes}` builds a tree (TreeWalker ControlView) under the target
  window, pruning offscreen and empty generic nodes, capped at 600 nodes; returns nodes with runtime ids
  (kept broker-side), names, roles, values, states, bounds.
- Hit-testing for coordinates uses the tree's bounds (not `FromPoint`), fixing the WinUI limitation.
- `desktop_snapshot` renders the grammar; the full tree is stored as a `snapshot` artifact.

### 5.4 Fine-grained desktop tools

| Tool | Effect | Broker action |
| --- | --- | --- |
| `desktop_snapshot(window?, depth?)` | observe | `snapshot` |
| `desktop_click(ref)` | submit dyn | Invoke pattern if available, else a paced pointer click at the box centre with the copper cursor |
| `desktop_type(ref, text, submit?)` | input (submit dyn) | ValuePattern `SetValue` for edits, else focused keystrokes; `[password]` → credential deny |
| `desktop_select(ref, option)` | input | SelectionItem / ExpandCollapse |
| `desktop_toggle(ref)` | input dyn | Toggle pattern |
| `desktop_scroll(ref, direction)` | navigate | Scroll pattern or wheel |
| `desktop_press(key)` | input dyn | Key chords |
| `desktop_verify_text(text)`, `desktop_verify_value(ref, value)`, `desktop_verify_element(role, name)` | observe (assertion) | snapshot checks |
| `desktop_draw(subject)` | submit | Existing visual planner macro |
| `desktop_open_application(application_id)` | local_exec | Existing |

Approval previews: the broker's `overlay` op shows the copper ring on the target and the worker captures a
crop; the preview carries it (as V2-10 does for browsers). Stale-ref guard as V2-10 §5.5.

### 5.5 Other hands

- Android (V2-21) emits the grammar natively.
- Sandbox (V2-11): OpenBot's snapshot plus `snapshotId` normalized into the grammar; `sandbox_click(ref)` maps
  back to OpenBot's `{ref, snapshotId}`.

### 5.6 Evaluation

The coarse `desktop_do` planner stays default until the fine tools win on the desktop golden tasks (Notepad
type-and-save, Calculator expression, Settings toggle, File Explorer rename with approval, Paint drawing via
the macro): success ≥ coarse, fewer model calls per task, p50 latency ≤ coarse + 10 %.

## 6. Implementation steps

1. Grammar renderer and parser shared by all hands (Python), with fixtures.
2. Broker `snapshot` op (Worker tree walk, pruning, bounds) and HMAC protocol update.
3. Fine-grained desktop tools with policy observations, password refusal and previews.
4. Normalizers for Android and sandbox.
5. Evaluation against the coarse planner; switch the default per the gate.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Grammar render/parse round trip; ref prefixes; password values hidden | `backend/tests/hands/test_snapshot_grammar.py` |
| Unit | Role mapping table | same |
| Integration | `Panini.Desktop.Fixture` app (existing test fixture) snapshot and actions | `panini-core/capabilities/desktop/tests/` + `scripts/panini-native-smoke.py` |
| Live | Desktop golden tasks with `PANINI_DESKTOP_TOOLS=fine` | `npm run panini:test:desktop-live` |

## 8. Acceptance criteria

- [x] Desktop snapshot works in Win32, WPF and WinUI apps (including where `FromPoint` fails).
- [ ] Fine tools meet the evaluation gate on the desktop golden tasks.
- [x] One grammar across web, Windows, Android and sandbox (shared parser tests).

The [Windows checkpoint report](../qualification-desktop-fine-2026-10-05.md) records genuine Win32 and
WPF fixture runs, plus installed Notepad with Win32/XAML providers. Fine pointer actions use bounded tree
hit-testing, bypassing UIA `FromPoint`. The Notepad task types, asserts and saves a disposable file; its
contents are checked on disk. These runs do not establish the coarse-planner comparison gate. Dedicated
draw/open macros, combined type-and-submit and remaining hand normalizers are pending. Selected-window
desktop recording/replay and non-invoking focus are delivered in the [Windows teaching checkpoint](../qualification-teach-desktop-2026-10-05.md);
remaining recording qualification is explicit there.
Browser snapshots now expose a normalized grammar alongside their original driver output. Sandbox snapshots
return `s` refs mapped server-side to the exact upstream `snapshotId`; a subsequent snapshot or action invalidates
them. The shared parser tests cover all four prefixes and an Android accessibility tree. This does not establish
the live OpenBot sandbox or the golden-task comparison gate.

## 9. Rollout and rollback

`fine` opt-in, then default per the gate. Rollback: `coarse`.

## 10. Open questions

- Expose `desktop_snapshot` and fine tools to MCP clients (V2-23)? Recommendation: yes, observe tools by
  default, effects with approvals, once the gate passes.
