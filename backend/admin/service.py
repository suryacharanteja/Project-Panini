"""PANINI Admin: settings, key tests, health probes and the audit log.

Settings live in PANINI's .env (the file every component already loads). A change is
validated against the catalogue in schema.py, written in place, applied to this
process immediately (os.environ) unless the setting needs a restart, and recorded in
the audit log. Secret values are never returned, logged or recorded; the audit keeps
only "changed". Non-secret changes can be restored from their audit entry.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx

from .. import config
from . import envfile
from .schema import BY_KEY, GROUPS, SETTINGS

HEALTH_TTL = 600


class AdminError(ValueError):
    pass


def env_path():
    return Path(config.ROOT) / ".env"


def mask(value):
    return f"••••{value[-4:]}" if value and len(value) > 8 else ("••••" if value else "")


def validate(setting, value):
    """Return the normalised string to store, or raise AdminError."""
    if value is None:
        return None
    if setting.type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        if text in ("true", "1", "yes", "on"):
            return "true"
        if text in ("false", "0", "no", "off", ""):
            return "false"
        raise AdminError(f"{setting.label} must be on or off.")
    if setting.type == "int":
        try:
            number = int(str(value).strip())
        except ValueError:
            raise AdminError(f"{setting.label} must be a whole number.") from None
        if setting.minimum is not None and number < setting.minimum:
            raise AdminError(f"{setting.label} must be at least {setting.minimum}.")
        if setting.maximum is not None and number > setting.maximum:
            raise AdminError(f"{setting.label} must be at most {setting.maximum}.")
        return str(number)
    if setting.type == "enum":
        text = str(value).strip()
        if text not in setting.options:
            raise AdminError(
                f"{setting.label} must be one of: {', '.join(o or '(default)' for o in setting.options)}."
            )
        return text
    if setting.type == "paths":
        paths = value if isinstance(value, list) else json.loads(value or "[]")
        cleaned = []
        for raw in paths:
            path = Path(str(raw).strip()).expanduser()
            if not str(raw).strip():
                continue
            if not path.is_absolute() or not path.is_dir():
                raise AdminError(f"{raw} is not an existing folder.")
            # Forward slashes: .env parsers treat backslashes as escapes, and Windows accepts both.
            cleaned.append(path.resolve().as_posix())
        return json.dumps(sorted(set(cleaned)))
    text = str(value).strip()
    if "\n" in text or "\r" in text:
        raise AdminError(f"{setting.label} must be a single line.")
    if setting.type == "secret" and text and len(text) < 8:
        raise AdminError(f"{setting.label} looks too short to be a key.")
    return text


class Admin:
    def __init__(self, app, store=None, clock=time.time):
        self.app, self.clock = app, clock
        self.store = store
        self._health = None
        self._health_at = 0.0
        self._lock = asyncio.Lock()
        self.restart_needed = set()
        if store is not None:
            store.execute(
                "CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL, "
                "kind TEXT NOT NULL, name TEXT NOT NULL, action TEXT NOT NULL, summary TEXT NOT NULL, "
                "diff TEXT NOT NULL DEFAULT '{}')"
            )

    # -- Audit ----------------------------------------------------------------
    def audit(self, kind, name, action, summary, diff=None, actor="person"):
        if self.store is None:
            return None
        entry_id = uuid4().hex
        self.store.execute(
            "INSERT INTO audit(id, at, actor, kind, name, action, summary, diff) VALUES (?,?,?,?,?,?,?,?)",
            (entry_id, self.clock(), actor, kind, name[:200], action, summary[:500], json.dumps(diff or {})),
        )
        return entry_id

    def audit_log(self, limit=200):
        if self.store is None:
            return []
        rows = self.store.all("SELECT * FROM audit ORDER BY at DESC LIMIT ?", (limit,))
        return [{**r, "diff": json.loads(r["diff"])} for r in rows]

    # -- Settings -------------------------------------------------------------
    def settings(self):
        stored = envfile.read(env_path())
        rows = []
        for s in SETTINGS:
            current = os.environ.get(s.key)
            in_file = stored.get(s.key)
            source = "file" if in_file is not None else ("environment" if current is not None else "default")
            value = current if current is not None else in_file
            row = {**s.view(), "source": source, "restart_pending": s.key in self.restart_needed}
            if s.type == "secret":
                row.update(configured=bool(value), masked=mask(value or ""))
            elif s.type == "paths":
                try:
                    row["value"] = json.loads(value) if value else []
                except ValueError:
                    row["value"] = []
            else:
                row["value"] = value if value is not None else s.default
            if source == "environment" and in_file is not None and in_file != current:
                row["overridden"] = True
            rows.append(row)
        return {"groups": GROUPS, "settings": rows, "restart_needed": sorted(self.restart_needed)}

    async def update(self, changes, actor="person"):
        """Validate everything first, then write once; returns the new settings view."""
        if not isinstance(changes, dict) or not changes:
            raise AdminError("Nothing to change.")
        normalised = {}
        for key, value in changes.items():
            setting = BY_KEY.get(key)
            if not setting:
                raise AdminError(f"{key} is not a PANINI setting.")
            normalised[key] = validate(setting, value)
            if key == "PANINI_LOCAL_BASE_URL" and normalised[key]:
                from panini_cli.agent.local_model import endpoint
                from panini_cli.agent.model import ModelError

                try:
                    endpoint(normalised[key])
                except ModelError as exc:
                    raise AdminError(str(exc)) from None
        for key, value in normalised.items():
            setting = BY_KEY[key]
            if setting.type == "secret" and value and setting.test:
                ok, detail = await self.test_key(setting.test, value)
                if not ok:
                    raise AdminError(f"{setting.label} was not saved: {detail}")
        before = envfile.read(env_path())
        envfile.update(env_path(), normalised)
        diff = {}
        for key, value in normalised.items():
            setting = BY_KEY[key]
            if setting.restart:
                self.restart_needed.add(key)
            else:
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if setting.type == "secret":
                diff[key] = {"secret": True}
            else:
                diff[key] = {"old": before.get(key), "new": value}
        self._health = None  # Probe again with the new values.
        labels = ", ".join(BY_KEY[k].label for k in normalised)
        self.audit("settings", labels, "update", f"Changed {labels}", diff, actor)
        return self.settings()

    async def restore(self, entry_id, actor="person"):
        row = self.store.one("SELECT * FROM audit WHERE id=?", (entry_id,)) if self.store else None
        if not row or row["kind"] != "settings":
            raise AdminError("That change cannot be restored.")
        diff = json.loads(row["diff"])
        changes = {k: v["old"] for k, v in diff.items() if not v.get("secret")}
        if not changes:
            raise AdminError("Keys cannot be restored from history; enter the key again.")
        result = await self.update(
            {k: (v if v is not None else BY_KEY[k].default) for k, v in changes.items()}, actor
        )
        self.audit(
            "settings", row["name"], "restore", f"Restored the change from {time.ctime(row['at'])}", {}, actor
        )
        return result

    # -- Keys -----------------------------------------------------------------
    async def test_key(self, which, value=None):
        """(ok, detail). Uses the given value, or the configured one."""
        from .. import private_mode

        if private_mode.active():
            return False, private_mode.reason()
        if which == "openai":
            key = value or os.getenv("OPENAI_API_KEY", "")
            url, headers = "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}
        elif which == "assemblyai":
            key = value or os.getenv("ASSEMBLYAI_API_KEY", "")
            url, headers = "https://api.assemblyai.com/v2/transcript?limit=1", {"Authorization": key}
        else:
            raise AdminError("Unknown key test.")
        if not key:
            return False, "not configured"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError:
            return False, "the provider could not be reached; check the internet connection"
        if response.status_code == 200:
            return True, "accepted"
        if response.status_code in (401, 403):
            return False, "the provider rejected this key"
        return False, f"the provider answered HTTP {response.status_code}"

    # -- Health ---------------------------------------------------------------
    async def health(self, refresh=False):
        async with self._lock:
            if not refresh and self._health and self.clock() - self._health_at < HEALTH_TTL:
                return self._health
            checks = await asyncio.gather(
                self._check_key("openai", "OpenAI", "keys"),
                self._check_key("assemblyai", "AssemblyAI (dictation)", "keys"),
                self._check_configured("AI_GATEWAY_API_KEY", "AI Gateway", "keys"),
                self._check_search(),
                self._check_extension(),
                self._check_docker(),
                self._check_harness(),
                self._check_capabilities(),
            )
            flat = [c for group in checks for c in (group if isinstance(group, list) else [group])]
            worst = "ok"
            for c in flat:
                if c["state"] == "error":
                    worst = "error"
                elif c["state"] == "warning" and worst == "ok":
                    worst = "warning"
            self._health = {"state": worst, "checked": self.clock(), "checks": flat}
            self._health_at = self.clock()
            return self._health

    async def _check_key(self, which, label, fix):
        ok, detail = await self.test_key(which)
        state = "ok" if ok else ("warning" if detail == "not configured" and which != "openai" else "error")
        return {"id": which, "label": label, "state": state, "detail": detail.capitalize(), "fix": fix}

    async def _check_configured(self, key, label, fix):
        ok = config.configured(key)
        return {
            "id": key.lower(),
            "label": label,
            "state": "ok" if ok else "warning",
            "detail": "Configured" if ok else "Not configured",
            "fix": fix,
        }

    async def _check_search(self):
        worker = Path(config.ROOT) / "panini-core/capabilities/search/dist-worker/worker.mjs"
        ready = (
            worker.is_file()
            and config.configured("OPENAI_API_KEY")
            and config.configured("AI_GATEWAY_API_KEY")
        )
        detail = (
            "Ready"
            if ready
            else (
                "Search worker is not built (npm run build)"
                if not worker.is_file()
                else "Needs the OpenAI and AI Gateway keys"
            )
        )
        return {
            "id": "search",
            "label": "Search worker",
            "state": "ok" if ready else "warning",
            "detail": detail,
            "fix": "keys",
        }

    async def _check_extension(self):
        bridge = getattr(self.app.state, "extension_bridge", None)
        devices = self.app.state.pairing.devices() if getattr(self.app.state, "pairing", None) else []
        status = bridge.status() if bridge else {}
        if status.get("connected") and status.get("attached_tab"):
            state, detail = "ok", f"Connected · {status.get('tab_title') or 'tab attached'}"
        elif status.get("connected"):
            state, detail = "warning", "Connected, no tab attached"
        elif devices:
            state, detail = "warning", "Paired, not connected (open Chrome)"
        else:
            state, detail = "warning", "Not paired"
        return {
            "id": "extension",
            "label": "Chrome extension",
            "state": state,
            "detail": detail,
            "fix": "devices",
        }

    async def _check_docker(self):
        from panini_cli.agent import codex_sandbox, native_probe, native_sandbox, sandbox

        mode = os.getenv("PANINI_CLI_SANDBOX", "native" if os.name == "nt" else "none")
        if mode == "native":
            ready, reason = native_sandbox.available()
            if ready:
                ready, reason = await asyncio.to_thread(native_probe.verify_boundary)
            return {
                "id": "docker",
                "label": "PANINI native sandbox (experimental)",
                "state": "ok" if ready else "error",
                "detail": reason,
                "fix": "code",
            }
        if mode == "codex":
            ready, reason = await asyncio.to_thread(codex_sandbox.available, config.ROOT)
            return {
                "id": "docker",
                "label": "Native command sandbox",
                "state": "ok" if ready else "error",
                "detail": reason,
                "fix": "code",
            }
        ready, reason = await asyncio.to_thread(sandbox.available)
        wanted = os.getenv("PANINI_CLI_SANDBOX", "native" if os.name == "nt" else "none") == "docker"
        state = "ok" if ready else ("error" if wanted else "idle")
        return {
            "id": "docker",
            "label": "Docker sandbox",
            "state": state,
            "detail": "Running" if ready else reason,
            "fix": "code",
        }

    async def _check_harness(self):
        harness = getattr(self.app.state, "harness", None)
        if not harness:
            return {
                "id": "harness",
                "label": "Agent harness",
                "state": "warning",
                "detail": "Turned off",
                "fix": "agent",
            }
        job = harness.worker._job
        alive = job is not None and not job.done()
        return {
            "id": "harness",
            "label": "Background worker",
            "state": "ok" if alive else "error",
            "detail": "Running" if alive else "Stopped",
            "fix": "agent",
        }

    async def _check_capabilities(self):
        coordinator = getattr(self.app.state, "panini", None)
        if not coordinator:
            return []
        rows = []
        for name, adapter in coordinator.adapters.items():
            try:
                available = await asyncio.wait_for(adapter.availability(), 5)
            except Exception:  # noqa: BLE001 -- A capability that cannot answer is reported, not raised.
                available = {"state": "unavailable", "reason": "Did not respond"}
            ready = available.get("state") == "ready"
            rows.append(
                {
                    "id": f"capability:{name}",
                    "label": name.capitalize(),
                    "state": "ok" if ready else "idle",
                    "detail": "Ready" if ready else (available.get("reason") or "Unavailable"),
                    "fix": name if name in ("code", "desktop", "browser", "search", "voice") else "agent",
                }
            )
        return rows
