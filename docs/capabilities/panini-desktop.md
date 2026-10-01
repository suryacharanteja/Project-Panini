# Desktop

PANINI operates any Windows application on this PC the way a person does: it looks at the window,
moves a visible cursor, clicks, types, scrolls and draws, and checks the result before it says it is done.

## Implemented

- Windows window/control discovery and UI Automation observations.
- Window screenshots and the native graphical action contract: click, double-click, pointer move, drag,
  **draw**, scroll, key combinations, Unicode typing and wait.
- UI Automation invoke, value, selection, toggle and scroll patterns.
- Observation freshness, window identity, foreground and coordinate checks; bounded owned workers and
  authenticated broker IPC.
- A **visible cursor overlay** (below), a readiness/recovery companion and deterministic verification fixtures.
- Registered applications that PANINI can open with confirmation and verify by their native window:
  Notepad, Calculator, **Paint**, File Explorer, Visual Studio Code, **Windows Terminal** and **PANINI-CLI**
  (in a Windows Terminal tab).

## Full access

This PC is PANINI's workspace. **Every application window is a valid target**, including terminals,
command lines, PANINI-CLI and other Electron apps. Two things are deliberately never targeted: PANINI's own
avatar window and its native helpers (that would be self-control), and windows that run elevated (Windows
does not let a normal process send them input).

- To restrict Desktop to certain applications, set `PANINI_DESKTOP_APPS` to a comma-separated list of process
  names (for example `notepad,mspaint`). The default is `*`, everything.
- The agent's file and command tools cover every local drive by default. Set `PANINI_COMPUTER_ACCESS=restricted`
  to confine them to your home folder and configured workspaces. Files that look like secrets (`.env`, keys,
  credentials) are never returned in either mode.
- Safety that stays: PANINI never fills password fields, asks before irreversible actions (sending, deleting,
  paying, installing; or before every action with `PANINI_APPROVALS=all`), and stops at once if you move the
  mouse while it is drawing.

## Drawing

The planner uses one `draw` action for canvas apps such as Paint:

1. It makes sure a drawing tool is active (for example it clicks **Pencil**, using the tool's UI Automation
   identity) and looks at the screenshot to find the empty canvas.
2. It sends up to 40 strokes, each a compact list of waypoints (`xy: [x1,y1,x2,y2,...]`, `smooth`, `closed`).
   Faint amber grid lines, labelled with pixel coordinates, are overlaid on the planning copy of the screenshot
   so positions can be read accurately. Evidence and verification always use the untouched capture.
3. PANINI fits smooth curves through the waypoints (Catmull-Rom), clamps everything inside the window, and
   sends dense paths to the native worker.
4. The worker glides the real pointer to the start of each stroke, presses, moves in small timed steps so the
   application registers a continuous line, and releases, lifting between strokes. If the worker is ever
   stopped mid-stroke, the broker releases the mouse button.
5. It looks at the canvas again, compares it with the goal, and adds or repairs details in a later step.

A drawing gesture may run for up to a minute (ordinary requests: 12 seconds). Very long drawings are
rejected with feedback so the planner draws them in smaller steps. The planner call for a drawing has its own
larger token budget, because a drawing is hundreds of coordinates.

## The cursor overlay

While PANINI works, a large copper cursor moves across the screen where the real pointer goes:

- it glides between targets with easing, and **ripples on every press**;
- it leaves an **amber trail while it drags or draws**;
- a pill beside it says in words what is happening: *Looking at Untitled - Paint*, *Thinking…*,
  *Clicking*, *Drawing 3/8*, *Typing “hello”*, *Waiting for your approval* (with a pulsing ring), *Done*;
- it is click-through, never takes focus, has no title and no automation peer, so it cannot intercept input or be
  targeted, and it flips to stay on screen near the edges;
- it fades a moment after the task ends, and never stays on screen if PANINI goes quiet;
- it follows Windows' reduced-motion setting (no gliding, trail or pulsing; the words remain).

Set `PANINI_DESKTOP_OVERLAY=0` to turn it off (the brief static flash returns). It lives in the broker
(`Panini.Desktop.Broker/CursorOverlay.cs`), which observes the real pointer and button state, so it is always
exactly in step with the input the worker sends.

## Prerequisites and ownership

Windows interactive desktop and the built .NET companions. Auto resolves a unique requested application;
ambiguous windows produce choices. Manual **Desktop** selection is also available. PANINI's model-driven
planning/admission also needs configured OpenAI and AI Gateway credentials. The shell starts its own broker
and supplies per-launch authentication.

Source: `panini-core/capabilities/desktop/` (broker, worker, runtime host) and
`backend/capabilities/desktop.py`, `desktop_visual.py`. The native broker executes; the backend coordinator owns
approval and cancellation. Desktop is available when the PANINI shell runs, because the shell starts the broker
with its per-launch pipe secret.

## Limits and validation

Supported Windows applications and observable postconditions determine what can be verified. Elevated/protected
input, stale windows and password fields are restricted. A drawing is judged visually, so its outcome is
reported as *partial* (a visual match), not as exact verification. Native primitive success does not establish
that model planning works for every application.

Validation:

- `npm run panini:test:paint` starts the real broker and worker, launches Paint, selects the Pencil, draws, and
  checks the canvas changed, no mouse button is left held and the overlay is visible mid-stroke. No model calls.
  Evidence: `artifacts/paint-smoke/`.
- `npm run panini:test:desktop-live` replays "Open Paint and draw a simple elephant" through the real avatar,
  planner and companion (a few cents of model calls; it takes the real mouse). Evidence: `artifacts/desktop-live/`.
- `npm run panini:test:desktop` and `.venv\Scripts\python.exe panini-core/capabilities/desktop/tests/graphical-smoke.py`
  operate their own fixtures. Broader multi-monitor/DPI, RDP, lock/resume and accessibility UAT remain necessary.
