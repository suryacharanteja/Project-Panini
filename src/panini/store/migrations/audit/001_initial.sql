-- Audit log — append-only by database trigger, not by convention (DD-141).
--
-- Scope of this control, stated honestly: these triggers defend against the
-- assistant itself, including a compromised tool. They do not defend against an
-- attacker with local code execution, who could delete the file outright — but
-- such an attacker already has the shell tool, a more direct route.

-- Idempotent: executescript() cannot run inside an explicit transaction, so a
-- crash mid-migration must leave a safe retry (see AuditStore._migrate).
CREATE TABLE IF NOT EXISTS audit (
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
    effective_tier    INTEGER NOT NULL,   -- escalation is visible in the record
    approval_required INTEGER NOT NULL,
    approval_reason   TEXT,
    approval_granted  INTEGER,
    is_egress         INTEGER NOT NULL,
    destination       TEXT,
    outcome           TEXT    NOT NULL DEFAULT 'pending',
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_turn ON audit(turn_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts   ON audit(ts_begin);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit(tool_name, ts_begin);

-- No deletion, ever.
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

-- Only a pending row may be updated, and only to resolve it.
CREATE TRIGGER IF NOT EXISTS audit_immutable_once_resolved
BEFORE UPDATE ON audit
WHEN OLD.outcome != 'pending'
BEGIN
    SELECT RAISE(ABORT, 'resolved audit entries are immutable');
END;

-- Even on a pending row, identity and decision columns cannot change.
-- Without this, a caller could resolve a row while rewriting what it recorded.
CREATE TRIGGER IF NOT EXISTS audit_fields_frozen
BEFORE UPDATE ON audit
WHEN OLD.invocation_id  != NEW.invocation_id
  OR OLD.tool_name      != NEW.tool_name
  OR OLD.args_redacted  != NEW.args_redacted
  OR OLD.turn_id        != NEW.turn_id
  OR OLD.session_id     != NEW.session_id
  OR OLD.tainted        != NEW.tainted
  OR OLD.declared_tier  != NEW.declared_tier
  OR OLD.effective_tier != NEW.effective_tier
  OR OLD.is_egress      != NEW.is_egress
  OR OLD.ts_begin       != NEW.ts_begin
BEGIN
    SELECT RAISE(ABORT, 'audit identity and decision fields are immutable');
END;
