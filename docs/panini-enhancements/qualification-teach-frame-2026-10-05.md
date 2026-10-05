# V2-22 named iframe development checkpoint — 2026-10-05

The pinned Playwright Python recorder emits `page.locator(...).content_frame` for iframe actions.
PANINI now parses this syntax into reviewed targets and replays steps only when the recorded selector
names a single iframe. The replay requires exactly one iframe in both the current DOM and the fresh
snapshot, binds its ref to descendants, and checks the selector and target again immediately before
each effect. Other selectors and ambiguous frames remain blocked.

`node scripts/panini-teach-smoke.mjs` passed against a disposable, real managed Edge browser and the
actual recorder, registry, policy, ledger, review and assertion paths. It recorded a query and Save
inside a named iframe, approved the skill and its effect, replayed with a different query, and observed
the result inside that iframe. Its normalized recording and receipts are in `artifacts/teach-browser/`.
The focused teach suite passed 92 tests, including missing/ambiguous iframe refusal and DOM-count
checks. The full repository gate also passed.

This does not qualify multiple or nested frames, cross-origin identity changes, Chrome attachment,
or a live healer provider. The browser smoke uses a deterministic healer for its separate changed-label
case. V2-22 and the full LLD program remain in progress. Red-team stays disabled and deferred.

## Multi-frame increment

Managed-browser replay now allows multiple top-level named iframes when the live DOM count matches
the fresh Playwright snapshot, the reviewed selector matches one DOM iframe, and its label is unique.
The binding is rechecked before each effect. The real recorder/replay smoke adds a decoy iframe with
the same controls before the reviewed Report iframe; replay with a changed input updates only Report.
Focused tests reject DOM/snapshot label conflicts, duplicate labels and ambiguous matches. A second
managed-browser recording/replay used an iframe served from a different loopback port. It reached the
actual owner effect review, replayed with a changed input, and left the same-controls decoy untouched.
The recording, replay result and receipts are under `artifacts/teach-browser/`. This qualifies one
cross-origin loopback layout, not arbitrary external origins, nested/hidden frames, Chrome attachment,
or a live healer provider.
