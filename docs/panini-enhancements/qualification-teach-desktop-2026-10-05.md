# Windows teaching development checkpoint — 2026-10-05

## Delivered

V2-22 now records an explicitly selected, allowed Windows window using a dedicated worker, signed broker
operations and owner console routes. The recorder is opt-in and requires Teach, durable reviews, the
ledger and fine desktop tools. The broker permits one recording, binds stop to the recording task/token,
blocks product input during recording and fences starts racing with Stop. Its 15-minute expiry kills the
recording worker; an independent worker watchdog exits on broker death even if a UIA call is blocked.

Mouse hooks emit positions for bounded UIA tree hit-testing. Keyboard hooks emit text-change markers,
never printable key codes or raw keyboard text. Approved field values are aggregated through UIA;
password and credential ancestry are checked before and after reading. Event counts, queues, field values,
tree sizes and replay schemas are bounded. Unsupported or uncertain interactions become manual steps;
incomplete recordings become interrupted. Restart never resumes a recording or repeats its effects.

Normalization strips native IDs and retains accessible role/name/ancestry selectors. It proposes editable
inputs and stores sanitized start/end snapshots. Private ancestors also mask descendant snapshot values.
Saving still requires a reviewed version with a specific expectation. Replay selects exactly one window
of the reviewed application, resolves fresh refs for each step and uses the normal registry, current
permissions and receipts. Missing or ambiguous targets stop replay; uncertain effects are not retried.
Passing repairs remain proposed versions awaiting owner review.

Keyboard replay uses `desktop_focus`, a separately receipted SetFocus operation, then verifies fresh focus
before typing or pressing keys. The native dispatch now distinguishes control focus from legacy window
activation; the native proof starts on a different control and verifies that focus actually moves without
invoking the button. Desktop element expectations may bind a current
ref to its native anchor, so a replacement control cannot satisfy the assertion solely by sharing a name.
Cleanup uncertainty prevents a passing replay result.
Each desktop replay owns a separate native task, so its cleanup cannot fence the invoking agent's desktop
session or later actions. A regression test preserves an existing session across a replay.

Replay explicitly calls the receipted `desktop_activate` action to bring the selected allowed window
forward. Observations never activate it. Selection, current window title, application and native identity
are checked around review and dispatch; thread input attachment is always detached. Replay pins the
initial window title and selected tab identity and stops if the document changes between steps. Notepad's
leading unsaved marker may change; a different selected tab with the same title still stops replay. The
tab binding is an opaque digest, never a native UIA ID. Recording also activates only its
explicitly selected window before installing hooks.

Recorded typing now replaces the complete captured field value rather than appending it. Document
replacement requires TextPattern readback, the same focused editable non-credential control and exact
result verification. Empty replacement is supported. Enter and Tab remain separate reviewed steps;
invalid control characters are rejected before Select All. Unicode input is paced per UTF-16 unit with
fresh focus/foreground checks, including between surrogate halves. This fixes corrupted surrogate input
observed in Notepad while low-level recording hooks were active. A readback failure never resends input.

Admin → Build → Skills includes a Windows source, explicit window selection, reviewed application and
ancestry display, desktop expectations and an application-filtered replay picker. Stop remains usable
after disabling Teach and while replay is awaiting a decision. The existing copper styling is retained.

## Verification and evidence

- `scripts/panini-teach-desktop-smoke.py`: genuine Windows hooks, WPF UIA and registry replay against a
  disposable owned fixture. Records Unicode text and arrow keys, resets the field, replays the reviewed
  steps, checks the visible result and inspects receipts. Separately checks token/task mismatch, concurrent
  recording/input refusal, Stop/start fencing, fresh recording after cancellation and focus without invoke.
  A test driver types a fixed string into the fixture's focused PasswordBox after checking the owned
  HWND/PID; neither that string nor the fixture's password appears in replay data or snapshot artifacts.
  It also suspends only the fixture's UIA provider, terminates only its own broker and verifies that the
  recording child exits within five seconds. This covers cleanup independently of a responsive provider.
- `scripts/panini-teach-notepad-smoke.py`: installed Notepad, real recording hooks and registry replay
  against a newly created disposable file. Records Unicode typing and Ctrl+S, reviews a parameterized
  skill, replaces a different existing value with a different Unicode input, verifies the accessible
  document and compares the saved file exactly. A separate owned WPF window is explicitly focused first
  to prove replay activation. Empty replacement is also read back. Only the script's current document
  is closed; existing user documents and the shared Notepad process are not closed. Evidence is under
  `artifacts/teach-notepad/` and copied into the release checkpoint. This qualifies selected-window
  recording/type/save; opening Notepad inside a recording and Save As dialogs remain unqualified.
- `backend/tests/teach/test_desktop.py`: malformed selectors, bounded data, sensitive ancestry, fresh refs,
  exact window scope, parameterized Unicode, database/start/stop failures, revoked flags, keyboard/private
  focus, replacement assertions, ambiguous windows and no retry after an unknown effect.
- `scripts/panini-teach-ui-smoke.mjs`: built Admin in real Chrome at desktop and 412-pixel phone widths.
  Uses explicitly identified fixture API responses to inspect request payloads, window filtering, desktop
  expectations and Stop controls. This UI test is separate from the native proof; it is not an Android
  hardware result. Screenshots were inspected for clipping and overflow.
- The full repository gate, focused suite, native build, unsigned package smoke and exact source/file hash
  checks are recorded under `artifacts/teach-desktop/`. The release verification JSON records the source
  commit, test counts and artifact hashes after successful execution.

The native confirmation callbacks test the review path, not a live human approval exercise. No
model provider is used in the native smoke. A failed evidence-harness attempt tried to read an artifact
byte buffer as a file; its log is retained as `private-proof-harness-failure.log`, and the harness was fixed.

## Remaining requirements

V2-22 remains **in progress**. Opening applications as recorded steps, Save As, rapid focus changes, application
dialogs outside the selected root, per-step crops, drag gestures, recording indicators outside Admin,
model-assisted input/expectation suggestions, frame-scoped browser replay and live healer qualification
remain open. Shortcuts whose pre-key receiver cannot be verified are manual rather than guessed. The
current native proof covers one Windows provider and does not qualify arbitrary apps or input timing.

V2-26 still needs reviewed macros, remaining hand normalizers and the coarse/fine golden-task gate. The
full LLD acceptance audit remains incomplete. Real SIP/calendar, audio/outcome/billing qualification,
physical Android, clean-machine testing and signing remain open. This checkpoint ships locally as an
unsigned development installer and a source bundle. Red-team remains disabled and deferred.
