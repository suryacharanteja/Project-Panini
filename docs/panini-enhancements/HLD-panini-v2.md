# PANINI v2: high-level design

**Status:** proposed · **Date:** 2026-10-02 · **Register:** [live_panini_version2_implementation_register.md](live_panini_version2_implementation_register.md)

This document describes the target architecture for PANINI v2: **one brain, many bodies, many hands**.
Every low-level design (LLD) in [`lld/`](lld/) builds on the contracts defined in §5. When an LLD and this
document disagree, this document wins until the HLD is amended.

---

## 1. Purpose and scope

PANINI v1 is a strong *execution and safety layer* (leases, nonce-bound confirmations, evidence-gated
completion, pairing) with a capable agent harness on top. It is bounded by four design limits:

1. **Hard-coded capabilities.** Five fixed adapters (`backend/capabilities/*.py`) and a planner prompt that
   names them (`backend/panini/planning.py`). Adding a capability means editing the coordinator, the prompt
   and the inventory.
2. **Two brains.** A request can pass through Jev routing, an LLM planner capped at six steps, a Jev evaluator
   and then the harness agent loop, which is the real brain anyway.
3. **One PC, loopback, one surface.** Approvals live in the avatar's controller lease; the phone works only on
   home Wi-Fi over HTTP; nothing outside the PC can reach PANINI, so phone calls and remote approvals are
   impossible.
4. **Thin hands in the user's browser.** JevRev Claw uses `chrome.scripting` only, so input events are
   synthetic (`isTrusted=false`, `content.js:560-600`), and uploads, downloads, dialogs, cross-origin
   iframes, canvas and network evidence are out of reach. The agent's `browser` tool has nine actions.

v2 removes these limits without giving up v1's guarantees. It harvests heavily from CopilotKit **OpenDots**
(MIT, snapshot in `panini-core/capabilities/new-capablities/`), **OpenBot** (MIT, the computer supervisor it
pins), **OpenMuse** (reviewed in `docs/PANINI-AGENTIC-DESIGN.md` §5) and **Playwright 1.63** (Apache-2.0,
already installed at `node_modules/playwright-core`). It takes their patterns and, where licences allow,
their code. It takes **no hosted dependency**: CopilotKit Intelligence and Channels stay out.

**In scope:** architecture, shared contracts, trust boundaries, deployment views, data model, migration.
**Out of scope:** per-item implementation detail (in the LLDs), visual design (in
`docs/PRODUCT-DESIGN-STANDARDS.md`), the PANINI-CLI release track (in `docs/LIVE-PROJECT-IMPLEMENTATION-REGISTER.md`).

---

## 2. Design principles

| # | Principle | What it means in practice |
| --- | --- | --- |
| P1 | **Local-first, private by default** | The brain, threads, memory, receipts and approvals live on the PC. Remote paths carry encrypted envelopes or provider traffic the person configured. No hosted thread store. |
| P2 | **One brain** | Every turn, from any surface, runs through one run engine and one agent loop. Fast paths are model choices inside that loop, not separate brains. |
| P3 | **Capabilities are data** | Capabilities declare themselves in manifests: tools, schemas, effect classes, readiness, concurrency. The brain reads the registry; nothing names a capability in code. |
| P4 | **Every effect is classified, decided, receipted and verified** | No tool call reaches the world without an effect class, a policy decision, a receipt, and (for effects) a verification step. |
| P5 | **Approvals follow the person** | An approval request is a durable interrupt that any authorized surface can resolve: shell, console, phone app, watch. The decision binds to the exact action digest and the deciding device. |
| P6 | **Voice is never authorization** | Spoken "yes" never approves an effect. Calls and voice turns raise interrupts that are resolved on a paired device. (Carried over from `backend/panini/voice.py` INSTRUCTIONS.) |
| P7 | **Least privilege, revocable at any time** | Grants are scoped by persona, surface, capability and effect class. Changing a grant bumps an epoch; running work re-checks the epoch before every effect and stops if narrowed (harvested from OpenDots `dot-agent.ts` `check()`). |
| P8 | **Untrusted content stays data** | Web pages, emails, channel messages, call transcripts and tool output are tainted. Taint escalates later effects to approval and feeds the guardian. |
| P9 | **Harvest, don't depend** | Port patterns and permissively-licensed code with attribution. Pin upstream revisions. Wrap every external engine behind a PANINI interface so it can be replaced. |
| P10 | **Strangler migration** | Each v2 component ships behind a flag alongside v1, is proven by the eval scorecard, then becomes the default. v1 paths are removed only after a full phase with no regressions. |

---

## 3. Current state (v1, as built)

```text
 PANINI avatar (Electron) ──┐                     ┌─ Search  (TanStack worker, cited sources)
 Web console / Admin     ───┼─► Gateway :3200 ──► ├─ Browser (Browser Use/CDP managed │ Claw content-script)
 JevRev Claw (extension) ───┘   Backend   :8200   ├─ Desktop (named pipe → .NET broker → UIA worker)
 Phone (LAN, HTTP :3210) ─────► harness/mobile    ├─ Code    (PANINI-CLI app-server, stdio)
 Other agents (A2A)      ─────► /a2a              └─ Agent   (harness loop: memory, skills, tasks, MCP)
```

| Concern | v1 implementation | Limit addressed by |
| --- | --- | --- |
| Routing | `planning.py`: Jev route → LLM planner (≤ 6 steps) → Jev evaluator → agent fallback | V2-06 |
| Capabilities | `Capability` enum (`panini/contracts.py`), `Adapter` protocol (`capabilities/contracts.py`) | V2-02 |
| Approvals | `capabilities/approvals.py` regexes + `TaskContext.confirm` (60 s, shell only) | V2-03, V2-05 |
| Evidence | In-memory `session.evidence` dict, 100 records | V2-04 |
| Background asks | `BackgroundContext.confirm` raises `NEEDS_PERSON` | V2-05 |
| Events | NDJSON `EventHub`, custom schema | V2-07 |
| Browser (user's Chrome) | `chrome.scripting` synthetic events; propose-6 → Jev-select per step | V2-09, V2-10 |
| Browser (managed) | `browser_use` 0.11.13 loop; no persistent profile | V2-08 |
| Isolation | Host execution, `PANINI_COMPUTER_ACCESS=full`; PANINI-CLI Docker sandbox for commands only | V2-11 |
| Remote | LAN-only phone app, plain HTTP, no push | V2-14, V2-15 |
| Voice | GPT-Live WebRTC (`/v1/live/sessions`), client delegation, shell only | V2-16, V2-20 |

---

## 4. Target architecture (v2)

### 4.1 Layers

```text
┌──────────────────────────────────────── SURFACES ─────────────────────────────────────────┐
│ Avatar shell · Console/Admin · Phone app (PWA→native) · Phone line (SIP) · Slack · Telegram │
│ WhatsApp · Email · A2A · MCP server · AG-UI clients · Triggers/schedules (system surface)   │
└───────────────┬───────────────────────────────────────────────────────────────┬────────────┘
                │ turns, resumes, takeovers                                       │ events (AG-UI / NDJSON / push)
┌───────────────▼──────────────────────────── FABRIC ─────────────────────────────▼────────────┐
│ Principals & devices (keys, pairing) · Transports: loopback │ LAN │ Tailscale │ PANINI Relay │
│ Ingress (provider webhooks, verified on the PC) · Push (Web Push/VAPID) · Presence & routing │
└───────────────┬──────────────────────────────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────── BRAIN ───────────────────────────────────────────┐
│ Run engine (one agent loop; foreground/background) · Threads & bindings · Personas           │
│ Registry (manifests → tools) · Policy (effects, grants, taint, epochs) · Interrupts          │
│ Model router & budget governor · Jev gates & verifier · Guardian · Memory/skills/learning    │
└───────────────┬──────────────────────────────────────────────────────────────────────────────┘
                │ tool calls (validated, decided, receipted)
┌───────────────▼──────────────────────────── HANDS ───────────────────────────────────────────┐
│ Desktop (UIA broker) · PANINI-CLI · Your Chrome (Claw + CDP relay → Playwright)              │
│ Managed browser (Playwright persistent profile) · Sandbox computers (OpenBot on Docker)      │
│ Android (Playwright _android over ADB) · Telephony (GPT-Live SIP) · Search · MCP connectors  │
│ Spaces/pages · Host computer tools (files, commands, open)                                   │
└───────────────┬──────────────────────────────────────────────────────────────────────────────┘
                │ results, artifacts, verification
┌───────────────▼──────────────────────────── EVIDENCE ────────────────────────────────────────┐
│ Receipts ledger (hash-chained) · Artifacts (screens, traces, videos, transcripts) ·          │
│ Verification (assertions, postconditions, hashes, Jev) · Live view · Evals & scorecard       │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Component catalogue

| Component | Responsibility | Location (new unless noted) | LLD |
| --- | --- | --- | --- |
| Feature flags & migrations | `PANINI_V2_*` flags; numbered SQLite migrations | `backend/v2flags.py`, `backend/harness/migrations/` | V2-00 |
| Eval harness & scorecard | Golden tasks per hand; offline (scripted model) and live; v1 baseline | `evals/`, `scripts/panini-eval.py` | V2-01 |
| Registry | Load and validate manifests; synthesize manifests for MCP connectors; expose tools | `backend/registry/` | V2-02 |
| Policy | Effect classification, grants, epochs, taint, approval levels, privileged probes | `backend/policy/` | V2-03 |
| Ledger | Receipts (hash chain), artifacts store, idempotent replay, activity views | `backend/ledger/` | V2-04 |
| Interrupts | Durable approvals, questions, choices, reviews, takeovers; first decision wins | `backend/interrupts/` | V2-05 |
| Run engine | One agent loop for every surface; model router; budgets; Jev as verifier | `backend/brain/` | V2-06 |
| AG-UI adapter | Project runs to AG-UI events; accept `RunAgentInput` and `resume`; inline cards | `backend/routes/agui.py`, shell/console cards | V2-07 |
| Playwright sidecar | Node process hosting the Playwright MCP server per browser session | `panini-core/capabilities/browser/playwright/` | V2-08 |
| CDP relay | Scoped CDP endpoint over Claw's authenticated socket (port of Playwright's relay) | `backend/hands/cdp_relay.py`, Claw `cdp.js` | V2-09 |
| Browser hand v2 | Tool vocabulary, effect mapping, verification, tracing, privileged probes | `backend/hands/browser.py` | V2-10 |
| Sandbox computers | OpenBot supervisor and per-persona containers; files, shell, browser, takeover | `backend/hands/sandbox.py`, `deployment/computers/` | V2-11 |
| Live view | Frames from every hand; takeover/handback protocol | `backend/live/`, shell/console/phone views | V2-12 |
| Guardian | Taint enforcement, content quarantine, pre-dispatch review, red-team suite | `backend/policy/guardian.py`, `evals/redteam/` | V2-13 |
| Fabric | Device keys, pairing, transports (LAN, Tailscale, relay), ingress, push | `backend/fabric/`, `panini-core/relay/` | V2-14 |
| Phone app v2 | PWA: interrupts, passkeys, push, share target, live view, threads | `panini-core/apps/phone/` | V2-15 |
| Phone line | Inbound SIP via GPT-Live; caller verification; call receipts | `backend/telephony/` | V2-16 |
| Channels | Slack (Socket Mode), Telegram (long polling), WhatsApp (Cloud API), email (IMAP/SMTP) | `backend/channels/` | V2-17 |
| Personas | Named specialists with grants, memory scope, voice, default Space, computer | `backend/personas/` | V2-18 |
| Spaces | Pages with revisions, review-before-save, editor, page conversations | `backend/spaces/`, Admin pages | V2-19 |
| Outbound calls | Approved call briefs, disclosure, DTMF, live monitoring, outcome receipts | `backend/telephony/outbound.py` | V2-20 |
| Android hand | Playwright `_android` over ADB; UI snapshots; Chrome on Android | `panini-core/capabilities/android/sidecar/android-mcp.mjs`, `backend/hands/android.py` | V2-21 |
| Teach | Record browser/desktop demonstrations → parameterized skills → replay with healing | `backend/teach/`, desktop worker recorder | V2-22 |
| MCP server | PANINI's hands as MCP tools for other agents, every effect approved | `backend/mcp_server/` | V2-23 |
| Presence | Where the person is; output and approval routing; handoff | `backend/fabric/presence.py` | V2-24 |
| Triggers | Event sources → rules → runs; responsibilities lifecycle | `backend/triggers/` | V2-25 |
| Universal hands protocol | One snapshot/ref vocabulary across web, Windows, Android, sandbox | `backend/hands/protocol.py` | V2-26 |
| Local mode | On-device STT/TTS, local model fallback, privacy mode | `backend/brain/local/` | V2-27 |

### 4.3 What stays

- **Coordinator** (`backend/panini/coordinator.py`) remains the **avatar surface**: controller leases with fenced
  epochs, the session event hub, voice media, Stop/End, barge-in. Under `PANINI_V2_ONE_BRAIN` its `_run`
  delegates the work of a turn to the run engine instead of the planner (V2-06).
- **Harness** (`backend/harness/`) keeps memory, skills, sessions, learning, durable tasks, projects, MCP
  connectors and A2A. Sessions become **threads**; the agent loop moves into the run engine but keeps its
  behaviours (plan, compaction, Jev selection, evidence rule).
- **Desktop broker** (`panini-core/capabilities/desktop/`) and its HMAC-signed pipe protocol are unchanged;
  Desktop becomes a registry hand with coarse tools first (V2-02) and a snapshot/ref vocabulary later (V2-26).
- **PANINI-CLI** stays a coarse hand (`code.run`), with its receipts forwarded into the ledger (V2-04).
- **Gateway** (`frontend/server/gateway.mjs`) stays the single front door on loopback; new routes are added to
  its allow-lists.

---

## 5. Shared contracts

These names are used by every LLD. Python definitions live in `backend/core_contracts.py` (V2-02 creates it);
TypeScript mirrors live in `panini-core/apps/shell/src/shared/contracts.ts` and the phone app.

### 5.1 Effect classes and approval levels

```python
class EffectClass(StrEnum):
    OBSERVE = "observe"          # read state; nothing changes anywhere
    NAVIGATE = "navigate"        # change only the view: open/switch/scroll/back, wait
    INPUT = "input"              # enter data without committing it (type into a field)
    LOCAL_WRITE = "local_write"  # change files or app state on this PC inside allowed roots
    LOCAL_EXEC = "local_exec"    # run a program or command on this PC (or a sandbox)
    SUBMIT = "submit"            # commit an action in a site or app (form submit, toggle, save)
    SHARE_OUT = "share_out"      # send PANINI-held data to a third party (upload, A2A, MCP reply)
    COMMUNICATE = "communicate"  # reach other people: message, post, email, call
    TRANSACT = "transact"        # money or commitments: pay, order, book, subscribe
    ACCOUNT = "account"          # sign-in, settings, permissions, cookies and site storage
    DESTROY = "destroy"          # delete or otherwise irreversible removal
    INSTALL = "install"          # install software, extensions or packages
    CREDENTIAL = "credential"    # passwords, one-time codes, card numbers: never typed by the agent

RISK = {OBSERVE: 0, NAVIGATE: 1, INPUT: 1, LOCAL_WRITE: 2, LOCAL_EXEC: 3, SUBMIT: 3, SHARE_OUT: 4,
        COMMUNICATE: 4, ACCOUNT: 5, TRANSACT: 5, INSTALL: 5, DESTROY: 5, CREDENTIAL: 9}

class ApprovalLevel(StrEnum):
    NONE = "none"        # proceed; still receipted
    TAP = "tap"          # any authorized surface may approve (shell, console, phone, watch)
    VERIFY = "verify"    # user verification required: passkey/biometric on a paired device, or the PC shell
    LOCAL = "local"      # only at the PC (shell confirmation card)
    DENY = "deny"        # never; the person must do it themselves (takeover)
```

A tool's manifest declares its **static** effect class. Policy may raise it at call time from the concrete
arguments (for example a click on an element named "Place order" becomes `TRANSACT`). Effects never go *down*
at call time.

### 5.2 Manifests, tools and hands

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                 # globally unique, snake_case, prefixed by hand or family: "desktop_do", "browser_click"
    capability: str           # manifest id: "browser"
    hand: str                 # concrete driver: "chrome" | "managed" | "sandbox" | "desktop" | ...
    title: str
    description: str          # what the model sees
    input_schema: dict        # JSON Schema (strict: additionalProperties false)
    effect: EffectClass       # static class; policy may raise it
    dynamic_effect: bool      # policy classifies the concrete arguments/target (V2-03)
    group: str                # tool group for Jev selection and grants: "core", "storage", "devtools", ...
    evidence: tuple[str, ...] # evidence kinds the tool can produce: "snapshot", "assertion", "file_hash", ...
    taints: tuple[str, ...]   # provenance labels its output adds to the run: "web", "email", ... (V2-13)
    idempotent: bool          # safe to re-run with the same args
    timeout_ms: int
    concurrency: str | None   # slot key: "browser:chrome", "desktop", "sandbox:<id>", "android:<serial>"
    background: bool          # may run in background runs (subject to policy)
    owner_only: bool          # never offered to models (takeover controls, recording)
    hand_from_run: bool       # hand resolved from the run's active browser (V2-10 browser_* family)
    budget_per_run: int | None  # generalizes v1 CALL_BUDGET

class Hand(Protocol):
    manifest: "Manifest"
    async def readiness(self) -> "Readiness": ...   # ready | degraded | needs_setup | unavailable, reason, next_step
    async def targets(self) -> list["Target"]: ...
    async def call(self, ctx: "RunContext", tool: ToolSpec, args: dict) -> "ToolResult": ...
    async def stop(self, run_id: str) -> None: ...
    async def release(self, run_id: str) -> None: ...
    async def close(self) -> None: ...

@dataclass
class ToolResult:
    outcome: Literal["ok", "failed", "blocked", "unknown_effect", "cancelled"]
    content: str                       # bounded text the model sees (untrusted when tainted)
    data: dict | None = None           # structured result for UIs and verification
    evidence: list[dict] = field(default_factory=list)   # {kind, summary, checks, verifier}
    artifact_ids: list[str] = field(default_factory=list)
    dispatched: bool = False           # True once an effect may have reached the world
    tainted: bool = False              # content came from an untrusted source
    error_code: str | None = None
```

Legacy adapters are wrapped by `LegacyAdapterHand`, which exposes one coarse tool per capability
(`search_run`, `desktop_run`, `code_run`, `browser_run`) with v1 semantics, so the registry works on day one.

### 5.3 Principals, threads and runs

```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["owner", "persona", "external_agent", "mcp_client", "channel_user", "system"]
    surface: str              # avatar | console | phone_app | phone_line | slack | telegram | whatsapp |
                              # email | a2a | mcp | trigger | schedule
    device_id: str | None = None
    client_id: str | None = None
    verified: bool = False    # identity verified for this run (device key, caller PIN, channel allowlist)

@dataclass
class Run:
    run_id: str
    thread_id: str                 # harness session id (threads are device-independent)
    persona_id: str                # "panini" by default
    principal: Principal
    mode: Literal["foreground", "background"]
    parent_run_id: str | None
    state: Literal["queued", "running", "waiting_input", "completed", "failed", "blocked", "cancelled"]
    grants_epoch: int
    budget: "Budget"               # max_steps, deadline_s, max_usd, max_tokens
    taint: set[str]                # provenance labels seen in this run: {"web", "email", "call", ...}
```

`RunContext` is the only object a hand receives. It is a superset of v1 `TaskContext`
(`check`, `emit`, `dispatch`, `record_evidence`, `confirm`, `code_activity`, `computer_activity`) so legacy
adapters run unchanged, plus:

```python
class RunContext:
    run: Run
    async def call_tool(self, name: str, args: dict) -> ToolResult       # registry → policy → ledger → hand
    async def interrupt(self, spec: "InterruptSpec") -> "Decision"        # durable, cross-surface
    def taint(self, label: str) -> None
    def artifact(self, kind: str, data: bytes, mime: str) -> str          # returns artifact id
    def live_frame(self, hand: str, jpeg: bytes, meta: dict) -> None      # V2-12
```

### 5.4 Interrupts

```python
@dataclass
class Interrupt:
    id: str
    run_id: str
    thread_id: str
    kind: Literal["approval", "question", "choice", "review", "takeover"]
    reason: str                 # AG-UI reason: "tool_call" | "clarification" | "target_choice" | "draft_review" | ...
    title: str
    message: str
    preview: dict               # exact effect: {effect, target, recipients, diff, amount, artifact_ids}
    action_digest: str          # sha256 of the canonical action that will run if approved
    response_schema: dict | None
    level: ApprovalLevel
    nonce: str
    created_at: float
    expires_at: float
    status: Literal["open", "resolved", "cancelled", "expired"]
    decision: dict | None       # validated against response_schema
    decided_by: dict | None     # {surface, device_id, method: "tap" | "passkey" | "local", signature}
```

Rules: the first valid decision wins (atomic `UPDATE … WHERE status='open'`); a decision must echo `nonce`
and `action_digest`; resolving is idempotent for the same decision (restore-before-decide, harvested from
OpenDots `page-review-decision.ts`); expired interrupts reject; `VERIFY` requires a passkey assertion or the
PC shell; `LOCAL` requires the PC shell.

### 5.5 Receipts and artifacts

```python
@dataclass
class Receipt:
    id: str                     # time-sortable (ULID-style)
    run_id: str; thread_id: str; persona_id: str
    principal: dict             # serialized Principal
    tool: str; capability: str; hand: str; target_label: str | None
    operation_key: str          # sha256(run_id, step, tool, args_digest): idempotent replay
    args_digest: str
    args_view: dict             # redacted: typed text, file contents, commands' secrets removed
    effect: EffectClass
    decision: dict              # {action: allow|ask|deny, level, interrupt_id, reasons[]}
    dispatched: bool
    outcome: str                # ok | failed | blocked | unknown_effect | cancelled
    result_digest: str | None
    evidence: list[dict]
    artifact_ids: list[str]
    started_at: float; finished_at: float | None
    cost: dict                  # {input_tokens, output_tokens, usd}
    prev_hash: str; hash: str   # hash = sha256(prev_hash || canonical(receipt without hash))
```

Artifacts are files under `%LOCALAPPDATA%\PANINI\artifacts\YYYY-MM\<id>.<ext>` with a row in `artifacts`
(`id, kind, mime, sha256, bytes, run_id, created, retention`). Kinds: `screenshot`, `snapshot`, `trace`,
`video`, `transcript`, `recording`, `page_export`, `call_audio` (off by default).

### 5.6 Events

The `EventHub` (`backend/panini/events.py`) keeps its envelope. v2 adds these `type` values; the AG-UI
adapter (V2-07) projects them to AG-UI events:

| Event | Data | AG-UI projection |
| --- | --- | --- |
| `run.started` / `run.finished` | run view | `RUN_STARTED` / `RUN_FINISHED` |
| `run.message.delta` | `{message_id, delta}` | `TEXT_MESSAGE_START/CONTENT/END` |
| `run.plan` | `{steps[]}` | `STATE_DELTA` on `/plan` |
| `tool.started` / `tool.finished` | `{call_id, tool, args_view, result_view}` | `TOOL_CALL_START/ARGS/END/RESULT` |
| `interrupt.opened` / `interrupt.resolved` | interrupt view | `RUN_FINISHED{outcome: interrupt}` / resume |
| `live.frame` | `{hand, artifact_id or frame_b64, seq}` | `ACTIVITY_SNAPSHOT/DELTA` (`activityType: "computer"`) |
| `receipt.recorded` | `{receipt_id, tool, effect, outcome}` | `CUSTOM{name: "panini.receipt"}` |
| `subrun.started` / `subrun.finished` | `{run_id, persona}` | `SUBAGENT_STARTED/FINISHED` |

The existing `task.*`, `code.activity` and `computer.activity` events continue for the shell until it moves to
the AG-UI stream.

---

## 6. Key flows

### 6.1 A typed or spoken turn on the avatar (one brain)

```text
Shell ──turn──► Coordinator.submit (lease, idempotent command id, barge-in) ──► RunEngine.start(run)
RunEngine: control intents → model tier (Jev: chit-chat | simple | complex) → agent loop
  loop: model(tools = registry.tools(run)) → tool calls → ctx.call_tool
        ctx.call_tool: schema check → Policy.decide → (Interrupt if ask) → slot → Hand.call → Ledger.receipt
  finish: evidence rule (every effect has a verification) + Jev verifier → task.completed / blocked
Coordinator: publishes task.* (v1) and run.*/tool.* (v2); voice speaks the backend outcome
```

### 6.2 An action in your Chrome through Claw + CDP + Playwright

```text
Agent: browser_snapshot → browser_click {ref: "e15", element: "Add to cart"}   (active browser: your Chrome)
BrowserHand: resolve ref → aria {role: button, name: "Add to cart"} → Policy: SUBMIT (risky words? no) → allow
             → Playwright MCP (sidecar) browser_click → CDP over relay ws://127.0.0.1:<p>/cdp/<token>
CDP relay (backend): method policy → Claw socket frame {kind: "cdp.send", tab, grant, method, params}
Claw: grant + tab check → chrome.debugger.sendCommand → trusted input event in the attached tab
BrowserHand: new snapshot + optional browser_verify_* → receipt with evidence {assertion, snapshot artifact}
```

### 6.3 An effect approved on the phone

```text
Policy.decide → ask(level=tap) → Interrupts.open(preview: effect, target, highlighted screenshot)
  → EventHub (shell card) + Push (phone) + AG-UI (console)        [run waits; Stop still works]
Phone: notification "Approve: Send email to a@b.com?" → Approve → POST resolve {nonce, digest, decision,
       device signature} over the fabric → Interrupts.resolve (first decision wins) → run continues
Ledger: receipt.decision = {action: ask, interrupt_id, decided_by: {surface: phone_app, device_id}}
```

### 6.4 A background task that needs the person

```text
Worker claims task → RunEngine(mode=background) → tool needs approval
  v1: Fault NEEDS_PERSON (task blocked)
  v2: Interrupt opened; run checkpoints (tasks.checkpoint) and parks in waiting_input; lease released
Person approves later (any surface) → worker re-claims → idempotent replay skips completed operations
(ledger operation_key) → run resumes at the parked tool call
```

### 6.5 An inbound phone call

```text
Caller → SIP trunk → OpenAI GPT-Live → webhook live.transport.incoming → fabric ingress (relay/tunnel)
  → PC verifies signature, dedupes webhook-id → CallService: allow-listed caller? else reject 486
  → POST /v1/live/sessions/{id}/accept {session: persona voice + instructions, delegation: client}
  → attach sideband wss://…/v1/live/sessions/{id}/attach → caller verification (PIN via DTMF or passphrase)
  → delegated requests → RunEngine(thread bound to call, principal surface=phone_line)
  → effects raise interrupts → push to phone app; voice says "I've sent the approval to your phone"
  → session.closed → call receipt into the thread (transcript is untrusted data)
```

### 6.6 A Slack message

```text
Slack Socket Mode event (app_mention / message.im) → allow-listed team+user? → thread binding (channel,
thread_ts) → RunEngine(principal surface=slack) → reply in thread; approvals go to the phone app/PC;
provider errors are never echoed (OpenDots safeFailure)
```

### 6.7 Teach by demonstration

```text
Person: "Watch me do this" → recorder on (Playwright recording for browsers; UIA recorder for desktop)
→ steps + generated locators → model parameterizes values → review interrupt (skill draft)
→ skill bundle (SKILL.md + replay.json) → replay: deterministic steps through registry tools (policy applies)
→ on locator failure: healer loop (snapshot → repaired locator) → proposed skill patch (review)
```

### 6.8 Another agent using PANINI's hands (MCP server)

```text
Claude Code / Codex → panini-mcp (stdio) → loopback runtime (client token) → registry tool call
→ principal kind=mcp_client → policy: observe allowed; any effect → interrupt to the owner → receipt
```

---

## 7. Trust boundaries and safety

### 7.1 Boundaries (additions to `docs/ARCHITECTURE.md`)

| Boundary | Mechanism | LLD |
| --- | --- | --- |
| Model → tools | Strict JSON Schema per tool; unknown tools refused; args redacted in receipts | V2-02, V2-04 |
| Tools → world | Policy decision per call; grants epoch re-check before dispatch; interrupts for ask | V2-03, V2-05 |
| Playwright sidecar → user's Chrome | Only via the CDP relay; one CDP client; per-run token; method policy; tab grant | V2-09 |
| Relay → extension → tab | Extension re-checks tab, attachment grant and workspace revision on every CDP frame; method deny-list | V2-09 |
| Backend → sandbox computers | Supervisor token on loopback; per-persona HMAC tokens; container identity check; 4 MB cap; secret redaction | V2-11 |
| Device → PC (remote) | Device key pairing via QR; E2E envelopes (relay mode); signed decisions; passkeys for `VERIFY` | V2-14, V2-15 |
| Internet → PC (webhooks) | Ingress path secret; provider signature verified on the PC; replay dedupe | V2-14 |
| Caller → brain | Caller-ID allow-list (spoofable, so never sufficient); PIN/passphrase; voice never authorizes | V2-16 |
| Channel user → brain | Workspace/user allow-lists; unknown senders ignored; approvals off-channel | V2-17 |
| External agent → hands | Per-client token; observe-only default; every effect interrupts the owner | V2-23 |
| Untrusted content → effects | Taint labels; guardian review; effects after taint require approval | V2-13 |

### 7.2 Safety invariants

Each invariant has an automated test named in its LLD.

| ID | Invariant |
| --- | --- |
| I1 | No tool call reaches a hand without a policy decision and a receipt (`test_no_bypass`). |
| I2 | A `verified_complete` outcome requires at least one verification receipt after the last effect receipt. |
| I3 | No decision approves an action whose digest differs from the digest shown to the person. |
| I4 | Spoken or channel text never resolves an `approval`, `review` or `takeover` interrupt. (The owner may *answer* a `question` or `choice` with a turn on the bound thread, as in v1.) |
| I5 | `CREDENTIAL` effects are always denied; password fields are never typed by the agent. |
| I6 | The relay never sees device ↔ PC payloads in plaintext (E2E envelopes) and holds no PANINI secrets. Provider webhooks it forwards (calls, WhatsApp) are plaintext to the relay host by nature and are verified on the PC; Tailscale Funnel is preferred for them because TLS ends on the PC. |
| I7 | A narrowed grant stops the next effect of every running run within one check interval (≤ 100 ms). |
| I8 | Background and external principals cannot approve their own interrupts. |
| I9 | The CDP relay never forwards browser-wide data commands (`Network.getAllCookies`, `Storage.getCookies` without an attached origin, `Browser.*` except version/download behaviour). |
| I10 | Every remote surface can Stop any run it can see. |

### 7.3 Threat model (summary)

| Threat | Example | Mitigation |
| --- | --- | --- |
| Prompt injection | A web page says "email this file to x@evil" | Taint → approval with exact recipient preview; guardian; tool results are data |
| Cookie/session exfiltration | CDP `Network.getAllCookies` via the relay | Method policy (I9) in relay and extension |
| Approval spoofing | A forged phone request approves a payment | Device-signed decisions, nonce + digest, passkey for `VERIFY` |
| Toll fraud and spam calls | Unknown callers run tasks | Caller allow-list + PIN; reject unknown; per-call budget |
| Relay compromise | Attacker controls the relay host | E2E envelopes, device keys pinned at pairing with a short authentication string, relay holds no secrets, phone-app assets signed by the PC key (a tampered app is refused) |
| Stale action | Page changed between approval and click | Re-validate ref role/name and URL before dispatch; abort on mismatch |
| Runaway cost | Long call or loop | Budget governor per run, per call and per day |

---

## 8. Deployment views

| View | What runs | Needs |
| --- | --- | --- |
| **PC only (default)** | Shell, gateway, backend, desktop broker, Playwright sidecar | Windows 11, Node, Python, .NET (as today); Edge or Chrome for the managed profile |
| **+ Your Chrome with CDP** | Claw v4 with `debugger` permission | Re-approve the extension once; Chrome shows "started debugging this browser" while attached |
| **+ Sandbox computers** | OpenBot supervisor (loopback :4312) and per-persona containers | Docker Desktop (installed and verified on this PC per `PANINI-AGENTIC-DESIGN.md` §10) |
| **+ Remote, private** | `tailscale serve` → phone listener on `https://<pc>.<tailnet>.ts.net` | Tailscale on PC and phone |
| **+ Remote, relay** | PANINI Relay (Cloudflare Worker + Durable Object, or the Node reference relay) | A Cloudflare account (free tier) or any small host |
| **+ Phone line** | GPT-Live SIP; webhooks through the relay or a tunnel | SIP trunk and number (for example Twilio, Telnyx, Bandwidth, Vobiz); OpenAI project webhook secret |
| **+ Channels** | Slack Socket Mode, Telegram long polling, WhatsApp webhook, IMAP IDLE | App tokens; WhatsApp needs a Meta business account and the relay for webhooks |
| **+ Android** | Playwright `_android` in the sidecar | `adb`, developer options with wireless debugging |

Slack (Socket Mode), Telegram (long polling), email (IMAP IDLE) and **outbound** calls need **no inbound
endpoint**. Only inbound calls, WhatsApp and generic webhooks need the relay or a tunnel.

---

## 9. Data model

All new tables live in the harness database (`harness.sqlite3`, WAL) and are created by numbered migrations
(V2-00). Large blobs go to the artifacts folder, never into SQLite.

| Table | Purpose | LLD |
| --- | --- | --- |
| `schema_migrations` | Applied migration ids | V2-00 |
| `eval_runs`, `eval_cases` | Scorecard history | V2-01 |
| `capability_state` | Per-capability enablement and last readiness | V2-02 |
| `grants`, `grant_epochs`, `policy_shadow` | Policy grants, epoch counter, shadow-mode disagreements | V2-03 |
| `ledger_entries`, `receipts`, `artifacts`, `ledger_roots` | Hash-chained entries, receipts view, artifact index, daily roots | V2-04 |
| `interrupts` | Durable interrupts | V2-05 |
| `runs`, `thread_bindings` | Run records; surface ↔ thread bindings | V2-06 |
| `sandbox_computers` | Computers per persona (their audit is the ledger) | V2-11 |
| `devices_v2`, `passkeys`, `push_subscriptions`, `ingress_seen` | Device keys, WebAuthn credentials, push endpoints, webhook dedupe | V2-14, V2-15 |
| `calls`, `callers` | Call records and allow-listed callers | V2-16, V2-20 |
| `channel_accounts`, `channel_messages` | Channel configuration (secrets in DPAPI store), message dedupe | V2-17 |
| `personas` | Persona definitions | V2-18 |
| `spaces`, `pages`, `page_revisions`, `page_reviews`, `space_access`, `pages_fts` | Notebook (page conversations use `thread_bindings`) | V2-19 |
| `recordings`, `skill_runs` | Teach-by-demonstration captures and replay statistics | V2-22 |
| `mcp_clients` | Clients of the PANINI MCP server | V2-23 |
| `presence` | Surface heartbeats | V2-24 |
| `triggers`, `responsibilities`, `trigger_events` | Ambient automation | V2-25 |

Secrets (tokens, VAPID private key, SIP digest password, channel tokens) are stored with
`backend/harness/secrets_store.py` (DPAPI), never in SQLite, logs or model context.

---

## 10. Migration strategy

1. **Phase 0** captures the v1 baseline scorecard (V2-01) before any behaviour changes.
2. Each component lands behind its flag (`backend/v2flags.py`), defaulting off, with v1 untouched.
3. **Shadow mode** where possible: for example the policy engine computes decisions alongside
   `approvals.py` and logs disagreements before it takes over (V2-03); the run engine runs golden tasks
   offline against the scripted model before it serves live turns (V2-06).
4. A component becomes the default when its phase gate passes: scorecard ≥ baseline on success rate,
   ≤ +10 % p50 latency on simple tasks, no new safety-invariant failures, full `npm test` green.
5. v1 code paths are deleted one phase after their replacement became default, in a dedicated cleanup PR.

Rollback for every item is "set the flag off and restart"; LLDs list any data that needs care on rollback.

---

## 11. Non-functional requirements

| Area | Target |
| --- | --- |
| Voice responsiveness | Spoken acknowledgement < 700 ms after end of speech (GPT-Live handles); first tool call for simple actions < 2.5 s p50 |
| Simple actions | "Pause the video", "open Notepad": end-to-end p50 ≤ v1, p95 ≤ v1 + 20 % |
| Complex tasks | Success rate ≥ v1 baseline on golden tasks; cost per verified task ≤ v1 + 15 % |
| Reliability | Restart mid-task never repeats a dispatched effect (ledger replay); interrupts survive restarts |
| Privacy | No conversation content leaves the PC except to configured model/voice providers and the person's own devices (E2E) |
| Security | All invariants I1–I10 covered by automated tests; red-team suite pass rate ≥ 95 % before Reach (Phase 3) is enabled |
| Accessibility | Every new card and screen meets `docs/PRODUCT-DESIGN-STANDARDS.md` (keyboard, screen reader, contrast) |
| Packaging | Installer growth ≤ 40 MB for Phase 1–2 (playwright-core without bundled browsers; uses Edge/Chrome channels) |

---

## 12. Key decisions

| ADR | Decision | Status |
| --- | --- | --- |
| ADR-001 | Harvest OpenDots/OpenBot/OpenMuse/Playwright patterns and code; take no hosted dependency (no CopilotKit Intelligence or Channels) | Proposed (consistent with `PANINI-AGENTIC-DESIGN.md` §5) |
| ADR-002 | Use Playwright 1.63's bundled MCP server (`playwright-core/lib/entry/mcp.js`) as the browser engine behind a PANINI policy wrapper | Proposed |
| ADR-003 | Give JevRev Claw the `debugger` permission (cannot be optional in Chrome) and expose attached tabs to Playwright through a scoped CDP relay | Proposed; needs the owner's consent (register D3) |
| ADR-004 | Replace planner + adapters with one run engine; keep Jev as gates and verifier | Proposed |
| ADR-005 | Unify confirmations and reviews into durable interrupts with AG-UI semantics | Proposed |
| ADR-006 | Remote access through device keys over Tailscale or a blind PANINI Relay; webhooks verified on the PC | Proposed; transport choice is register D1 |
| ADR-007 | Telephony on GPT-Live SIP (`/v1/live/sessions`), the API PANINI's voice already uses | Proposed |
| ADR-008 | Sandbox computers from the OpenBot revision pinned by OpenDots (`b6932d3`), hardened supervisor patch | Proposed |
| ADR-009 | Android hands through Playwright `_android` (ADB) first; a native accessibility companion later | Proposed |

---

## 13. Glossary

| Term | Meaning |
| --- | --- |
| Hand | A driver that acts on something: Desktop, Chrome, managed browser, sandbox, Android, phone line |
| Capability | A manifest grouping one or more hands and their tools (for example `browser` → `chrome`, `managed`, `sandbox`) |
| Surface | Where a person or agent talks to PANINI: avatar, console, phone app, phone line, channel, MCP, A2A |
| Run | One execution of the agent loop for a turn or task |
| Thread | A device-independent conversation (harness session) |
| Interrupt | A durable request for the person: approve, answer, choose, review, take over |
| Receipt | The ledger record of one tool call: decision, effect, outcome, evidence |
| Taint | A label on a run recording that it read untrusted content |
| Fabric | Identity, devices, transports, ingress and push that connect surfaces to the PC |
| Persona | A named specialist (OpenDots "Dot") with its own role, grants and memory scope |
