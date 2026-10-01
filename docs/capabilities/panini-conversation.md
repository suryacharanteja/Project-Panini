# PANINI conversation and control

## Implemented

- Electron capsule/card, typed instructions, capability/target selection, tray, remembered placement and optional always-on-top.
- Live voice/WebRTC, captions, microphone selection, mute and push-to-talk.
- One controller lease, bounded event replay, confirmations, duplicate-command handling, Stop/End and runtime supervision.
- The web console shows the live conversation read-only (observer tickets); only the shell can issue commands.
- Code live output views and capability readiness/recovery information in settings.
- Automatic target resolution, bounded multi-capability plans, visible step progress and resumable clarification choices.

## Ownership and prerequisites

Source: `panini-core/apps/shell/`, `backend/panini/`, `backend/routes/panini.py`, and `frontend/public/panini-activity.*`. The shell launches the same runtime as `npm run dev` (gateway 3200, backend 8200 by default) and is its controller. A process already using those ports, including a running `npm run dev`, blocks startup rather than being terminated.

Auto routing and voice require provider credentials. Explicit capability selection bypasses the routing-model step. API credentials saved through the shell are encrypted for the Windows account. Runtime authentication tokens stay outside the renderer.

## Limits and validation

The router produces up to six sequential capability steps and stops when evidence is missing or an outcome is partial. See [the coordinator](../ARCHITECTURE.md#panini-coordinator) for target resolution, continuation and verification. Human microphone/acoustic testing is distinct from synthetic WebRTC tests. Never infer task completion from animations or a model's own assertion.

`npm run panini:test:unified` validates the shell and console on one runtime; `npm run panini:smoke -- --observer` adds the full observer flow. `--voice` adds synthetic media testing. Renderer and companion crash tests are available through the smoke script. Signing and production deployment qualification remain separate release gates.

