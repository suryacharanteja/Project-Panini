# V2-27 local evidence gates — 2026-10-05

## Delivered

Private-mode early completion and verified-claim checks now ask the configured loopback model for
schema-constrained booleans. The local gate accepts exactly the requested fields and boolean types;
missing fields, strings such as `"true"`, extra fields, tool calls, malformed JSON, timeout and model
errors fail closed. PANINI retains the existing evidence and effect-verification rules. Other modes
continue to use the existing Jev behavior.

The Admin local-model probe still checks a synthetic tool call. In private mode it also checks a
synthetic unsupported achievement through the JSON schema path; a model that asserts the unsupported
claim fails the probe. No user content is sent by the probe.

## Verification

- Local-mode tests: 12 passed, including a mock loopback transport and positive, malformed and
  unavailable evidence-gate decisions.
- Focused Admin and Jev compatibility suite: 52 passed.
- Full `npm test` gate: 1,281 backend tests passed; 16/16 scripted offline cases; 125 CLI tests
  passed with 1 skipped; frontend, phone, search, Admin and shell tests plus typechecks passed.

## Still open

The tests do not measure decision accuracy on a real local model. The model may reject the structured
output request; in that case PANINI reports a partial result rather than an unverified success. The
blocking-proxy privacy test, local speech path, physical offline task and comparative quality guard
remain open. V2-27 acceptance boxes remain unchecked. Red-team stays disabled and deferred.
