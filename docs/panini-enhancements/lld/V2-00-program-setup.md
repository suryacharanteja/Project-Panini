# V2-00: Program setup

| Field | Value |
| --- | --- |
| Phase / priority / size | Phase 0 · P0 · S (1–2 days) |
| Depends on | — |
| Flag | — (introduces the flag mechanism) |
| Status | Not started |

## 1. Outcome

The repository is ready for v2: the OpenDots snapshot is a clean, ignored reference instead of a fake
capability; every v2 component has a typed feature flag visible in Admin; the harness database has numbered
migrations; and third-party notices cover what v2 will harvest.

## 2. Scope and non-goals

- In scope: move the OpenDots snapshot, flag module, Admin "PANINI v2" settings group, migration runner,
  notices, branch/PR conventions.
- Not in scope: any behaviour change. Every flag defaults off.

## 3. Current state

- `panini-core/capabilities/new-capablities/` holds a 12 MB OpenDots snapshot (8.2 MB of demo media), no
  `.git`, untracked. Living under `capabilities/` makes it look like a capability to people and tools.
- Flags are ad-hoc `os.getenv(...)` reads (for example `PANINI_AGENT_DEFAULT` in `backend/panini/planning.py:204`,
  `PANINI_JEV_GATES` in `backend/config.py:18`).
- `backend/harness/store.py` applies one `SCHEMA` string with `CREATE … IF NOT EXISTS`; modules such as
  `harness/mobile.py` and `harness/learning.py` create their own tables in constructors. There is no version
  table, so column changes cannot be expressed safely.
- `THIRD_PARTY_NOTICES.md` describes packaging-time licence collection only.

## 4. Harvest

| Source | What we take | Adaptation |
| --- | --- | --- |
| OpenDots `.env.example` | Grouped, commented configuration with setup-state behaviour ("the UI opens in setup state until these fields are configured") | Admin settings group with `help` text and a readiness view per v2 component |
| OpenDots `deployment/computers/harden-supervisor.mjs` | "Fail closed if the pinned upstream contract changes" | Same rule for every pinned upstream we build from (Playwright, OpenBot): the build checks a known marker and refuses on mismatch |

## 5. Design

### 5.1 Components and files

| File | Change |
| --- | --- |
| `references/opendots/` | Moved from `panini-core/capabilities/new-capablities/` (keep `LICENSE`, `README.md`, `src/`, `deployment/`, `docs/`, `tests/`) |
| `references/README.md` | What each reference is, its upstream, snapshot date, licence, and "never imported at runtime" |
| `.gitignore` | Add `references/*/` except `references/README.md`; demo media is never committed |
| `backend/v2flags.py` | Typed flag registry |
| `backend/admin/schema.py` | New group `v2` with one `Setting` per flag (`advanced=True`, `restart=True` where needed) |
| `backend/harness/migrations/__init__.py` | Migration runner |
| `backend/harness/migrations/0001_baseline.sql` | No-op marker for the current schema |
| `backend/harness/store.py` | Calls the runner after `SCHEMA` |
| `THIRD_PARTY_NOTICES.md` | Section "Harvested sources" (OpenDots, OpenBot, Playwright, AG-UI) |
| `docs/panini-enhancements/` | This program's documents (already created) |

### 5.2 Interfaces

```python
# backend/v2flags.py
"""PANINI v2 feature flags. Each flag is read once per call site evaluation, from the environment
(.env / Admin). Defaults are off until the item's phase gate passes (see the v2 register)."""
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Flag:
    key: str
    item: str          # "V2-02"
    default: str       # "false" | "legacy" | "off" ...
    choices: tuple[str, ...] = ("true", "false")
    help: str = ""

FLAGS = {f.key: f for f in [
    Flag("PANINI_V2_REGISTRY", "V2-02", "false", help="Serve tools from capability manifests."),
    Flag("PANINI_V2_POLICY", "V2-03", "shadow", ("off", "shadow", "enforce"),
         help="Policy engine: shadow logs decisions next to v1 approvals; enforce makes them binding."),
    Flag("PANINI_V2_LEDGER", "V2-04", "false"),
    Flag("PANINI_V2_INTERRUPTS", "V2-05", "false"),
    Flag("PANINI_V2_ONE_BRAIN", "V2-06", "false"),
    Flag("PANINI_V2_AGUI", "V2-07", "false"),
    Flag("PANINI_BROWSER_DRIVER", "V2-08", "legacy", ("legacy", "playwright")),
    Flag("PANINI_CLAW_CDP", "V2-09", "false"),
    Flag("PANINI_SANDBOX", "V2-11", "false"),
    Flag("PANINI_LIVE_VIEW", "V2-12", "false"),
    Flag("PANINI_GUARDIAN", "V2-13", "false"),
    Flag("PANINI_REMOTE_MODE", "V2-14", "off", ("off", "lan", "tailscale", "relay")),
    Flag("PANINI_PHONE_LINE", "V2-16", "false"),
    Flag("PANINI_CHANNELS", "V2-17", "", (), help="Comma list: slack,telegram,whatsapp,email"),
    Flag("PANINI_PERSONAS", "V2-18", "false"),
    Flag("PANINI_SPACES", "V2-19", "false"),
    Flag("PANINI_OUTBOUND_CALLS", "V2-20", "false"),
    Flag("PANINI_ANDROID", "V2-21", "false"),
    Flag("PANINI_TEACH", "V2-22", "false"),
    Flag("PANINI_MCP_SERVER", "V2-23", "false"),
    Flag("PANINI_PRESENCE", "V2-24", "false"),
    Flag("PANINI_TRIGGERS", "V2-25", "false"),
    Flag("PANINI_DESKTOP_TOOLS", "V2-26", "coarse", ("coarse", "fine")),
    Flag("PANINI_LOCAL_MODE", "V2-27", "off", ("off", "fallback", "private")),
]}

def value(key: str) -> str:
    flag = FLAGS[key]                     # KeyError for unknown flags: typos fail loudly in tests
    raw = os.getenv(key, flag.default).strip().lower()
    if flag.choices and raw not in flag.choices:   # choices=() means free text (for example a comma list)
        return flag.default
    return raw

def enabled(key: str) -> bool:
    return value(key) not in {"", "false", "off", "legacy", "0", "no"}
```

```python
# backend/harness/migrations/__init__.py
"""Numbered, forward-only SQLite migrations. File names: NNNN_short_name.sql (or .py exposing
apply(connection)). Each runs once, in order, inside a transaction, and is recorded in schema_migrations."""
def migrate(connection, directory=Path(__file__).parent) -> list[str]: ...
```

### 5.3 Storage

```sql
CREATE TABLE IF NOT EXISTS schema_migrations(id TEXT PRIMARY KEY, applied REAL NOT NULL);
```

Migration numbers written in the LLDs (`0002_eval` … `0017_triggers`) follow the planned order and are
provisional: when an item is implemented, it takes the next free number.

Rules: migrations never edit earlier files; destructive changes are new tables plus copy; every migration has
a test that runs it on a v1-shaped database fixture (`backend/tests/fixtures/harness-v1.sqlite3`, created
from the current `SCHEMA` by the test itself).

### 5.4 API and events

`GET /api/admin/v1/settings` already lists settings; the new `v2` group appears automatically. Add
`GET /api/harness/v2/status` returning `{flags: {key: value}, items: {id: {flag, enabled}}}` for the
register and Admin "Build" page.

### 5.5 Flows

Startup: `Store.__init__` → `executescript(SCHEMA)` → `migrate(connection)` → modules that create their own
tables keep doing so until their owning item moves them into migrations.

### 5.6 Policy and security

- `references/` is never on `sys.path`, never bundled, never imported. A test asserts no module under
  `backend/` or `panini-core/` imports from `references`.
- Moving the snapshot removes its `.env.example` from any glob that loads env files.

### 5.7 Failure modes

| Failure | Behaviour |
| --- | --- |
| A migration fails | Transaction rolls back; the backend logs the id and refuses to start the harness (the coordinator still serves v1 turns without the harness, as today when harness is disabled) |
| Unknown flag key in code | `KeyError` at import time in tests |

### 5.8 Configuration

All flags above, editable in Admin → Settings → PANINI v2 (advanced).

## 6. Implementation steps

1. Move the snapshot to `references/opendots/`, add `references/README.md`, update `.gitignore`.
2. Add `backend/v2flags.py` with tests; add the `v2` Admin group generated from `FLAGS`.
3. Add the migration runner, `0001_baseline.sql`, wire it into `Store`; tests on a v1 fixture.
4. Add `GET /api/harness/v2/status` and show it on the Admin Build page (one table).
5. Add the "Harvested sources" section to `THIRD_PARTY_NOTICES.md`; add `scripts/check-pins.mjs` that
   verifies `node_modules/playwright-core/package.json` is `1.63.0` and the OpenBot pin string in
   `references/opendots/compose.computers.yml` (used by later items' builds).

## 7. Tests

| Level | Test | File |
| --- | --- | --- |
| Unit | Flag defaults, choices, unknown key | `backend/tests/test_v2flags.py` |
| Unit | Migrations: order, idempotence, failure rollback, v1 fixture upgrade | `backend/tests/harness/test_migrations.py` |
| Unit | No runtime import of `references/` | `backend/tests/test_boundaries.py` (extend) |
| Integration | Admin settings include the `v2` group | `backend/tests/test_admin.py` (extend) |

## 8. Acceptance criteria

- [ ] `panini-core/capabilities/` contains only `browser`, `cli`, `desktop`, `search`.
- [ ] `git status` shows no OpenDots files; `references/README.md` is tracked.
- [ ] Every flag in `FLAGS` appears in Admin with help text; all default off.
- [ ] `schema_migrations` exists on a fresh and on an upgraded v1 database.
- [ ] `npm test` green.

## 9. Rollout and rollback

No behaviour change. Rollback is a revert.

## 10. Open questions

- Keep the OpenDots demo media at all? Recommendation: delete `references/opendots/docs/demos/*.mp4|*.gif`
  locally; the README links the upstream recordings.
