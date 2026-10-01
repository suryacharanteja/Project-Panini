# PANINI product architecture

PANINI is a real-time, interactive, voice-first Windows assistant. The product is
not "a CLI" and not "a code editor." It is a coordinator with multiple
capabilities.

```text
PANINI
├─ Voice/avatar shell
│  └─ real-time spoken and typed interaction
├─ Backend coordinator
│  └─ routing, approvals, leases, evidence, memory and settings
└─ Capabilities
   ├─ PANINI Browser
   │  └─ operates managed Browser Use sessions and approved signed-in Chrome tabs
   ├─ PANINI Desktop
   │  └─ observes and operates supported Windows apps
   ├─ PANINI Search
   │  └─ retrieves, ranks and cites web sources
   └─ PANINI-CLI
      └─ Codex-like terminal/system agent for commands, installs, tests and validation
```

## PANINI-CLI's Place

PANINI-CLI is a first-class PANINI capability, similar in category to Codex CLI.
It should own terminal UI, agent loop, tool protocol, command execution,
long-running terminal sessions, sandbox integration, session replay and verified
receipts.

It is not merely a code editor. It can edit code, but its larger job is
command-line agency: installing, validating, checking processes, running setup,
inspecting logs, operating dev servers, applying patches and proving the result.

```text
User voice/text
  -> PANINI coordinator
     -> PANINI-CLI app-server
        -> agent loop
        -> tools
        -> command/terminal runner
        -> sandbox
        -> evidence back to PANINI
```

The repository now uses a single PANINI hierarchy. Applications live under `panini-core/apps/`; capabilities live under `panini-core/capabilities/`; voice packages live under `panini-core/voice/`.

## Source Layout

| Location                                                   | Role                                                                            | Product identity          |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------- |
| `panini-core/apps/shell/`                                  | Electron avatar/window, tray, installer config                                  | PANINI voice/avatar shell |
| `backend/`                                                 | coordinator, routes, capability adapters, settings, evidence                    | PANINI backend            |
| `panini-core/capabilities/search/`                         | search UI/worker and web retrieval pipeline                                     | PANINI Search             |
| `panini-core/capabilities/desktop/`                        | Windows broker/worker/runtime host                                              | PANINI Desktop            |
| `panini-core/capabilities/cli/`                            | terminal/system agent runtime, tools, sandbox and tests                         | PANINI-CLI                |
| `panini-core/capabilities/browser/`                        | browser capability home: managed Browser Use plus extension adapter docs/source | PANINI Browser            |
| `panini-core/capabilities/browser/runtime/panini_browser/` | managed Browser Use and signed-in-tab extension runtime                         | PANINI Browser            |
| `backend/*browser*.py`, `backend/extension_*.py`           | Browser compatibility shims, API integration and coordinator hooks              | PANINI Browser/backend    |
| `scripts/`                                                 | setup, build, package, release gates and smoke tests                            | build system              |
| `artifacts/`                                               | generated outputs only                                                          | not source                |

Generated outputs:

| Location                                          | Meaning                                           |
| ------------------------------------------------- | ------------------------------------------------- |
| `artifacts/panini-cli/PANINI-CLI.exe`             | standalone frozen PANINI-CLI build output         |
| `artifacts/panini-runtime/`                       | combined staged runtime copied into the installer |
| `artifacts/panini-package/PANINI Setup 0.1.0.exe` | Windows installer candidate                       |

## What PANINI-CLI Contains

PANINI-CLI should be understood as this stack:

```text
PANINI-CLI
├─ terminal UI
├─ app-server protocol for PANINI voice/avatar
├─ agent loop
├─ tool protocol
├─ file/read/search/edit tools
├─ command runner
├─ long-running terminal sessions
├─ approval modes
├─ sessions/resume
├─ hooks
├─ sandbox adapters
│  ├─ native PANINI Windows sandbox
│  ├─ docker adapter
│  ├─ codex adapter
│  └─ local/no-sandbox mode
└─ verification receipts
```

Current source for that stack is mostly under
`panini-core/capabilities/cli/src/panini_cli/agent/`. The installed command remains `panini-cli`.

## Sandbox Location

The sandbox is not a separate product folder. It is a PANINI-CLI execution
component currently implemented here:

```text
panini-core/capabilities/cli/src/panini_cli/agent/native_sandbox.py
panini-core/capabilities/cli/src/panini_cli/agent/native_probe.py
```

When packaged, it is bundled into:

```text
resources/runtime/cli/PANINI-CLI.exe
resources/runtime/backend/Panini.Backend.exe
```

Use this command to inspect it without an API key:

```powershell
PANINI-CLI.exe sandbox status
```

## Difference From Codex CLI

Codex CLI is the focused product:

```text
Codex CLI
└─ terminal agent + tool protocol + sandbox + model integration
```

PANINI is the broader product:

```text
PANINI
└─ voice/avatar coordinator + Browser + Desktop + Search + PANINI-CLI + memory + admin
```

PANINI-CLI should compete with Codex CLI inside that broader PANINI platform.
The goal is not to make PANINI look like one flat CLI repo. The goal is to make
PANINI-CLI a clean capability with Codex-class terminal/sandbox behavior, while
PANINI remains the JARVIS-style coordinator.

## Current Gap

The current code already has many PANINI-CLI building blocks: CLI entry point,
agent loop, tool protocol, patching, command execution, sessions, hooks,
terminal sessions, and sandbox probes. The hierarchy cleanup is now complete. The remaining production work is capability depth: durable receipts, replay, stronger terminal sessions, clean-VM packaging, benchmark comparison and a production-grade PANINI-owned Windows sandbox.
