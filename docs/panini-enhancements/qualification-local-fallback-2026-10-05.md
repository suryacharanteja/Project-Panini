# V2-27 local fallback development qualification — 2026-10-05

V2-27 remains in progress. This is a local development checkpoint, not a production or full
offline release. Red-team remains disabled and deferred.

## Implemented

- A missing cloud key starts the CLI model locally; a transient cloud error switches to local
  with a bounded cloud attempt. Successful cloud planner replies still return normally.
- The coordinator validates local plans against its schema, live capability readiness and the
  installed-app catalog. A local route requires a bounded local admission decision. Missing or
  malformed decisions stop execution; a failed offline step cannot recover through the
  unqualified agent.
- A catalog-grounded intent handles an explicit short request to open an installed app and type
  draft text. It never adds a save step. More complex requests still go through model planning.
- Fallback desktop work uses the semantic UI Automation path. Candidate actions use local
  admission when AI Gateway is absent or fails; missing admission stops before input. App-launch
  verification requires a newly observed window.

## Evidence

- `npm test`: 1,297 backend tests; 7 scorecard tests; 16/16 scripted offline cases; 125 CLI
  tests with one skipped; frontend, phone, search, Admin, shell, formatting and TypeScript gates
  passed. Five existing Python warnings remained. The final route and launch changes then passed
  a focused 56-test suite and Ruff checks.
- `scripts/panini-local-route-probe.py` with `qwen2.5:7b-instruct-q4_K_M`: the general local
  planner first invented command syntax in plan fields, then made schema-invalid retries. It
  failed closed with `INVALID_PLAN` in 16.391 seconds. After adding the narrow catalog intent,
  the same no-action probe returned two grounded desktop steps in 0.015 seconds. The latest
  machine-readable record is `artifacts/local-eval/notepad-route.json`.
- A direct real-model Goal/Action probe returned schema-valid UI Automation goal and
  `set_value` proposal for synthetic Notepad controls. This was not a real desktop execution.

## Open acceptance

- No full push-to-talk → local transcription → launch → desktop typing run was completed on a
  physical microphone and real Notepad in this qualification. The route probe uses synthetic
  installed-app inventory and sends no OS input.
- No whole-process blocking-proxy proof of zero private-mode cloud egress.
- No local TTS playback qualification or comparative local-vs-cloud scorecard threshold.
- The separate V2-20 real SIP/audio/IVR/takeover/billing and physical Android requirements remain
  open. No Git remote, production deployment target, or signing certificate is configured.
