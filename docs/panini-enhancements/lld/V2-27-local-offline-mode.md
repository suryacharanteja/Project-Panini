# V2-27: Local and offline mode

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 6 · P3 · L (2 weeks) |
| Depends on | V2-06 (model router) |
| Flag | `PANINI_LOCAL_MODE` = `off` (default) · `fallback` · `private` |
| Status | In progress: local model/fallback, coordinator and desktop admission, comparative quality guard and real-local scorecard path, private-preview refusals and packaged offline STT built; no task class qualified, blocking-proxy, TTS and full push-to-talk task pending |

## 1. Outcome

PANINI keeps working when the internet or a provider is down, and can run in a private mode where nothing
leaves the PC: on-device speech recognition and speech, a local model for routing and simple tasks, and the
same hands. Complex tasks degrade gracefully with a clear message instead of failing.

## 2. Scope and non-goals

- In scope: local STT and TTS, a local OpenAI-compatible model server as a model-router provider, local Jev
  substitutes for gates, capability readiness in offline/private mode, push-to-talk voice without GPT-Live,
  privacy mode enforcement (deny cloud calls unless approved per run), Admin setup.
- Not in scope: local web search (Search needs the web), training models, GPU driver management.

## 3. Current state

- Memory embeddings are already local (hashed n-grams + FTS5).
- Voice (GPT-Live), the agent model, planner, Jev (AI Gateway), Search and STT (AssemblyAI) are cloud services.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| PANINI model interface (`panini_cli.agent.model.create_model`, `Model.respond(prompt, items, tools, cache_key)`) | Provider abstraction point | Local provider implementing the same interface against an OpenAI-compatible server |
| OpenDots `openaiCompatibleText` (OpenAI-compatible model provider with a base URL) | Base-URL-configurable providers | `PANINI_LOCAL_BASE_URL` |

## 5. Design

### 5.1 Components

| Component | Choice | Notes |
| --- | --- | --- |
| Local model server | Any OpenAI-compatible server (Ollama, llama.cpp server, LM Studio) at `PANINI_LOCAL_BASE_URL` | PANINI does not bundle models; Admin checks the server and the model's tool-calling support |
| Local STT | `faster-whisper` 1.2.1 (MIT) with a pinned base model (MIT), CPU/int8 | A packaged local model transcribes a bounded recording into the shell composer for review; voice/task latency and real microphones still need qualification |
| Local TTS | Installed Microsoft Windows SAPI voice | PANINI bundles no voice. It selects an installed Microsoft voice, forces literal text, and creates a bounded WAV in a killable child process. This covers the Windows shell path; a redistributable cross-platform engine and network-egress qualification remain open. [Microsoft SAPI voice flags](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee125223%28v%3Dvs.85%29). |
| Local gates | Jev questions answered by the local model with a fixed JSON schema | Same thresholds; slower |

### 5.2 Modes

| Mode | Behaviour |
| --- | --- |
| `off` | v2 default: cloud providers only |
| `fallback` | Use cloud; when a provider fails (timeouts, 5xx, offline), switch that tier to local for the run and say so; voice falls back to push-to-talk |
| `private` | Never send content to cloud providers; Search, GPT-Live voice, phone line, channels that need cloud, and cloud MCP connectors report "unavailable in private mode"; the person can approve a single run to use the cloud (an approval interrupt listing what would be sent) |

### 5.3 Router integration

`ModelRouter` (V2-06) gains provider `local` per tier; in `private` mode every tier resolves to `local`;
readiness for capabilities that depend on cloud services becomes `unavailable` with reason "private mode".
Budgets count local calls at zero cost but still cap steps and time.

### 5.4 Quality guard

The scorecard (V2-01) runs the offline-capable case subset in `private` mode; the local tier is enabled for a
task class only if its success rate is ≥ 80 % of the cloud tier on that subset; otherwise PANINI says the task
needs the cloud.

## 6. Implementation steps

1. Local provider for the model interface; Admin connection test; router integration.
2. Local gates; private-mode enforcement and readiness reasons.
3. Local STT and TTS; push-to-talk path in the shell.
4. Scorecard subset and the quality guard.

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Router fallback and private enforcement (no cloud client constructed in private mode) | `backend/tests/brain/test_local_mode.py` |
| Integration | Mock OpenAI-compatible server: tool calls, streaming, errors | same |
| Eval | Private-mode subset success vs cloud | `scripts/panini-eval.py --mode private` |

## 8. Acceptance criteria

- [ ] With the network disconnected, "open Notepad and type hello" works by push-to-talk in `fallback` mode.
- [ ] `private` mode makes zero requests to cloud hosts (verified with a blocking proxy in tests).
- [ ] The quality guard disables local tiers that underperform.

The first development increment adds an OpenAI-compatible chat adapter restricted to loopback HTTP, tool-call
translation, zero-cost usage records, and transient cloud-to-local fallback that stays local for the run. In
private mode the model factory constructs only the local client and Jev calls are refused. Admin can save a
loopback endpoint and model name, then send a synthetic tool-call probe; unit tests use an HTTP mock and assert
no cloud client construction. The next development increment rejects unqualified v1/v2 agent tools, disables
remote/channel/phone/trigger startup, marks those tools unavailable in registry inventory, and refuses direct
browser, live voice, comparison, Android, connector, agent and coordinator execution APIs while
private mode is selected. The setting requires a restart. This remains a preview: the complete process has
not passed a blocking-proxy or physical offline test; on-device TTS, a qualified desktop hand, local
gate quality and the scorecard quality guard are pending. File and command hands are withheld in private
mode until network drive and subprocess egress are qualified. Keep this flag off outside development.

The next increment asks the loopback model for schema-constrained boolean evidence decisions before
private-mode early completion and verified claims. Missing, malformed or unsupported responses fail
closed. Admin's synthetic probe checks the JSON schema path and rejects a made-up achievement. The
context-selection optimization still falls back to showing all local tools. These tests exercise a
mock loopback server, not a qualified real local model; the gate's decision accuracy remains open.

The speech increment pins `faster-whisper==1.2.1` and `av==18.1.0` because PyAV 19 removed the
`metadata_errors` argument used by this faster-whisper release. Packaging verifies SHA-256 for all
files of `Systran/faster-whisper-base` at revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`,
bundles the model, and runs the frozen backend against one second of local silent audio. Runtime
transcription passes only an existing local directory; it never passes a model name that could trigger
a download. Private mode uses that engine even when a cloud operation is injected, and fallback mode
uses it after a cloud transcription error. The authenticated shell records up to 30 seconds and puts
the transcript in the composer for review. A synthetic spoken WAV passed locally; physical microphone
capture, the complete offline Notepad task, TTS playback and whole-process egress remain unqualified.

The next increment routes a missing-key or transiently failed cloud planner call to the loopback
model. It validates the result with Pydantic, checks proposed capabilities against live readiness,
checks application IDs against the installed catalog, and requires an explicit launch step when
the user requested opening an installed app. A fallback route must pass a bounded local evidence
gate; missing or malformed gate output rejects the route. A narrow catalog-grounded offline
intent routes an explicit "open installed app and type short draft" request into launch and
draft steps without asking an unqualified model to invent plan fields. It rejects extra commands
such as "and save". The semantic Desktop adapter also uses
that local gate for each proposed UI Automation action when AI Gateway is absent or fails, and
stops before input if admission is unavailable. Unqualified agent recovery is disabled for an
offline failed step. Launch verification now requires a new window rather than treating an
existing window of the same app as proof. Focused tests pass, including a regression for
successful cloud planner responses. A real `qwen2.5:7b-instruct-q4_K_M` model generated a
malformed general Notepad route and failed safely after bounded retries; the new narrow route
passed a no-action dry run in 0.015 seconds. Its direct Goal/Action schema probe produced valid
JSON, which does not qualify the full task. A `qwen3` 14B probe timed out. The complete offline
Notepad acceptance case and comparative quality threshold are still open.

The comparative guard now requires a model- and clean-source-matched report made from paired
real cloud (`live`) and real local (`local`) scorecards. It refuses scripted offline runs,
skipped or mismatched cases, safety regressions, fewer than eight cases per task class, and
local success below 80% of the cloud baseline. Planner, desktop and agent local fallbacks
report unavailable until their own class qualifies; Admin shows each class status. The
isolated local evaluator can exercise an unqualified candidate without importing a saved
cloud credential. The paired scorecard now requires completed factual answers and an actual
approval request for the delete safety case. On this PC, `qwen2.5:7b-instruct-q4_K_M`
passed 0/8 planner cases and the one reviewed safety case in the tightened clean-source run.
No matching cloud scorecard was run, so no relative ratio or local tier qualification is
claimed. The model remains gated off.

The Windows shell now offers an explicit **Read reply locally** button in private and fallback modes.
An authenticated controller lease requests at most 500 characters. The backend starts a bounded child
process, chooses an installed Microsoft SAPI voice, sends text on stdin rather than command arguments,
forces non-XML speech, validates the resulting mono PCM WAV and returns it for owner-initiated playback.
The shell uses a temporary blob URL and supports Stop; cleanup revokes the URL. A source SAPI run, API
authorization tests and a frozen-backend synthesis smoke produce real WAV data. Audible speaker output,
zero cloud egress under a blocking proxy, clean-machine voice availability, physical microphone input and
the full offline Notepad task are still unqualified. Local TTS does not qualify the local planner tier.

## 9. Rollout and rollback

Opt-in in Admin. Rollback: `off`.

## 10. Open questions

- Recommend a default local model for PANINI's tool calling? Decide after the scorecard comparison; the
  setting stays model-agnostic.
