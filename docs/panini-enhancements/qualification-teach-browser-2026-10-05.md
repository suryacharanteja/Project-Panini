# Teach-by-demonstration browser checkpoint — 2026-10-05

## Delivered implementation

V2-22 now provides owner browser recording, parameter candidates, reviewed versioned bundles,
deterministic registry replay, assertions, bounded target repair proposals and skill metrics. The flag
`PANINI_TEACH` defaults off. The action ledger and durable reviews are mandatory; replay enforces current
permissions even when general policy is in shadow mode. Saving a skill grants no permission.

The pinned Playwright recorder produces Python statements through a PANINI event-sink guard. A bounded
AST parser normalizes literal actions without evaluating or executing recorder source. Navigation, click,
double click, fill, press, option values, check/uncheck, hover and file selection are normalized. Unknown
syntax becomes a generic manual step; its raw source is discarded. Frame locators retain their scope for
review. A later checkpoint supports the pinned recorder's `content_frame` syntax for a single named iframe;
the frame selector is checked against the live DOM and snapshot before replay and before each effect.
Raw selectors without accessible names and
label/index-only option selection also require correction.

Sensitive controls block native typing while recording; the person stops before entering credentials.
The recorder sink replaces sensitive/secret-pattern actions with manual steps. Screenshots and snapshots
are omitted on pages with sensitive controls or recognized secret values. Start/end snapshots and bounded
per-action screenshots become indexed artifacts and are copied into approved bundles. No credential
workflow is advertised as replayable.

Bundles contain `SKILL.md`, `replay.json`, recording evidence and `CHANGELOG.md`. SQLite stores the exact
reviewed digest, approval and version history; disk edits cannot change executable replay. Competing
proposals cannot replace a different approved base. Plain skill edits refuse demonstration bundles;
deletion retires replay versions and cancels an active replay. The Markdown entry point retains a human
procedure when Teach is switched off.

Replay refreshes the page for each target, requires a unique role/name or bounded literal prefix, checks
the reviewed target again after approval, and runs each action/assertion through the registry. Nested
steps have separate receipts and share occurrence counts so intentionally repeated steps remain distinct.
An uncertain effect stops without retry. Missing targets can request at most two repairs from a fresh,
tool-free and memory-free model. Only a run with passing assertions proposes a new version; approval is
still required. Action permissions apply to the repaired target independently.

Admin Skills provides Watch me, Stop, draft review, input defaults, expected text, replay, metrics and
deletion. Inbox shows durable version/effect reviews, every normalized step and available screenshots.
The avatar shows recording and has a direct Stop control. Phone Inbox keeps the entire bounded version
preview; PWA cache version is v12. Disabling a prerequisite through Settings stops recording/replay.

## Verification and limits

The real-browser smoke uses an isolated Edge profile, the actual pinned MCP recorder and real registry,
ledger, policy, review and assertion code. It records a local report page, reviews the resulting skill,
replays with oranges instead of apples, changes the label to Send report, obtains the separate communicate
approval, verifies the result, and opens a pending version patch. The shared Inbox UI approves that patch.
Native password keystrokes are blocked and no corresponding screenshots/snapshots are imported.

The healer model in that smoke is deterministic test data, with no live provider request. This proves the
repair/review workflow, not live model quality. Browser layout checks at desktop and 412 px width are not
physical Android qualification. Focused tests cover parser limits, secret dropping, repeated-step receipts,
pause, decline, uncertain effects, repair limits, failed assertions, version replacement, retirement,
external-client refusal, startup cancellation and stronger approval levels.

Evidence is retained in `artifacts/teach-browser/`: full release gate, focused tests, real-browser smoke,
built UI checks, normalized recording, real-browser receipts and screenshots. Installer/source archive
hashes and exact-commit verification are recorded there after packaging.

## Still pending

- Desktop recorder hooks, UIA bounds hit-testing, signed broker recording operations, desktop normalization
  and a real Notepad recording/replay with new text.
- Broader frame layouts and origin/frame identity qualification; broader recorded fixtures,
  including popup/file chooser flows and label/index option selection.
- Live healer-provider qualification, optional model-assisted input naming and screenshot-assisted repair.
- Physical Android qualification, real SIP/audio/IVR/takeover/billing and remaining LLD acceptance evidence.
- Signed production release and clean-machine qualification. Red-team is disabled and deferred.

V2-22 and the full LLD program remain **in progress**. This is a local development checkpoint.
