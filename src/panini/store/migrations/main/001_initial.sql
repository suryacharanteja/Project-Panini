-- Conversation history and long-term facts.
-- Idempotent: executescript() cannot run inside an explicit transaction, so a
-- crash mid-migration must leave a safe retry.

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    persona_id TEXT,
    summary    TEXT                     -- rolling session summary (memory tier 2)
);

CREATE TABLE IF NOT EXISTS turns (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    modality      TEXT NOT NULL,
    input_text    TEXT NOT NULL,
    state         TEXT NOT NULL,
    -- Persisted for forensics: if something goes wrong later, the history shows
    -- which turns handled untrusted content. NOT reloaded as live taint state —
    -- a restored session starts clean (FR-512).
    tainted       INTEGER NOT NULL DEFAULT 0,
    taint_sources TEXT,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, seq);

CREATE TABLE IF NOT EXISTS segments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id      TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    content      TEXT NOT NULL,
    metadata     TEXT
);

CREATE INDEX IF NOT EXISTS idx_segments_turn ON segments(turn_id, seq);

-- Long-term facts (memory tier 3).
--
-- origin_tainted and confirmed together close the taint-laundering path
-- (DD-120, DD-121): taint resets per turn, but memory crosses turns, so a fact
-- extracted from injected content could otherwise re-enter a clean turn as
-- trusted background.
CREATE TABLE IF NOT EXISTS facts (
    id             TEXT PRIMARY KEY,
    subject        TEXT NOT NULL,
    statement      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    last_used_at   TEXT,
    use_count      INTEGER NOT NULL DEFAULT 0,
    source_turn_id TEXT,

    origin_tainted INTEGER NOT NULL,     -- permanent provenance (DD-120)
    confirmed      INTEGER NOT NULL,     -- gate before retrieval (DD-121 M-1)
    edited_by_user INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_facts_subject   ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_retrieval ON facts(confirmed, last_used_at);
