# Program continuation qualification — 2026-10-04

The full 28-LLD program is not complete. Red-team qualification/enforcement remains disabled and
 deferred by owner. Existing Done labels are implementation labels; several source acceptance lists
 still lack recorded verification. No production promotion is claimed.

## Implemented in this increment

- V2-15: passkey enrollment/removal, exact-decision WebAuthn verification through the encrypted fabric,
  stable origin setup in Admin, single-use challenges, required user verification, counter validation,
  revoke cleanup and refusal of PC-only decisions. Public credentials only; no biometric data stored.
- V2-19 phone integration: notebook creation/editing, local draft recovery, revision conflicts with
  explicit owner review, trash/restore, saved task/call receipts and Markdown/PDF downloads. Newer local
  drafts survive late saves; successful conflict saves remove the outdated comparison action.
- V2-18: team members are scoped to their own role. A real gpt-4.1 Scout-to-Quill notebook scenario passed
  with separate grants, source citations, durable attribution and metered child work. Prior attempts are
  retained: a probe setup error and an earlier fact-correct run with overlapping roles.
- Production promotion now runs a complete source LLD acceptance audit for the exact clean source
  commit, in addition to existing installer hashes/signatures and clean-VM functional evidence. Missing
  LLDs, unchecked active criteria and unfinished implementation statuses fail the gate. Red-team suite
  requirements are explicitly deferred; the audit does not certify production on its own.

## Evidence

The final full repository suite passed with **819 backend** tests, 7 eval, 16/16 offline cases,
125 CLI (one skipped), 102 frontend, 12 phone, 95 search, 21 Admin and 12 shell tests. Pins,
Prettier, Ruff and TypeScript passed. Five existing warnings remain. Focused fabric, persona/recovery
and program-gate suites passed **80 tests**. Admin build and dependency consistency checks passed.

Final mobile browser smoke covers real fabric pairing and signatures, real WebAuthn using a virtual
Chromium authenticator, notebook drafts and concurrent revision conflicts, actual export downloads,
saved call receipts, task stop, live view, share target, offline recovery and revocation. Screenshots
were inspected. These are browser tests, not physical Android or hardware biometric qualification.

Evidence: `artifacts/phone/passkeys-release-check.log`, `passkeys-targeted.log`, `passkeys-smoke.log`,
`android-passkeys.png`, `android-notebook.png`, and `artifacts/personas/team-live.json`.
`python scripts/panini-program-qualify.py --gate` records the incomplete acceptance audit and exits 1.

## Confirmed release blockers and outstanding implementation

- `adb devices -l` returned no connected Android device. Physical installation, lock-screen push,
  hardware biometric/share/lifecycle tests and Android-hands golden tasks cannot be certified.
- No usable current-user code-signing certificate, configured signing file or approved publisher
  thumbprint was found. A production installer cannot pass the publisher/timestamp gate.
- No Git remote, production deployment destination or clean-VM qualification record is configured.
  Previous checks also found no real SIP project/number/public HTTPS endpoint. These are setup facts,
  not simulated passing results.
- The retained multi-day evaluation has two passing samples (3/3 each) and a failed third sample caused
  by exhausted API credits. It is not a completed passing multi-day qualification. One later notebook
  or team probe does not replace it.
- V2-17 and V2-20–27 remain unimplemented: channels, outbound calling, Android hands, demonstration
  skills, MCP serving, continuity, triggers/responsibilities, universal hands and offline mode.
- V2-15 still needs full cross-surface conversations and photo/file/voice input. V2-16 still needs real
  SIP and remaining lifecycle/cost requirements; V2-18 needs multi-speaker and broader evaluation;
  V2-19 retains channel/demo integrations and further recovery/release qualification.

The committed source/built-surface bundle is a local development increment. An unsigned installer,
if built, remains a development candidate. Neither is full LLD delivery or production qualification.

## Frozen runtime correction

The first installer attempt failed before distribution: the frozen backend lacked the SQL and Python
migration files needed to initialize its database. Packaging now includes every numbered migration and
the built-in identity, ships the current phone assets and all capability manifests, and resolves the
phone application relative to the configured installed runtime. The frozen import check uses isolated
test data and checks migrated tables, identity, phone assets and the browser/sandbox/live inventory.
A regression test verifies the spec includes every migration, including dynamically loaded Python files.
The targeted package, fabric transport and migration checks passed (11 tests).
