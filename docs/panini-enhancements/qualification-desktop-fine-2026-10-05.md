# Fine Windows checkpoint — 2026-10-05

## Delivered

V2-26 adds a bounded hierarchical Windows UIA snapshot, opaque observation refs and an opt-in registry
hand. `PANINI_DESKTOP_TOOLS=coarse` remains the default. Fine tools require the action ledger and a private
foreground owner or a permitted persona context. External/channel clients cannot inherit PC access.

The worker traverses ControlView under the selected HWND/process/creation identity, with depth 30,
600 output nodes and 2,400 visited nodes as upper bounds. It prunes invisible controls and empty generic
containers, preserves hierarchy, maps roles through the common renderer and reports truncation separately.
Password controls are masked, their values omitted and their descendants excluded. The native worker is
still disposable, bounding hung UIA providers through the existing broker timeout.

The HMAC broker retains UIA runtime/automation IDs. Public `w` refs carry opaque identity and stable anchor
hashes. New snapshots invalidate old refs for that run; action tokens are consumed once, bound to target
identity and run, expire after 30 seconds and are retracted by Stop. Model-supplied native IDs never select
a target. The worker checks foreground, window title, control identity, type, bounds, current value and
state again before dispatch. Key chords additionally require the same focused non-credential control.

The registry hand delivers `desktop_windows`, `desktop_snapshot`, `desktop_click`, `desktop_type`,
`desktop_select`, `desktop_toggle`, `desktop_scroll`, `desktop_press` and text/value/element assertions.
Every effect refreshes the target before and after review. Reviews use the actual app, role, label and
bounds; current policy is enforced even in general shadow/off mode. The foreground agent, AG-UI and
persona children, inbound phone calls, background tasks and channel jobs preserve stronger approval levels
through a decision-aware callback into durable reviews.
The approval level is included in the action digest. Legacy two-argument callers refuse stronger reviews
instead of reducing them to tap. Nested legacy prompts reuse only the current approved tool scope. AG-UI
stores the exact waiting interrupt with its parked attempt, allowing a PC decision to resume after a
stream-runner restart with the same nonce and action digest. Android and the generic policy hook use the
same approval rule. Pause, tighter permissions, flag disable and
native uncertainty stop the operation rather than retrying it. Each action invalidates published refs.

Fine pointer actions use bounds and hierarchy for UIA hit-testing; they bypass `FromPoint` and use paced
native pointer input. Chords reuse the existing bounded native input implementation with key-up pairs in
the same batch. Document typing supports bounded plain Unicode input, while ValuePattern fields use SetValue. Document
line breaks/tabs require separately grounded key steps, preventing typing from moving into another field.
Terminal entry receives an execution classification and Settings toggles an account classification.
Assertions return explicit pass/fail evidence. A value assertion cannot match a replacement control solely
because its accessible name is unchanged.

When a review is needed, an available window capture is cropped to the target and linked as an artifact;
captures are omitted if the current snapshot contains a credential control. The existing copper pointer
remains in use during actions. Further preview presentation and overlay qualification remain open.

## Actual verification

- `scripts/panini-desktop-fine-smoke.py`: real broker, disposable worker and WPF fixture. Tests hierarchy,
  limits, password suppression/refusal, opaque-ref Unicode input, native stale/cross-run/cross-target
  rejection, Stop, and registry click/type/assert/toggle/select/scroll with concrete reviews and receipts.
- The same script with `--win32`: genuine Win32 controls in the native fixture, validating a separate UIA
  provider. Win32 static labels and editable controls share names; targets must use the correct role.
- `scripts/panini-desktop-notepad-smoke.py`: installed Notepad exposing Win32/XAML providers. Opens only a
  new disposable document, focuses its editor, types Unicode via fine registry tools, asserts the text,
  saves, verifies the exact text on disk and closes its tab. No model provider is used. The automatic
  fixture confirmation callback validates the review workflow; it is not a live human approval exercise.
- Focused backend tests exercise shadow-mode enforcement, exact review binding, decline, changed
  label/value/title/focus, stronger local reviews, pause/disable, credential focus, principal isolation,
  stale refs, replacement assertions, uncertain dispatch and cancellation with native Stop.
- Real foreground coordinator and AG-UI round trips exercise verify/local approvals, reject insufficient
  phone/AG-UI tap decisions, and verify single dispatch after PC approval. AG-UI resumes a PC-approved
  command after runner replacement and rejects a forged nonce and repeated completion. Persona delegation,
  background workers, channels and inbound phone requests preserve stronger reviews. Background/channel
  continuation checks earlier effects are not repeated. These use scripted models, provider fixtures and
  disposable data, with no live provider claims.

Evidence lives under `artifacts/desktop-fine/`, including native framework IDs, snapshots, logs, exact
source qualification and packaged release verification. Repository gates and package hashes are added
after those commands succeed; no unrun command is recorded as passing.

## Remaining LLD work

Dedicated reviewed open-application/draw macros, combined type-and-submit, broader combo/scroll fallback,
target overlay/preview presentation, desktop teaching hooks and normalization, sandbox/browser grammar
normalizers and the full golden-task comparison are pending. The default can change only after success is
at least coarse, model calls decrease and p50 latency is no more than coarse plus 10 percent. The single
Notepad result does not establish that gate.

V2-22, V2-26 and the full LLD program remain **in progress**. Live provider, physical Android, SIP/audio,
signed production release and clean-machine qualification remain open. Red-team is disabled and deferred.
