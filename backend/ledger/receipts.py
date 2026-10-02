"""Receipts: one per tool call, built from chained entries (begin, decision, dispatch, evidence, end,
recovered), with operation keys for idempotent replay (V2-04 §5.3–5.4)."""

import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from . import chain
from .redact import redact_args, redact_result


def new_id() -> str:
    """Time-sortable id: milliseconds then randomness."""
    return f"{int(time.time() * 1000):013d}{uuid4().hex[:12]}"


def operation_key(run_id: str, tool: str, args_digest: str, occurrence: int) -> str:
    return chain.digest([run_id, tool, args_digest, occurrence])


class Ledger:
    def __init__(self, store, clock=time.time, roots_file=None):
        self.store, self.clock, self.roots_file = store, clock, roots_file

    # Lifecycle -------------------------------------------------------------
    def begin(
        self,
        *,
        run_id,
        tool,
        capability,
        hand,
        args,
        effect,
        principal,
        thread_id=None,
        persona_id="panini",
        target_label=None,
        redactions=(),
    ) -> dict:
        args_digest = chain.digest(args or {})
        occurrence = self.store.one(
            "SELECT COUNT(*) AS n FROM receipts WHERE run_id=? AND tool=? AND args_digest=?",
            (run_id, tool, args_digest),
        )["n"]
        key = operation_key(run_id, tool, args_digest, occurrence)
        receipt_id = new_id()
        args_view = redact_args(args or {}, redactions)
        self._roll_root()
        self.store.execute(
            "INSERT INTO receipts(id, run_id, thread_id, persona_id, principal, tool, capability, hand, target_label, "
            "operation_key, args_digest, args_view, effect, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                run_id,
                thread_id,
                persona_id,
                json.dumps(principal),
                tool,
                capability,
                hand,
                target_label,
                key,
                args_digest,
                json.dumps(args_view, ensure_ascii=False),
                effect,
                self.clock(),
            ),
        )
        chain.append(
            self.store,
            receipt_id,
            "begin",
            {
                "run_id": run_id,
                "tool": tool,
                "capability": capability,
                "effect": effect,
                "args_digest": args_digest,
                "args_view": args_view,
                "operation_key": key,
                "principal": principal,
            },
            clock=self.clock,
        )
        return {"id": receipt_id, "operation_key": key, "args_digest": args_digest}

    def decision(self, receipt_id, decision: dict):
        self.store.execute(
            "UPDATE receipts SET decision=?, effect=COALESCE(?, effect) WHERE id=?",
            (json.dumps(decision), decision.get("effect"), receipt_id),
        )
        chain.append(self.store, receipt_id, "decision", decision, clock=self.clock)

    def dispatched(self, receipt_id):
        row = self.store.one("SELECT dispatched FROM receipts WHERE id=?", (receipt_id,))
        if row and not row["dispatched"]:
            self.store.execute("UPDATE receipts SET dispatched=1 WHERE id=?", (receipt_id,))
            chain.append(self.store, receipt_id, "dispatch", {}, clock=self.clock)

    def evidence(self, receipt_id, kind, summary, details) -> str:
        evidence_id = new_id()
        item = {
            "evidence_id": evidence_id,
            "kind": kind,
            "summary": str(summary)[:1000],
            "details_digest": chain.digest(details),
        }
        if isinstance(details, dict):
            for key in ("receipt_sha256", "exit_code", "verifier"):
                if key in details:
                    item[key] = details[key]
        row = self.store.one("SELECT evidence FROM receipts WHERE id=?", (receipt_id,))
        if row is not None:
            items = json.loads(row["evidence"]) + [item]
            self.store.execute(
                "UPDATE receipts SET evidence=? WHERE id=?", (json.dumps(items[-50:]), receipt_id)
            )
        chain.append(self.store, receipt_id, "evidence", item, clock=self.clock)
        return evidence_id

    def end(self, receipt_id, *, outcome, content="", data=None, artifact_ids=(), cost=None, dispatched=None):
        view = redact_result(content if content else json.dumps(data or {}, ensure_ascii=False))
        result_digest = chain.digest({"content": content, "data": data})
        if dispatched:
            self.dispatched(receipt_id)
        self.store.execute(
            "UPDATE receipts SET outcome=?, result_digest=?, result_view=?, artifact_ids=?, finished_at=?, cost=? WHERE id=?",
            (
                outcome,
                result_digest,
                view,
                json.dumps(list(artifact_ids)),
                self.clock(),
                json.dumps(cost or {}),
                receipt_id,
            ),
        )
        chain.append(
            self.store,
            receipt_id,
            "end",
            {
                "outcome": outcome,
                "result_digest": result_digest,
                "artifact_ids": list(artifact_ids),
            },
            clock=self.clock,
        )

    # Replay and recovery ---------------------------------------------------
    def prior(self, key: str, exclude: str | None = None) -> dict | None:
        """The most recent earlier receipt with this operation key, if any."""
        return self.store.one(
            "SELECT * FROM receipts WHERE operation_key=? AND id != ? ORDER BY started_at DESC LIMIT 1",
            (key, exclude or ""),
        )

    def replay_decision(self, key: str, exclude: str | None = None) -> tuple[str, dict | None]:
        """proceed | replay (return the stored result) | unknown (an earlier attempt may have taken effect)."""
        row = self.prior(key, exclude)
        if row is None:
            return "proceed", None
        if row["outcome"] == "ok":
            return "replay", row
        if row["outcome"] in {"pending", "unknown_effect"} and row["dispatched"]:
            return "unknown", row
        if row["outcome"] == "pending":
            self.end(row["id"], outcome="cancelled", content="Superseded by a new attempt.")
        return "proceed", None

    def recover(self) -> list[dict]:
        """Close receipts left pending by a previous process: never dispatched → cancelled; dispatched → unknown."""
        rows = self.store.all("SELECT id, dispatched, tool, run_id FROM receipts WHERE outcome='pending'")
        unknown = []
        for row in rows:
            outcome = "unknown_effect" if row["dispatched"] else "cancelled"
            chain.append(self.store, row["id"], "recovered", {"outcome": outcome}, clock=self.clock)
            self.store.execute(
                "UPDATE receipts SET outcome=?, finished_at=? WHERE id=?", (outcome, self.clock(), row["id"])
            )
            if row["dispatched"]:
                unknown.append(row)
        return unknown

    def verified_after_last_effect(self, run_id: str) -> bool:
        """I2 helper: every effect is followed by evidence (or carries its own)."""
        rows = self.store.all(
            "SELECT effect, evidence, outcome FROM receipts WHERE run_id=? ORDER BY started_at", (run_id,)
        )
        last_effect, verified_after = None, False
        for index, row in enumerate(rows):
            if row["effect"] not in {"observe", "navigate"} and row["outcome"] == "ok":
                last_effect, verified_after = index, bool(json.loads(row["evidence"]))
            elif last_effect is not None and json.loads(row["evidence"]):
                verified_after = True
        return last_effect is None or verified_after

    # Queries ---------------------------------------------------------------
    def query(self, *, run_id=None, tool=None, effect=None, since=None, until=None, text=None, limit=100):
        clauses, params = [], []
        for column, value in (("run_id", run_id), ("tool", tool), ("effect", effect)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if since:
            clauses.append("started_at>=?")
            params.append(since)
        if until:
            clauses.append("started_at<=?")
            params.append(until)
        if text:
            clauses.append("(tool LIKE ? OR args_view LIKE ? OR result_view LIKE ? OR target_label LIKE ?)")
            params += [f"%{text}%"] * 4
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.store.all(
            f"SELECT * FROM receipts{where} ORDER BY started_at DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        )
        return [self._view(r) for r in rows]

    def receipt(self, receipt_id):
        row = self.store.one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
        if row is None:
            return None
        view = self._view(row)
        view["entries"] = self.store.all(
            "SELECT seq, kind, body, ts, hash FROM ledger_entries WHERE receipt_id=? ORDER BY seq",
            (receipt_id,),
        )
        return view

    @staticmethod
    def _view(row):
        out = dict(row)
        for key in ("principal", "args_view", "decision", "evidence", "artifact_ids", "cost"):
            if out.get(key):
                try:
                    out[key] = json.loads(out[key])
                except ValueError:
                    pass
        return out

    # Daily roots -----------------------------------------------------------
    def _roll_root(self):
        today = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y-%m-%d")
        last = self.store.one("SELECT day FROM ledger_roots ORDER BY day DESC LIMIT 1")
        head = self.store.one("SELECT seq, hash FROM ledger_entries ORDER BY seq DESC LIMIT 1")
        if head is None or (last and last["day"] >= today):
            return
        first_today = self.store.one(
            "SELECT MIN(seq) AS seq FROM ledger_entries WHERE ts >= ?",
            (datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=UTC).timestamp(),),
        )
        if first_today and first_today["seq"]:
            return  # Entries from today exist already; the root for yesterday was written then.
        day = datetime.fromtimestamp(
            self.store.one("SELECT ts FROM ledger_entries WHERE seq=?", (head["seq"],))["ts"], UTC
        ).strftime("%Y-%m-%d")
        if day >= today:
            return
        self.store.execute(
            "INSERT OR IGNORE INTO ledger_roots(day, last_seq, root_hash) VALUES (?,?,?)",
            (day, head["seq"], head["hash"]),
        )
        if self.roots_file:
            try:
                with open(self.roots_file, "a", encoding="utf-8") as handle:
                    handle.write(f"{day} {head['seq']} {head['hash']}\n")
            except OSError:
                pass
