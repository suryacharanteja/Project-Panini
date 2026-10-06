# Spaces delivery increment — 2026-10-04

The pending PDF, saved-result/receipt and library-control work is implemented and integrated.
V2-19 remains **In progress** for the cross-LLD integrations and qualification listed below.
This is a local development release. Full LLD delivery and production qualification are incomplete.
Red-team remains disabled and deferred; pairing and action permissions remain active.

## Delivered

- PDF and Markdown export. The PDF renderer uses a fresh, credential-free Edge/Chrome context,
  blocks network requests, disables JavaScript and escapes raw document HTML. It bounds input,
  output, concurrency and runtime, closes the browser and terminates its owned process tree on timeout.
  The endpoint rechecks the feature flag and page revision before returning the document.
- Save to Space from finalized receipts (including call receipts), verified completed task/run results
  and completed conversation history. Source content and outcome come from durable server records.
  Failed, unfinished, malformed, oversized or truncated result/history sources are rejected. Receipt
  exports retain their actual outcome and recorded result preview. Source links are retained.
  Durable source mappings make retries return the same page without overwriting owner edits;
  each subsequent completed conversation run can become a new snapshot.
- Library list/grid, nesting and moves with revision checks, heading outline navigation, revision
  restoration as a new revision, trash/30-day restore and explicit persona access grant/revoke.
  The editor retains conflicting or failed local drafts. Restoration preserves a draft created while
  its request was in flight. Deep links open the selected space and page.
- Owner-only permanent deletion requires the exact title, a fresh revision and a durable local
  confirmation. It removes document content, retained revisions and source mappings atomically with
  a destroy receipt. It refuses pages with children. Earlier conversation quotes, audit operation
  records and receipts remain; it is not a global erasure operation.
- Reviewed agent create/edit/move/soft-delete, exact-match text patches, editable rendered Markdown
  previews, bounded unified diffs and previous-content previews. Agent writes require owner review.
  Direct owner Save to Space uses a separate console action with validated stored sources.
- Scoped FTS plus a local hashed-vector typo fallback over the latest 2,000 accessible active pages
  when FTS finds nothing. Exact FTS search still covers the entire accessible library.
- Validated persona default destinations; revocation clears the revoked default. Existing restricted,
  expired or denied tool grants are preserved by migrations. Access is checked before model entry,
  replay and writes. Page content and tool results retain untrusted-source provenance.
- Durable recovery when a resolved page review loses its response while its run is still parked:
  replay checks its proof and returns the saved result without another write.
- Strict provider schemas for Spaces tools: optional fields become nullable in provider declarations
  and are normalized before service validation. Unknown fields remain rejected. Empty capability
  tool enums are omitted. Durable run results remain valid JSON instead of being sliced mid-document;
  oversized results retain an explicit truncation marker and cannot be saved as complete notebook results.

Migrations 0020–0023 add saved-source mappings, recognized move/delete grant aliases, durable owner
 deletion and local page vectors. Applied migrations were not rewritten.

## Evidence

`npm test` exited 0: **798 backend**, 7 eval, 16/16 offline cases, 125 CLI (1 skipped), 102 frontend,
12 phone, 95 search, 21 Admin and 12 shell tests. Dependency pins, Prettier, Ruff and TypeScript passed.
Five existing dependency/Windows cleanup warnings remain. `artifacts/spaces/release-check.log`.
Focused export/review/ledger/API tests: `artifacts/spaces/final-targeted.log`.

Admin and shell builds passed. Admin's main chunk is 448 KB, notebook 475 KB and rendered preview
is lazy-loaded; there is no chunk-size warning. Build logs are in `artifacts/spaces/`.

Final browser smoke ran against the built Admin and real Spaces/AG-UI/interrupt services using a
throwaway database and scripted model. It passed owner-edited review, rendered preview, Save
conversation, task/table conversion, failed draft recovery, cancelled and confirmed conflicts, nesting,
moves, search, trash/restore, owner permanent deletion, list/grid, outline, history restoration,
real PDF HTTP export, desktop/mobile bounds and no JavaScript runtime errors.
`artifacts/spaces/browser-smoke.log`; `admin-desktop.png`, `admin-mobile.png` and `export.pdf`.
The PDF was rendered with Poppler, inspected visually and checked for extracted text with pypdf.
It contains one clean page with title/revision, heading, task and table.

A separate **live gpt-4.1** run offered the real tool catalog, called `pages_search` and correctly
cited the synthetic notebook's page ID, revision, launch date and destination.
`artifacts/spaces/live-citation.json`. This is one notebook citation check, not a Dot comparison or
broad provider qualification. Browser mobile emulation is not physical Android testing.

## Remaining work and release limits

- Dedicated Android PWA notebook/library, saved task/call receipts and export downloads are delivered
  in the following phone increment. Approved Teach skill page storage now uses a reviewed-version
  source mapping with idempotent retries; focused backend, authenticated owner API and built-Admin
  fixture UI tests cover it.
  A real-service browser qualification remains open. Channel-thread export still depends on its
  LLD integration. Physical Android qualification remains open.
- Additional crash-window qualification, including interruption after a parked run is marked resumed
  but before resumed execution, and multi-provider/repeated live evaluation remain open.
- Production deployment, installer signing, installed-runtime qualification and physical Android
  testing remain open. PDF export requires installed Edge or Chrome.
- V2-16/V2-18 real SIP calls, audio playback and live-provider qualification retain their existing
  open status. No full-program completion, production shipping or Dot equivalence is certified.

The local bundle contains a committed source patch, built surfaces, evidence and documentation;
it has no credentials or database and is not a standalone installer. Enable `PANINI_SPACES`,
`PANINI_V2_AGUI` and `PANINI_V2_INTERRUPTS` to use notebook conversations and reviews. Turning
Spaces off hides the surface/tools and preserves stored data.
