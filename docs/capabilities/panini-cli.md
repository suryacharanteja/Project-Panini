# PANINI-CLI

PANINI-CLI is PANINI's Codex-like terminal capability. It is not the whole PANINI product, and it is not the Electron shell. It is the capability PANINI should delegate to when a task needs shell work: installing packages, checking processes, running tests, editing files, validating setup, starting dev servers, inspecting logs, and doing long-running command-line workflows with evidence.

PANINI itself is the real-time voice/JARVIS system. PANINI-CLI is one of its tools, beside Browser, Desktop and Search.

```text
PANINI voice/avatar
  -> coordinator/backend
     -> Browser capability
     -> Desktop capability
     -> Search capability
     -> PANINI-CLI capability
        -> terminal UI
        -> agent loop
        -> tool protocol
        -> command runner
        -> terminal sessions
        -> sandbox integration
        -> evidence and replay records
```

## Current Implementation

The PANINI-CLI implementation lives at `panini-core/capabilities/cli/`. This is the canonical capability folder in the PANINI product hierarchy.

Important source locations:

| Concern | Current source |
| --- | --- |
| CLI entry point | `panini-core/capabilities/cli/src/panini_cli/agent/cli.py` |
| Agent loop | `panini-core/capabilities/cli/src/panini_cli/agent/loop.py` |
| Tool schemas and file tools | `panini-core/capabilities/cli/src/panini_cli/agent/tools.py` |
| Long-running terminal sessions | `panini-core/capabilities/cli/src/panini_cli/agent/terminals.py` |
| Command runner | `panini-core/capabilities/cli/src/panini_cli/agent/shell.py` |
| Native Windows sandbox | `panini-core/capabilities/cli/src/panini_cli/agent/native_sandbox.py` |
| Sandbox verification probes | `panini-core/capabilities/cli/src/panini_cli/agent/native_probe.py` |
| App-server protocol used by PANINI | `panini-core/capabilities/cli/src/panini_cli/agent/server.py` |
| PANINI backend adapter | `backend/capabilities/code.py` |

The internal Python package is `panini_cli`; the installed user-facing command remains `panini-cli`.

## Relationship To PANINI

PANINI-CLI must be reachable in two ways:

- Directly by a person in a terminal: `panini-cli`, `panini-cli exec ...`, `panini-cli resume`.
- Indirectly by PANINI voice/avatar: spoken or typed requests route through the backend to the PANINI-CLI app-server.

The target behavior is:

```text
User: "Install this dependency, verify it works, and fix any setup issue."
PANINI -> delegates to PANINI-CLI
PANINI-CLI -> plans terminal/file/process work
PANINI-CLI -> runs approved/sandboxed commands
PANINI-CLI -> verifies results with receipts
PANINI -> reports the verified outcome back to the user
```

This is broader than "open VS Code and write code." VS Code/editor automation can become another capability later. PANINI-CLI is for command-line agency.

## Codex CLI Comparison

Codex CLI is a focused terminal agent:

```text
Codex CLI
  -> terminal UI
  -> agent loop
  -> tool protocol
  -> file edits
  -> shell commands
  -> sandbox/approval policy
  -> session context
```

PANINI-CLI should match that category inside the PANINI platform:

```text
PANINI-CLI
  -> terminal UI
  -> app-server protocol for PANINI voice/avatar
  -> agent loop
  -> tool protocol
  -> file edits
  -> shell commands
  -> long-running terminal sessions
  -> sandbox/approval policy
  -> sessions/resume
  -> evidence, receipts and replayable outcomes
```

The main difference is product scope. Codex CLI is the product. PANINI-CLI is one capability inside a larger voice-first assistant.

## Tool Protocol

PANINI-CLI currently exposes these core tools to its agent loop:

- `list_files`
- `read_file`
- `search_files`
- `search_code`
- `write_file`
- `edit_file`
- `apply_patch`
- `run_command`
- `terminal_start`
- `terminal_send`
- `terminal_read`
- `terminal_stop`
- `update_plan`

`search_code` is the Jev-assisted retrieval path. It returns bounded locations with paths, line ranges, source hashes, candidate counts and incomplete flags. When `PANINI_CLI_JEV=true`, Jev reranks candidate locations only. Jev does not edit code and cannot hide lexical matches; the LLM remains the proposer/editor.

PANINI-CLI also has a JEV model router. In the default `auto` model mode, JEV evaluates the request and chooses the cheapest configured OpenAI model that should complete the task correctly in one pass. If JEV is unavailable or not configured, routing fails open to `PANINI_CLI_ROUTER_FALLBACK` or `gpt-4.1`. Explicit human choices still win: `--model gpt-...`, `/model gpt-...`, or phrases such as "use gpt-4.1" bypass the automatic downgrade/upgrade policy.

## Sandbox Model

PANINI-CLI owns command execution policy. Current modes:

| Mode | Purpose | Status |
| --- | --- | --- |
| `native` | PANINI-owned Windows AppContainer one-shot command sandbox | bundled, experimental, fail-closed |
| `docker` | Linux container command/session sandbox | optional adapter |
| `codex` | Delegates to installed Codex CLI sandbox | optional adapter, not product target |
| `none` | Approved local execution without filesystem/network isolation | development/fallback only |

The product target is PANINI-owned sandboxing, without requiring Docker Desktop or Codex CLI. The native sandbox handles one-shot workspace commands and verifies boundaries with probes. When a task needs downloads, installers, system inspection, or a persistent terminal, the agent can request `scope=host` for that action. Host scope runs as the signed-in Windows user with access to that user's files and network. The approval prompt and receipt show the chosen scope. The native sandbox itself still does not support interactive terminals.

Run this without an API key to inspect the bundled sandbox:

```powershell
PANINI-CLI.exe sandbox status
```

After packaging, `npm run panini:cli:smoke` runs the packaged executable in a
temporary workspace and checks both native and host commands, their actual
output, saved sessions, and receipt chains. A scripted model's final text
alone does not pass this gate.

## Direct CLI Usage

```powershell
panini-cli
panini-cli -C D:\proj
panini-cli exec "Install dependencies, run tests, and fix setup issues"
panini-cli resume
panini-cli sessions
panini-cli receipts SESSION_ID
panini-cli doctor
panini-cli sandbox status
panini-cli auth login
```

## Model Configuration

PANINI-CLI does not restrict model names to a fixed enum. Any OpenAI model id
that the configured API key can use may be passed with `--model`, `/model`, or
environment configuration.

The default model is `auto`. In that mode PANINI-CLI asks JEV to choose among
configured candidates before each model turn. The built-in candidate set is:

| Tier | Default candidate | Intended use |
| --- | --- | --- |
| Cheap | `gpt-4.1-nano` | mechanical edits, tiny lookups, simple shell checks |
| Balanced | `gpt-4.1-mini` | routine coding, setup checks, bounded debugging |
| Strong | `gpt-4.1` | harder multi-file work, packaging, sandboxing, ambiguous failures |

Override the candidates without code changes:

```powershell
$env:PANINI_CLI_ROUTER_MODELS = "gpt-4.1-nano:cheap:tiny work,gpt-4.1-mini:balanced:normal work,gpt-4.1:strong:hard work"
$env:PANINI_CLI_ROUTER_FALLBACK = "gpt-4.1"
```

Each candidate is `model_id:tier:description`; supported tiers are `cheap`,
`balanced`, `strong` and `frontier`. JEV itself requires `AI_GATEWAY_API_KEY`.
When that key is missing, the CLI still works with the fallback model.

The model fallback order is:

1. `--model` or `/model`
2. `PANINI_CLI_MODEL`
3. `JEV_REV_CODE_MODEL`
4. `PANINI_AGENT_MODEL`
5. `PANINI_PLANNER_MODEL`
6. `EXTENSION_PLANNER_MODEL`
7. `auto`

The current PANINI source tree uses these model roles:

| Role | Setting | Current default |
| --- | --- | --- |
| CLI/code work | `PANINI_CLI_MODEL`, falling back to `JEV_REV_CODE_MODEL` | `auto`, with `gpt-4.1` fallback |
| Browser/planner proposals | `EXTENSION_PLANNER_MODEL` | `gpt-4.1-2025-04-14` |
| Fast extraction | `PANINI_FAST_MODEL` | `gpt-4.1-mini` |
| Voice realtime | `VOICE_MODEL` | `gpt-live-1` |
| Search web retrieval | source code constant | `gpt-4.1-mini` |
| Legacy comparison demo | source code constant | `gpt-4o-mini` |

## OpenAI Key Setup

PANINI-CLI has its own secure credential path because it must work standalone
from a terminal and as a capability called by PANINI.

On Windows, the primary setup command is:

```powershell
PANINI-CLI.exe auth login
```

That stores the key in the current user's Windows Credential Manager under the
PANINI-CLI target. It never prints the key back. You can check setup with:

```powershell
PANINI-CLI.exe auth status
PANINI-CLI.exe doctor
```

Fallbacks are supported for development and automation: set `OPENAI_API_KEY`,
or place `OPENAI_API_KEY=...` in PANINI's own runtime `.env`. PANINI-CLI
intentionally does not load a workspace `.env`, because project folders often
contain application secrets that should not become the agent credential source.

PANINI Admin should eventually expose the same credential backend as a friendly
settings screen. The product rule is one credential store, two entry points:
Admin for PANINI users, `panini-cli auth login` for standalone CLI users.

Each tool call now appends hash-chained receipt events under PANINI's local
`cli-sessions/receipts/` data directory. The events record the tool, order,
sandbox mode, argument and output hashes, approval, and whether execution
completed or was interrupted. `panini-cli receipts SESSION_ID` verifies the
chain without an API key. Receipts omit raw commands and outputs; the saved
conversation remains the source for those payloads. This provides local
tamper detection for existing events, but it is not an independently anchored
audit trail and cannot prove that a whole file was not deleted or truncated.

Approval modes:

| Mode | Behavior |
| --- | --- |
| `read-only` | no changes or commands |
| `ask` | ask before every change and command |
| `edits` | apply file edits without asking; commands still ask |
| `auto` | approve everything; disposable workspaces only |

For fast UAT inside an interactive PANINI-CLI session, run:

```text
/power
```

This switches the current session to `auto` approval mode. For a persistent
terminal environment, set `PANINI_CLI_APPROVAL_MODE=auto` before launching the
CLI.

The terminal UI presents command actions as readable cards instead of raw shell
logs. Each command card shows the access level, what that access means, the
working folder, and the exact command. Host access is labeled as `Windows host
access` so local-machine tasks such as Downloads, RAM/storage, processes and
installed tools are visually distinct from workspace-sandbox commands.

## Production Target

PANINI-CLI should become the primary local-machine action capability for PANINI. The release bar is:

1. First-class capability identity: docs, UI and backend call this PANINI-CLI, not merely a code editor.
2. Durable sessions: every action observable, replayable and resumable.
3. Strong shell execution: one-shot commands and long-running interactive terminal sessions.
4. PANINI-owned Windows sandbox: no Docker/Codex requirement for the production default.
5. Honest verification: success requires command/file/process evidence, not model claims.
6. Voice delegation: PANINI can route suitable tasks to PANINI-CLI and stream progress back.
7. Packaging: `PANINI-CLI.exe` ships inside the PANINI installer and works standalone.

## Local Machine Tasks

PANINI-CLI is not only a repo/file editor. It is PANINI's command-line capability
for the user's Windows machine. Requests such as "check downloads folder",
"show RAM and storage", "what process is using this port", "is Node installed",
or "validate this installation" should be treated as local-machine tasks.

For these requests PANINI-CLI now gives the agent explicit local-machine hints:

- Windows known folders such as Downloads, Desktop and Documents are inspected
  with host-scoped PowerShell commands, not workspace-only file tools.
- RAM, storage/ROM, CPU, GPU, process, port and installed-tool questions are
  answered from real host evidence such as `Get-CimInstance`, `Get-ComputerInfo`,
  `Get-PSDrive`, `Get-Volume`, `Get-Process` and equivalent commands.
- The auto model router applies a floor for local-machine tasks so JEV cannot
  choose the cheapest nano-tier model for host/system inspection.

## Verification

Offline:

```powershell
npm test
```

Focused CLI tests:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider panini-core/capabilities/cli/tests
```

Packaging:

```powershell
npm run panini:package -- --unsigned
```

