# V2-27 private-preview checkpoint — 2026-10-05

## Delivered

Private mode selects the loopback model and now refuses unqualified cloud paths at several layers:
cloud credentials report unavailable; remote, channel, phone, outbound, trigger, MCP and presence
background services do not start; the agent and registry hide and refuse unqualified tools; direct
browser, voice, transcription, comparison, Android, connector, agent and coordinator execution APIs
return an unavailable response. Remote MCP connector startup and OAuth HTTP calls are refused even
when invoked outside the agent. Registry inventory and coordinator adapters explain their private-mode
unavailability.

The private allowlist is limited to built-in local memory, skill, project, page, activity and control
tools. File, command, browser and desktop hands remain unavailable pending egress qualification. The
Admin setting requires a restart. Red-team enforcement remains disabled and deferred.

## Verification

- `backend/tests/brain/test_local_mode.py`: 10 passing tests cover loopback-only endpoint parsing,
  no cloud-model construction, local tool-call translation, persistent fallback, cloud key/feature
  refusals, v1 and registry dispatch, inventory, direct API refusal, connector lifecycle and adapter
  readiness. The connector test uses a provider mock that fails on any request.
- `backend/tests/test_boundaries.py` plus local-mode tests: 18 passed, including the chunked-body
  413 limit after replacing the initial middleware with a pure ASGI boundary.
- Full backend regression: 1,278 passed. The first run caught the chunked-body regression; it was
  corrected before this passing rerun.
- Existing offline scripted scorecard: 16/16 passed. CLI suite: 125 passed, 1 skipped. Search,
  Admin and shell tests passed; frontend typechecks passed. Admin copy was retested separately.

## Still open

These tests do not prove zero process-wide egress. A blocking-proxy run with configured providers,
existing sessions, background jobs, redirects, child processes and a real local model is still
required. Local STT/TTS, push-to-talk, local gates, the quality guard, file/desktop hands and physical
offline operation remain unqualified. No V2-27 acceptance box is checked, and this is a development
candidate only.
