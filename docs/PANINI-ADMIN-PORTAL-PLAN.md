# PANINI Admin: plan

**Design proposal · 2026-09-25 · for decision before any build**

It turns the web console at `http://localhost:3200` into **PANINI Admin**. There you
configure, extend, observe and govern PANINI entirely from the UI, without editing
code, `.env` or JSON files again.

---

## 1. The problem (design thinking: empathise, define)

### What configuring PANINI takes today

The inventory was taken from the repository on 2026-09-25.

| To do this | You currently have to |
| --- | --- |
| Change an API key, model, voice or port | Edit `.env` by hand (about 30 settings), then restart |
| Turn on coding, pick workspaces | Start with `npm run panini:code`, or edit `PANINI_CLI_WORKSPACES` as a JSON array |
| Add a connector (MCP) | Hand-write `connectors.json`, then click Reload |
| Let PANINI ask another agent | Hand-write `agents.json` |
| Add a skill | Create a folder and write `SKILL.md` with frontmatter |
| Add hooks | Write `hooks.json`, then run `panini-cli hooks --trust` in a terminal |
| Enable the Docker sandbox | Set an environment variable, and start Docker yourself |
| Know that something is broken | Wait for a symptom. Today's revoked OpenAI key surfaced only as a voice error. |

The console also has two visual systems:

- the JevRev pages (Compare, Browser, Search);
- the PANINI pages (Activity, Agent).

Its home page is a model-comparison tool, not an overview.

### Who it's for

| Persona | Job to be done |
| --- | --- |
| **Owner-operator** (you, day to day) | "Tell me PANINI is healthy, show me what it did, and let me approve or fix things in one place." |
| **Builder** (you, extending it) | "Add a skill, connector or new assistant in minutes, test it, then roll it out, all without code." |
| **Observer** (later: family or team member) | "See activity and results, and change nothing." |

### Problem statement

> How might we let the person who owns PANINI shape everything it knows, can do
> and is allowed to do from one trustworthy place, with safe defaults, instant
> feedback and an undo for every change?

### Success measures

| Measure | Target |
| --- | --- |
| File or code edits needed for any supported change | **0** |
| Time to add and test a connector | **under 2 minutes** |
| Time to add and test a skill | **under 3 minutes** |
| Time to notice a broken dependency (key, extension, Docker, search) | **under 1 minute**, as an alert on Home and in the tray |
| Changes that need a restart | shown up front, with a one-click restart |
| Every change | audited, diffable and rollback-able |

---

## 2. Patterns worth borrowing

| Source | Pattern | How PANINI uses it |
| --- | --- | --- |
| **ChatGPT** | *Custom instructions / Personalization* ("What should ChatGPT know about you, how should it respond") | **Personalization**: About me and Response style, feeding every profile |
| ChatGPT | *GPT builder*: "Create" (conversational) and "Configure" (form) tabs, with a live preview | **Profile and Skill studios**: a Describe tab (PANINI drafts it), a Configure tab (form), and a Try-it pane |
| ChatGPT | *Apps and connectors directory*: Connect button, OAuth, enable per chat | **Connectors directory**: a curated catalog, one-click Connect, and enable per profile |
| ChatGPT | *Memory*: "Manage memories", plus toggles for saved memories and chat history | **Memory**: search, edit and forget, plus toggles for auto-remember and use-in-answers |
| ChatGPT | *Tasks* (scheduled), and *agent mode* with approvals and takeover | **Automations**: schedules and watches, with approvals in the Inbox |
| ChatGPT Enterprise | *Admin console*: workspace settings, app allowlists, audit and compliance logs | **Policies** and the **Audit log** |
| **Claude** | *Projects*: instructions plus knowledge scoped to a workspace | **Profiles** carry instructions, knowledge (phase 4), skills and connectors |
| Claude | *Skills*: upload a zip, toggle on or off, "create a skill with Claude" | **Skills library**: import a zip, folder or URL; toggles; build one by describing it |
| Claude | *Connectors*: directory plus "Add custom connector" by URL, with OAuth | Directory plus **Custom** (a remote URL with sign-in, or a local command) |
| Claude Code | `settings.json` **permissions** (allow/ask/deny rules), **hooks**, MCP at user or project scope, **plugins and marketplaces** | **Per-tool permissions**; **Automations → Hooks** with templates; **Extensions** (bundles of skills, connectors, hooks and a profile) |
| Claude.ai admin | Usage analytics, spend limits | **Usage & cost**, with budgets and alerts |
| **Google (AIP / SRE / Cloud console)** | Resource-oriented APIs (standard methods, field masks, ETags, long-running operations) | Admin API v1: the UI, CLI and agent all use the same API |
| Google | Declarative config: `kind/metadata/spec/status`, reconciled, with validation at write time | Every setting is a typed **resource**: validated, versioned, hot-reloaded |
| Google | Config revisions, and rollback as a first-class action | **History** on every resource: diff, restore, and who or what changed it |
| Google | Secret Manager separate from config; secrets are write-only in the UI | **Keys & secrets** vault (DPAPI): masked, last-verified, "Test" and "Rotate" |
| Google | SLIs, health checks, golden signals, alerting | **Home health board**: model access, search, extension, Docker, voice; alerts |
| Google | Re-authentication for sensitive actions | **Step-up confirmation** for revealing or rotating keys, LAN access and trusting hooks |
| Material / Cloud console | Left nav, command palette, empty states that teach, progressive disclosure | App shell and design system (section 5) |

The pattern that makes "never go back to code" actually hold is **schema-driven
UI**. Every configurable thing is described once as a typed schema. Forms,
validation, documentation, defaults and API docs are all generated from it, so a
new setting means one schema entry, not new UI code.

---

## 3. Information architecture

```
PANINI Admin
├── Home ................ health board, needs-attention, what's running, today's usage, quick actions
├── Inbox ............... approvals, questions, outcome-unknown reviews, results, watch alerts, suggestions
├── Activity ............ live run, full history, per-run trace (plan → steps → tool/model calls → evidence)
├── Build
│   ├── Profiles ........ assistants: instructions, model, capabilities, skills, connectors, policy (default: PANINI)
│   ├── Skills .......... library, studio (describe / configure / try), import-export, versions
│   ├── Connectors ...... directory, custom (URL + sign-in or local command), per-tool allow/ask/deny, test
│   ├── Knowledge ....... (phase 3) files and folders PANINI can search, per profile
│   ├── Automations ..... schedules, watches, hooks (templates), triggers
│   └── Agents .......... agents that can ask PANINI (tokens), agents PANINI can ask
├── Memory .............. memories, auto-remember toggle, export
├── Capabilities
│   ├── Voice ........... voice, model, language, session length, microphone, wake word
│   ├── Browser ......... extension pairing, default browser, domain allow/deny, run length
│   ├── Search .......... providers, sources, freshness
│   ├── Desktop ......... on/off, approved applications, visual mode
│   └── Code ............ on/off, workspaces (folder picker), sandbox (Docker status), approval mode, hooks
├── Playground .......... Compare, Search, Browser (today's JevRev pages, now "try a capability")
├── Usage & cost ........ tokens and cost by day, capability, model and profile; budgets and alerts
├── Audit log ........... every configuration change and sensitive action; filter and export
└── Settings
    ├── Models & keys ... providers, API keys (vault, test, rotate), a model per role (planner, agent, code, voice, search)
    ├── Personalization . about me, response style, presence (avatar on / background)
    ├── Policies ........ approval defaults, background privileges, spending limits, data retention
    ├── Devices ......... browser extension, phones, sign-in sessions
    ├── Extensions ...... installed bundles, install from file or URL, trust
    ├── Data ............ backup / restore, export / import config, reset
    └── About ........... versions, diagnostics (doctor), logs download, restart runtime
```

Top bar, on every page:
- a **status pill** (Healthy / 2 issues);
- a **command palette** (Ctrl+K: "add connector", "rotate OpenAI key", "pause automations");
- **Inbox** count;
- a **Restart required** chip when needed.

---

## 4. The key screens (what "no code" looks like)

### Home

Five blocks:

1. **Health board.** One card per dependency, with a traffic light and a *Fix* button that
   deep-links to the setting:
   - OpenAI key;
   - AI Gateway;
   - AssemblyAI;
   - search worker;
   - Chrome extension;
   - Docker;
   - Windows companion;
   - voice.

   Probes run on start, every 10 minutes and on demand.
2. **Needs attention:** approvals, failing automations, expiring reviews.
3. **Running now:** the live run, with Stop.
4. **Today:** runs, success rate, tokens and cost.
5. **Quick actions**, each opening the matching guided flow:
   - add a connector;
   - new skill;
   - new automation;
   - pair a device.

### Connectors: add one in under 2 minutes

1. **Choose.** Pick from the directory: curated MCP servers with logo, description,
   publisher and required scopes. Or choose **Custom**, with two options:
   - **Remote:** a URL.
   - **Local:** a command, with a guided field for arguments and environment.
     Secret values go to the vault, never to the file.
2. **Connect.** OAuth opens automatically, as already built. A local server is started
   and its tools listed.
3. **Review tools.** Each tool shows its description and read-only badge, and gets a
   permission of **Allow / Ask / Deny**. Read-only tools default to Allow, the rest to
   Ask.
4. **Test.** Pick a read-only tool, fill a generated form from its input schema, and
   see the result.
5. **Enable for profiles**, then Save. This creates a versioned resource you can undo.

### Skills studio

- **Describe:** "When I say 'weekly report', collect…". PANINI drafts the name,
  description and steps, like the GPT builder or "create a skill with Claude".
- **Configure:** name, description (the trigger), a Markdown instructions editor with
  preview, and resource files.
- **Try:** run the skill in a sandboxed test conversation with the live trace beside
  it. The skill is saved only after a successful try.
- **Library:** toggles; origin badge (built-in / yours / project / extension); version
  history; import (zip or folder) and export.

### Profiles (custom assistants)

A profile is a Custom GPT or Claude Project for PANINI. It holds:

- a name and avatar;
- instructions;
- a model per role;
- which capabilities, skills and connectors it may use;
- its approval policy and memory scope.

"PANINI" is the default profile. You might add "Research buddy" (search only,
research skills) or "Code helper" (code and GitHub connector, sandbox on). The
avatar gets a profile switcher.

### Capabilities → Code

- An on/off toggle, replacing `npm run panini:code`.
- A workspace list with a **folder picker**, replacing the JSON env var.
- A sandbox toggle with live Docker status, including a *Start Docker Desktop* helper.
- The default approval mode.
- Hooks, with the trust flow as a UI step.

### Settings → Models & keys

- Each provider shows: key present, last four characters, **last verified** time and
  result, **Test** and **Rotate** buttons, and a link to the provider's key page.
- Keys are **write-only**: never displayed, never sent back to the browser, stored
  with DPAPI.
- If `.env` also sets a key, the UI shows the precedence ("Overridden by .env") and
  offers **Move to vault**.
- A model per role (planner, agent, code, voice, search), with a *Test prompt* button
  and estimated cost.

### Activity → run trace

A timeline for one run, like a trace viewer:

- the plan;
- each step with its capability, target and duration;
- model calls (model, tokens, latency, cost);
- tool calls with inputs and outputs (with redaction);
- approvals (who approved, and when);
- evidence records.

It can be shared as a read-only link, and exported as JSON.

---

## 5. Architecture (engineering)

### 5.1 One configuration model

Every configurable thing becomes a resource, versioned in SQLite:

```
Resource { kind, name, spec (validated by the kind's schema), status (reconciled, read-only),
           revision, etag, updated_by (person | agent | cli | migration), updated_at }
Kinds: Settings, Provider, Secret(ref only), Profile, Skill, Connector, ToolPermission,
       Automation(Schedule/Watch/Hook), RemoteAgent, AgentClient, Device, Policy, Extension
```

- **Schemas** are Pydantic models, one per kind, exported as JSON Schema. They drive
  validation, form generation, API docs and defaults.
- **Writes** are validated, then stored as a new revision, then applied by a
  **reconciler**. The reconciler hot-reloads the affected subsystem: connector pool,
  skills catalog, model clients, hooks, capability flags. It writes `status` back,
  such as "Connected · 12 tools" or "Error: …".
- **Settings that truly need a restart** (ports, for example) are flagged in their
  schema. The UI shows *Restart required* and offers one click.
- **Revisions:** every change keeps the previous one. History shows a diff and
  **Restore**. Nothing is ever edited in place without a record.
- **Files are import/export only.** `connectors.json`, `agents.json`, `hooks.json`,
  `mobile.json`, the `SKILL.md` folders and `.env` are migrated once into resources.
  After that they are only import and export formats ("config as code" for power
  users, like Claude Code's `settings.json`). `.env` remains a fallback, and the UI
  shows precedence.

### 5.2 Secrets

- A separate DPAPI vault, extending `connector-secrets.bin` into a general one.
- Resources hold only references (`secret://openai`).
- The API never returns values.
- Rotation writes a new version and tests it before switching.
- A failed test leaves the old key active.

### 5.3 Admin API v1 (resource-oriented, Google AIP style)

- **Routes:** `GET/POST /api/admin/v1/{kind}`, and `GET/PATCH/DELETE
  /api/admin/v1/{kind}/{name}` with `update_mask`.
- **Concurrency:** `ETag` / `If-Match` for optimistic concurrency.
- **Custom methods:** `:test`, `:connect`, `:restore`, `:rotate`, `:export`.
- **Long-running operations** for Docker pulls, OAuth and skill test runs:
  `GET /api/admin/v1/operations/{id}`.
- **One API, three clients:** the Admin UI, a `panini admin …` CLI, and the agent
  itself. When you ask PANINI "add the GitHub connector", it calls the same API and
  the change needs your approval.

### 5.4 Security and governance

- **Access.** The portal stays **localhost-only**, behind the existing one-time sign-in
  and HttpOnly cookie. Roles: owner, admin, viewer (the observer role already exists).
- **Step-up confirmation** for revealing or rotating keys, enabling LAN or phone
  access, trusting hooks, installing extensions and deleting everything.
- **Append-only audit log**, recording:
  - who (person / agent / CLI);
  - what (kind/name, with a diff);
  - when;
  - from where (console, shell, phone).
- **Extensions** (bundles) show exactly what they add, and every permission they
  request, before install.

### 5.5 Observability

- A **run trace** per task, using spans for plan, step, model call and tool call, with
  token and cost attributes. It is stored locally with a retention policy.
- **SLIs:**
  - model access;
  - search success rate;
  - extension connected;
  - voice setup success;
  - background task success.
- **Alerts** go to the Inbox and the tray when a probe fails or an SLI drops, for
  example "OpenAI rejected the key · Fix".
- **Usage & cost** comes from the token counts already returned by each model call,
  plus a price table (editable in the UI).

### 5.6 Frontend

- **One single-page app** for the whole portal, on the **React + TanStack Router +
  Vite** stack that `panini-core/capabilities/search` already uses. It is served by the gateway at
  `/`, with a strict CSP.
- **Schema-driven forms:** one generic form renderer turns a kind's JSON Schema, plus
  small UI hints, into accessible forms with inline validation.
- **One design system** replaces today's two styles:
  - tokens (colour, type, spacing) with light and dark themes;
  - components (app shell, nav, cards, tables, forms, dialogs, toasts, status pills,
    empty states that teach);
  - WCAG 2.2 AA.
- **Playground.** Compare, Search and Browser are kept, and move under
  *Playground*.

---

## 6. Plan and phases

Each phase ends with a working, tested product and a review with you.

**Phase 0: design and prototype (about 3–4 days)**
- **Build:**
  - validate personas and jobs to be done with you;
  - finish the navigation and screen flows;
  - a **clickable prototype** of Home, Connectors (add flow), Skills studio,
    Models & keys, Code capability and Activity trace;
  - design tokens;
  - Admin API v1 specification;
  - a schema inventory of every setting.
- **Exit:** you walk through the prototype and approve or redirect.

**Phase 1: foundation (about 2 weeks)**
- **Build:**
  - resource store with revisions, validation and the reconciler (hot reload);
  - secrets vault with Test and Rotate;
  - Admin API v1;
  - migration from `.env` and the JSON files;
  - app shell (nav, command palette, status pill);
  - **Home health board and alerts**;
  - **Settings: Models & keys, Personalization, Policies, Devices, Data (backup and
    restore), About**;
  - **Capabilities: Voice, Browser, Search, Desktop, Code**, where the code toggle
    and workspace picker replace `panini:code`;
  - audit log.
- **Exit criteria:**
  - every setting in `.env.example` is editable in the UI;
  - rotating a key is tested live;
  - a broken key shows an alert within 1 minute;
  - restore works on every kind.

**Phase 2: build studio (about 2 weeks)**
- **Build:**
  - Connectors directory, custom connectors, per-tool permissions and test;
  - Skills studio (describe / configure / try, import and export, versions);
  - Profiles, with a profile switcher in the avatar;
  - Automations: schedules, watches, and hooks with templates;
  - Agents (A2A);
  - Memory settings;
  - the agent can make config changes through the API, with approval.
- **Exit criteria:**
  - a connector is added and tested in under 2 minutes, and a skill in under 3;
  - zero file edits;
  - every change is undoable.

**Phase 3: operate (about 1.5 weeks)**
- **Build:**
  - Activity with run traces;
  - Usage & cost with budgets;
  - alerts;
  - diagnostics bundle;
  - Extensions (install from file or URL, permissions review);
  - viewer role;
  - **minimal Knowledge**: per-profile files and folders (text, Markdown, PDF),
    indexed with local embeddings and full-text search, with answers that cite them.
- **Exit criteria:**
  - any run can be explained from its trace;
  - a budget alert fires in a test.

**Phase 4: later**
- Knowledge, advanced: cloud-drive sync, OCR for scans, watched folders.
- An extension marketplace.
- A signed-in remote portal over HTTPS.
- Multiple people with their own profiles.

**Testing throughout:**
- schema and API unit tests;
- reconciler tests (hot reload without restart);
- Playwright end-to-end flows for each job to be done ("add connector", "rotate key",
  "new skill", "turn on code");
- accessibility checks (axe);
- a usability check against the success measures in section 1.

---

## 7. Decisions

Recommended on 2026-09-25, awaiting your go.

| # | Decision | Recommendation |
| --- | --- | --- |
| 1 | Scope | **The whole console becomes PANINI Admin.** Home is a health and attention board. Compare, Search and Browser move under *Playground*, and the old URLs redirect so bookmarks and extension flows keep working. |
| 2 | Frontend | **A React 19 single-page app** on the repo's existing stack: TanStack Router (single-page mode, no server rendering), Tailwind 4, Vite 8, TypeScript, Vitest and Playwright. It uses **schema-driven forms** generated from the backend's JSON Schemas. The phone companion stays plain HTML. |
| 3 | Profiles | **Yes, with progressive disclosure.** There is always one default "PANINI"; the switcher appears only once a second profile exists. A profile is the unit of instructions, tools and permissions (least privilege). |
| 4 | Knowledge | **A minimal version moves to Phase 3**, right after Profiles: per-profile files and folders, local embeddings and full-text search, and mandatory citations. Sync, OCR and watched folders stay in Phase 4. |
| 5 | Reach | **The portal stays on this PC only.** The phone keeps its task-focused companion. Remote admin comes later, and only with HTTPS and passkeys. |
| 6 | Start | **Phase 0 first**: a clickable prototype with realistic data, tested against five tasks, plus the API spec and schema inventory in parallel. |

Original questions:

1. **Scope of "Admin".** The whole console becomes PANINI Admin, and Compare, Search
   and Browser move to *Playground* (recommended). Or keep the JevRev pages as the
   home and add Admin beside them?
2. **Frontend stack.** A React SPA reusing `panini-core/capabilities/search`'s stack (recommended for a
   forms-heavy portal), or stay with plain HTML modules?
3. **Profiles.** Multiple assistants with their own instructions and tools
   (recommended, phase 2), or only the one PANINI?
4. **Knowledge / files.** Keep it in phase 4 (recommended), or bring it into phase 2?
5. **Where the portal is reachable.** Localhost only (recommended). The phone keeps its
   own limited companion.
6. **Start with Phase 0.** A clickable prototype you review before engineering starts
   (recommended).

---

## 8. Shipped (2026-09-25)

The first release of PANINI Admin is live at `http://localhost:3200/` (sign in from the
tray: **Open PANINI Admin**). It follows `docs/PANINI_Design_Thinking_Documents`:

- Algorithmic Antiquity tokens: Obsidian `#0A0A0C`, Zinc `#18181B`, Copper `#B87333`, Amber `#FFBF00`
  (reserved for active execution), Gold `#D4AF37` (success) and Slate `#E2E8F0`.
- Segoe UI Light headings, JetBrains Mono / Consolas telemetry, and faint Devanagari subscripts.
- Motion that "derives" lists, a 12 BPM breath, and an amber success flash.

| Area | What you can do without code | Where |
| --- | --- | --- |
| **Home: The PANINI Genesis** | Animated Algorithmic P: nodes derive outward from the root (Dhatu), breathe at 12 BPM, and fire Sutra threads to the capability nodes. Live health board with *Fix* links, needs-you, today, capabilities, quick actions. | `panini-core/apps/admin/src/genesis/`, `pages/Home.tsx` |
| Keys & models | Replace keys (write-only, masked, **tested before saving**), test the current key, choose models | `/settings/keys` |
| Capabilities | Voice, Browser (extension pairing), Search, Desktop toggle, Code toggle, workspaces, sandbox, hooks editor | `/capabilities/*` |
| Build | Skills editor, connectors (directory, remote/local, encrypted secrets, sign-in, try a read-only tool), automations and watches, A2A agents | `/build/*` |
| Operate | Inbox (approvals, results, suggestions), Activity with run traces, Memory, Devices (extension and phone), System, **Audit log with restore** | `/inbox`, `/runs`, `/memory`, `/settings/*`, `/audit` |
| Playground | Compare (moved to `/playground/compare`), Search, Browser, live view | `/playground` |

How it works:

- **Backend:** `backend/admin/` holds the settings catalogue (schema-driven), `.env` editing that keeps
  comments, key tests, health probes and the audit log. `backend/routes/admin.py` serves Admin API v1.
- **Safety:**
  - secrets are never returned or logged;
  - rejected keys are never saved;
  - Windows paths are stored with `/`;
  - restart-only settings are flagged;
  - built-in skills are read-only;
  - only read-only connector tools can be tried from the console.
- **Compatibility:**
  - if the Admin app is not built, `/` still serves Compare;
  - `/browser`, `/search`, `/activity` and `/agent` are unchanged.

Not yet built from the plan: Profiles (multiple assistants), Knowledge files, usage and cost,
extensions/marketplace, and the viewer role.


