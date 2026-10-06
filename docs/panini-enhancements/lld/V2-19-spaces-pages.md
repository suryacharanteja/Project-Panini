# V2-19: Spaces and pages (PANINI's notebook)

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 4 · P1 · M (5 days) |
| Depends on | V2-05 (reviews), V2-07 (shared UI and Admin conversations); V2-18 for persona access |
| Flag | `PANINI_SPACES` (default `false`) |
| Status | In progress |
| Decision | Register D9 (UI in Admin) |

## 1. Outcome

PANINI gets a notebook: Spaces of pages you and PANINI write together. Research, call receipts, meeting
notes and drafts become editable pages with revisions; PANINI shows a draft and saves only when you approve;
each page has its own conversation; and PANINI can search and cite your pages the way it uses memory.

## 2. Scope and non-goals

- In scope: Spaces and pages store with revisions and nested subpages, optimistic concurrency, soft delete,
  full-text search, agent page tools, review-before-save through interrupts (idempotent), page conversations,
  the Admin library and editor (port of OpenDots' Tiptap editor), "Save to Space" from results and receipts,
  Markdown and PDF export.
- Not in scope: multi-user collaboration, embeds, file attachments inside pages beyond images (later).

## 3. Current state

No document workspace. Results live in tasks, notices and the agent inbox; projects carry text context.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/server/pages.ts` | Tables `pages(id, spaceId, parentId, title, content, revision, createdAt, updatedAt, sourceThreadId)`, `page_threads(pageId, dotId, threadId UNIQUE, ready, leaseUntil)`, `page_reviews(threadId, toolCallId → pageId, spaceId)`; revision checks on update | SQLite tables (§5.3) with `persona_id` instead of `dotId` and `interrupt_id` instead of `toolCallId` |
| OpenDots `src/server/page-tools.ts` | Tools `list_authorized_spaces`, `list_space_pages`, `read_space_page`, `create_space_page`, `edit_space_page`; scoped to the Dot's Spaces; re-read current content before edits (CAS) | Agent tools (§5.4) |
| OpenDots `src/shared/page-review.ts` + `PageReviewCard.tsx` + `page-review-decision.ts` | `review_space_page` human-in-the-loop with idempotent save keyed by the tool call | `request_review` (V2-05) with the page draft; the save is keyed by the interrupt id |
| OpenDots `src/client/editor/` (`RichEditor.tsx`, `autosave.ts`, `use-page-autosave.ts`, `markdown.ts`, `slash-commands.ts`, `DocumentMenu.tsx`), `PageDocument.tsx`, `PageOutline.tsx`, `SpaceLibrary.tsx`, `SpaceNav.tsx`, `SpaceWorkspace.tsx`, `editor.css` | Tiptap 3 editor (MIT) with slash commands, tables, task lists, Markdown source mode; autosave with progress, failed saves keep the draft, revision checks; searchable library with grid/list and nested subpages | Ported to Admin (React 19, Tailwind 4), restyled with PANINI tokens |
| OpenDots `src/server/page-service.ts` + tests | One thread per page and persona, reserved before use and recovered after restarts; history export rejects failed or oversized history | Page conversations through `thread_bindings(surface="page", external_id="<page>:<persona>")` |
| OpenDots `client/page-context.ts` and the agent prompt ("Current page: untrusted document content; re-read before edits") | Page context handling | Page text enters the run as `<untrusted source="page">` when edited by others (single owner: pages are trusted for the owner but content pasted from the web keeps its taint label in metadata) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/spaces/store.py` | Spaces, pages, revisions, CAS, soft delete, FTS |
| `backend/spaces/tools.py` | Agent tools |
| `backend/spaces/export.py` | Stored-source saving; PDF through a fresh isolated Playwright print context |
| `backend/routes/spaces.py` | Console API |
| `backend/harness/migrations/0016_spaces.sql` | Tables (0014 is already used by calls) |
| Admin `src/pages/spaces/` | `SpaceLibrary`, `SpaceWorkspace`, `PageDocument`, `PageOutline`, editor folder (ported) |
| `panini-core/apps/shared-ui/cards/` | "Save to Space" action on result and receipt cards |

### 5.2 Rules

- Pages are Markdown (`content` ≤ 200,000 characters; the review draft limit stays 20,000 as in OpenDots).
- Every update supplies `expected_revision`; a mismatch returns `409 PAGE_CHANGED` with the current revision
  (the editor keeps the local draft and offers merge or overwrite).
- Delete is soft (`deleted_at`), restorable for 30 days; hard delete is a `destroy` effect.
- Personas see only Spaces in their access list (V2-18); the default destination is the persona's
  `default_space_id`.

### 5.3 Storage

```sql
CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY, space_id TEXT NOT NULL, parent_id TEXT, title TEXT NOT NULL,
  content TEXT NOT NULL, revision INTEGER NOT NULL, source_thread_id TEXT, created REAL NOT NULL, updated REAL NOT NULL,
  deleted_at REAL);
CREATE TABLE IF NOT EXISTS page_revisions(page_id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
  content TEXT NOT NULL, author TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(page_id, revision));
CREATE TABLE IF NOT EXISTS page_reviews(interrupt_id TEXT PRIMARY KEY, page_id TEXT NOT NULL, space_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS space_access(persona_id TEXT NOT NULL, space_id TEXT NOT NULL, PRIMARY KEY(persona_id, space_id));
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(title, content, content='pages', content_rowid='rowid');
```

Revisions keep the last 50 per page.

### 5.4 Agent tools

| Tool | Effect | Notes |
| --- | --- | --- |
| `spaces_list()` | observe | Spaces this persona may use |
| `pages_list(space_id, query?)` | observe | Titles, ids, updated |
| `pages_read(page_id)` | observe | Current content and revision |
| `pages_search(query)` | observe | FTS5 over accessible pages (plus hashed embeddings like memory) |
| `pages_create(space_id?, parent_id?, title, content, review)` | local_write | `review=true` (or the person asked to see it, or the run is tainted) → `request_review` with the draft; saving after approval is idempotent per interrupt id (`page_reviews`) |
| `pages_edit(page_id, expected_revision, title?, content | patch)` | local_write | Review shows a diff when `review=true` or tainted |
| `pages_move(page_id, parent_id)` | local_write | |
| `pages_delete(page_id)` | local_write (soft) | Hard delete is owner-only in Admin |

### 5.5 Review-before-save

```text
pages_create(review=true) → request_review {title, draft, space} (preview: rendered Markdown)
person approves (optionally edits the draft) → interrupt resolved → tool saves (idempotent by interrupt id)
→ result {page_id, url: "/admin/spaces/<space>/pages/<page>"} → the agent links it
restore-before-decide: the card first checks page_reviews for this interrupt (a previous save may have
committed even if its response never arrived)
```

### 5.6 Page conversations

Each page has a "Conversation" panel. `thread_bindings(surface="page", external_id="<page>:<persona>")`
gives one thread per page and persona (OpenDots `page_threads`). The run receives the page's current
content and revision as context; edits must re-read (`pages_read`) before `pages_edit` (CAS).

### 5.7 Editor

Ported from OpenDots with these changes: PANINI tokens and typography; Admin routes
(`/spaces`, `/spaces/:space`, `/spaces/:space/pages/:page`); autosave every 1.5 s of inactivity with visible
progress; offline/failed saves keep the draft in `localStorage` and retry; Markdown source toggle; slash
commands (headings, lists, tasks, table, quote, code, divider, "Ask PANINI to continue", "Summarize
selection").

### 5.8 Integrations

- Result and receipt cards: **Save to Space** uses the owner console's validated stored-source endpoint,
  with an explicit destination and source links. Agent-created pages always require review.
- Call receipts (V2-16/20) and channel threads can be saved as pages.
- Teach-by-demo skills (V2-22) store their human-readable description as a page.

## 6. Implementation steps

1. Store, revisions, FTS, migration; API routes with CAS.
2. Agent tools and review-before-save via interrupts (idempotent).
3. Admin library and editor port; autosave and conflict handling.
4. Page conversations (bindings) and the editor's conversation panel.
5. Save to Space actions; export (Markdown, PDF).

## 7. Tests

Ported from OpenDots and extended:

| Test | File |
| --- | --- |
| One thread per page and persona under concurrent requests | `backend/tests/spaces/test_page_threads.py` |
| Export rejects failed or oversized history without creating a page | `backend/tests/spaces/test_export.py` |
| Tools scoped to the persona's Spaces; re-read with CAS; pause enforcement | `backend/tests/spaces/test_tools.py` |
| Same reserved thread recovered after restart and after a remote-success retry | `backend/tests/spaces/test_recovery.py` |
| Persona without access is refused before any model call | `backend/tests/spaces/test_access.py` |
| Multiple Spaces granted without changing thread identity; revocation applies to existing tools | `backend/tests/spaces/test_access.py` |
| Review save idempotent (approve twice, restore after lost response) | `backend/tests/spaces/test_review.py` |
| Editor autosave: failed save keeps draft; conflict path | `panini-core/apps/admin/test/autosave.test.tsx` |

## 8. Acceptance criteria

- [x] Create, edit with conflicts, nest, search, soft delete and restore work in Admin.
- [x] "Show me the draft before saving" produces a review card; approve saves once; decline saves nothing.
- [x] PANINI cites a page found by `pages_search` in an answer (one live gpt-4.1 synthetic case).

## 9. Rollout and rollback

Flag on after step 3. Rollback: flag off hides Spaces and tools; data stays.

## 10. Open questions

- Sync pages to a folder of Markdown files (for Obsidian/VS Code users)? Recommendation: optional one-way export
  to `Documents\PANINI Spaces` in a later item.

## 11. Delivery evidence — 2026-10-04

Implemented: atomic CAS and 50 revisions, nested-parent validation, 30-day restore, filtered FTS,
persona space access, canonical read tools and approved create/edit tools, durable review replay,
owner-edited approvals, stable page/persona threads and AG-UI page conversations. Access is checked
before every model entry. Page context and search/read results carry quarantined provenance.
Admin has a lazy-loaded Tiptap 3.31.4 editor, Markdown mode, tables/tasks, insertion menu, autosave,
local draft recovery, search, nested creation, trash/restore, page discussions and Markdown export.

The next integrated increment delivers validated result/receipt/history saving and rejection tests,
isolated PDF export, grid/outline, moves, history restoration, access controls, reviewed move/delete,
owner destroy, validated default destinations and resolved-decision recovery while a run is parked.
The editor modules are separated into PageDocument, SpaceLibrary, PageOutline and shared types.

`npm test`: **798 backend** tests passed; all other repository gates passed. Final browser smoke
uses the built Admin, throwaway database and real AG-UI/interrupt code with a scripted model; it
also verifies moves, history restoration, owner destroy and real PDF export. A separate live
gpt-4.1 run searched and cited a synthetic page correctly. The PDF and responsive screenshots were
visually inspected. Evidence and limits: [qualification](../qualification-spaces-2026-10-04.md).

Phone notebook actions, conflict recovery, stored-source saving and export downloads are now verified
through the real fabric browser preview. Approved Teach skills can now be saved from Admin as a
versioned, validated page source; retries preserve owner edits and pending or integrity-failed
versions are refused. Focused backend tests, an authenticated owner API test and a built-Admin fixture
API smoke cover this path. Still open: channel integrations, a real-service Teach-to-Spaces browser qualification, additional
crash-window and live-provider qualification, installed-runtime and production release gates.
This is a local development increment; V2-19 and the full LLD program remain in progress.


## Channel integration — 2026-10-06

Long verified channel results (Telegram, Slack, WhatsApp, email) become pages in the owner's default Space, with
a short reply naming the page (`backend/channels/manager.py`, `test_a_long_channel_result_is_saved_as_a_space_page_with_a_short_reply`).
