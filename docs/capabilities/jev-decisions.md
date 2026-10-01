# Jev decides; the LLM proposes

PANINI splits every step into two roles:

- **Jev** (`typesafe-ai/jev`) makes decisions. It is an evaluator: it takes a state and typed
  questions, and returns calibrated probabilities. It bills input tokens only, at
  $0.042 per million, which is about 50× cheaper than GPT-4.1 input.
- **LLMs** do what Jev cannot: propose candidate actions, extract plans, write replies and
  run tool loops.

Before either model is asked, deterministic code checks what it can, for example search
verification, evidence quotes and grounding.

## Previous state and current state

### Before (as of 2026-09-26)

- **Decisions made by an LLM alone:**
  - routing a request to a capability, with GPT-4.1 on every request;
  - the browser's "blocked, needs the user" verdict;
  - desktop visual success;
  - the agent's "finish / success" claim.
- **Browser proposer input on every step:**
  - the full page text, with the viewport repeated up to three times;
  - all 120 controls, including CSS class context, bounds, node IDs and tracking URLs;
  - a high-detail screenshot;
  - the last 8 outcomes;
  - the active tab's own text again.
- **Other surfaces:**
  - **Agent:** every tool schema, skill, connector and memory on every turn, and a clock line
    at the start of the prompt that broke prompt caching every minute.
  - **Agent finish:** one extra model turn just to call `finish`.
  - **Voice:** up to 16,000 characters of transcript that duplicated the conversation's own
    history.
  - **Desktop:** full UIA elements with long type and pattern names, runtime IDs and empty
    fields.
  - **PANINI-CLI:** a 200k-token context budget, 16k characters of command output and
    100k-character file reads.
- **Measurement:** no per-call token or cost measurement.

### Now

- **Jev decides:**
  - routing of plain requests;
  - blocked verdicts;
  - visual success;
  - agent success;
  - agent early finish;
  - which page passages and links the browser proposer reads;
  - which skills, connectors, memories and tools the agent sees.
- **LLMs only propose, extract, write and call tools**, with the compact inputs described
  under _Token budget rules_.
- **Speed:**
  - Jev page selection runs at the same time as the screenshot capture.
  - An unchanged page reuses its selection without calling Jev.
  - The Jev sidecar is warmed at startup.
- **Measurement:** every LLM and Jev call is recorded in PANINI Admin → **Usage & cost**.

### Measured

**Live, on a LinkedIn-style results page with 118 controls**

| Per browser step       | Before        | Now                                                                  |
| ---------------------- | ------------- | -------------------------------------------------------------------- |
| GPT-4.1 proposer input | 24,107 tokens | 6,374 tokens (74% fewer)                                             |
| GPT-4.1 proposer time  | 10.5 s        | 3.2 s                                                                |
| Jev page selection     | –             | 1.3–2.4 s warm, overlapping the screenshot; 0 s on an unchanged page |
| Total step             | about 10.5 s  | about 4.5–5.6 s                                                      |

**Routing**

| Check                          | Result                                                     |
| ------------------------------ | ---------------------------------------------------------- |
| Live routing eval with GPT-4.1 | 6 of 6 pass                                                |
| Jev routing                    | 9 of 9 test requests routed correctly, about $0.00003 each |

**Offline benchmark** (`scripts/token_bench.py`)

| Surface                                | Before             | Now               |
| -------------------------------------- | ------------------ | ----------------- |
| Browser task, 15 steps                 | 289k tokens, $0.58 | 88k tokens, $0.14 |
| Desktop observation, per step          | 5.9k tokens        | 3.8k tokens       |
| Agent instructions and tools, per turn | 1.7k tokens        | 0.7k tokens       |

### Next options (not yet done)

The ~3 s GPT-4.1 proposer call is now the slowest part of each browser step. Two ways to
cut it further, both of which could affect accuracy:

- Use `gpt-4.1-mini` as the proposer.
- Skip the proposer when a single grounded action is the only option.

## Who decides what

| Surface                                                            | LLM proposes or writes                                                                                         | Jev decides                                                                                                                                                                                               |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Routing (`panini/planning.py`)                                     | The plan, only when needed: small talk, clarifications, named targets, app launches, several steps, follow-ups | The capability for a plain single-capability request, in one call with no LLM                                                                                                                             |
| Search (`panini-core/capabilities/search`)                                          | Web retrieval lanes                                                                                            | Intent (time window, sources, query) and relevance ranking                                                                                                                                                |
| Browser, your Chrome (`extension_loop.py`, `extension_planner.py`) | Up to six grounded candidate actions                                                                           | Which off-screen passages and links the proposer reads (one call), the action, "done", and whether a claimed block is real                                                                                |
| Browser, managed (`chat_jev.py`)                                   | Candidate actions                                                                                              | The action                                                                                                                                                                                                |
| Desktop                                                            | Goal and candidate action                                                                                      | Whether each action may run, and whether visual evidence satisfies the goal                                                                                                                               |
| Agent (`harness/agent.py`)                                         | The tool loop                                                                                                  | Which skills, connectors, memories and optional tools the task sees (once per task); whether a verified step finished the goal, so no extra model turn is needed; whether results support a success claim |

When Jev is unavailable, each gate falls back to its previous, conservative behaviour. For
example:

- a blocked claim still stops the task;
- routing uses the LLM planner;
- the proposer sees the whole page;
- the agent sees every tool and skill.

## Token budget rules

- **Page actions:**
  - Actions are sent as `id | kind | label | key=value` lines.
  - The executor keeps the node handles, bounds and CSS class context.
  - Tracking parameters are removed from links.
  - Above 40 links, Jev keeps the relevant ones (at least 15). Every form field, scroll and
    wait action is always kept.
- **Page text:**
  - The viewport text is sent once.
  - Off-screen text up to 3,000 characters is sent whole.
  - For longer text, Jev selects the relevant passages, up to 4,000 characters.
  - Verification always uses the complete page.
- **Screenshots:** sent only when the page has unlabeled (icon-only) controls. The detail is
  _low_ (85 tokens) unless the last action failed or changed nothing. Set with
  `PANINI_SCREENSHOT_DETAIL`.
- **Other tabs:** only readings from other tabs are sent, at 1,500 characters each.
- **History:** the last 4 outcomes, plus a count of older ones.
- **Desktop controls:**
  - Controls use observation-scoped handles instead of runtime IDs.
  - Type and pattern names are short (`Button`, `Invoke`), and empty fields are dropped.
- **Agent:**
  - The instructions describe only the tools that are offered.
  - Tool outputs are capped at 8,000 characters.
  - Context selection runs once per task, so the prompt prefix stays cacheable.
- **PANINI-CLI:**
  - The context budget is 96k tokens, above which older tool outputs are dropped
    (`PANINI_CLI_CONTEXT_TOKENS`).
  - Command output is capped at 10,000 characters.
  - File reads are capped at 50,000 characters; larger files are read in line ranges.
- **Prompt caching:** static rules come first and stay byte-stable, and each surface passes a
  `prompt_cache_key`. The agent's clock line is placed last so its prefix stays cacheable.
- **Fast model:** `PANINI_FAST_MODEL` (default `gpt-4.1-mini`) extracts the browser task plan.
- **Voice:** the native transcript added to each voice turn is capped at 4,000 characters,
  because the conversation module already sends its own history.

## Measuring

- **Admin:** PANINI Admin → **Usage & cost** shows calls, tokens (including cached tokens)
  and estimated cost per surface since PANINI started. The data comes from
  `GET /api/admin/v1/usage`, recorded by `backend/usage_log.py`.
- **Offline benchmark:** `scripts/token_bench.py` compares model input before and after
  these rules, with no network calls:

  | Surface                                               | Before             | After                                |
  | ----------------------------------------------------- | ------------------ | ------------------------------------ |
  | Browser proposer, 15-step LinkedIn-style task         | 289k tokens, $0.58 | 88k tokens, $0.14 (69% fewer tokens) |
  | Desktop observation, 80 controls, per step            | 5.9k tokens        | 3.8k tokens                          |
  | Agent instructions and tools, per turn, research goal | 1.7k tokens        | 0.7k tokens                          |

- **Live Jev checks:**

  | Check          | Result                                                     | Cost                |
  | -------------- | ---------------------------------------------------------- | ------------------- |
  | Page selection | Kept all 16 job links and dropped 34 menu and footer links | $0.0002             |
  | Routing        | 9 of 9 test requests routed correctly                      | about $0.00003 each |

- **Settings:** `PANINI_JEV_GATES=false` turns off Jev routing, page selection, agent context
  selection, early finish and the success check. Tests do this by default so they never call
  a live service.

## Approvals

The approval policy lives in `backend/capabilities/approvals.py`. The setting is
`PANINI_APPROVALS`, with `risky` as the default. In risky mode, PANINI asks only before
actions that are irreversible or reach other people:

- paying or ordering, sending or posting;
- deleting, signing in, applying or connecting;
- installing, and destructive or network shell commands.

Browsing, typing into fields, searching and in-workspace code edits run without
interruption. The check is deterministic, so it adds no latency. `all` restores asking
before every action.

Jev checks that exist only to save tokens or time give up after 4 s
(`JEV_GATE_TIMEOUT`) and fall back, so they never stall a step.

## The agent is the default brain (2026-09-26)

- **Routing:**
  - Jev sends a request straight to a capability only when it is sure the request is one plain
    step, such as searching jobs on a website.
  - Everything else goes to the agent (`PANINI_AGENT_DEFAULT`): questions that need an answer,
    "just open X", small talk, vague or multi-step requests.
- **Computer tools:** the agent has four tools that work anywhere in the home folder
  (`backend/harness/computer.py`):
  - `list_files` and `read_file`, which never read secret-looking files;
  - `run_command`, using PowerShell, where risky commands ask first;
  - `open`, for folders, files, apps, `ms-settings:` pages and URLs.
- **Recovery:** when a capability blocks or fails, Jev decides whether another route could still
  work. If it could, the agent retries with the error. Approvals the person refused, logins and
  payments are never retried.
- **Speed:**
  - Each task can use search at most 3 times and the browser at most 2 times.
  - One search is usually enough.
  - Jev's early finish reports the top results directly.

**Live eval** (`scripts/panini-capability-eval.py`), 20 everyday requests with real models:

| Measure                | Result                                                                 |
| ---------------------- | ---------------------------------------------------------------------- |
| Passed                 | 20 of 20, after fixing three checks that were wrong in the eval itself |
| Median time            | 5–6 s                                                                  |
| Tokens per request     | 2–17k                                                                  |
| Search questions       | 11–24 s                                                                |
| Opening an app or site | 4–6 s                                                                  |

The requests covered files, commands, apps, settings, websites, search, a multi-step task,
the approval gate and a vague request that needed a clarifying question. Desktop screen
automation and your connected Chrome aren't covered, because the eval runs outside the
PANINI app.


