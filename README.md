# PANINI

PANINI is a voice-first assistant for Windows. Its Electron avatar stays on your
desktop, takes spoken or typed instructions, delegates them to capabilities, and
reports verified results in real time:

| Capability     | What it does                                                                             | Details                                     |
| -------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------- |
| **Search**     | Retrieves, ranks and links web sources                                                   | [docs](docs/capabilities/panini-search.md)  |
| **Browser**    | Operates managed Browser Use sessions and explicitly attached signed-in Chrome tabs      | [docs](docs/capabilities/panini-browser.md) |
| **Desktop**    | Operates any Windows application with a visible cursor: clicks, types, scrolls and draws | [docs](docs/capabilities/panini-desktop.md) |
| **PANINI-CLI** | Codex-like terminal/system agent for installs, commands, tests, validation and code work | [docs](docs/capabilities/panini-cli.md)     |

It is **one product with one runtime**. The avatar is the controller. The
**web console** at `http://localhost:3200` shows the same capabilities as
hands-on demos (Compare, Browser, Search) and the live PANINI activity, and it
stays usable while PANINI works. See [architecture](docs/ARCHITECTURE.md).

**How PANINI thinks, acts, asks and tidies up, and every setting behind it, is in one place:
[docs/PANINI-HOW-IT-WORKS.md](docs/PANINI-HOW-IT-WORKS.md).**

```
 PANINI avatar (Electron) ──┐                    ┌─ Search
 Web console (browser)   ───┼─► Runtime :3200 ──►├─ Browser ─► Browser Use/Chromium or JevRev Claw tab
 JevRev Claw (extension) ───┘   gateway+backend  ├─ Desktop ─► Windows companion (named pipe)
                                                 └─ PANINI-CLI ─► terminal/system agent in approved workspaces
```

## Quick start (Windows)

Requires Windows 11 x64, Node.js 22.19+, Python 3.12 and the .NET 10 SDK.

Run `setup-panini.bat` from the repository root. It checks or installs Node.js,
Python and the .NET SDK, installs project dependencies, creates `.env` when
missing, builds the runtime, and checks the built executables. Add your provider
keys to `.env` to enable online features. If Windows installs a prerequisite,
open a new terminal and rerun the batch file to refresh `PATH`.
Quit PANINI completely, including its tray icon, and stop any `npm run dev`
session before rerunning setup. The installer checks for running PANINI processes
before changing dependencies, because Windows locks native modules used by the app.

```powershell
.\setup-panini.bat
npm run panini                # the avatar and the runtime
```

- **Web console:** in the PANINI tray menu choose **Open web console**. It opens
  signed in. Links are one-time; `npm run console` opens a fresh one.
- **Browser extension:** load `panini-core/capabilities/browser/extension/` in `chrome://extensions` (Developer
  mode → Load unpacked). In PANINI choose **Pair browser extension** and type the
  8-character code into the JevRev Claw popup. You pair **once per Chrome profile**;
  it reconnects automatically afterwards. Then attach the tab(s) PANINI may use.
- **PANINI-CLI:** `npm run panini:code` enables the CLI capability for this
  repository, or `npm run panini:code -- --workspace D:\path\to\project` for
  another folder. The same capability runs directly as `panini-cli` (see
  [PANINI-CLI](docs/capabilities/panini-cli.md)).
- **Without the avatar:** `npm run dev` runs the same runtime and prints a console
  sign-in link (Desktop needs the avatar's Windows companion; the rest works).

`Ctrl+Shift+Space` opens PANINI; `Ctrl+Shift+Escape` stops the current task.
Closing the card minimizes to the tray; **Quit PANINI** ends the whole runtime.

## How it behaves

- **Your browser by default.** Browser tasks use the Chrome tab you attached in
  JevRev Claw. PANINI never silently opens another browser: if Chrome is not
  paired, connected or attached, it tells you which step is missing. Say "use a
  separate browser" to run in a managed browser instead.
- **You approve effects.** Clicks, typing, navigation, app launches, file changes
  and commands wait for your confirmation in the avatar. Password fields are never
  filled. A task is reported complete only with verification evidence.
- **Shared, not duplicated.** The console and PANINI use the same executors. One
  browser task runs at a time; if PANINI is using the browser, the console says so
  (and vice versa) instead of either being disabled.
- **Run length.** Browser runs default to _until the task completes · Stop
  anytime_; presets of 25 steps/5 minutes and 100 steps/15 minutes are available.
  Provider calls always have deadlines.

- **An agent with memory.** For open-ended or multi-step goals, anything to
  remember, a skill to save, or work to do later, PANINI's agent plans the work,
  uses the capabilities above step by step, and reports with evidence. It
  remembers your preferences locally (free, on-device embeddings; secrets are
  refused), and you can view, edit, forget or export every memory. See
  [docs/capabilities/agent-harness.md](docs/capabilities/agent-harness.md).
- **A visible hand on your PC.** Desktop works on any application, including terminals and PANINI-CLI. While
  it works, a large copper cursor glides across the screen, ripples on every click, trails while it draws, and
  says in words what it is doing. It can draw: ask it to open Paint and draw an elephant. It stops if you move
  the mouse. See [Desktop](docs/capabilities/panini-desktop.md).
- **Avatar on, or background mode.** Keep the avatar on screen, or let PANINI
  work from the tray: the tray and Windows notifications show what it is doing,
  and the avatar appears only when it needs you. Background tasks survive
  restarts and are never blindly replayed after an interruption.
- **Watches, inbox and connectors.** Ask PANINI to watch something ("tell me when
  the price drops below…"); it checks on a schedule and notifies you only when the
  condition holds. The console's **Agent** page (`/agent`) is where results,
  suggestions, reviews, memories, skills and MCP connectors live.
- **Projects for continuing work.** PANINI Admin's **Projects** page groups background
  tasks under a durable goal and editable context. Pause a project to prevent its
  queued tasks from starting; resume it when ready. Project tasks retain their
  progress and results across restarts. Background work remains read-only and
  uses the same review rules as other scheduled tasks.
- **Agents, phone and sign-in connectors.** Other agents can ask PANINI over A2A
  (each request waits for your approval), you can pair your phone on home Wi-Fi to
  approve decisions and send requests, and remote MCP connectors sign in with
  OAuth. PANINI-CLI adds hooks, interactive terminal sessions, command execution
  and sandboxed validation.

## The PANINI avatar

The floating window's avatar is the living **Algorithmic P**, in the brand's Obsidian and
copper theme:

- it breathes at 12 BPM when idle;
- it ripples as an amber mandala with your voice;
- it fires sutra threads to Search, Browser, Desktop or PANINI-CLI while working;
- it holds an amber halo when it needs your approval;
- it speaks with an orbiting ring of Śiva-sūtra characters.

The full-screen delegation moment is optional (avatar settings). See
[docs/capabilities/avatar.md](docs/capabilities/avatar.md).

## PANINI Admin (the console)

Open **PANINI Admin** from the tray. Everything is configured there, with no code or file edits:

- **Keys and models:** keys are tested before they are saved.
- **Voice, Browser, Search, Desktop and PANINI-CLI:** the CLI toggle and workspace folders live here.
- **Skills, connectors, automations and agents.**
- **Memory, the inbox and run traces.**
- **An audit log** with restore.

The home page is the animated **PANINI Genesis**, with a live health board. Compare, Search and
Browser are under **Playground**. See
[docs/PANINI-ADMIN-PORTAL-PLAN.md](docs/PANINI-ADMIN-PORTAL-PLAN.md). Build it with
`npm run admin:build` (it is part of `npm run build`).

## Web console features

- **Compare** (`/`): Jev and a direct LLM on the same classification tasks, with
  probabilities, latency and estimated cost.
- **Browser** (`/browser`): typed or spoken browser instructions with streamed
  decisions, extension pairing, tab workspaces and a live conversation mode.
- **Search** (`/search/`): natural-language search with source/time filters,
  streamed ranked results, caching, themes and an installable offline shell.
- **Activity** (`/activity`): the live PANINI conversation (read-only).

## Security model

Everything binds to loopback and every client authenticates:

| Client            | Credential                                                                       |
| ----------------- | -------------------------------------------------------------------------------- |
| PANINI avatar     | Per-launch bootstrap token → controller lease with fenced epochs                 |
| Web console       | One-time signed sign-in link → HttpOnly, SameSite=Strict session                 |
| Browser extension | One-time pairing code → device secret; mutual HMAC challenge on every connection |
| Windows companion | Per-launch HMAC-signed, deadline-bound, replay-protected named pipe              |

The backend also refuses console routes that did not come through the signed-in
gateway, so other local programs cannot skip sign-in. Host and Origin checks
block websites and DNS rebinding. Extension pairings are stored in
`%LOCALAPPDATA%\PANINI`; forget one from the extension popup.

## Configuration

One root `.env` (see `.env.example`); existing environment variables win.

| Variable                                         | Needed for                                                         |
| ------------------------------------------------ | ------------------------------------------------------------------ |
| `OPENAI_API_KEY`                                 | Planning, browser proposals, web retrieval, live voice, comparison |
| `AI_GATEWAY_API_KEY`                             | Jev evaluation, browser admission and search ranking               |
| `ASSEMBLYAI_API_KEY`                             | Optional recorded-audio transcription                              |
| `FRONTEND_PORT` / `BACKEND_PORT` / `SEARCH_PORT` | Runtime ports (default 3200 / 8200 / 3230)                         |
| `PANINI_DESKTOP_APPS`                            | Optional: restrict Desktop to these process names (default: all)   |
| `PANINI_DESKTOP_OVERLAY`                         | `0` turns off the on-screen cursor overlay (default on)            |
| `PANINI_COMPUTER_ACCESS`                         | `restricted` confines file and command tools to home and workspaces |

`GET /api/health` reports readiness and which providers are configured, never
key values. Live voice, planning, the browser proposer and Jev evaluation are
separate paid provider calls.

## Verify

```powershell
npm test                     # lint, format, Python/Node/search tests, typechecks (no provider calls)
npm run test:runtime         # the real runtime end to end: sign-in, boundaries, pairing, handshake
npm run panini:build
npm run panini:test:unified  # the avatar and console on one runtime
npm run panini:smoke         # shell smoke; add --observer --voice --browser --desktop --search for live providers
npm run test:live            # live comparison providers (uses credits)
```

Run shell tests one at a time; they share ports. Evidence is written under `artifacts/`.

## Package

```powershell
.venv\Scripts\python.exe -m pip install -r backend/requirements-build.txt
npm run panini:package -- --unsigned
```

This produces an unsigned development installer in `artifacts/panini-package/`.
For the source-versus-install layout and the exact location of PANINI's bundled
Windows sandbox, see [PANINI product architecture](docs/PANINI-PRODUCT-ARCHITECTURE.md).
`npm run panini:release:verify` is the fail-closed promotion gate for packaged
runtime hashes, secret-file exclusion, Authenticode signatures, and clean-VM
qualification evidence. It must fail for an unsigned candidate. A signed
package build runs the hash/signature stage (`-BuildOnly`) first; it is only a
candidate until clean-VM evidence for that exact installer is supplied through
`PANINI_RELEASE_QUALIFICATION_EVIDENCE` and the full gate passes.
The signed build requires `CSC_LINK` (or `WIN_CSC_LINK`) and
`PANINI_RELEASE_PUBLISHER_THUMBPRINT` for the approved publisher certificate;
the verifier runs under PowerShell 7 (`pwsh`) and also requires a trusted
timestamp on PANINI-owned executables.
Release still requires a trusted code-signing identity, clean-machine
install/update/uninstall and Windows sandbox adversarial tests, plus human
microphone/accessibility acceptance testing. A successful local build or smoke
test alone is not a production release.

On a disposable clean Windows 11 VM, run
`pwsh -NoProfile -File scripts/panini-clean-vm-qualify.ps1 -Installer <path> -DisposableMachine`.
It refuses hosts with an existing PANINI installation or shortcut, exercises
first-run CLI and credential onboarding, verifies every installed runtime file
against its release manifest, exercises native sandbox attacks, verifies the
repair install, and uninstalls, then writes JSON evidence. For an unsigned development candidate
only, add `-AllowUnsignedCandidate`; that run cannot satisfy the signing gate.
An upgrade from a previous version and interactive desktop/accessibility
acceptance still require separate evidence.

## Project layout

| Path                                                       | Contents                                                                       |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `panini-core/apps/shell/`                                  | Electron avatar (main process, renderer, tests)                                |
| `frontend/server/`                                         | Runtime composition root, gateway, console sign-in                             |
| `frontend/public/`                                         | Web console pages                                                              |
| `backend/`                                                 | FastAPI backend: coordinator (`panini/`), capability adapters, routes          |
| `panini-core/capabilities/browser/`                        | PANINI Browser capability home and extension adapter                           |
| `panini-core/capabilities/browser/runtime/panini_browser/` | Managed Browser Use/Chromium executor and signed-in-tab extension runtime      |
| `backend/*browser*.py`, `backend/extension_*.py`           | Compatibility shims and coordinator integration for Browser                    |
| `panini-core/capabilities/search/`                         | Search app (SSR) and search worker                                             |
| `panini-core/capabilities/desktop/`                        | Windows companion: broker, worker, runtime host (.NET)                         |
| `panini-core/capabilities/cli/`                            | PANINI-CLI capability: terminal/system agent runtime, tools, sandbox and tests |
| `panini-core/voice/live-spoken-conversation/`              | Live voice conversation package                                                |
| `scripts/`                                                 | Setup, build, packaging and live smoke tests                                   |

## Licenses

Original PANINI code is licensed under the MIT License in [LICENSE](LICENSE).
Code adapted from third-party projects retains its original license and is
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
