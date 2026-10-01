# The PANINI avatar

The avatar is the living Algorithmic P. It is built from the brand team's
*Algorithmic Interface V2* concepts as a hybrid:

- **Concept 1, Geometric Base:** the body.
- **Concept 2, Cymatic Swarm:** listening.
- **Concept 4, Sutra Ribbon:** speaking.

It follows the Visual Lexicon palette:

- Obsidian and Zinc surfaces;
- Copper `#B87333` for identity;
- Amber reserved for hot states (listening, execution, approval);
- Gold for success;
- no purple.

## States

Every state is driven by real events: task phases, the active capability, confirmations,
the live microphone level and PANINI's own voice level (`avatar/state.ts`, unit-tested).

| State | Shows | When |
| --- | --- | --- |
| Offline | The P deriving itself from the root, with parsing rings | Connecting |
| Idle | Copper core breathing at 12 BPM | Ready |
| Listening | Amber cymatic mandala shaped by your voice; the emblem dims | Microphone live |
| Thinking | Concentric rings counter-rotating | Planning (`preparing`) |
| Working | Core fragments, then a sutra thread from the root to the capability, with pulses travelling. `{ }` for Code; all four anchors for the agent. | Executing |
| Approval | Core still, steady amber halo, thread held | Awaiting your confirmation or input |
| Speaking | A ring of Śiva-sūtra characters orbiting with PANINI's voice | PANINI is talking |
| Success | Gold ring expands, then copper returns | Verified complete |
| Blocked | The thread snaps and the copper dims. Calm, not alarming. | Blocked or failed |
| Muted | Hollow root, slow breath | Microphone muted |

**Small sizes.** In the capsule (and at any size under 72 px), the avatar reduces to the
Visual Lexicon's 4-point node cluster, which stays legible at tray scale.


## The full-screen stage (default view)

PANINI opens as **The Algorithmic Avatar**: a full-screen, JARVIS-style stage laid out as
in the brand's design system (the work area is used, so the taskbar stays visible).

| Area | Contents |
| --- | --- |
| Title bar | "PANINI \| THE ALGORITHMIC AVATAR", the current state and a live clock |
| 1. The Lexicon (telemetry), left | A live JetBrains Mono stream of runtime events, plus gateway health per capability |
| 2. Engine (Aṣṭādhyāyī), centre | The large avatar, with a copper dust field, the Dhatu ring and the state caption (for example "STATE 1 \| LISTENING — Listening ripples, not bars") |
| 3. Capabilities (Vritti), right | Search, Browser, Desktop and Code cards with the design system's line-art icons (radiating node, globe, window cascade, derivation tree) and Devanagari subscripts |
| Dock, bottom | Typed instructions, Talk / Mute / Stop, and the conversation with steps, results and approvals |
| Backdrop | A ghost network and slowly drifting Śiva-sūtra columns |

- **Threads:** sutra threads route from the core to the real on-screen position of the
  active capability card, and the card lights amber while it works.
- **Cards:** clicking a card chooses that capability for your next instruction.
- **Views:** switch between the stage and the compact card with the ⤢ / ⤡ button in the
  title bar, or press **Esc** to go to the compact card. The capsule and tray modes are
  unchanged.
- **Small screens:** below 1100 px wide, the telemetry panel is hidden. Every control
  stays.

## Full-screen delegation moment (opt-in)

This is off by default, following Material's rule that motion should inform without
pulling you away from your work. To turn it on, open the avatar's settings and choose
**Show delegation across the screen**.

When PANINI hands work to a capability:

- A sutra thread flies from the avatar to that capability's corner of the screen, then
  fades after about 1.5 s.
- The corners are Search top-left, Code top-right, Desktop bottom-right and Browser
  bottom-left.
- The overlay is transparent, click-through and never focused.

## Performance and accessibility

- **Rendering:** Canvas 2D with a small perspective projection. There is no WebGL
  dependency, and it is strict-CSP safe.
- **Frame rate:**
  - idle and muted render at about 30 fps;
  - active states render at full rate;
  - nothing renders while hidden.
- **Reduced motion:** the Windows reduced-motion setting, or PANINI's own setting,
  renders a still frame.
- **Status in text:** the status is always shown in words too (heading, status line and
  the button's tooltip), so colour and motion are never the only signal.

## Code and tests

| What | Where |
| --- | --- |
| Engine | `panini-core/apps/shell/src/renderer/avatar/Avatar.tsx`, sharing `panini-core/apps/admin/src/genesis/model.ts` with the Admin home page (one emblem definition) |
| State mapping and its tests | `panini-core/apps/shell/src/renderer/avatar/state.ts`, `panini-core/apps/shell/test/avatar-state.test.ts` |
| Full-screen delegation overlay | `panini-core/apps/shell/src/renderer/overlay.ts`, and `showDelegation` in `src/main/main.ts` |

Visual test pages, run with `npx vite panini-core/apps/shell/test/gallery`:

- `/` shows every state, with `?speaking` adding PANINI's voice;
- `/shell.html` shows the real window with a simulated task.

## Instrument refinement

Tilted orbital layers add depth around the central P and become still under reduced motion.
Telemetry shows the six latest events with earlier activity in an expandable history.
The result panel has more desktop space, the voice action is prominent, and captions
describe runtime state. Capability cards distinguish execution from a pending decision.
Desktop and narrow preview checks use simulated events, not live account workflows.
