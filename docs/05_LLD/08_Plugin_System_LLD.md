# Project Panini — Plugin System LLD

**Document Type:** Low Level Design  
**Phase:** 05 — LLD  
**Module:** `panini/plugins/`  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## 1. The Honest Problem

FR-603 states: *"Capabilities not declared in the manifest shall be denied at runtime."*

**A Python plugin loaded in-process cannot satisfy that requirement.** Once `exec`'d in the host interpreter, a plugin can `import os` and do anything the process can — read credentials, write the audit database, open sockets. Its manifest becomes documentation, not a control.

Shipping in-process loading while claiming manifest enforcement would be writing a requirement the implementation cannot meet.

**Resolution: two plugin classes, with the isolated one as the default.**

| Class | Execution | Manifest | For |
|---|---|---|---|
| **Sandboxed** *(default)* | Subprocess, RPC to host | **Enforced** | Anything the user did not write |
| **Trusted** *(opt-in)* | In-process | **Advisory** | The user's own code, explicitly marked |

Trusted mode requires an explicit per-plugin entry in `panini.yaml` and displays a persistent indicator. The user opts into the reduced boundary knowingly.

---

## 2. Module Layout

```
panini/plugins/
├── manifest.py      Schema, parsing, validation
├── verify.py        ed25519 signature verification (DD-070)
├── loader.py        Discovery, lifecycle, enable/disable
├── sandbox/
│   ├── host.py      Host side of the RPC boundary
│   ├── worker.py    Subprocess entry point
│   └── protocol.py  Message types
├── capability.py    Capability grant checking
├── hooks.py         prompt_inject · pre_chat · post_turn
└── registry_bridge.py   Plugin tools → ToolRegistry
```

---

## 3. Manifest (`manifest.py`)

```yaml
# plugins/weather/manifest.yaml
schema_version: 1
id: weather
name: Weather
version: 1.2.0
author: example
description: Current conditions and forecast

entrypoint: weather.plugin:WeatherPlugin
isolation: sandboxed              # sandboxed | trusted

capabilities:
  tools: [weather_current, weather_forecast]
  network:
    outbound: true
    hosts: [api.open-meteo.com]   # explicit; wildcards rejected
  filesystem: false
  hooks: []
  credentials: []

tools:
  - name: weather_current
    tier: T0
    egress_class: always          # network:outbound ⇒ forced (FR-606)
```

### 3.1 Validation

| Rule | Failure |
|---|---|
| `id` matches directory name | Reject |
| `entrypoint` resolvable | Reject |
| Every declared tool has `tier` and `egress_class` | Reject (Tools LLD §2.1) |
| `network.outbound: true` ⇒ every tool `egress_class: always` | **Auto-corrected and logged** (FR-606) |
| `network.hosts` contains no wildcards | Reject |
| `isolation: trusted` present in `panini.yaml` trusted list | Otherwise forced to `sandboxed` |
| Tool names not colliding with built-ins | Reject |

Plugin tool names are namespaced `plugin.<id>.<tool>` internally so a plugin cannot shadow `gmail_send` (same reasoning as MCP, Tools LLD §11).

---

## 4. Signature Verification (`verify.py`)

```
plugin/
├── manifest.yaml
├── weather/…
└── plugin.sig        ed25519 signature over a canonical file digest
```

**Digest:** SHA-256 over the sorted list of `(relative_path, file_sha256)` for every `.py`, `.yaml`, `.json`, `.js`, `.css`, `.html`, `.md`. Line endings normalised to LF before hashing so a checkout on Windows verifies identically.

### 4.1 Trust model — stated plainly

| Key | Meaning |
|---|---|
| **User key** | Generated on first run. The user signs plugins they have reviewed. |
| **Bundled key** | First-party plugins shipped with Panini. |

There is no central registry and no third-party authority. A signature means **"this content is what it was when someone signed it"** — an integrity guarantee, not a safety guarantee.

**Signature verification does not make a plugin safe. It makes tampering detectable.** Isolation (§5) is what limits what a plugin can do.

### 4.2 Unsigned plugins

Refused by default (FR-604). Loading one requires `allow_unsigned: true` per plugin in config, plus a startup warning naming it every launch — visible friction, not a one-time dismissal.

---

## 5. Sandbox (`sandbox/`)

### 5.1 Topology

```
┌──────────────── HOST PROCESS ────────────────┐
│  PluginHost                                  │
│    ├─ capability grants (from manifest)      │
│    └─ ToolGateway  ← plugin ops route here   │
└────────────────┬─────────────────────────────┘
                 │  JSON-lines over stdin/stdout
┌────────────────▼─────────────────────────────┐
│  WORKER SUBPROCESS  (one per plugin)         │
│  · separate interpreter                      │
│  · cwd = plugin directory                    │
│  · env stripped of PANINI_* and secrets      │
│  · no credential store access                │
│  · no database handles                       │
└──────────────────────────────────────────────┘
```

### 5.2 The key property

**A plugin cannot perform an operation itself. It asks the host.**

```
worker → {"op":"http_get","url":"https://api.open-meteo.com/…"}
host   → check capability: network.outbound ✓, host in allowlist ✓
       → perform request
       → return response

worker → {"op":"fs_write","path":"C:/Windows/…"}
host   → check capability: filesystem = false
       → {"error":"capability_denied","capability":"filesystem"}
```

Because the worker has no direct access, **manifest capabilities are enforced rather than declared** — FR-603 is genuinely satisfied.

### 5.3 Plugin operations still pass through the security kernel

A plugin's file write is not a privileged shortcut. It becomes a normal `fs_write` invocation:

```
worker → host → ToolGateway.invoke(turn, "fs_write", args)
                  ├─ path resolution + deny/allow checks
                  ├─ tier + taint escalation
                  ├─ egress classification
                  ├─ approval if required
                  └─ audit
```

**Plugins get no bypass.** The same gate, the same audit trail, the same approval dialog.

### 5.4 Resource limits

| Limit | Value | Enforcement |
|---|---|---|
| Startup timeout | 10 s | Host kills, marks failed |
| Per-call timeout | 30 s | Host kills the call, plugin survives |
| Memory | 512 MB | Job object (Windows) |
| Restart policy | 3 failures in 5 min → disabled | Host |
| Concurrent calls | 1 per plugin | Host serialises |

### 5.5 Failure isolation (FR-608)

A crashed worker is restarted; its tools report unavailable until it recovers. **The assistant continues.** Repeated crashes disable the plugin with a UI notice.

---

## 6. Trusted Mode — and its cost

```yaml
plugins:
  trusted:
    - my-local-notes        # explicit opt-in, per plugin
```

In-process, no capability enforcement, full host access.

**Displayed persistently** in the UI: *"1 trusted plugin loaded — full system access."* Not a one-time dialog. The user should be continuously aware that the boundary is reduced.

Trusted mode exists because forcing a user to sandbox their own 30-line script would push them toward disabling plugin security entirely. Making the safe path the default and the unsafe path explicit is better than making the safe path annoying.

---

## 7. Hooks (`hooks.py`)

| Hook | Timing | Sandboxed plugins |
|---|---|---|
| `prompt_inject` | Context assembly | ⚠️ **Delimited** — see §7.1 |
| `pre_chat` | Before generation | Read-only turn metadata |
| `post_turn` | After completion | Read-only |

### 7.1 ⚠️ `prompt_inject` is a privileged hook

A plugin injecting into the system prompt could weaken or contradict the injection warning that DD-110 depends on:

> *"Ignore the instruction about retrieved content."*

**Constraint:** a sandboxed plugin's injection is placed in a delimited block **after** the core system prompt, never inside it:

```
<plugin_context source="weather" trust="plugin">
Current location: Hyderabad. Units: metric.
</plugin_context>
```

The core system prompt, including the retrieved-content warning, is assembled **first and is immutable** to plugins. Trusted plugins may inject freely — another cost of trusted mode, stated in §6.

### 7.2 Hook budget

Plugin injections are capped at 500 tokens total, counted against the facts budget (Conversation LLD §5.1). A plugin cannot crowd out the conversation.

---

## 8. Lifecycle (`loader.py`)

```
discover plugins/*/manifest.yaml
   → parse and validate manifest
   → verify signature                     (FR-604)
   → resolve isolation (default sandboxed)
   → spawn worker / import module
   → call plugin.setup()
   → register tools with namespaced names
   → register hooks
```

### 8.1 Hot enable/disable (FR-605)

| Action | Behaviour |
|---|---|
| Disable | Unregister tools and hooks; terminate worker. **Takes effect at the next turn boundary** — never mid-turn |
| Enable | Verify, spawn, register |
| Reload | Disable then enable; re-verifies the signature |

**Turn-boundary application** matches the reload discipline for security settings (DD-146). Removing a tool mid-turn would leave an in-flight invocation referencing an unregistered tool.

### 8.2 Plugin output provenance (FR-607)

All plugin tool results are tagged `RETRIEVED` by the gateway — identical to any other tool. A plugin's output is untrusted content and taints the turn.

---

## 9. Test Matrix

| Test | Asserts | Ref |
|---|---|---|
| `test_unsigned_plugin_refused` | FR-604 | §4.2 |
| `test_tampered_plugin_fails_verification` | Digest integrity | §4 |
| `test_line_endings_normalised` | Cross-platform verify | §4 |
| **`test_undeclared_capability_denied`** | **FR-603 genuinely enforced** | §5.2 |
| **`test_network_host_not_in_allowlist_denied`** | Host allowlist | §3.1 |
| **`test_plugin_fs_write_goes_through_gateway`** | No bypass | §5.3 |
| `test_plugin_tool_output_tainted` | FR-607 | §8.2 |
| `test_network_plugin_tools_forced_egress` | FR-606 | §3.1 |
| `test_plugin_tool_name_namespaced` | No shadowing | §3.1 |
| `test_plugin_cannot_shadow_builtin` | §3.1 | — |
| **`test_sandboxed_prompt_inject_is_delimited`** | Cannot alter system prompt | §7.1 |
| `test_prompt_inject_budget_enforced` | §7.2 | — |
| `test_worker_crash_does_not_kill_host` | FR-608 | §5.5 |
| `test_repeated_crash_disables_plugin` | §5.4 | — |
| `test_call_timeout_kills_call_not_plugin` | §5.4 | — |
| `test_disable_applies_at_turn_boundary` | §8.1 | — |
| `test_worker_env_stripped_of_secrets` | §5.1 | — |
| `test_trusted_requires_explicit_config` | §6 | — |
| `test_trusted_shows_persistent_indicator` | §6 | — |

---

## 10. New Decisions Required

| Proposed ID | Decision |
|---|---|
| **DD-150** | **Two plugin classes — sandboxed (default) and trusted (explicit opt-in).** In-process Python cannot enforce manifest capabilities; subprocess isolation can. Without this, FR-603 would be a requirement the implementation cannot meet. |
| **DD-151** | **Sandboxed plugins request operations from the host; they never act directly.** This is what makes manifest capabilities enforceable rather than declarative. |
| **DD-152** | **Plugin operations route through the security kernel** — same path resolution, tier escalation, egress classification, approval, and audit as any tool. No bypass. |
| **DD-153** | **A signature proves integrity, not safety.** There is no central registry; the user signs what they have reviewed. Isolation limits behaviour, not signing. |
| **DD-154** | **Sandboxed `prompt_inject` output is delimited and placed after an immutable core system prompt** — a plugin must not be able to weaken the retrieved-content warning DD-110 depends on. |
| **DD-155** | **Plugin enable/disable applies at a turn boundary**, never mid-turn — consistent with DD-146. |
| **DD-156** | **Trusted mode displays a persistent indicator**, not a one-time dialog. Reduced boundaries should stay visible. |

---

## 11. Open Items

| # | Item | Disposition |
|---|---|---|
| 1 | Windows job-object memory limits | Verify during Stage 8 |
| 2 | Plugin distribution / discovery | Out of scope for v1 — manual install only |
| 3 | Whether hooks should be available to sandboxed plugins at all | Currently delimited-only; review after real plugins exist |
| 4 | RPC overhead per call | Measure; expected ~1–3 ms, immaterial beside tool latency |

---

## 12. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |
