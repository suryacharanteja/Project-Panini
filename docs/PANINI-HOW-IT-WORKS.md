# How PANINI works now

This is the single reference for how PANINI thinks, acts, asks and tidies up, and for every
setting behind that. For the Jev and token design in depth (decision tables, token budgets,
benchmarks), see [capabilities/jev-decisions.md](capabilities/jev-decisions.md).

## 1. The brain: Jev decides, the agent acts

Every request takes one of two paths.

| Path                    | When                                                                                                                                                                                                  | Speed              |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| **Jev shortcut**        | Jev is sure the request is one plain step, such as "search LinkedIn for AI manager jobs". The request goes straight to one capability, with no planning call.                                         | Fastest            |
| **Agent** (the default) | Everything else: questions that need an answer, "open X", small talk, vague or multi-step requests. The agent plans, picks tools and capabilities, recovers from failures and asks only when it must. | About 5–6 s median |

- **Jev** (`typesafe-ai/jev`) makes decisions: which route, which skills, memories and tools the
  agent sees, whether a failure is worth retrying, whether the task is done, and which windows
  to close.
- **LLMs** only propose, write and call tools.

### The agent's tools

| Tool                                                 | What it does                                                                                                                                                                        |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_files`, `read_file`                            | Anywhere in your home folder and workspaces. Never reads secret-looking files such as `.env` or keys.                                                                               |
| `run_command`                                        | PowerShell with a timeout. Risky commands ask you first (see section 3).                                                                                                            |
| `open`                                               | Folders, files, apps (Notepad, Calculator), `ms-settings:` pages and websites. Websites open in **your Chrome** when JevRev Claw is connected.                                      |
| `browser`                                            | Instant actions in your Chrome tab: read, scroll down or up, navigate, click, type. Each returns what's now visible, so PANINI can walk you through a site. Risky clicks ask first. |
| `use_capability`                                     | Search (sourced web research), Browser (your Chrome), Desktop (clicking and typing in Windows apps) and Code (multi-file coding in a workspace).                                    |
| Memory, skills, scheduling, connectors, other agents | Offered only when Jev judges the task needs them.                                                                                                                                   |

### Recovery and speed

- **Recovery:** if a capability fails, Jev decides whether another route could work, and the
  agent retries with the error. It never retries an approval you refused, a login or a
  payment.
- **Speed limits per task:** at most 3 searches and 2 browser runs. Jev can finish a task as
  soon as the results answer it.

## 2. Browser: your Chrome first

- **Default:** PANINI works in your own Chrome through the **JevRev Claw** extension, using the
  attached tab or tab workspace.
- **A connected Chrome is never bypassed.** The separate managed browser is used only when:
  - Chrome is not connected at all (PANINI tells you why in one line); or
  - you choose it yourself.
- **Connected but no tab attached:** PANINI asks you to attach a tab instead of opening another
  browser.
- **Choosing a browser:** use the **Browser** picker in the shell dock. Your choice applies to
  the rest of the conversation. The options are:
  - _Your Chrome when connected_ (the default);
  - a specific Chrome tab;
  - _Separate browser_.
- **Extension address:** `ws://127.0.0.1:3200/ws/extension`. If a saved address fails, the
  extension falls back to this one automatically.

## 3. Approvals: only for risky actions

This is set by `PANINI_APPROVALS`, and the default is `risky`.

| Runs without asking                                                       | Asks first                                                                |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Reading, navigating, scrolling, typing into fields, searching             | Paying, ordering, sending, posting, deleting                              |
| In-workspace code edits, ordinary commands (`python x.py`, `Get-PSDrive`) | Signing in, Easy Apply, Connect, installing                               |
| Opening apps, folders, settings and sites                                 | Destructive or network commands (`rm`, `git push`, `curl`, `pip install`) |

- **Declining:** declining an approval stops the task.
- **Strict mode:** `all` restores asking before every action.
- **Rules:** the rules live in `backend/capabilities/approvals.py`. The check is deterministic,
  so it adds no delay.

## 4. Tidying up after a task

- **Tracking:** PANINI notes which windows are open when a task starts.
- **At the end of a successful task:** Jev decides whether each new window was something you
  asked to see or only a means to an end.
  - "Open Notepad" leaves Notepad open.
  - "Write 'buy milk' in Notepad and save it" closes Notepad afterwards.
  - Changing a setting closes the Settings window.
- **Safe closing:** windows are closed politely, so an app with unsaved work asks rather than
  losing anything.
- **Never closed:** browsers, VS Code, terminals, Claude and PANINI.
- **When unsure:** nothing is closed.
- **Setting:** `PANINI_CLEANUP` can be `auto` (the default), `always` or `never`.

## 5. The shell (avatar window)

- **Window controls:** one pill in the title bar. Each control shows its name on hover or focus,
  and always in full screen.

  | Control               | Shortcut                          | What it does                          |
  | --------------------- | --------------------------------- | ------------------------------------- |
  | Admin                 | Ctrl+,                            | Opens PANINI Admin, already signed in |
  | Full screen / Compact | F11 (Esc also returns to compact) | Switches the view                     |
  | Minimize              | Ctrl+H                            | Shrinks PANINI to the bubble (below)  |

- **The bubble (minimized PANINI):** PANINI never disappears when you minimize it. It becomes
  a round bubble about 5 cm across that floats on top.
  - **What it shows:** the living avatar and a one-line status, such as "Working · Browser"
    with what is happening.
  - **Using it:** click to open, drag to move (it remembers where you put it), or press Enter
    when it has focus.
  - **When PANINI needs you:** the ring pulses amber and a Windows notification says why. It
    never steals focus, and clicking the notification opens PANINI.
- **Never jumps open:** approvals appear as a small dialog over the bubble, and questions make the
  bubble pulse with a notification. PANINI opens only when you click it.
- **Hiding completely:** a deliberate choice, made from the tray menu (_Background mode_).
  Bring PANINI back from the tray icon or with **Ctrl+Shift+Space**.
- **Taskbar:** PANINI has a taskbar button while its window is open, so Alt+Tab finds it. The
  bubble has none.
- **Dock:** Browser picker, Work with (capability) and Target. The open dropdown lists are
  always dark and readable.
- **Stage:** the full-screen avatar design is described in
  [capabilities/avatar.md](capabilities/avatar.md).

## 6. PANINI Admin

Admin is at `http://127.0.0.1:3200/`, or one click on **Admin** in the shell. It has:

- **Settings → Devices:** extension and phone pairing codes.
- **Keys & models:** API keys, models, and the switches listed below.
- **Usage & cost:** tokens and cost for every LLM and Jev call, per area.

## 7. Settings reference

| Setting                      | Default        | What it does                                                      |
| ---------------------------- | -------------- | ----------------------------------------------------------------- |
| `PANINI_APPROVALS`           | `risky`        | `risky` or `all` (section 3)                                      |
| `PANINI_CLEANUP`             | `auto`         | `auto`, `always` or `never` (section 4)                           |
| `PANINI_BROWSER_FALLBACK`    | `separate`     | When Chrome isn't connected: use the separate browser, or `ask`   |
| `PANINI_AGENT_DEFAULT`       | `true`         | Send non-shortcut requests to the agent                           |
| `PANINI_JEV_GATES`           | `true`         | Jev routing, selection, early finish, recovery and success checks |
| `PANINI_FAST_MODEL`          | `gpt-4.1-mini` | Simple extraction, such as the browser task plan                  |
| `PANINI_SCREENSHOT_DETAIL`   | `auto`         | Browser screenshots: low detail unless the last step failed       |
| `PANINI_CLI_CONTEXT_TOKENS` | `96000`        | PANINI-CLI's context budget                                      |

## 8. Testing

| Check                                                                                                                                              | Command                                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| All checks                                                                                                                                         | `npm test`                                                             |
| Live capability eval: 20 everyday requests with real models; results in `artifacts/capability-eval.json`. Last run: 20 of 20 passed, median 5–6 s. | `.venv\Scripts\python.exe scripts/panini-capability-eval.py`           |
| Offline token benchmark                                                                                                                            | `.venv\Scripts\python.exe scripts/token_bench.py`                      |
| Live routing eval                                                                                                                                  | `.venv\Scripts\python.exe scripts/panini-routing-eval.py --configured` |

## 9. Identity, memory and learning

PANINI improves from use, with no fine-tuning. All improvement comes from memory, skills,
lessons and Jev's evaluations. The code is in `backend/harness/learning.py`.

- **Identity:** `backend/harness/persona.md` holds PANINI's name, what it is named after, and
  that it was built by nDimensions.AI and founded and developed by CharanTheAiGuy. It opens
  every agent and voice prompt. Edit it in Admin → **Identity & learning**.
- **Automatic memory:** after each turn, Jev decides whether you said something durable about
  yourself. A small model words the memory, Jev checks it's accurate and not sensitive, and
  it's saved once.
- **Feedback:**
  - Praise ("well done") and corrections ("that was too long") are recognised by Jev and
    attached to the task they refer to.
  - 👍 and 👎 appear under each finished result in the shell.
  - Jev grades every task.
- **Lessons:** praise or a correction leads a small model to propose one general rule. Jev
  checks that it is:
  - general;
  - supported by your feedback;
  - safe (no new permissions, no skipped approvals);
  - consistent with existing lessons.

  Lessons that pass go live automatically, and the agent sees a lesson only when it's relevant.

- **Retirement:** once a lesson has been used five times, it's retired automatically if its
  tasks score clearly below PANINI's average.
- **Control:** Admin → Identity & learning shows grades, praise and corrections, every lesson
  with its evidence, and automatic memories. You can remove anything.
- **Live eval:** `scripts/panini-learning-eval.py`. The last run passed 5 of 5:
  - PANINI answered "who are you" and "who founded you" from its identity;
  - it remembered "AI manager in Dubai, prefers short bullet answers";
  - it learned a lesson from "too long" and gave a three-bullet answer on the next request.

