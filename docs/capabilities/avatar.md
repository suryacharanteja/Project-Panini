# The PANINI avatar

The avatar is the living Algorithmic P. It is built from the brand team's
_Algorithmic Interface V2_ concepts as a hybrid:

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

| State     | Shows                                                                                                                                        | When                                |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| Offline   | The P deriving itself from the root, with parsing rings                                                                                      | Connecting                          |
| Idle      | Copper core breathing at 12 BPM                                                                                                              | Ready                               |
| Listening | Amber cymatic mandala shaped by your voice; the emblem dims                                                                                  | Microphone live                     |
| Thinking  | Concentric rings counter-rotating                                                                                                            | Planning (`preparing`)              |
| Working   | Core fragments, then a sutra thread from the root to the capability, with pulses travelling. `{ }` for Code; all four anchors for the agent. | Executing                           |
| Approval  | Core still, steady amber halo, thread held                                                                                                   | Awaiting your confirmation or input |
| Speaking  | A ring of Śiva-sūtra characters orbiting with PANINI's voice                                                                                 | PANINI is talking                   |
| Success   | Gold ring expands, then copper returns                                                                                                       | Verified complete                   |
| Blocked   | The thread snaps and the copper dims. Calm, not alarming.                                                                                    | Blocked or failed                   |
| Muted     | Hollow root, slow breath                                                                                                                     | Microphone muted                    |

**Small sizes.** In the capsule (and at any size under 72 px), the avatar reduces to the
Visual Lexicon's 4-point node cluster, which stays legible at tray scale.

## Conversation-first workspace (default view)

The workspace makes the conversation, current task feedback, and instruction composer
primary. A smaller Algorithmic P remains alongside the work, with plain-language status
and a visible explanation of its voice action.

- Tools and runtime diagnostics are collapsed disclosures. Automatic tool selection remains available.
- Status captions describe listening, planning, work, required input, and verified results.
- Approval copy refers to the current request; it does not promise that all background work is stopped.
- Window controls have visible labels. Compact view, bubble mode, F11, Esc and Ctrl+H remain available.
- At narrow widths the workspace stacks, keeping conversation and composer before the avatar.
- Background ornamentation is removed from the default workspace. Copper and the geometric P retain PANINI's identity.

Visual QA uses the real renderer with simulated events; it is not evidence of real app workflow success.

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

| What                           | Where                                                                                                                                                           |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Engine                         | `panini-core/apps/shell/src/renderer/avatar/Avatar.tsx`, sharing `panini-core/apps/admin/src/genesis/model.ts` with the Admin home page (one emblem definition) |
| State mapping and its tests    | `panini-core/apps/shell/src/renderer/avatar/state.ts`, `panini-core/apps/shell/test/avatar-state.test.ts`                                                       |
| Full-screen delegation overlay | `panini-core/apps/shell/src/renderer/overlay.ts`, and `showDelegation` in `src/main/main.ts`                                                                    |

Visual test pages, run with `npx vite panini-core/apps/shell/test/gallery`:

- `/` shows every state, with `?speaking` adding PANINI's voice;
- `/shell.html` shows the real window with a simulated task.
