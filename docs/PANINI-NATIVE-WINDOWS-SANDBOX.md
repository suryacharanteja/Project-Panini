# PANINI-owned Windows command sandbox

Target: PANINI-CLI must not require Docker Desktop or Codex CLI. Approved
commands should run under a Windows-enforced workspace and no-network boundary
that PANINI packages, operates, probes, and tests itself. Any unavailable or
failed boundary must stop the command rather than fall back to local execution.

## Current Windows default (experimental, fail closed)

`panini_cli.agent.native_probe` calls the Windows
`Experimental_CreateProcessInSandbox` API directly with an AppContainer profile.
It serializes the documented `SBOX` FlatBuffer spec from PANINI code, passes a
scrubbed environment, and deletes its temporary profile. On this Windows 11
host, tests verify allowed writes inside the granted directory and denied
reads, writes, and loopback network access outside it. A one-shot PowerShell
runner is selected by default on Windows and can be selected explicitly with
`--sandbox native` or `PANINI_CLI_SANDBOX=native`. It does not call Docker or
Codex CLI. Unsupported hosts and failed probes stop command execution.
Native-mode startup now fails closed unless fresh probes confirm workspace
writes work while outside reads, writes, and loopback network access fail. The
runner rechecks this boundary before each command. It passes an allowlisted
environment and grants PANINI's own Python runtime read-only for tool execution.
It checks for Windows 11 before loading the experimental API and loads
`processmodel.dll` and related system libraries from System32 only, so a DLL
placed in the workspace cannot shadow the Windows sandbox engine.
Its kill-on-close Job Object now limits each command tree to 64 active processes
and 2 GiB committed memory; a 4 MiB output-file observation limit stops runs
that exceed it, though this is not yet a hard disk quota.
Regression tests also cover a
junction escape and an ordinary child process staying within the boundary.
Adversarial testing found that a pre-existing hard link inside the workspace
could overwrite the same file outside it. PANINI now refuses any workspace
containing hard-linked files or reparse points before launching a native
command. This is fail-closed mitigation, not a complete race-proof boundary:
another host process could still change the workspace after the scan. A
production design needs stronger isolation, such as a disposable copied
workspace with verified change application, before the hard-link release gate
can be closed.

This API is explicitly **experimental** and available only on sufficiently new
Windows 11 builds. PANINI's runner executes approved one-shot commands and
relays bounded output, but it does **not** support interactive terminals or
older Windows versions. Child-process escape testing and broader filesystem
attack tests remain open; one symlink test is skipped on the current host
because symlink creation is unavailable. It is the development default but
must not be advertised as a production-ready release mode. Passing tests on
one Windows 11 machine is not a production security
certification or an installer release.

## Release gates

1. Implement a PANINI-owned runner with a stable Windows fallback for supported
   hosts, plus explicit OS-version capability detection. Do not treat a Job Object
   alone as a sandbox.
2. Enforce workspace-only writes, controlled reads, no network, stripped secrets,
   and command/terminal lifecycle. Test child-process inheritance, symlinks,
   alternate data streams, junctions, UNC paths, and escape attempts.
3. Run commands against disposable workspaces, compare pre/post filesystem
   snapshots, and verify stop, timeout, cancellation, output limits, and cleanup.
4. Bundle the runner in PANINI's installer, sign it, and test clean Windows
   machines without Docker or Codex CLI installed.
5. Only then call `native` production-ready. Keep Docker/Codex adapters optional.

The development machine now selects `native`; `none` remains approval-gated
local execution, not a sandbox. An unsigned local installer candidate is not a
signed production release.

## Local build evidence (2026-09-27)

- PANINI-CLI: 100 passed, 1 skipped (symlink creation unavailable on this host).
- Full repository check: 340 backend, 96 frontend, 95 search, 5 admin, and 6
  shell tests passed; Python lint/format and TypeScript typechecks passed.
- Unsigned Windows NSIS candidate built at
  `artifacts/panini-package/PANINI Setup 0.1.0.exe`.
- Icon-branded standalone `PANINI-CLI.exe` is built at
  `artifacts/panini-cli/PANINI-CLI.exe` and bundled in the installer runtime.
- The package script now requires the frozen `Panini.Backend.exe` to execute a
  scripted native sandbox command successfully before creating the installer.
  It also requires the standalone PANINI-CLI binary to pass the same smoke;
  both run with only Windows system directories on `PATH`, without discovering
  developer-installed Docker, Codex CLI, or PowerShell 7.
- The frozen PANINI-CLI rejects a pre-existing hard-link escape and leaves the
  outside sentinel unchanged; future package builds run this as a smoke gate.
- The frozen PANINI-CLI also runs with a fake `processmodel.dll` placed in its
  working folder, confirming it resolves the sandbox engine from System32.
- Each native command now probes a junction created inside the AppContainer and
  refuses launch unless reads and writes through it to an outside directory are
  denied. Direct adversarial junction tests passed on this Windows 11 host.
- The packaged runtime manifest is refreshed after electron-builder copies and
  transforms executables. The release verifier checks every packaged runtime
  hash and rejects unlisted files; it correctly rejects this unsigned candidate.
- The frozen CLI's `doctor --sandbox native` confirmed filesystem and loopback
  network boundary probes on this Windows 11 build.
- The packaged desktop smoke passed on alternate free ports; the first attempt
  was blocked by another process on the default backend port. The smoke
  reported no UI errors, but optional capabilities were unconfigured.
- Shell TypeScript typecheck passed.

Current local artifact SHA-256 values:

- `PANINI-CLI.exe`: `34DDD4EA992544C5067703E25679602475E0D5C9B0D75CB68F33E6D8BF41AFA0`
- `PANINI Setup 0.1.0.exe`: `66053C9F0E4851544DE6FF698DA381D62A76BD4C9AD8DFDEE0FCBC941E58D102`

The Codex comparison bar includes dedicated lower-privilege users, firewall
enforcement, private-desktop isolation, an ACL/token fallback, and supported
Windows coverage. PANINI's current AppContainer path has not proven parity on
all of those dimensions, so a "stronger than Codex" claim is not yet justified.

This is a local release candidate. It has not been signed, installed on a clean
machine, tested across the supported Windows matrix, or independently security
reviewed. The output-file relay also needs a stronger disk-usage bound.

References: [Microsoft Create Process in Sandbox](https://learn.microsoft.com/en-us/windows/win32/secauthz/createprocessinsandbox),
[Microsoft AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation),
[Microsoft FlatBuffer schema](https://github.com/microsoft/mxc/blob/main/external/windows-sdk/BaseContainerSpecification.fbs).


