"""PANINI Admin API v1: everything the Admin portal can configure, without files or code.

Console routes: the gateway admits only a signed-in console session (ConsoleBoundary).
Secrets are write-only; responses never contain key values or connector secrets.
"""

import json
import platform
import re
import sys
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..admin.service import Admin, AdminError

router = APIRouter(prefix="/api/admin/v1")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "TurnEnd")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsPatch(Strict):
    changes: dict


class ConnectorSpec(Strict):
    kind: str = Field(pattern="^(remote|local)$")
    url: str = Field(default="", max_length=2000)
    command: str = Field(default="", max_length=500)
    args: list[str] = Field(default_factory=list, max_length=50)
    env: dict[str, str] | None = None  # Secret values; stored in the vault.
    headers: dict[str, str] | None = None  # e.g. a fixed API key; stored in the vault.
    description: str = Field(default="", max_length=300)


class AgentSpec(Strict):
    url: str = Field(min_length=8, max_length=2000)
    description: str = Field(default="", max_length=300)
    token: str | None = Field(default=None, max_length=4000)


class SkillSpec(Strict):
    description: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1, max_length=20000)


class HooksSpec(Strict):
    hooks: dict


class ToolCall(Strict):
    arguments: dict = Field(default_factory=dict)


class BackgroundGrant(Strict):
    tool: str = Field(min_length=1, max_length=200)
    allowed: bool


def admin(request: Request) -> Admin:
    state = request.app.state
    if getattr(state, "admin", None) is None:
        harness = getattr(state, "harness", None)
        state.admin = Admin(request.app, harness.store if harness else None)
    return state.admin


def harness(request: Request):
    h = request.app.state.harness
    if h is None:
        raise HTTPException(409, "Turn on the agent harness to manage this.")
    return h


def _name(name):
    if not NAME.match(name):
        raise HTTPException(422, "Names use lowercase letters, digits and hyphens (2-41 characters).")
    return name


def _bad(exc):
    return HTTPException(422, str(exc).strip("'"))


class PersonaCreate(Strict):
    id: str = Field(min_length=1, max_length=41)
    values: dict = Field(default_factory=dict)


class PersonaPermissions(Strict):
    permissions: list[str] = Field(max_length=20)


class PersonaTest(Strict):
    text: str = Field(min_length=1, max_length=2000)


class PersonaSurface(Strict):
    surface: str = Field(min_length=1, max_length=150)
    persona_id: str = Field(min_length=1, max_length=41)


@router.put("/personas/surfaces/default")
async def persona_surface_default(request: Request, spec: PersonaSurface):
    try:
        harness(request).personas.bind_surface(spec.surface, spec.persona_id)
        return {"surface": spec.surface, "persona_id": spec.persona_id}
    except (ValueError, KeyError) as exc:
        raise _bad(exc) from None


@router.post("/personas/{persona_id}/test")
async def personas_test(request: Request, persona_id: str, spec: PersonaTest):
    from .. import v2flags

    if not v2flags.enabled("PANINI_PERSONAS"):
        raise HTTPException(409, "Personas are off. Enable PANINI_PERSONAS after qualification.")
    h = harness(request)
    try:
        h.personas.get(persona_id)
    except KeyError as exc:
        raise _bad(exc) from None
    agent = h.agent(delegate=None, capabilities=(), persona_id=persona_id, max_steps=1)
    agent.only_tools = set()  # Identity preview has no tool effects.
    # Use a depth-one preview so a mention in the test text cannot switch the chosen identity.
    agent.depth = 1
    result = await agent.run(spec.text)
    return {"persona_id": persona_id, "outcome": result.outcome, "message": result.message}


@router.get("/personas")
async def personas_list(request: Request):
    from .. import v2flags
    from ..personas.store import COLORS, PERMISSIONS

    h = harness(request)
    return {
        "personas": [h.personas.get(p["id"]) for p in h.personas.list()],
        "permission_options": list(PERMISSIONS),
        "colors": COLORS,
        "enabled": v2flags.enabled("PANINI_PERSONAS"),
        "surface_defaults": {
            row["surface"]: h.personas.surface_default(row["surface"])
            for row in h.store.all("SELECT * FROM persona_surfaces")
        },
    }


@router.post("/personas")
async def personas_create(request: Request, spec: PersonaCreate):
    try:
        return harness(request).personas.create(spec.id, **spec.values)
    except (ValueError, KeyError) as exc:
        raise _bad(exc) from None


@router.patch("/personas/{persona_id}")
async def personas_update(request: Request, persona_id: str, values: dict):
    try:
        return harness(request).personas.update(persona_id, **values)
    except (ValueError, KeyError) as exc:
        raise _bad(exc) from None


@router.put("/personas/{persona_id}/permissions")
async def personas_permissions(request: Request, persona_id: str, spec: PersonaPermissions):
    try:
        return harness(request).personas.set_permissions(persona_id, spec.permissions)
    except (ValueError, KeyError) as exc:
        raise _bad(exc) from None


@router.delete("/personas/{persona_id}")
async def personas_archive(request: Request, persona_id: str):
    try:
        harness(request).personas.archive(persona_id)
        return {"archived": True}
    except (ValueError, KeyError) as exc:
        raise _bad(exc) from None


@router.get("/personas/messages/{thread_id}")
async def persona_messages(request: Request, thread_id: str):
    return {
        "messages": harness(request).store.all(
            "SELECT m.*,p.name,p.color FROM persona_messages m JOIN personas p ON p.id=m.persona_id "
            "WHERE thread_id=? ORDER BY m.id DESC LIMIT 100",
            (thread_id,),
        )
    }


# -- Overview, health, system --------------------------------------------------
@router.get("/health")
async def health(request: Request, refresh: bool = False):
    return await admin(request).health(refresh=refresh)


@router.get("/overview")
async def overview(request: Request):
    h = request.app.state.harness
    since = time.time() - 24 * 3600
    today = {}
    if h:
        rows = h.store.all(
            "SELECT kind, COUNT(*) AS n FROM events WHERE at>? AND kind IN "
            "('verified_complete','partial','failed','blocked','evidence') GROUP BY kind",
            (since,),
        )
        today = {r["kind"]: r["n"] for r in rows}
    coordinator = request.app.state.panini
    return {
        "status": h.status() if h else None,
        "today": today,
        "capabilities": sorted(coordinator.adapters) if coordinator else [],
        "restart_needed": sorted(admin(request).restart_needed),
    }


@router.get("/system")
async def system(request: Request):
    from .. import config
    from ..extension_pairing import data_directory

    return {
        "version": request.app.version,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "runtime_root": str(config.ROOT),
        "data_directory": str(data_directory()),
    }


@router.get("/usage")
async def usage():
    """Tokens and cost per surface (LLM proposals, Jev decisions) since PANINI started."""
    from .. import usage_log

    return usage_log.summary()


# -- Settings, keys, audit -----------------------------------------------------
@router.get("/settings")
async def settings(request: Request):
    return admin(request).settings()


@router.patch("/settings")
async def update_settings(payload: SettingsPatch, request: Request):
    try:
        return await admin(request).update(payload.changes)
    except (AdminError, ValueError) as exc:
        raise _bad(exc) from None


@router.post("/keys/{which}/test")
async def test_key(which: str, request: Request):
    try:
        ok, detail = await admin(request).test_key(which)
    except AdminError as exc:
        raise HTTPException(404, str(exc)) from None
    return {"ok": ok, "detail": detail}


@router.get("/audit")
async def audit(request: Request):
    return {"entries": admin(request).audit_log()}


@router.post("/audit/{entry_id}/restore")
async def restore(entry_id: str, request: Request):
    try:
        return await admin(request).restore(entry_id)
    except AdminError as exc:
        raise _bad(exc) from None


# -- Connectors ------------------------------------------------------------------
@router.get("/connectors")
async def connectors(request: Request):
    h = harness(request)
    status = {row["name"]: row for row in await h.connectors.status()}
    specs = h.connectors.specs()
    rows = []
    for name, spec in specs.items():
        public = {k: v for k, v in spec.items() if k not in ("env", "headers")}
        public["kind"] = "remote" if spec.get("url") else "local"
        public["secret_keys"] = sorted((h.connectors.secrets.get(f"connector-env:{name}") or {}).keys())
        rows.append(
            {"name": name, "spec": public, **{k: v for k, v in status.get(name, {}).items() if k != "name"}}
        )
    return {"path": str(h.connectors.path), "connectors": rows}


@router.get("/connectors/receipts")
async def connector_receipts(request: Request):
    h = harness(request)
    return {
        "receipts": h.store.all(
            "SELECT id,server,tool,read_only,ok,created FROM connector_receipts "
            "ORDER BY created DESC LIMIT 50"
        )
    }


@router.put("/connectors/{name}")
async def save_connector(name: str, payload: ConnectorSpec, request: Request):
    h = harness(request)
    _name(name)
    if payload.kind == "remote":
        if not payload.url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise HTTPException(422, "Remote connectors need an https URL.")
        spec = {"url": payload.url, "description": payload.description}
        if payload.headers:
            spec["headers"] = payload.headers  # Remote headers stay in the file for the HTTP client.
        secret_env = None
    else:
        if not payload.command.strip():
            raise HTTPException(422, "Local connectors need a command.")
        spec = {"command": payload.command.strip(), "args": payload.args, "description": payload.description}
        secret_env = payload.env
    existed = name in h.connectors.specs()
    h.connectors.save(name, spec, secret_env)
    rows = await h.connectors.reload()
    admin(request).audit(
        "connector",
        name,
        "update" if existed else "create",
        f"{'Updated' if existed else 'Added'} connector {name} ({payload.kind})",
    )
    return next((r for r in rows if r["name"] == name), {"name": name})


@router.delete("/connectors/{name}")
async def delete_connector(name: str, request: Request):
    h = harness(request)
    try:
        h.connectors.remove(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    await h.connectors.reload()
    admin(request).audit("connector", name, "delete", f"Removed connector {name}")
    return {"ok": True}


@router.post("/connectors/{name}/tools/{tool}/try")
async def try_tool(name: str, tool: str, payload: ToolCall, request: Request):
    """Run a read-only tool from the console to check a connector works."""
    from ..harness.connectors import ConnectorError

    h = harness(request)
    try:
        server, found = h.connectors.tool(name, tool)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    if not found:
        raise HTTPException(404, f"{name} has no tool {tool}.")
    if not found["read_only"]:
        raise HTTPException(409, "Only read-only tools can be tried from the console.")
    try:
        return await server.call(tool, payload.arguments)
    except ConnectorError as exc:
        raise HTTPException(502, str(exc)) from None


@router.post("/connectors/{name}/background-grant")
async def background_grant(name: str, payload: BackgroundGrant, request: Request):
    h = harness(request)
    await h.connectors.status()
    try:
        granted = h.connectors.set_background_grant(name, payload.tool, payload.allowed)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    admin(request).audit(
        "connector",
        name,
        "background_grant",
        f"{'Allowed' if payload.allowed else 'Revoked'} {payload.tool} for background research",
    )
    return {"granted": granted}


# -- Agents PANINI can ask ------------------------------------------------------
@router.get("/agents")
async def agents(request: Request):
    h = harness(request)
    rows = []
    for name, spec in h.remotes.load().items():
        rows.append(
            {
                "name": name,
                "url": spec["url"],
                "description": spec.get("description", ""),
                "has_token": bool(spec.get("token") or h.remotes.secrets.get(f"agent-token:{name}")),
            }
        )
    return {"agents": rows, "clients": h.a2a_clients.list()}


@router.put("/agents/{name}")
async def save_agent(name: str, payload: AgentSpec, request: Request):
    h = harness(request)
    _name(name)
    if not payload.url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise HTTPException(422, "Agents need an https URL (or localhost).")
    h.remotes.save(name, payload.url, payload.description, payload.token)
    admin(request).audit("agent", name, "update", f"Saved agent {name}")
    return {"ok": True}


@router.delete("/agents/{name}")
async def delete_agent(name: str, request: Request):
    h = harness(request)
    try:
        h.remotes.remove(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    admin(request).audit("agent", name, "delete", f"Removed agent {name}")
    return {"ok": True}


# -- Skills ----------------------------------------------------------------------
@router.put("/skills/{name}")
async def save_skill(name: str, payload: SkillSpec, request: Request):
    h = harness(request)
    existing = next((s for s in h.skills.catalog() if s["name"] == name), None)
    if existing and existing["origin"] != "user":
        raise HTTPException(409, f"{name} is a {existing['origin']} skill. Save a copy under a new name.")
    try:
        skill = h.skills.create(name, payload.description, payload.instructions)
    except ValueError as exc:
        raise _bad(exc) from None
    admin(request).audit(
        "skill",
        name,
        "update" if existing else "create",
        f"{'Updated' if existing else 'Created'} skill {name}",
    )
    return skill


@router.delete("/skills/{name}")
async def delete_skill(name: str, request: Request):
    h = harness(request)
    try:
        h.skills.delete(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    admin(request).audit("skill", name, "delete", f"Deleted skill {name}")
    return {"ok": True}


# -- Hooks (PANINI-CLI, your own ~/.panini/hooks.json) --------------------------
def _hooks_file():
    from panini_cli.agent import hooks

    return hooks.Path.home() / ".panini" / "hooks.json"


@router.get("/hooks")
async def get_hooks():
    path = _hooks_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return {"path": str(path), "hooks": data.get("hooks") or {}, "events": list(HOOK_EVENTS)}


@router.put("/hooks")
async def save_hooks(payload: HooksSpec, request: Request):
    clean = {}
    for event, entries in payload.hooks.items():
        if event not in HOOK_EVENTS or not isinstance(entries, list):
            raise HTTPException(422, f"Unknown hook event {event}.")
        rows = []
        for entry in entries[:20]:
            if not isinstance(entry, dict) or not str(entry.get("command", "")).strip():
                raise HTTPException(422, "Every hook needs a command.")
            matcher = str(entry.get("matcher") or "")
            try:
                re.compile(matcher)
            except re.error:
                raise HTTPException(422, f"{matcher} is not a valid pattern.") from None
            rows.append(
                {
                    "matcher": matcher,
                    "command": str(entry["command"]).strip(),
                    "timeout": max(1, min(int(entry.get("timeout") or 30), 120)),
                }
            )
        if rows:
            clean[event] = rows
    path = _hooks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"hooks": clean}, indent=2), encoding="utf-8")
    temporary.replace(path)
    admin(request).audit(
        "hooks", "PANINI-CLI hooks", "update", f"Saved {sum(map(len, clean.values()))} hooks"
    )
    return {"path": str(path), "hooks": clean, "events": list(HOOK_EVENTS)}


# -- Identity and learning -------------------------------------------------------
class IdentitySpec(Strict):
    text: str = Field(default="", max_length=8000)


@router.get("/identity")
async def identity():
    from ..harness.learning import PERSONA, persona

    return {"text": persona(), "default": PERSONA.read_text(encoding="utf-8").strip()}


@router.put("/identity")
async def set_identity(spec: IdentitySpec):
    from ..harness.learning import set_persona

    try:
        return {"text": set_persona(spec.text)}
    except ValueError as exc:
        raise _bad(exc) from None


@router.get("/learning")
async def learning(request: Request):
    return harness(request).learning.summary()


@router.delete("/learning/lessons/{lesson_id}")
async def remove_lesson(lesson_id: str, request: Request):
    if not harness(request).learning.remove_lesson(lesson_id):
        raise HTTPException(404, "No such lesson.")
    return {"removed": lesson_id}
