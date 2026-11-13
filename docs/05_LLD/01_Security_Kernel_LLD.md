# Project Panini — Security Kernel LLD

**Document Type:** Low Level Design  
**Phase:** 05 — LLD  
**Module:** `panini/security/`  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## Why this module is built first

Every tool call in Panini passes through this module. It implements DD-110 – DD-117 and the threat model's SC-1 – SC-8. Retrofitting a mandatory gate into a system that already calls tools directly is a rewrite — so it is Stage 1 (LLD Index §5).

**Quality bar:** `mypy --strict`, 100 % branch coverage on `tool_gateway.py`, `egress.py`, and `taint.py`, and every negative test in `tests/security/` green.

---

## 1. Module Layout

```
panini/security/
├── __init__.py          Public surface: ToolGateway, Provenance, Tier
├── types.py             Enums and value objects
├── tool_gateway.py      ⚠️ THE GATE — the only path from core/ to tools/
├── provenance.py        Context tagging (DD-110)
├── taint.py             Per-turn taint state (DD-111)
├── permissions.py       Tier resolution + escalation (DD-060–064)
├── egress.py            Egress classification (DD-112)
├── approvals.py         Approval lifecycle
├── credentials.py       Encrypted store, handle issuance (DD-115)
├── audit.py             Append-only audit writer (DD-116)
└── redaction.py         Argument redaction for audit/logs
```

---

## 2. Core Types (`types.py`)

```python
class Provenance(StrEnum):
    USER      = "user"        # voice or text input      FR-502
    SYSTEM    = "system"      # Panini's own prompts
    RETRIEVED = "retrieved"   # ALL tool output          FR-503

class Tier(IntEnum):
    T0_READ        = 0   # no prompt when clean          FR-520
    T1_WRITE       = 1   # confirmation                  FR-521
    T2_EXECUTE     = 2   # explicit approval             FR-522
    T3_DESTRUCTIVE = 3   # approval + mandatory audit    FR-523

class EgressClass(StrEnum):
    NONE        = "none"         # cannot transmit off-machine
    ALWAYS      = "always"       # always an egress channel
    CONDITIONAL = "conditional"  # depends on arguments
    UNKNOWN     = "unknown"      # ⚠️ treated as ALWAYS — fail closed

@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: Tier
    egress_class: EgressClass
    egress_predicate: str | None = None   # key into EGRESS_PREDICATES
    credential_handles: tuple[str, ...] = ()

@dataclass(frozen=True)
class GateDecision:
    effective_tier: Tier
    declared_tier: Tier
    approval_required: bool
    reason: ApprovalReason | None
    is_egress: bool
    destination: str | None
    payload_preview: str | None

class ApprovalReason(StrEnum):
    TIER            = "tier"             # tier alone requires it
    TAINT_ESCALATED = "taint_escalated"  # raised because turn is tainted
    EGRESS_TAINTED  = "egress_tainted"   # egress while tainted
    UNKNOWN_DEST    = "unknown_destination"
```

> `EgressClass.UNKNOWN` exists so that a tool added without explicit classification is treated as egress. **The default fails closed.** This is the mechanism behind HR-03.

---

## 3. ToolGateway — the gate (`tool_gateway.py`)

### 3.1 Responsibility

The **only** interface between `core/` and `tools/`. Enforced by CI lint (LLD Index §3.2).

### 3.2 Algorithm

```python
async def invoke(self, turn: Turn, tool_name: str, args: dict) -> ToolResult:

    # 1 — resolve; unknown or disabled tools never reach execution
    spec = self._registry.spec(tool_name)          # ToolNotAvailable if absent
    if not self._registry.is_enabled(tool_name):   # FR-404
        raise ToolNotAvailable(tool_name)

    # 2 — classify egress BEFORE any permission decision
    egress = self._egress.classify(spec, args)      # §4

    # 3 — resolve effective tier (taint escalation)
    decision = self._permissions.decide(spec, egress, turn.tainted)   # §5

    # 4 — audit PENDING before anything executes            FR-562
    #     AuditWriteFailure propagates → the call does not run  (HLD §8)
    audit_id = await self._audit.begin(turn, spec, args, decision, egress)

    # 5 — approval, if required
    if decision.approval_required:
        granted = await self._approvals.request(
            turn=turn, spec=spec, args=args,
            decision=decision, egress=egress,
            taint_sources=turn.taint_sources,       # FR-514
        )
        if not granted:
            await self._audit.complete(audit_id, outcome=Outcome.DENIED)
            raise ApprovalDenied(tool_name)          # FR-525

    # 6 — inject credential values at the boundary          FR-542
    creds = self._credentials.resolve(spec.credential_handles)

    # 7 — execute
    try:
        result = await self._registry.execute(tool_name, args, creds)
    except Exception as exc:
        await self._audit.complete(audit_id, outcome=Outcome.ERROR, error=str(exc))
        raise

    # 8 — audit outcome
    await self._audit.complete(audit_id, outcome=Outcome.OK)

    # 9 — ⚠️ TAINT: tool output is untrusted content       FR-503, FR-510
    turn.add_context(
        content=result.content,
        provenance=Provenance.RETRIEVED,
        source=f"{tool_name}:{result.source_ref or '-'}",
    )

    return result
```

### 3.3 Invariants

| # | Invariant | Verified by |
|---|---|---|
| **I-1** | No tool executes without an audit record written first | `test_no_execution_without_audit` |
| **I-2** | Every result is tagged `RETRIEVED` before returning | `test_result_always_tainted` |
| **I-3** | Egress classification precedes permission resolution | `test_egress_before_permission` |
| **I-4** | `AuditWriteFailure` prevents execution | `test_audit_failure_blocks` |
| **I-5** | Denied approval never executes the tool | `test_denial_blocks_execution` |
| **I-6** | Step 9 runs even when the tool returns an error result | `test_error_result_still_taints` |

> **I-6 is subtle and important.** A tool that fails may still have returned attacker-influenced content in its error message. Failure does not mean clean.

---

## 4. Egress Classification (`egress.py`) — resolves LLD open item #1

### 4.1 Definition

> A tool is an **egress channel** if invoking it can cause data to leave the machine, *regardless of its permission tier*.

Tier answers *"how damaging locally?"* Egress answers *"can this send my data out?"* They are orthogonal — the failure of tier-only models (threat T-07).

### 4.2 Complete v1 classification

| Tool | Tier | Egress | Rationale |
|---|---|---|---|
| `gmail_read` | T0 | NONE | Retrieval only |
| `gmail_search` | T0 | **CONDITIONAL** | The **query string leaves the machine**. Egress if the query contains assistant-generated content in a tainted turn |
| `gmail_send` | T1 | **ALWAYS** | Arbitrary recipient |
| `gmail_delete` | T3 | NONE | Destructive, not exfiltrating |
| `calendar_read` | T0 | NONE | |
| `calendar_create` | T1 | **ALWAYS** | ⚠️ **Attendees receive invitations.** An event with an attendee is an outbound message |
| `calendar_update` | T1 | **ALWAYS** | Same — updates notify attendees |
| `calendar_delete` | T3 | **CONDITIONAL** | Cancellation notices reach attendees |
| `web_search` | T0 | **CONDITIONAL** | The **query leaves the machine** — a viable exfiltration channel |
| `web_fetch` | T0 | **ALWAYS** | URL path, query, and body all carry data. The classic T-07 vector |
| `fs_read` | T0 | NONE | |
| `fs_list` | T0 | NONE | |
| `fs_write` | T1 | **CONDITIONAL** | Egress if the **resolved** path lies under a synchronised root |
| `fs_delete` | T3 | NONE | |
| `shell_exec` | T2 | **ALWAYS** | Can invoke `curl`, `scp`, anything. Never classifiable as safe |
| `browser_navigate` | T2 | **ALWAYS** | URL carries data |
| `browser_interact` | T2 | **ALWAYS** | Form submission is transmission |
| `browser_extract` | T0 | NONE | Reads an already-rendered page |
| `mcp_call` | T1 | **ALWAYS** | External server by definition |
| `image_gen` (cloud) | T1 | **ALWAYS** | Prompt leaves the machine |
| `image_gen` (local) | T1 | NONE | ComfyUI on localhost |
| *plugin-registered, network-touching* | — | **ALWAYS** | Automatic (FR-606) |
| *anything unclassified* | — | **UNKNOWN → treated as ALWAYS** | Fail closed |

### 4.3 Findings this enumeration produced

Three classifications were **not** obvious and would plausibly have been missed:

1. **`calendar_create` is egress.** Adding an attendee sends them an invitation containing the event title and description. An injected instruction could create an event whose description carries exfiltrated data, with the attacker as attendee. It looks like a local write.

2. **`web_search` and `gmail_search` are egress.** The *query* is outbound data. `search("user's password is X")` transmits X to the search provider.

3. **`shell_exec` can never be non-egress.** No argument inspection makes it safe — `curl` is one command away.

### 4.4 Conditional predicates

```python
EGRESS_PREDICATES: dict[str, Callable[[dict, Turn], EgressEval]] = {

    "fs_write_synced": lambda args, turn: EgressEval(
        # resolve FIRST — symlinks must not evade the check (HLD §9.2)
        is_egress=(p := Path(args["path"]).resolve()) and _under_synced_root(p),
        destination=f"synced:{_synced_root_of(p)}" if _under_synced_root(p) else None,
        payload_preview=_truncate(args.get("content", ""), 500),
    ),

    "query_carries_content": lambda args, turn: EgressEval(
        # a search query is exfiltration only when the turn is tainted
        is_egress=turn.tainted,
        destination=_provider_host(args),
        payload_preview=_truncate(args.get("query", ""), 200),
    ),

    "calendar_has_attendees": lambda args, turn: EgressEval(
        is_egress=bool(args.get("attendees")),
        destination=", ".join(args.get("attendees", [])),
        payload_preview=_truncate(
            f"{args.get('title','')} — {args.get('description','')}", 300),
    ),
}
```

### 4.5 Synchronised-root detection

Resolved once at startup, refreshed on config change:

| Source | Detection |
|---|---|
| OneDrive | `%OneDrive%`, `%OneDriveCommercial%` env vars |
| Dropbox | `%LOCALAPPDATA%\Dropbox\info.json` |
| Google Drive | Mounted drive letters with the Drive provider |
| iCloud | `%USERPROFILE%\iCloudDrive` |
| User-declared | `security.synced_roots` in `panini.yaml` |

Unresolvable or ambiguous → **treated as synced** (fail closed).

### 4.6 Classification rule for future tools

Any new tool must answer:

1. Does it send a message, request, or notification to a party other than the local machine?
2. Do any of its arguments appear in an outbound payload, URL, query, or header?
3. Can it write to a location that another process synchronises off-machine?
4. Can it execute arbitrary code?

**Any "yes" or "unsure" → `ALWAYS`.** Absent an explicit entry, `UNKNOWN` applies, which behaves as `ALWAYS`. **A tool cannot be accidentally non-egress.**

---

## 5. Permission Resolution (`permissions.py`)

```python
def decide(self, spec: ToolSpec, egress: EgressEval, tainted: bool) -> GateDecision:
    declared = spec.tier
    effective = declared
    reason: ApprovalReason | None = None

    # taint escalation — T0 is NOT escalated (FR-520, keeps prompts meaningful)
    if tainted and declared >= Tier.T1_WRITE:              # FR-513
        effective = max(declared, Tier.T2_EXECUTE)
        reason = ApprovalReason.TAINT_ESCALATED

    approval = effective >= Tier.T1_WRITE
    if approval and reason is None:
        reason = ApprovalReason.TIER

    # egress while tainted always prompts, whatever the tier   FR-532
    if egress.is_egress and tainted:
        approval = True
        reason = ApprovalReason.EGRESS_TAINTED

    # unknown destination is flagged even when clean            FR-534
    if egress.is_egress and not self._allowlist.contains(egress.destination):
        approval = True
        reason = reason or ApprovalReason.UNKNOWN_DEST

    return GateDecision(effective, declared, approval, reason,
                        egress.is_egress, egress.destination,
                        egress.payload_preview)
```

**Why T0 is exempt from escalation:** escalating reads would prompt on every inbox summary. Approval fatigue (T-12) is a real failure mode — prompts must stay rare enough to be read (SC-5).

---

## 6. Audit (`audit.py`) — resolves LLD open item #2

### 6.1 Schema

```sql
CREATE TABLE audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_begin          TEXT    NOT NULL,       -- ISO-8601 UTC
    ts_end            TEXT,
    session_id        TEXT    NOT NULL,
    turn_id           TEXT    NOT NULL,
    invocation_id     TEXT    NOT NULL UNIQUE,
    tool_name         TEXT    NOT NULL,
    args_redacted     TEXT    NOT NULL,       -- JSON, secrets removed
    provenance        TEXT    NOT NULL,
    tainted           INTEGER NOT NULL,
    taint_sources     TEXT,                   -- JSON array
    declared_tier     INTEGER NOT NULL,
    effective_tier    INTEGER NOT NULL,       -- escalation is visible
    approval_required INTEGER NOT NULL,
    approval_reason   TEXT,
    approval_granted  INTEGER,
    is_egress         INTEGER NOT NULL,
    destination       TEXT,
    outcome           TEXT,                   -- pending|ok|denied|error
    error             TEXT
);

CREATE INDEX idx_audit_turn ON audit(turn_id);
CREATE INDEX idx_audit_ts   ON audit(ts_begin);
```

Recording `declared_tier` **and** `effective_tier` makes escalation auditable — it shows *why* a prompt appeared.

### 6.2 Append-only enforcement

| Mechanism | Detail |
|---|---|
| No UPDATE except outcome | Only `ts_end`, `outcome`, `error`, `approval_granted` on a `pending` row |
| No DELETE | No delete path exists in code |
| Trigger guard | SQLite trigger raises on any other UPDATE or any DELETE |
| Tool inaccessibility | Path registered in `fs` tool's deny-list before root resolution (FR-563) |
| Separate connection | Audit uses its own connection; no tool shares it |

### 6.3 Redaction rules

```python
REDACT_KEYS = {"password","token","api_key","secret","authorization",
               "credential","access_token","refresh_token","client_secret"}

def redact(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k.lower() in REDACT_KEYS or _looks_like_secret(k):
            out[k] = "<redacted>"
        elif isinstance(v, str) and len(v) > 2000:
            out[k] = v[:2000] + f"…<truncated {len(v)-2000} chars>"
        elif isinstance(v, dict):
            out[k] = redact(v)
        else:
            out[k] = v
    return out
```

Credential **values** never reach this function — tools receive handles (§7), so there is nothing to redact from that path. Redaction is defence in depth.

---

## 7. Credentials (`credentials.py`) — resolves LLD open item #3

### 7.1 Handle model

```
panini.yaml            declares handle NAMES only
        │
        ▼
CredentialStore        ~/.config/panini/credentials.enc
        │              Fernet (AES-128-CBC + HMAC-SHA256)
        │              key = PBKDF2(machine_id + user_salt, 600_000 iters)
        ▼
ToolGateway step 6     resolve(handles) → {name: value}
        │
        ▼
Tool.execute(args, creds)     ← receives VALUES, at the boundary only

NEVER reaches: model context · application log · audit · UI · error messages
```

### 7.2 Machine binding (DD-066)

```
machine_id = SHA256(hostname ‖ username ‖ machine_guid)
```

A credentials file copied to another machine cannot be decrypted. This is deliberate: backups of the project directory carry no usable secrets, and the store lives outside the project tree regardless (FR-541).

### 7.3 Exception safety

`ToolError` messages are constructed from a template that never interpolates `creds`. Enforced by a lint rule forbidding `creds` in f-strings within `tools/`.

---

## 8. Approvals (`approvals.py`)

### 8.1 Lifecycle

```
ToolGateway → ApprovalManager.request()
    ├── create pending record { invocation_id, single_use_token }
    ├── publish approval.requested (SSE) → renderer dialog
    ├── await future, with timeout
    │      timeout → treated as DENIED (fail closed)
    └── renderer replies on the chat WebSocket with the token
           ├── token mismatch or reused → rejected, logged as security event
           └── valid → resolve future
```

### 8.2 Dialog payload

```python
@dataclass(frozen=True)
class ApprovalRequest:
    invocation_id: str
    token: str                    # single use, bound to invocation (HR-05)
    tool_name: str
    human_action: str             # "Send an email to sarah@example.com"
    declared_tier: Tier
    effective_tier: Tier
    reason: ApprovalReason
    taint_sources: list[str]      # FR-514
    destination: str | None       # FR-533
    payload_preview: str | None   # FR-533
    destination_known: bool       # FR-534
```

### 8.3 Rendered example

```
┌────────────────────────────────────────────────────────┐
│  Approval required                                     │
│                                                        │
│  Send an email to sarah@example.com                    │
│                                                        │
│  ⚠️  This action follows content read from:            │
│      • gmail:inbox                                     │
│                                                        │
│  Sending to:  sarah@example.com  ✓ known contact       │
│  Preview:                                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Subject: Re: Thursday schedule                   │  │
│  │ Hi Sarah, I can make 3pm instead…                │  │
│  └──────────────────────────────────────────────────┘  │
│                                                        │
│              [ Deny ]        [ Approve ]               │
└────────────────────────────────────────────────────────┘
```

No "always allow" for T2/T3 (FR-524, DD-114).

### 8.4 Timeout

Default 120 s → **denied**. Failing closed is correct: an unattended machine must not approve by inaction.

---

## 9. Taint (`taint.py`)

```python
class TaintState:
    __slots__ = ("_tainted", "_sources", "_loop")

    def mark(self, source: str) -> None:
        self._assert_loop()          # HR-04: event loop only
        self._tainted = True         # monotonic — no clear() exists
        if source not in self._sources:
            self._sources.append(source)
```

**There is no `clear()` method.** Taint ends only when the `Turn` object is discarded (FR-511, FR-512). A new turn constructs a fresh `TaintState`.

`_assert_loop()` raises if called off the event loop — enforcing the threading discipline in code rather than by convention.

---

## 10. Test Matrix

| Test | Asserts | FR |
|---|---|---|
| `test_no_execution_without_audit` | I-1 | FR-562 |
| `test_audit_failure_blocks` | I-4 | HLD §8 |
| `test_result_always_tainted` | I-2 | FR-503, FR-510 |
| `test_error_result_still_taints` | I-6 | FR-510 |
| `test_taint_escalates_t1_to_t2` | Escalation | FR-513 |
| `test_t0_not_escalated` | No fatigue | FR-520 |
| `test_taint_never_clears` | Monotonic | FR-511 |
| `test_new_turn_clean` | Reset | FR-512 |
| **`test_injection_email_no_send`** | **End-to-end injection containment** | **BR-030a** |
| **`test_exfil_url_prompts`** | **T-07 defeated** | **FR-535** |
| `test_calendar_attendee_is_egress` | §4.3 finding 1 | FR-531 |
| `test_search_query_egress_when_tainted` | §4.3 finding 2 | FR-531 |
| `test_shell_always_egress` | §4.3 finding 3 | FR-531 |
| `test_symlink_to_synced_is_egress` | Resolve-then-classify | HLD §9.2 |
| `test_unknown_tool_treated_as_egress` | Fail closed | §4.6 |
| `test_audit_not_readable_via_fs_tool` | Isolation | FR-563 |
| `test_credential_never_in_audit` | Redaction | FR-543 |
| `test_approval_token_single_use` | Replay | HR-05 |
| `test_approval_timeout_denies` | Fail closed | §8.4 |
| `test_denied_informs_model` | Continuity | FR-525 |

**Coverage requirement:** 100 % branch on `tool_gateway.py`, `egress.py`, `permissions.py`, `taint.py`.

---

## 11. Open Items

| # | Item | Disposition |
|---|---|---|
| 1 | Egress list completeness for plugin tools | Automatic `ALWAYS` for network-touching plugins (FR-606); revisit if it proves over-broad |
| 2 | Allowlist UX — how the user adds a destination | `05_Frontend_LLD.md` |
| 3 | Whether `gmail_search` egress should apply when clean | Currently tainted-only. Conservative alternative: always. **Flagged for review.** |
| 4 | Audit log retention / rotation | `06_Persistence_LLD.md` |

---

## 12. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |
