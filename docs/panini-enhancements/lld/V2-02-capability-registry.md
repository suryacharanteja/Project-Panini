# V2-02: Capability and tool registry

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 1 · P0 · M (4–5 days) |
| Depends on | V2-00 |
| Flag | `PANINI_V2_REGISTRY` (default `false`) |
| Status | Not started |

## 1. Outcome

Capabilities declare themselves in manifests: their tools (with JSON Schemas), effect classes, readiness,
targets, concurrency and prompt guidance. The coordinator, the agent and every surface read one registry
instead of hard-coded enums, prompts and tool lists. Adding a capability (Playwright, sandbox, Android,
phone line) becomes "ship a manifest and a hand" with no edits to the brain.

## 2. Scope and non-goals

- In scope: shared contracts module, manifest schema, registry loader and validator, legacy wrappers for
  the five v1 adapters, manifests for the agent's direct tools (computer, Chrome direct) and harness tools,
  synthesized manifests for MCP connectors, prompt generation, readiness/inventory, concurrency slots,
  capability enablement, Admin read-only view.
- Not in scope: policy decisions (V2-03 plugs into the call pipeline), receipts (V2-04), interrupts (V2-05),
  the run engine (V2-06). This item leaves hook points for them with pass-through defaults.

## 3. Current state

| Touchpoint | Today |
| --- | --- |
| `backend/panini/contracts.py:14` | `Capability` enum: browser, search, desktop, code, agent; `Turn.capability_hint`; `SelectTarget.capability: Literal["browser","desktop","code"]` |
| `backend/capabilities/contracts.py` | `Adapter` protocol (`availability/targets/select/run/stop/release/close`) and `Outcome` |
| `backend/app.py:91-110` | Composition: a dict of five adapters; `AgentAdapter` gets the four others |
| `backend/panini/targets.py` | `inventory(adapters)`, `resolve_target(ctx, adapter, query, instruction)` |
| `backend/panini/planning.py:216-249` | Planner rules text names every capability |
| `backend/harness/agent.py:21, 107-318` | `CAPABILITIES` tuple; static `TOOLS` list (update_plan, use_capability, list_files, read_file, run_command, open, browser, recall, remember, forget, load_skill, create_skill, schedule_task, watch, projects…, use_connector, run_subagents, ask_agent, ask_user, finish); `CALL_BUDGET`; `TOOL_GROUPS` |
| `backend/harness/connectors.py` | MCP servers; `catalog(only)` text; `tool(server, name)` with `read_only` from annotations |

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `src/shared/computer-types.ts` | Zod input schemas per action, `.strict()` objects, path refinements (no absolute, no `..`, no `\0`) | JSON Schema with `additionalProperties: false`; shared refinements as named formats (`relative_path`, `http_url`) validated by the registry |
| OpenDots `src/server/computer-tools.ts` | Tools generated from a schema table; every `execute` calls `check()` first; `human_*` actions filtered out for the agent | Tools generated from manifests; `actor` filter (`owner_only: true`) hides tools from the model |
| OpenDots `src/server/tanstack-tools.ts` | Adapter from tool definitions to the model SDK's tool format | `Registry.model_tools(view)` → OpenAI Responses `function` tools (`strict` when the schema allows) |
| Playwright `lib/tools` MCP capability groups (`core`, `core-input`, `core-navigation`, `core-tabs`, `devtools`, `network`, `pdf`, `storage`, `testing`, `vision`) | Tool groups as the unit of enablement | `group` on every tool; Jev selection and grants operate on groups (generalizes `TOOL_GROUPS`) |
| MCP spec tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) | Effect hints from servers | Mapping to effect classes for synthesized connector manifests (§5.6) |

## 5. Design

### 5.1 Components and files

| File | Purpose |
| --- | --- |
| `backend/core_contracts.py` | HLD §5 types: `EffectClass`, `RISK`, `ApprovalLevel`, `ToolSpec`, `ToolResult`, `Principal`, `Readiness`, `Target` |
| `backend/registry/__init__.py` | `get_registry(app)` |
| `backend/registry/manifest.py` | Pydantic models and loader for `panini.capability/1` |
| `backend/registry/registry.py` | `Registry`: load, validate, tools, model tools, prompt sections, inventory, slots, `call` pipeline |
| `backend/registry/legacy.py` | `LegacyAdapterHand` (wraps v1 adapters) |
| `backend/registry/host.py` | `HostComputerHand`: `computer_list_files`, `computer_read_file`, `computer_run_command`, `computer_open` (moves logic out of `harness/agent.py:_computer`) |
| `backend/registry/chrome_direct.py` | `ChromeDirectHand`: the v1 instant `browser` tool (`BrowserAdapter.direct`) until V2-10 replaces it |
| `backend/registry/harness_hand.py` | Memory, skills, schedules, watches, projects, A2A, connectors as tools |
| `backend/registry/mcp.py` | Synthesizes manifests for each MCP connector server |
| `backend/registry/manifests/*.json` | Built-in manifests (host computer, harness, chrome direct) |
| `panini-core/capabilities/{browser,desktop,search,cli}/panini.capability.json` | Capability-owned manifests (legacy coarse tools now; fine-grained tools in later items) |
| `backend/harness/migrations/0003_capability_state.sql` | Enablement table |
| `backend/routes/harness.py` | `GET /api/harness/registry`, `PATCH /api/harness/registry/{id}` |
| Admin `src/pages/Build.tsx` (or new `Capabilities.tsx`) | Read-only list: manifests, readiness, tools, effects |

### 5.2 Manifest schema (`panini.capability/1`)

```json
{
  "schema": "panini.capability/1",
  "id": "desktop",
  "title": "PANINI Desktop",
  "version": "1.0.0",
  "summary": "Clicks, types, scrolls and draws inside any Windows application with a visible cursor.",
  "entry": "backend.registry.legacy:LegacyAdapterHand",
  "entry_args": {"adapter": "desktop"},
  "hands": [{"id": "desktop", "label": "Windows desktop", "concurrency": "desktop"}],
  "readiness": {"requires_env": [], "probe": true},
  "targets": {"kind": "window", "selectable": true},
  "tools": [
    {
      "name": "desktop_do",
      "hand": "desktop",
      "title": "Do something in a Windows app",
      "description": "Carry out one complete instruction inside a Windows application, for example 'In Paint, draw a simple elephant'. Name the application.",
      "input_schema": {
        "type": "object", "additionalProperties": false,
        "properties": {"instruction": {"type": "string", "maxLength": 5000}},
        "required": ["instruction"]
      },
      "effect": "submit",
      "dynamic_effect": true,
      "group": "core",
      "evidence": ["postcondition", "screenshot"],
      "idempotent": false,
      "timeout_ms": 300000,
      "background": false,
      "budget_per_run": 3
    },
    {
      "name": "desktop_open_application",
      "hand": "desktop",
      "title": "Open an application",
      "description": "Open an installed application by its application_id from the inventory.",
      "input_schema": {"type": "object", "additionalProperties": false,
                        "properties": {"application_id": {"type": "string", "maxLength": 200}},
                        "required": ["application_id"]},
      "effect": "local_exec", "group": "core", "evidence": ["postcondition"],
      "idempotent": true, "timeout_ms": 30000, "background": false
    }
  ],
  "prompt": {
    "principles": [
      "For drawing give only the subject and never prescribe shapes or tools; the desktop planner draws the whole picture itself."
    ]
  }
}
```

Field rules:

| Field | Rule |
| --- | --- |
| `id` | `^[a-z][a-z0-9_]{1,31}$`, unique |
| `entry` | `module:Class`; only modules under `backend.` may be loaded (no code from user folders) |
| `tools[].name` | `^[a-z][a-z0-9_]{2,63}$`, globally unique, prefixed by its hand or capability |
| `tools[].input_schema` | Valid JSON Schema 2020-12; `additionalProperties: false` at the top level |
| `tools[].effect` | An `EffectClass`; `dynamic_effect: true` means policy classifies concrete arguments |
| `tools[].owner_only` | Hidden from models; callable by owner surfaces (takeover controls) |
| `tools[].budget_per_run` | Optional; generalizes `CALL_BUDGET` |
| `tools[].background` | Default `false`; policy (V2-03) still decides |
| `prompt.principles` | Shown only when at least one of the manifest's tools is offered |

Manifest sources, in precedence order (later duplicates of an `id` are rejected, not merged):

1. Capability-owned: `panini-core/capabilities/*/panini.capability.json`.
2. Built-in: `backend/registry/manifests/*.json`.
3. Synthesized: MCP connectors (§5.6) and A2A remote agents (one tool each, `a2a_ask_<name>`).
4. User: `%LOCALAPPDATA%\PANINI\capabilities\*.json`, **declarative only**: `kind: "mcp"` (points to a
   connector) or `kind: "a2a"`. A user manifest with `entry` is rejected.

### 5.3 Interfaces

```python
# backend/registry/registry.py
class Registry:
    def __init__(self, app, *, sources: list[Path], connectors, remotes, clock=time.monotonic): ...
    def load(self) -> list["ManifestError"]                 # validates all; invalid manifests are skipped and reported
    def hands(self) -> dict[str, Hand]
    def tool(self, name: str) -> ToolSpec                   # KeyError → "This tool is unavailable."
    def tools(self, view: "ToolView") -> list[ToolSpec]     # filtered by enablement, readiness, owner_only, view
    def model_tools(self, view: "ToolView") -> list[dict]   # OpenAI function tools (+ meta tools)
    def prompt_sections(self, offered: set[str]) -> list[str]
    async def inventory(self) -> dict[str, dict]            # replaces panini/targets.inventory output shape
    def slot(self, key: str) -> "Slot"                      # async context manager with owner info
    async def call(self, ctx, name: str, args: dict) -> ToolResult   # §5.5 pipeline

@dataclass(frozen=True)
class ToolView:
    capabilities: frozenset[str] | None   # None = all enabled
    groups: frozenset[str] | None         # Jev selection
    surface: str                          # "avatar", "phone_line", ...
    background: bool
    private: bool                         # memory tools offered only when True (v1 rule)
    depth: int                            # sub-agent depth (v1 rule for run_subagents etc.)
```

```python
# backend/registry/legacy.py
class LegacyAdapterHand:
    """One coarse tool per v1 adapter, with v1 semantics: target resolution, adapter.run, release.
    Outcome('verified_complete', ...) → ToolResult(outcome='ok', evidence=[{id,...}]); 'blocked'/'failed'
    map directly; 'unknown_effect' sets dispatched=True."""
    def __init__(self, manifest, adapter): ...
    async def call(self, ctx, tool, args):
        target = await resolve_target(ctx, self.adapter, args.get("target_query", ""), args["instruction"])
        outcome = await self.adapter.run(ctx, args["instruction"], target)
        return to_tool_result(outcome, ctx)
```

### 5.4 Meta tools (token efficiency)

Two tools stay "meta" so the model sees a short catalogue instead of hundreds of schemas:

| Meta tool | Routes to | Effect |
| --- | --- | --- |
| `use_capability(capability, instruction)` | the capability's coarse `*_do`/`*_run` tool | that tool's effect |
| `use_connector(server, tool, arguments)` | `mcp_<server>__<tool>` | the synthesized tool's effect |

Policy and receipts always operate on the **resolved** tool, never on the meta tool. The `capability` enum of
`use_capability` is generated from the registry (ready, enabled, offered) instead of `CAPABILITIES`.

### 5.5 The call pipeline

`Registry.call` is the only path from the brain to a hand (invariant I1). In this item the policy, ledger
and interrupt stages are pass-through hooks; V2-03/04/05 fill them in.

```text
call(ctx, name, args)
  1. resolve meta tool → concrete ToolSpec; refuse unknown/disabled/owner_only (for model callers)
  2. validate args against input_schema (jsonschema, formats); refuse with a model-readable error
  3. budget: budget_per_run (v1 CALL_BUDGET semantics: "Answer now with what you have.")
  4. decision = policy.decide(ctx.run, spec, args)            [V2-03; default allow]
  5. if decision.ask: await ctx.interrupt(...)                [V2-05; default ctx.confirm v1]
  6. receipt = ledger.begin(ctx.run, spec, args, decision)    [V2-04; default no-op]
  7. async with slot(spec.concurrency): ctx.check(); result = await hand.call(ctx, spec, args)
  8. ledger.finish(receipt, result); emit tool.finished
  9. return result (content truncated to OUTPUT_CHARS for the model)
```

### 5.6 MCP connector synthesis

For each server in `Connectors.servers` after `status()`:

| MCP annotation | Effect class | Approval default (V2-03) |
| --- | --- | --- |
| `readOnlyHint: true` | `observe` (result tainted when `openWorldHint` is not false) | none |
| `readOnlyHint: false`, `destructiveHint: true` (or missing) | `destroy` | tap |
| `readOnlyHint: false`, `destructiveHint: false` | `submit` | tap (parity with v1: every non-read-only connector call is confirmed) |

`background: true` only for tools in `server.spec["background_read_only"]` (existing per-tool grant).
Tool names: `mcp_<server>__<tool>` with non-matching characters replaced by `_`; the original names are kept
for the call.

### 5.7 Prompt generation

`Agent.instructions()` keeps PANINI's generic principles (`INSTRUCTIONS` head and capability-neutral
bullets). Capability-specific bullets move into manifests' `prompt.principles` (for example the drawing rule
now in `harness/agent.py:53`, the browser bullet at `:46`, the computer-tools bullet at `:43`). The planner
prompt (`planning.py:216`) builds its capability sentence from `manifest.summary` for ready capabilities.
A snapshot test fixes the generated text so prompt drift is visible in review.

### 5.8 Storage

```sql
CREATE TABLE IF NOT EXISTS capability_state(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
  updated REAL NOT NULL, readiness TEXT);
```

Existing environment switches (`settings.desktop_enabled`, `settings.code_enabled`) remain authoritative in
v1 mode; with the registry on, `capability_state.enabled = 0` additionally hides a capability.

### 5.9 API

| Route | Auth | Returns |
| --- | --- | --- |
| `GET /api/harness/registry` | console | `{manifests: [...], errors: [...], inventory: {...}}` |
| `PATCH /api/harness/registry/{id}` | console | `{enabled}` toggle; audit log entry |
| `GET /api/panini/capabilities` (existing) | shell | unchanged shape, now from `Registry.inventory()` when the flag is on |

### 5.10 Failure modes

| Failure | Behaviour |
| --- | --- |
| Invalid manifest | Skipped; error listed in Admin with the file and the validation message |
| Readiness probe hangs | 10 s timeout (v1 value); state `unavailable`, reason "readiness check timed out" |
| Duplicate tool name | Second manifest rejected |
| Hand raises | Pipeline converts to `ToolResult(outcome="failed", content=<safe message>)`; full error logged |

## 6. Implementation steps

1. `backend/core_contracts.py` with the HLD §5 types; unit tests for enums and dataclasses.
2. Manifest models, JSON Schema validation, loader with precedence and errors.
3. `LegacyAdapterHand` and manifests for browser, desktop, search, cli (coarse tools); `to_tool_result`.
4. `HostComputerHand` and `ChromeDirectHand` (move code out of `harness/agent.py:_computer` / `_browser`
   without behaviour change); harness tools manifest.
5. MCP and A2A synthesis.
6. `Registry.call` pipeline with hook interfaces (`PolicyHook`, `LedgerHook`, `InterruptHook`) and
   pass-through defaults; slots with owner reporting.
7. Agent integration behind the flag: `Agent.tools()` and `Agent._call()` use the registry for hand and
   harness tools; control tools (`update_plan`, `ask_user`, `finish`, `run_subagents`) stay in the agent.
8. Inventory and planner prompt from the registry; `GET/PATCH /api/harness/registry`; Admin view.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Manifest validation (good, bad, duplicates, user `entry` rejected) | `backend/tests/registry/test_manifest.py` |
| Unit | Tool filtering by view (background, private, depth, owner_only, groups) | `backend/tests/registry/test_views.py` |
| Unit | MCP annotation → effect mapping | `backend/tests/registry/test_mcp.py` |
| Unit | Legacy outcome mapping, budgets, meta-tool resolution | `backend/tests/registry/test_legacy.py` |
| Parity | With the flag on, the agent's model tool list equals v1's (names and schemas) | `backend/tests/registry/test_parity.py` |
| Parity | Generated prompt snapshot | `backend/tests/registry/test_prompt.py` |
| Invariant | I1 groundwork: every hand call in the agent goes through `Registry.call` (monkeypatched hands assert the pipeline ran) | `backend/tests/registry/test_no_bypass.py` |
| Eval | Offline scorecard unchanged with the flag on | `scripts/panini-eval.py compare` |

## 8. Acceptance criteria

- [ ] With `PANINI_V2_REGISTRY=true`, all existing backend tests pass and the offline scorecard matches v1.
- [ ] No code outside `backend/registry/` names the five capabilities in a list or prompt (grep check in
      `test_boundaries.py`), except v1 code paths kept for rollback.
- [ ] Admin shows every manifest with readiness and tools.
- [ ] A new capability can be added with a manifest and a hand class and no other edits (proven by a test
      capability in `backend/tests/registry/fixtures/`).

## 9. Rollout and rollback

Default off. Turn on for development after step 7; default on at the Phase 1 gate. Rollback: flag off;
the `capability_state` table is ignored by v1.

## 10. Open questions

- Should `use_capability` stay once fine-grained browser tools exist (V2-10)? Recommendation: keep it for
  desktop, code and search (their planners are inside the hands) and retire `browser` from its enum when the
  Playwright tools are offered.
