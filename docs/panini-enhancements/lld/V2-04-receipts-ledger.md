# V2-04: Receipts and artifacts ledger

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 1 · P0 · M (4–5 days) |
| Depends on | V2-02 (call pipeline), V2-00 (migrations) |
| Flag | `PANINI_V2_LEDGER` (default `false`) |
| Status | Not started |

## 1. Outcome

Every tool call PANINI makes, from any surface, leaves an append-only, hash-chained record: what was asked,
what policy decided, whether an effect was dispatched, what happened, and the evidence and artifacts that
prove it. After a crash, PANINI never repeats an effect it already dispatched, and anything with an unknown
outcome goes to the person for review. "What did PANINI do yesterday at 3 pm?" has an exact answer.

## 2. Scope and non-goals

- In scope: ledger entries and chain, receipts view, artifacts store with retention, redaction, idempotent
  replay keys, recovery of pending receipts, evidence API compatibility, PANINI-CLI receipt bridge, daily
  roots, activity API and Admin page, an `activity_search` tool.
- Not in scope: run checkpointing (V2-06 uses this ledger for it), trace/video capture (V2-10 produces those
  artifacts), external anchoring (open question).

## 3. Current state

- `TaskContext.record_evidence` (`backend/panini/coordinator.py:198`) keeps up to 100 records / 1 MiB in
  `session.evidence` in memory; the coordinator checks that `outcome.evidence_ids` belong to the task and
  capability before accepting `verified_complete` (`coordinator.py:817-826`).
- The harness keeps hash-only `connector_receipts` (`harness/store.py:33`, written in `agent.py:_connector`).
- PANINI-CLI has hash-chained durable metadata receipts in progress (register CLI-02).
- `computer.activity` frames are kept in `task.trace` (48 items) and published as events.
- Nothing survives a restart; background tasks record free-text `events`.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/server/computer-store.ts` + `computer-service.ts:audited()` | Begin/finish audit around every action; `actor` (owner/agent); outcome pending → succeeded/failed; latest 1,000 per Dot; **audit deliberately excludes typed values, file contents and full commands** | Begin/dispatch/end entries; actor = principal; redaction rules (§5.5); retention by age and size instead of a count |
| OpenDots `src/server/pages.ts` `page_reviews(threadId, toolCallId → pageId)` and `page-review-decision.ts` | "A previous save may have committed even if its response never arrived": look up by key before acting | Operation keys: before dispatching, look up a completed receipt with the same key and return its result |
| OpenMuse idempotent tool replay via operation hashes (`PANINI-AGENTIC-DESIGN.md` §5) | Replay safety after crashes | `operation_key = sha256(run_id, tool, args_digest, occurrence)` |
| PANINI-CLI receipts (CLI-02) | Hash chain format already in use | Same canonical-JSON + SHA-256 convention; CLI receipts are bridged, not duplicated |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/ledger/__init__.py` | `Ledger` facade |
| `backend/ledger/chain.py` | Canonical JSON, entry hashing, append with `BEGIN IMMEDIATE`, chain verification |
| `backend/ledger/receipts.py` | Receipt lifecycle (begin → dispatch → end), evidence, operation keys, recovery |
| `backend/ledger/redact.py` | Redaction of args and results |
| `backend/ledger/artifacts.py` | Artifact files, index, retention |
| `backend/ledger/views.py` | Queries for API, Admin and the `activity_search` tool |
| `backend/harness/migrations/0005_ledger.sql` | Tables |
| `backend/registry/registry.py` | Pipeline hooks call the ledger (V2-02 step 6) |
| `backend/panini/coordinator.py` | `record_evidence` and evidence validation use the ledger when the flag is on |
| `backend/capabilities/code.py` | Forwards PANINI-CLI receipt events |

### 5.2 Storage

Append-only entries carry the chain; `receipts` is a materialized view for queries.

```sql
CREATE TABLE IF NOT EXISTS ledger_entries(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  receipt_id TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- begin | decision | dispatch | evidence | end | recovered
  body TEXT NOT NULL,                 -- canonical JSON
  ts REAL NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS ledger_entries_receipt ON ledger_entries(receipt_id);

CREATE TABLE IF NOT EXISTS receipts(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, thread_id TEXT, persona_id TEXT, principal TEXT NOT NULL,
  tool TEXT NOT NULL, capability TEXT NOT NULL, hand TEXT, target_label TEXT,
  operation_key TEXT NOT NULL, args_digest TEXT NOT NULL, args_view TEXT NOT NULL,
  effect TEXT NOT NULL, decision TEXT, interrupt_id TEXT,
  dispatched INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'pending',
  result_digest TEXT, result_view TEXT, evidence TEXT NOT NULL DEFAULT '[]', artifact_ids TEXT NOT NULL DEFAULT '[]',
  started_at REAL NOT NULL, finished_at REAL, cost TEXT);
CREATE INDEX IF NOT EXISTS receipts_run ON receipts(run_id, started_at);
CREATE INDEX IF NOT EXISTS receipts_opkey ON receipts(operation_key);
CREATE INDEX IF NOT EXISTS receipts_time ON receipts(started_at);

CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, mime TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
  path TEXT NOT NULL, run_id TEXT, receipt_id TEXT, created REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
  expires REAL);

CREATE TABLE IF NOT EXISTS ledger_roots(day TEXT PRIMARY KEY, last_seq INTEGER NOT NULL, root_hash TEXT NOT NULL);
```

`hash = sha256(prev_hash + "\n" + kind + "\n" + receipt_id + "\n" + body)` with `body` in canonical JSON
(sorted keys, no whitespace, UTF-8, the same `digest()` convention as `coordinator.py:35`). The first entry's
`prev_hash` is 64 zeros. Appends run inside `BEGIN IMMEDIATE` so concurrent writers serialize.

### 5.3 Receipt lifecycle

```text
begin(run, spec, args, decision)        → entry begin {tool, effect, args_digest, args_view, operation_key,
                                            principal}, entry decision {action, level, reasons, interrupt_id}
mark_dispatched(receipt)                 → entry dispatch {}   (called by hands via ctx.dispatch(), before the
                                            side effect leaves the process)
add_evidence(receipt, kind, summary, details) → entry evidence {evidence_id, kind, summary, details_digest}
end(receipt, result)                     → entry end {outcome, result_digest, result_view, artifact_ids, cost}
```

`ctx.dispatch()` already exists in v1 (`TaskContext.dispatch` counts dispatched effects); hands keep calling
it, and with the ledger on it also writes the `dispatch` entry for the current receipt.

### 5.4 Idempotent replay and recovery

```python
def operation_key(run_id, tool, args_digest, occurrence): ...
# occurrence = number of earlier receipts in this run with the same (tool, args_digest)
```

Before `hand.call`, the pipeline looks up `receipts` by `operation_key`:

| Found | Action |
| --- | --- |
| Nothing | Proceed |
| `outcome = ok` | Return the stored `result_view` as the result (marked `replayed: true`); no hand call |
| `outcome = pending`, `dispatched = 0` | The earlier attempt never dispatched: mark it `cancelled`, proceed |
| `outcome = pending`, `dispatched = 1`, or `unknown_effect` | Do not proceed; return `blocked` with "an earlier attempt may have taken effect"; open a review interrupt (V2-05) |
| `failed` / `blocked` / `cancelled` | Proceed (a new attempt) |

On startup, `Ledger.recover()` closes every `pending` receipt older than the process: `dispatched=0` →
`cancelled`; `dispatched=1` → `unknown_effect` and a review interrupt for the person. This is the ledger form
of the v1 durable-task rule "requeue only when no effect was dispatched" (`harness/tasks.py`).

### 5.5 Redaction

| Data | Stored in receipts |
| --- | --- |
| Typed text (`text`, `value`, `fields[].value`) | Length and SHA-256 only; `credential` effects store nothing |
| File contents (`contents`, uploads) | SHA-256, byte length, path |
| Commands | First 200 characters with secret patterns (`harness/memory.py:_SECRET`) masked, plus full-command SHA-256 |
| URLs | Kept; query strings masked when they match secret patterns |
| Results | `result_view` keeps the model-visible text truncated to 2,000 characters, with secret patterns masked |
| Infrastructure tokens | Never (the OpenDots rule: replace any reflected secret with `[redacted]`, `computer-service.ts:108`) |

Tools may add paths to `Decision.redactions` (V2-03). A unit test sweeps known secret shapes through every
path.

### 5.6 Artifacts

- Files at `%LOCALAPPDATA%\PANINI\artifacts\YYYY-MM\<id>.<ext>`; `id` is time-sortable; the row stores
  SHA-256 and size.
- Kinds: `screenshot`, `snapshot`, `trace`, `video`, `transcript`, `recording`, `page_export`, `call_audio`.
- Retention: default 90 days and 2 GB total, oldest unpinned first; artifacts referenced by a page (V2-19), a
  skill (V2-22) or an open interrupt are pinned. Settings: `PANINI_ARTIFACT_DAYS`, `PANINI_ARTIFACT_MAX_GB`.
- Served only to authenticated surfaces: `GET /api/harness/artifacts/{id}` (console session or shell token),
  `Content-Type` from the row, `Content-Disposition: inline`, never directory listing.

### 5.7 Evidence compatibility

- `ctx.record_evidence(kind, summary, details)` writes an `evidence` entry on the current receipt (or a
  run-level pseudo-receipt `tool="run"` when called outside a tool call) and returns the evidence id. The
  16 KB size limit and `EVIDENCE_TOO_LARGE` stay.
- Coordinator validation (`coordinator.py:817`) checks `evidence_id ∈ ledger` with matching `run_id` (the
  task id) and capability.
- Invariant I2 helper: `Ledger.verified_after_last_effect(run_id) -> bool` (used by V2-06 `finish`).

### 5.8 PANINI-CLI bridge

`CodeAdapter` already receives app-server events. Each CLI receipt event becomes an `evidence` entry on the
`code_do` receipt with `{source: "panini-cli", cli_receipt_hash, kind, exit_code, verifier}`. The CLI's own
chain remains the source of truth for CLI internals.

### 5.9 Daily roots

At the first append after local midnight, the ledger writes `ledger_roots(day, last_seq, root_hash)` for the
previous day and appends the line to `%LOCALAPPDATA%\PANINI\ledger-roots.txt`. Admin shows the latest roots;
`scripts/panini-ledger-verify.py` recomputes the chain and compares roots.

### 5.10 API and tools

| Route / tool | Purpose |
| --- | --- |
| `GET /api/harness/receipts?run_id=&tool=&effect=&since=&until=&limit=` | Query (console) |
| `GET /api/harness/receipts/{id}` | Receipt with entries, evidence and artifacts |
| `GET /api/harness/ledger/verify` | Chain verification result |
| `GET /api/harness/artifacts/{id}` | Artifact bytes |
| Tool `activity_search(query, since, until)` (`observe`) | "What did you do…?" answered from receipts; results are PANINI's own records (not tainted) |
| Event `receipt.recorded` | `{receipt_id, run_id, tool, effect, outcome}` for live activity views |

### 5.11 Failure modes

| Failure | Behaviour |
| --- | --- |
| Disk full / SQLite busy beyond 5 s | The pipeline refuses the call **before** dispatch ("could not record this action"); never dispatches unrecorded |
| Chain verification fails | Admin banner; `scripts/panini-ledger-verify.py` names the first bad `seq`; PANINI keeps running (evidence of tampering is surfaced, not hidden) |
| Artifact write fails | Receipt records `artifact_error`; the tool result is still returned |

## 6. Implementation steps

1. `chain.py` + migration `0005_ledger.sql`; append and verify; tests including concurrent appends.
2. `receipts.py` lifecycle, operation keys, `recover()`; registry hooks.
3. `redact.py` with the secret sweep test.
4. `artifacts.py` + retention job (runs from the harness worker `background_cycle`).
5. Coordinator evidence compatibility behind the flag; I2 helper.
6. CLI bridge in `capabilities/code.py`.
7. API routes, Admin Activity page (filters, receipt detail with entries and artifacts), `activity_search`.
8. Daily roots and `scripts/panini-ledger-verify.py`.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Chain append/verify, tamper detection, concurrent writers | `backend/tests/ledger/test_chain.py` |
| Unit | Lifecycle, operation keys, replay table (§5.4) | `backend/tests/ledger/test_receipts.py` |
| Unit | Redaction sweep | `backend/tests/ledger/test_redact.py` |
| Unit | Retention and pinning | `backend/tests/ledger/test_artifacts.py` |
| Invariant | I1: a fake hand called through the registry always has begin and end entries | `backend/tests/ledger/test_no_bypass.py` |
| Integration | Kill during a dispatched call (simulated) → restart → `unknown_effect` + review interrupt, no second dispatch | `backend/tests/ledger/test_recovery.py` |
| Integration | v1 coordinator evidence validation passes with the ledger | `backend/tests/panini/test_evidence_ledger.py` |

## 8. Acceptance criteria

- [ ] Every registry call writes begin/end entries; I1 test green.
- [ ] Simulated crash after dispatch never re-dispatches; review interrupt raised.
- [ ] `scripts/panini-ledger-verify.py` verifies a 10,000-entry chain in under 2 s.
- [ ] Admin Activity shows receipts with redacted args, evidence and artifacts.

## 9. Rollout and rollback

Flag on in development as soon as step 2 lands. Rollback: flag off; tables remain and are ignored.

## 10. Open questions

- External anchoring of daily roots (for example committing `ledger-roots.txt` to a private Git repository or
  emailing it to yourself)? Recommendation: optional setting, off by default.
