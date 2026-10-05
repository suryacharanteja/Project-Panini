# V2-27: Local and offline mode

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 6 · P3 · L (2 weeks) |
| Depends on | V2-06 (model router) |
| Flag | `PANINI_LOCAL_MODE` = `off` (default) · `fallback` · `private` |
| Status | In progress: loopback local model, transient fallback and Admin tool-call probe built; private egress, speech and quality gate pending |

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
| Local STT | `faster-whisper` (MIT) with a small model, CPU by default | Push-to-talk on the avatar; ≤ 2 s for a 5 s utterance on a modern CPU |
| Local TTS | Piper (MIT) voices | Avatar speech in offline mode |
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
no cloud client construction. This is not a private-mode release: browser, channels, connectors and other
service egress have not passed a blocking-proxy test; on-device STT/TTS and the scorecard quality gate are
pending. Keep this flag off outside development.

## 9. Rollout and rollback

Opt-in in Admin. Rollback: `off`.

## 10. Open questions

- Recommend a default local model for PANINI's tool calling? Decide after the scorecard comparison; the
  setting stays model-agnostic.
