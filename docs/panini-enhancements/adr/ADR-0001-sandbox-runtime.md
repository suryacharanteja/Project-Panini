# ADR-0001: The sandbox runtime is PANINI's job, not the person's

| Field | Value |
| --- | --- |
| Status | Accepted (2026-10-03) |
| Item | V2-11 sandbox computers |
| Decides | Which runtime hosts PANINI's sandbox computers, and who starts it |

## Context

V2-11 first shipped with a manual step: the person had to start Docker Desktop before PANINI's sandbox
computers could run. The owner asked whether that is the right design. It is not: an assistant that needs its
user to operate infrastructure has leaked an implementation detail into the product.

### How OpenAI products isolate agents (the pattern we are measured against)

| Product | Isolation | What the user does |
| --- | --- | --- |
| Codex CLI (local) | The operating system's own sandbox (Seatbelt on macOS, Landlock/seccomp on Linux, a native sandbox on Windows), with approval modes from read-only to full access | Nothing: no extra runtime to install or start |
| Codex cloud, ChatGPT agent | A hosted container or virtual computer per task | Nothing locally |
| Agents SDK computer use | A pluggable computer environment (a local browser, a container, a hosted VM) behind one interface | Chooses an environment when building the agent |

Three principles follow:

1. **The runtime is invisible.** The product starts, checks and recovers it; the user never runs it by hand.
2. **One interface, several environments.** The agent sees the same tools whatever hosts them.
3. **Approvals cover effects, not plumbing.** The person approves what the agent does, and at most once that
   PANINI may start supporting software.

### Options

| Option | Isolation | Weight and licence | Fit |
| --- | --- | --- | --- |
| Docker Desktop (OCI) | Container per computer, its own volumes, `cap_drop: ALL`, no-new-privileges | Heavy; free for personal use, education and small businesses, paid for larger companies | Works today; OpenBot images are pinned and built |
| Podman Desktop / Podman machine (OCI) | Same containers, daemonless | Lighter; Apache-2.0, no licence threshold | Same images and compose file; engine differs |
| Plain WSL2 distribution per computer | All distributions share one utility VM and kernel; Windows drives and interop are on by default | Light | Weaker isolation than containers; would also need PANINI to replace the supervisor |
| Windows Sandbox | Strong (disposable VM) | Windows Pro/Enterprise only; not persistent; no API for a long-lived agent computer | Poor fit for persistent computers |
| Native Windows process sandbox (Codex style) | Restricted token, job object, workspace ACLs | No install | Right for host *commands*, not for a computer with a browser; a half-secure one is worse than none |
| Hosted computer (cloud VM) | Strong | No local runtime; costs money; data leaves the PC | A later provider behind the same interface |

## Decision

1. **OCI images stay the sandbox format.** The pinned OpenBot computer and hardened supervisor run unchanged on
   Docker Desktop or Podman, and the same images can run on a hosted provider later.
2. **PANINI owns the runtime lifecycle** (`backend/hands/sandbox_runtime.py`):
   - detects installed engines (`PANINI_SANDBOX_ENGINE=auto|docker|podman`; auto prefers Docker Desktop, then
     Podman);
   - starts the engine when a sandbox is needed (Docker Desktop's app, or `podman machine start`) and waits for it;
   - asks once (`PANINI_SANDBOX_AUTOSTART=ask`, the default) through the normal approval flow, remembers the answer
     in the DPAPI secret store, and can be set to `on` or `off`;
   - starts the supervisor with `docker|podman compose` and the right engine socket, and waits for its health
     check;
   - never builds images in the middle of a task: building is a setup step.
3. **One-click setup in Admin → Computers:** tokens, engine, image build (first time), supervisor, health, with
   progress shown. The owner's click is the consent.
4. **Nothing falls back to this PC unsandboxed.** If no engine is installed, the native sandbox (5) takes over
   shell, files and browser. Work never runs on the host as the person.
5. **Native Windows sandbox, Codex style** (`backend/hands/native_sandbox.py`, amended 2026-10-03): used when
   `PANINI_SANDBOX_ENGINE=native`, or when `auto` finds no engine. Each computer gets its own AppContainer profile
   (Low integrity, no capabilities, so no network) and its own workspace folder, the only folder granted to it.
   Commands run with `cmd.exe` under a job object that ends the whole process tree at the timeout or when the
   command ends, caps per-process memory and the number of processes, and applies UI restrictions. The same
   permissions, pause and revocation checks apply as for container computers.
   **Its browser** (`backend/hands/native_browser.py`, amended 2026-10-03, owner-approved "use both"): Chrome and
   Firefox cannot run inside an AppContainer (their crash reporter and child-process pipes are denied), so each
   native computer gets PANINI's isolated Playwright browser: a throwaway headless profile, web pages in Chromium's
   own renderer sandbox, service workers off, `check_url` before every navigation, and Playwright's blocked-origins
   list for clicks and redirects. That list now carries a `:*` form of every private host, because Playwright
   matches ports and a bare `http://127.0.0.1` let `http://127.0.0.1:8000` through (this also fixes V2-08's
   isolated preset). Closing the computer closes its browser and discards the profile.
6. **Still a follow-up:** a hosted computer provider, behind the same interface.

## Consequences

- The person never starts Docker by hand. The first sandbox task (or the setup click) may take longer while the
  engine starts.
- Podman users get the sandbox without Docker Desktop's licence terms. The Podman path is covered by tests with
  fakes; it has not been run live on this PC, which has only Docker Desktop.
- A missing engine is a clear, actionable message in Admin and in the tools.

## Verification

- `backend/tests/hands/test_sandbox_runtime.py`: detection, asking once, `off`, Podman commands and socket,
  build-then-up order, setup phases, and a start that finds the supervisor down and brings the runtime up first.
- Live: images built from the pinned revision on Docker Desktop 29.8, supervisor started by PANINI, and
  `scripts/panini-sandbox-live.py` (isolation, persistence, revocation within 1 s, browsing in the sandbox).
- Live on Podman 5 (rootful WSL machine): PANINI started the supervisor and `scripts/panini-sandbox-live.py`
  passed 4/4.
- Native: `backend/tests/hands/test_native_sandbox.py` (commands run at Low integrity in the workspace, exit codes,
  files round-trip, separate containers per computer, timeout ends the command, memory cap) and the native-mode
  service tests in `backend/tests/hands/test_sandbox.py`.
- Native browser, live: page read and link click on the web, a local server refused, a stale ref refused, files
  and commands in the container, and the browser closed on stop.
