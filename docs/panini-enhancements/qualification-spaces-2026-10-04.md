# Spaces delivery increment — 2026-10-04

V2-19 is **In progress**. This is an integrated local developer release. The full LLD program and
production release qualification remain incomplete. Red-team remains disabled and deferred.

## Implemented and verified

- Migration 0016 adds spaces, explicit persona access, pages, revisions and FTS triggers. Migrations
  0017/0019 add canonical aliases only to recognized existing persona grants; expiry, surface restrictions
  and denials are preserved. Custom restricted grants are unchanged. 0018 adds durable page reviews.
- Page writes are serialized transactions with CAS, parent/cycle checks, rollback, 50-revision retention,
  soft deletion and restore for 30 days. Parent deletion refuses while active children remain.
- Native tools expose scoped listing/search/read and create/edit drafts. Unverified/external contexts are
  refused. Writes require a durable conversation and owner review; default destination uses the persona
  field and access is checked before use. Flag defaults off and is checked again on save.
- Reviews commit their durable record before parking. Replaying the same run/tool slot restores its
  interrupt, checks the exact draft, and returns the saved result without a second page write. Owner edits
  to approved content are supported. CAS, space access, write permissions and pause are checked again.
- Page conversations reserve one thread per page/persona inside a transaction and persist its AG-UI
  alias. Existing page discussions reject revoked or archived personas before history or model access;
  access is checked again at every model entry, including compaction. Page content is quarantined.
- Admin `/spaces` uses a lazy-loaded, pinned Tiptap editor with rich/Markdown modes, task lists, tables,
  an insertion menu, nesting, search, trash/restore, Markdown export and page conversations. Autosave
  retains failed and conflicting drafts. A recovered stale draft cannot overwrite a changed revision;
  cancelling conflict review does not rebase it. Unmounted saves cannot erase a newer local draft.
- Page review cards show the full review draft, and Admin lets the owner edit it before approval.
  AG-UI now releases streams when setup or terminal projection fails.

## Evidence

`npm test` passed (exit 0): **783 backend**, 7 eval, 16/16 offline cases, 125 CLI (1 skipped), 102 frontend,
12 phone, 95 search, 21 Admin and 12 shell tests. Pins, formatting, Ruff and TypeScript passed. Five existing
backend dependency/Windows cleanup warnings remain. Log: `artifacts/spaces/release-check.log`.
Final registry, provenance and real SSE review checks passed **18 tests** after extending notebook
metadata/write-result provenance. Log: `artifacts/spaces/final-targeted.log`.

Admin and shell builds passed. The notebook editor is loaded only on its route. Vite reports a 509 KB
editor chunk warning; the main Admin chunk is 444 KB. Logs: `artifacts/spaces/admin-build.log` and
`artifacts/spaces/shell-build.log`. Final changed-file formatting and Ruff checks passed after preview
and visual verification updates.

`scripts/panini-spaces-smoke.mjs` passed against the built Admin and the real notebook/AG-UI/interrupt
services on loopback with a throwaway database and scripted model. It covers owner-edited approval,
task/table conversion, autosave, failed draft reload, stale recovered drafts, cancelled/confirmed
conflicts, nested creation, search and trash/restore. Desktop/mobile screenshots were inspected.
The mobile check waits for the navigation resize animation and verifies editor bounds.
Log: `artifacts/spaces/browser-smoke.log`; screenshots: `artifacts/spaces/admin-{desktop,mobile}.png`.

The preview performs no live model/provider calls. These checks are neither a live citation evaluation
nor an Android hardware test.

## Remaining V2-19 work

Save-to-Space from results, receipts and validated history; PDF export; grid/outline and move/history/access
controls; reviewed move/delete tools and owner destroy; destination validation in persona editing;
lost-response recovery when an interrupt was already resolved; live page search/citation qualification.
Full V2-19 and full Dot equivalence are not claimed. Real SIP, physical Android and the other open LLDs
continue to require their own implementation and qualification cycles.
