# PANINI live project implementation register

## Update: 2026-10-01

- Local Git repository initialized on `main`; private remote, protected CI,
  publisher identity and signed release provenance still need completion.
- Durable projects, approved sequential research plans, restart reconciliation,
  and evidence-gated advancement are implemented and tested. Project context
  and recent verified results carry into later tasks.
- MCP connector calls now create hash-only receipts. Background connector calls
  require an explicit per-tool read-only grant in addition to the MCP hint.
- The Admin build destination was corrected to the gateway's packaged
  `frontend/public/admin` path.
- The 20-case live capability run passed 19 cases; the one failed case was a
  verifier encoding error. After the verifier fix, that case passed on rerun.
  A three-sample, 24-hour-spaced reliability run has started with a 3/3 baseline.
- The release gate verified 3,063 packaged file hashes and correctly refused
  the unsigned development installer. Signing and clean-VM qualification remain.

**Last updated:** 2026-09-27  
**Scope:** `D:\AI\Jev-Rev-PANINI` source, docs, runtime tests, PANINI agent capability architecture, and the PANINI-CLI production-release track. Credentials were not read or reproduced.

## Governing vision

PANINI is the real-time Windows voice/JARVIS assistant. PANINI-CLI is not a separate product named “PANINI-CLI”; it is one first-class PANINI capability beside Desktop/computer-use, Browser and Search.

The release goal is clear: **PANINI-CLI must become a Codex-CLI-class terminal/system agent, exposed as a single coherent capability that PANINI can delegate to for command-line work, installations, validation, process checks, code edits, test runs, long-running terminal sessions and sandboxed machine operations.**

The folder and documentation model must reflect that product model:

```text
PANINI assistant
├─ PANINI Desktop      Windows GUI/computer-use capability
├─ PANINI Browser      browser/web capability
├─ PANINI Search       retrieval/search capability
└─ PANINI-CLI          Codex-like terminal/system capability
   ├─ terminal UI
   ├─ agent loop
   ├─ tool protocol
   ├─ observable command execution
   ├─ durable sessions/replay
   ├─ Jev evaluator support
   └─ PANINI-owned Windows sandbox
```

## Executive status

The codebase already contains a working terminal agent implementation, tool protocol, search-code capability, command execution, session logging, native Windows sandbox prototype, packaged executable path and tests. The source tree is now organized under one PANINI hierarchy: apps in `panini-core/apps/`, capabilities in `panini-core/capabilities/`, and voice packages in `panini-core/voice/`.

The intended product truth is now reflected physically: **PANINI-CLI is the terminal/system capability at `panini-core/capabilities/cli/`**, used directly through the `panini-cli` command and indirectly by the PANINI assistant.

The project is still **not production-release-governed**. There is no `.git` repository under the workspace, so CI provenance, rollback, release tagging and artifact traceability cannot yet be proven. The sandbox is not yet “better than Codex” by evidence; it has a native Windows direction, but needs adversarial validation, packaging gates and measurable guarantees.

## Evidence collected

| Area | Latest verified result |
| --- | --- |
| PANINI-CLI focused tests | `pytest -q -p no:cacheprovider panini-core/capabilities/cli/tests/test_agent_cli.py panini-core/capabilities/cli/tests/test_agent_loop.py panini-core/capabilities/cli/tests/test_agent_server.py backend/tests/test_voice.py backend/tests/test_admin.py`: **67 passed**. |
| Full repository test command | `npm test`: **passed**. |
| Backend tests | **340 passed**, one upstream Starlette deprecation warning. |
| PANINI-CLI / Python agent tests | **100 passed, 1 skipped**. |
| Frontend/browser/voice tests | **96 passed**. |
| Search tests | **95 passed**. |
| Admin tests | **5 passed**. |
| Shell tests | **6 passed**. |
| Type checks / formatting | Prettier and TypeScript checks passed in `npm test`. |
| Version control | **No `.git` directory** at the root or descendants during audit. |
| Deleted obsolete nested source | The old nested CLI/Jev experiment was audited, useful ideas were already covered by current `search_code`/Jev selector design, and the unused nested repo was removed. |

## Current source-of-truth layout

| Folder / file | Product meaning | Current status |
| --- | --- | --- |
| `backend/` | PANINI coordinator and capability routing. | Active. Routes voice/planning to Browser, Search, Desktop and PANINI-CLI style capabilities. |
| `panini-core/capabilities/cli/` | PANINI-CLI runtime, tools, sandbox and tests. | Active canonical capability folder. |
| `panini-core/capabilities/cli/src/panini_cli/agent/` | PANINI-CLI command-line agent loop, terminal tooling, sandbox integration and server. | Active. |
| `panini-core/capabilities/search/` | PANINI Search capability. | Active. Separate capability, not the CLI itself. |
| `panini-core/capabilities/desktop/` | PANINI Desktop/computer-use capability. | Active. Separate GUI/computer-use capability. |
| `artifacts/panini-cli/` | Built/generated PANINI-CLI release artifacts. | Generated output, not source of truth. Needs provenance/signing. |
| `docs/capabilities/panini-cli.md` | PANINI-CLI capability spec. | Updated to reflect the clarified product model. |
| `docs/PANINI-PRODUCT-ARCHITECTURE.md` | Whole-system architecture. | Updated to show PANINI-CLI as one capability under PANINI. |

## Critical findings

### C1 — Source hierarchy is now corrected; release governance is still missing

**Evidence:** runtime code now lives under `panini-core/capabilities/cli/src/panini_cli/...`; shell/admin/search/desktop/voice code now lives under the PANINI hierarchy.  
**Impact:** folder fragmentation is resolved, but release provenance is still not proven without Git/CI/signing.  
**Required action:** create the Git baseline, protected CI and signed artifact provenance before calling this production-ready.

### C2 — PANINI-CLI is not yet Codex-class until actions are observable, durable, replayable and honestly verified

**Evidence:** command execution, tests, sessions and search exist, but there is not yet one uniform receipt model for every action with replay inputs, outputs, hashes, approvals, sandbox state and verification evidence.  
**Impact:** the agent can do work, but cannot yet prove every action with Codex-grade or better auditability.  
**Required action:** make the tool protocol append-only and receipt-based: every command/tool call records request digest, normalized args, workspace snapshot/hash, stdout/stderr digest, exit code, duration, sandbox identity, approval decision and verification result.

### C3 — PANINI-owned Windows sandbox is the right target, but production strength is not yet proven

**Evidence:** native Windows sandbox/probe code exists, and docs now state Docker/Codex must not be required.  
**Impact:** without adversarial tests, clean-VM packaging and escape-resistance checks, we cannot honestly claim “better than Codex sandbox.”  
**Required action:** ship PANINI-owned sandbox as a tested subsystem: disposable workspace, restricted token/user, filesystem write allowlist, process tree cleanup, outbound network policy, environment scrubbing, registry/process guardrails, and clear fallback/error behavior.

### C4 — No release provenance or CI baseline

**Evidence:** no `.git` directory exists in the workspace.  
**Impact:** no rollback, signed release identity, reproducible benchmark, protected main, or auditable production release.  
**Required action:** initialise or attach the proper Git remote, commit a clean baseline, protect main, run CI on Windows and Linux where applicable, and attach every built `PANINI-CLI.exe` to a source commit/SBOM/signature.

### C5 — Self-improvement/Jev loop is still partial

**Evidence:** Jev selector/search support exists, but the stronger loop—Jev as evaluator/router, LLM as proposer, paired task benchmarks, and rejection of cheaper configs that lose solves—is not fully wired as a release gate.  
**Impact:** token optimisation could reduce quality if not measured by verified solves.  
**Required action:** add an evaluation harness that runs paired tasks against baseline PANINI-CLI and candidate configurations, measuring solve rate, token cost, Jev cost, elapsed time, tool count and verification evidence.

## PANINI-CLI production implementation register

| ID | Work item | Priority | Exit criteria | Status |
| --- | --- | --- | --- | --- |
| CLI-00 | Product identity and folder consolidation | P0 | Source lives under canonical `panini-core/capabilities/cli/`; import namespace is `panini_cli`; root-level PANINI app/capability folders are consolidated under `panini/`; tests pass. | Done |
| CLI-01 | Codex-class terminal UX | P0 | Interactive CLI supports clear session start/resume, plans, streamed command output, approvals, cancellations, progress, final evidence summary and recoverable logs. | Open |
| CLI-02 | Observable/durable/replayable tool protocol | P0 | Every tool call produces an immutable receipt with args, hashes, output digest, exit code, approval, sandbox state and verifier result. A session can be replayed/audited without trusting memory. | In progress: hash-chained durable metadata receipts and CLI verification added; full payload replay, independent anchoring, exit-code and verifier evidence remain open. |
| CLI-03 | Long-running terminal/session engine | P0 | PANINI-CLI can manage persistent PowerShell/CMD sessions, installations, prompts, process checks, timeouts, cancellation and cleanup without losing transcript continuity. | In progress: persistent host-scoped terminal sessions are available even when native workspace sandbox is selected; full PTY, hidden prompts and native-isolated interactive sessions remain open. |
| CLI-04 | PANINI-owned Windows sandbox | P0 | `PANINI-CLI.exe` works on a clean Windows machine without Docker Desktop or Codex CLI; sandbox restricts filesystem/process/network scope; escape and cleanup tests pass. | Open |
| CLI-05 | Honest verification layer | P0 | Completion requires concrete checks: tests, version probes, process status, file hashes, app launch checks or explicit reason why verification is impossible. | Open |
| CLI-06 | PANINI Agent routing integration | P0 | Voice assistant can delegate terminal/system tasks to PANINI-CLI with workspace, goal, approval policy, progress streaming and final evidence handoff. | Open |
| CLI-07 | API key and onboarding hardening | P0 | First launch explains missing API key/config clearly, supports secure local setup, never crashes silently and never prints secrets. | Open |
| CLI-08 | Standalone release package | P0 | Signed/hashed `PANINI-CLI.exe` plus manifest, SBOM, icon/branding, clean-VM smoke test and rollback path. | Open |
| CLI-09 | Jev evaluator + LLM proposer loop | P1 | Jev selects where to read/search and evaluates candidates; LLM proposes changes; tests verify results; learned rules cannot bypass safety. | Open |
| CLI-10 | Bounded `search_code` and retrieval core | P1 | Search returns paths, line ranges, source hashes and incomplete flags; full excerpts are requested only when needed; Windows path handling is tested. | In progress |
| CLI-11 | Codex comparison benchmark suite | P1 | Same model/task corpus run against PANINI-CLI and Codex-style baseline; report solve rate, tokens, cost, time, failures and safety outcomes. | Open |
| CLI-12 | Observability dashboard/report | P1 | Local report shows sessions, tool receipts, costs, durations, sandbox events, failures, approvals and verification status. | Open |
| CLI-13 | Documentation and operator runbooks | P1 | `README`, architecture docs, capability spec, sandbox doc and release runbook all describe one coherent PANINI-CLI capability. | In progress |

## Broader platform implementation register

| ID | Work item | Owner | Priority | Exit criteria | Status |
| --- | --- | --- | --- | --- | --- |
| GOV-01 | Create Git baseline and private remote | Project owner | P0 | Clean initial commit, secret review recorded, protected main, CI runs on push | Open |
| GOV-02 | Add release provenance | Platform | P0 | Build embeds commit SHA, dependency hashes, timestamp and SBOM; artifacts reference it | Open |
| OBS-01 | Add structured runtime metrics/events | Backend | P0 | Task/capability/approval/recovery/learning events queryable locally; no secret or prompt-body logging | Open |
| OBS-02 | Define live SLOs and health checks | Backend + QA | P0 | Explicit success, latency, cleanup, recovery and cost budgets; alerts/visible health screen | Open |
| LEARN-01 | Persist task-to-lesson attribution | Harness | P0 | Restart/crash test proves attribution survives and retirement sees it | Open |
| LEARN-02 | Implement low-grade candidate pipeline | Harness | P0 | Low-grade tasks produce reviewed candidates only; no automatic policy activation | Open |
| LEARN-03 | Make learning jobs durable | Harness | P0 | Idempotent SQLite queue, retry/backoff, dead-letter/review state, visible status | Open |
| LEARN-04 | Add calibrated evaluation and canary policy | Harness + QA | P1 | Matched baseline, minimum sample, confidence guard, rollback and audit trail | Open |
| HAR-01 | Split coordinator execution into testable services | Backend | P1 | Planning/execution/evidence/cleanup/learning boundaries independently tested | Open |
| HAR-02 | Add crash/cancel fault-injection matrix | QA | P1 | Tests cover each capability before/during/after effect and learning persistence | Open |
| PERF-01 | Establish performance benchmark suite | QA + Backend | P1 | Reproducible p50/p95 startup/task/memory/recovery results and regression budgets | Open |
| PERF-02 | Measure SQLite contention and data growth | Backend | P1 | Load/recovery report, WAL/backup policy, migration/retention plan | Open |
| REL-01 | Produce machine-readable test/coverage reports | Platform | P1 | `npm test` emits JUnit/JSON summary and coverage thresholds; CI retains reports | Open |
| REL-02 | Define artifact retention policy | Platform | P2 | Only provenance-linked reports retained; releases signed/hashed; generated output excluded from source | Open |

## Recommended delivery sequence

1. **Lock the product shape:** finish CLI-00 and CLI-13 so the repository itself teaches the correct model: PANINI assistant owns a PANINI-CLI capability.
2. **Make actions provable:** implement CLI-02, CLI-05 and OBS-01 before adding more tools. Every action needs a durable receipt and verification.
3. **Make terminal execution strong:** implement CLI-01 and CLI-03 so PANINI-CLI can handle real installations, prompts, process checks and long-running tasks.
4. **Ship the PANINI-owned sandbox:** implement CLI-04 with clean-VM and adversarial tests. No Docker Desktop or Codex CLI dependency is acceptable for the target release.
5. **Wire PANINI voice delegation:** implement CLI-06 so spoken tasks route to PANINI-CLI with progress and evidence back to the assistant.
6. **Benchmark honestly:** implement CLI-11 and reject any cheaper/faster Jev/LLM configuration that loses verified solves.
7. **Package and release:** implement CLI-07 and CLI-08 with API-key onboarding, branding/icon, signing, SBOM and release manifest.

## Production acceptance gates for PANINI-CLI

- A user can install/run PANINI-CLI on a clean Windows machine without Docker Desktop, Codex CLI or undocumented manual setup.
- Missing API key/config results in a clear guided setup path, not a crash or silent failure.
- Every command/tool action is observable, durable, replayable and tied to a verification result.
- The sandbox restricts writes/process/network behavior and proves cleanup of child processes and temporary state.
- Long-running installations and validation flows survive prompts, timeouts, cancellation and resume.
- PANINI voice assistant can delegate to PANINI-CLI and receive progress plus final evidence.
- Benchmark reports compare PANINI-CLI against the baseline on solve rate, elapsed time, tokens/cost, verification quality and safety failures.
- Packaged artifacts are signed/hashed and tied to source revision, dependency manifest and test report.
- Documentation consistently describes PANINI-CLI as one capability under PANINI, not as a fragmented set of unrelated folders.


