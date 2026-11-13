# Project Panini — Persistence LLD

**Document Type:** Low Level Design  
**Phase:** 05 — LLD  
**Module:** `panini/store/`  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## Resolves

| Open item | Source | Section |
|---|---|---|
| Audit log schema and redaction | Security Kernel LLD §6 | §4 |
| Audit retention / rotation | Security Kernel LLD §11 item 4 | §4.5 |
| Fact storage | Conversation LLD §4.3 | §3.3 |

---

## 1. Storage Topology

Three separate stores, deliberately not one.

```
%LOCALAPPDATA%\Panini\
├── panini.db          Conversations · turns · segments · facts · settings
├── audit.db           ⚠️ Append-only audit log — SEPARATE FILE
├── audit-archive/
│   └── audit-2026-09.db.gz
└── ~/.config/panini/
    └── credentials.enc    Encrypted, machine-bound (Security Kernel LLD §7)
```

### Why the audit log is a separate file

| Reason | |
|---|---|
| **Clean deny path** | `DENY_ROOTS` names one file; no risk of an allow-rule exposing it (FR-563) |
| **Independent durability** | Audit uses `synchronous=FULL`; the main DB uses `NORMAL` |
| **Blast radius** | Corruption or a migration fault in the main DB cannot damage the audit trail |
| **Rotation** | Archived independently without touching conversation data |
| **Distinct trigger regime** | Append-only triggers apply to the whole file, not one table among many |

Credentials are **not** SQLite — an encrypted blob outside the data directory entirely (FR-541).

---

## 2. Connection Management

### 2.1 Pragmas

```python
MAIN_PRAGMAS = {
    "journal_mode": "WAL",        # concurrent readers with one writer
    "synchronous":  "NORMAL",     # adequate for conversation data
    "foreign_keys": "ON",
    "busy_timeout": 5000,
}

AUDIT_PRAGMAS = {
    "journal_mode": "WAL",
    "synchronous":  "FULL",       # ⚠️ durability over speed — this is evidence
    "foreign_keys": "ON",
    "busy_timeout": 5000,
}
```

`synchronous=FULL` on the audit store means each commit reaches disk before returning. It is slower, and correct: an audit entry that survives only in the page cache is worthless after a crash.

### 2.2 Async access

SQLite is synchronous. All database work runs off the event loop:

```python
async def execute(self, sql: str, params: tuple = ()) -> list[Row]:
    return await asyncio.to_thread(self._execute_sync, sql, params)
```

**Writer serialisation:** each store holds a single writer connection guarded by an `asyncio.Lock`. Readers use a small connection pool. WAL permits concurrent reads during a write.

### 2.3 Integrity check at startup

```python
async def open(self) -> None:
    if (await self.execute("PRAGMA quick_check"))[0][0] != "ok":
        raise StoreCorrupted(self.path)
```

Corruption of `panini.db` degrades the assistant to a fresh session with a visible warning. **Corruption of `audit.db` is fatal to startup** — running without a working audit trail would violate FR-560.

---

## 3. Main Schema (`panini.db`)

### 3.1 Sessions and turns

```sql
CREATE TABLE sessions (
    id           TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    persona_id   TEXT,
    summary      TEXT                    -- rolling session summary (Tier 2)
);

CREATE TABLE turns (
    id            TEXT PRIMARY KEY,      -- uuid7, time-ordered
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,      -- ordinal within session
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    modality      TEXT NOT NULL,         -- voice | text
    input_text    TEXT NOT NULL,
    state         TEXT NOT NULL,         -- completed | interrupted | failed
    tainted       INTEGER NOT NULL DEFAULT 0,
    taint_sources TEXT,                  -- JSON array
    UNIQUE(session_id, seq)
);

CREATE INDEX idx_turns_session ON turns(session_id, seq);

CREATE TABLE segments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id      TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    content      TEXT NOT NULL,
    metadata     TEXT                    -- JSON
);

CREATE INDEX idx_segments_turn ON segments(turn_id, seq);
```

`tainted` and `taint_sources` are persisted on the turn for **forensics** — if something goes wrong later, the history shows which turns handled untrusted content. They are not reloaded as live taint state; a restored session starts clean (FR-512).

### 3.2 Why segments are stored separately

Storing the response as one blob would lose content-type boundaries, forcing re-parsing on reload and risking different results than the original render. Segments are the durable form.

### 3.3 Facts (DD-118, DD-120, DD-121)

```sql
CREATE TABLE facts (
    id             TEXT PRIMARY KEY,
    subject        TEXT NOT NULL,
    statement      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    last_used_at   TEXT,
    use_count      INTEGER NOT NULL DEFAULT 0,
    source_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,

    origin_tainted INTEGER NOT NULL,     -- ⚠️ DD-120 — permanent provenance
    confirmed      INTEGER NOT NULL,     -- ⚠️ DD-121 — gate before retrieval
    edited_by_user INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_facts_subject   ON facts(subject);
CREATE INDEX idx_facts_retrieval ON facts(confirmed, last_used_at);
```

**The retrieval query enforces the gate in SQL, not only in application code:**

```sql
SELECT * FROM facts
WHERE confirmed = 1                      -- DD-121 M-1
ORDER BY last_used_at DESC;
```

A fact that is `origin_tainted = 1 AND confirmed = 0` is unreachable by the retrieval path by construction.

`edited_by_user = 1` sets `origin_tainted = 0` (DD-137) — authorship transferred.

### 3.4 Settings

```sql
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,            -- JSON
    updated_at TEXT NOT NULL
);
```

Runtime state only — tool enable/disable, allowlisted destinations, window mode. **User configuration lives in `panini.yaml`** (DD-092); this table holds state the UI mutates, not authored config. Keeping them separate prevents the config file being rewritten by the application.

---

## 4. Audit Schema (`audit.db`)

### 4.1 Table

```sql
CREATE TABLE audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_begin          TEXT    NOT NULL,
    ts_end            TEXT,
    session_id        TEXT    NOT NULL,
    turn_id           TEXT    NOT NULL,
    invocation_id     TEXT    NOT NULL UNIQUE,
    tool_name         TEXT    NOT NULL,
    args_redacted     TEXT    NOT NULL,
    provenance        TEXT    NOT NULL,
    tainted           INTEGER NOT NULL,
    taint_sources     TEXT,
    declared_tier     INTEGER NOT NULL,
    effective_tier    INTEGER NOT NULL,
    approval_required INTEGER NOT NULL,
    approval_reason   TEXT,
    approval_granted  INTEGER,
    is_egress         INTEGER NOT NULL,
    destination       TEXT,
    outcome           TEXT    NOT NULL DEFAULT 'pending',
    error             TEXT
);

CREATE INDEX idx_audit_turn ON audit(turn_id);
CREATE INDEX idx_audit_ts   ON audit(ts_begin);
CREATE INDEX idx_audit_tool ON audit(tool_name, ts_begin);
```

### 4.2 Append-only enforcement

Triggers make this a database-level guarantee rather than an application convention.

```sql
-- no deletion, ever
CREATE TRIGGER audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

-- only a pending row may be updated, and only to resolve it
CREATE TRIGGER audit_immutable_once_resolved
BEFORE UPDATE ON audit
WHEN OLD.outcome != 'pending'
BEGIN
    SELECT RAISE(ABORT, 'resolved audit entries are immutable');
END;

-- even on a pending row, identity and decision columns cannot change
CREATE TRIGGER audit_fields_frozen
BEFORE UPDATE ON audit
WHEN OLD.invocation_id  != NEW.invocation_id
  OR OLD.tool_name      != NEW.tool_name
  OR OLD.args_redacted  != NEW.args_redacted
  OR OLD.turn_id        != NEW.turn_id
  OR OLD.tainted        != NEW.tainted
  OR OLD.declared_tier  != NEW.declared_tier
  OR OLD.effective_tier != NEW.effective_tier
  OR OLD.is_egress      != NEW.is_egress
BEGIN
    SELECT RAISE(ABORT, 'audit identity and decision fields are immutable');
END;
```

Only `ts_end`, `outcome`, `error`, and `approval_granted` can transition on a `pending` row (FR-562).

> **Honest limitation:** a process with filesystem access could delete the file outright. These triggers defend against *the assistant itself* — including a compromised tool — not against an attacker who already has local code execution. At that point the shell tool is a more direct route. This is the correct scope for the control.

### 4.3 Write path

```python
async def begin(self, turn, spec, args, decision, egress) -> int:
    try:
        return await self._insert_pending(...)
    except Exception as exc:
        raise AuditWriteFailure(str(exc)) from exc     # ⇒ tool does NOT execute
```

`AuditWriteFailure` propagates to `ToolGateway` and aborts the invocation (HLD §8). **An unauditable action does not happen.**

### 4.4 Redaction

Per Security Kernel LLD §6.3, applied **before** the insert. Credential values never reach this path — tools receive handles — so redaction is defence in depth rather than the primary control.

### 4.5 Retention and rotation

Deletion is impossible by trigger, so size is bounded by **rotation**, not pruning.

```
Monthly, or when audit.db exceeds 100 MB:
  1. Close the writer connection
  2. Rename audit.db → audit-archive/audit-YYYY-MM.db
  3. Create a fresh audit.db with the same schema and triggers
  4. gzip the archive
  5. Record a rotation marker as the first row of the new file
```

| Policy | Value |
|---|---|
| Archive retention | Indefinite by default — user-deletable, never automatic |
| Query scope | Live file by default; the UI can open archives on request |

Archives are never deleted by the application. Removing audit history is a decision only the user makes, outside the assistant.

**Estimated growth:** ~1 KB per tool invocation. At 50 invocations/day ≈ **18 MB/year**. Rotation is precautionary, not pressing.

---

## 5. Migrations

### 5.1 Mechanism

```sql
CREATE TABLE schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

Forward-only, numbered SQL files:

```
panini/store/migrations/
├── main/001_initial.sql
├── main/002_add_facts.sql
└── audit/001_initial.sql
```

At startup: read current version, apply pending migrations in a transaction, record each.

### 5.2 Rules

| Rule | Reason |
|---|---|
| Forward-only; no down-migrations | Single-user local app — a rollback path is speculative complexity |
| Each migration in its own transaction | Partial application leaves a known version |
| **Back up before migrating** | `panini.db.bak-v{n}` retained for one version |
| **Audit migrations must preserve every row** | Never `CREATE TABLE new; INSERT SELECT; DROP old` — the DROP would trip the delete trigger and, worse, could silently lose entries |

Audit schema changes use `ALTER TABLE ADD COLUMN` exclusively. If a change cannot be expressed that way, the correct action is rotation to a new file rather than rewriting history.

---

## 6. Backup Guidance

| Item | Include? | Reason |
|---|---|---|
| `panini.db` | ✅ Yes | Conversations and facts |
| `audit.db` + archives | ✅ Yes | Accountability record |
| `credentials.enc` | ❌ **No** | Machine-bound — useless elsewhere, and copying secrets is bad practice (DD-066) |
| `panini.yaml` | ✅ Yes | Configuration |

A restored backup on a new machine produces a working assistant that must re-authenticate connectors. **That is the intended behaviour**, not a defect.

---

## 7. Data Access API

```python
class HistoryStore:
    async def create_session(...) -> Session
    async def append_turn(turn: Turn) -> None
    async def load_recent_turns(session_id, limit) -> list[Turn]
    async def load_session(session_id) -> Session | None

class FactStore:
    async def add(fact: Fact) -> None
    async def load_confirmed() -> list[Fact]          # DD-121 gate in SQL
    async def load_unconfirmed() -> list[Fact]        # review queue
    async def confirm(fact_id) -> None
    async def edit(fact_id, statement) -> None        # clears origin_tainted
    async def delete(fact_id) -> None
    async def touch(fact_id) -> None                  # last_used_at, use_count

class AuditStore:
    async def begin(...) -> int
    async def complete(audit_id, outcome, error=None) -> None
    async def query(filters, limit, offset) -> list[AuditEntry]
    async def rotate_if_needed() -> None
    # ⚠️ no delete(), no update() — deliberately absent from the API surface
```

`AuditStore` exposes no mutation beyond `complete`. The trigger is the enforcement; the missing methods are the design intent made obvious.

---

## 8. Test Matrix

| Test | Asserts | Ref |
|---|---|---|
| **`test_audit_delete_raises`** | Trigger blocks DELETE | §4.2 |
| **`test_audit_resolved_update_raises`** | Immutable once resolved | §4.2 |
| **`test_audit_identity_fields_frozen`** | Cannot rewrite decision fields | §4.2 |
| `test_audit_pending_can_resolve` | Legitimate path works | FR-562 |
| **`test_audit_write_failure_aborts_tool`** | Unauditable ⇒ no execution | HLD §8 |
| `test_audit_uses_synchronous_full` | Durability pragma | §2.1 |
| **`test_unconfirmed_fact_not_in_load_confirmed`** | Gate enforced in SQL | DD-121 |
| `test_edit_fact_clears_origin_tainted` | DD-137 | — |
| `test_restored_session_starts_clean` | Taint not rehydrated | FR-512 |
| `test_segments_roundtrip_content_type` | No re-parsing | §3.2 |
| `test_rotation_preserves_archive` | No data loss | §4.5 |
| `test_rotation_creates_fresh_triggers` | New file is still append-only | §4.5 |
| `test_migration_forward_only` | §5.2 | — |
| `test_migration_backs_up_first` | §5.2 | — |
| `test_corrupt_audit_db_fatal` | §2.3 | FR-560 |
| `test_corrupt_main_db_degrades` | §2.3 | AP-7 |
| `test_concurrent_reads_during_write` | WAL | §2.2 |

---

## 9. New Decisions Required

| Proposed ID | Decision |
|---|---|
| **DD-139** | **Audit log is a separate database file**, not a table in `panini.db` — clean deny path, independent durability, isolated blast radius, independent rotation. |
| **DD-140** | **Audit uses `synchronous=FULL`**; the main store uses `NORMAL`. An audit entry surviving only in the page cache is worthless after a crash. |
| **DD-141** | **Append-only is enforced by SQLite triggers**, not application convention. `AuditStore` exposes no delete or general update method. |
| **DD-142** | **Audit is bounded by rotation, not pruning.** Archives are never deleted by the application; removing history is a decision only the user makes. |
| **DD-143** | **Corrupt `audit.db` is fatal to startup**; corrupt `panini.db` degrades gracefully. Running without an audit trail would violate FR-560. |
| **DD-144** | **Credentials are excluded from backups.** A restored backup requires re-authentication — intended behaviour, not a defect. |

---

## 10. Open Items

| # | Item | Disposition |
|---|---|---|
| 1 | Audit archive viewer UI | `05_Frontend_LLD.md` — currently live-file only |
| 2 | Whether conversation history should have retention limits | Deferred; ~180 MB/year is tolerable |
| 3 | Full-text search over history | Not in v1; FTS5 is available if needed later |

---

## 11. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |
