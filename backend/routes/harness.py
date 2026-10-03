"""Harness API: memories, skills, background tasks and reviews.

/api/harness/* are console routes (the gateway signs the person in). The shell
reads status and manages the same data through /api/panini/harness/* with its
own runtime credential.
"""

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..harness import a2a
from ..harness.memory import KINDS, MemoryError
from .panini import principal

router = APIRouter()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewMemory(Strict):
    text: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="fact", pattern="^(" + "|".join(KINDS) + ")$")


class EditMemory(Strict):
    text: str = Field(min_length=1, max_length=2000)


class NewSkill(Strict):
    name: str = Field(min_length=2, max_length=63)
    description: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1, max_length=20000)


class NewTask(Strict):
    goal: str = Field(min_length=1, max_length=4000)
    delay_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)
    every_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)
    condition: str | None = Field(default=None, max_length=1000)
    project_id: str | None = Field(default=None, min_length=32, max_length=32)


class NewProject(Strict):
    title: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=12000)


class EditProject(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    goal: str | None = Field(default=None, min_length=1, max_length=4000)
    context: str | None = Field(default=None, max_length=12000)
    status: str | None = Field(default=None, pattern="^(active|paused|completed)$")


class ApprovePlan(Strict):
    revision: int = Field(ge=1)


class NoticeUpdate(Strict):
    status: str = Field(pattern="^(read|dismissed)$")


class Decision(Strict):
    decision: str = Field(pattern="^(rerun|dismiss)$")
    answer: str = Field(default="", max_length=1000)


def harness(request: Request):
    h = request.app.state.harness
    if h is None:
        raise HTTPException(404, "The PANINI harness is disabled.")
    return h


def _bad(exc):
    return HTTPException(422, str(exc).strip("'"))


def build(prefix, guard):
    """The same resource set under a prefix with its own access check."""
    r = APIRouter(prefix=prefix)

    @r.get("/status")
    async def status(request: Request):
        guard(request)
        return harness(request).status()

    @r.get("/memories")
    async def memories(request: Request, q: str = ""):
        guard(request)
        h = harness(request)
        return {
            "memories": h.memory.search(q, limit=50, budget_chars=100_000) if q.strip() else h.memory.list()
        }

    @r.post("/memories")
    async def add_memory(payload: NewMemory, request: Request):
        guard(request)
        try:
            return harness(request).memory.add(payload.text, kind=payload.kind, source="person")
        except MemoryError as exc:
            raise _bad(exc) from None

    @r.patch("/memories/{memory_id}")
    async def edit_memory(memory_id: str, payload: EditMemory, request: Request):
        guard(request)
        try:
            if not harness(request).memory.update(memory_id, payload.text):
                raise HTTPException(404, "Unknown memory")
        except MemoryError as exc:
            raise _bad(exc) from None
        return {"ok": True}

    @r.delete("/memories/{memory_id}")
    async def forget_memory(memory_id: str, request: Request):
        guard(request)
        return {"deleted": harness(request).memory.forget(memory_id)}

    @r.delete("/memories")
    async def forget_everything(request: Request):
        guard(request)
        return {"deleted": harness(request).memory.forget(everything=True)}

    @r.get("/memories/export")
    async def export(request: Request):
        guard(request)
        return {"exported": time.time(), "memories": harness(request).memory.list(limit=100_000)}

    @r.get("/skills")
    async def skills(request: Request):
        guard(request)
        return {"skills": harness(request).skills.catalog()}

    @r.get("/skills/{name}")
    async def skill(name: str, request: Request):
        guard(request)
        try:
            return harness(request).skills.load(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from None

    @r.post("/skills")
    async def create_skill(payload: NewSkill, request: Request):
        guard(request)
        try:
            return harness(request).skills.create(payload.name, payload.description, payload.instructions)
        except ValueError as exc:
            raise _bad(exc) from None

    @r.get("/tasks")
    async def tasks(request: Request):
        guard(request)
        return {"tasks": harness(request).tasks.list()}

    @r.get("/projects")
    async def projects(request: Request):
        guard(request)
        return {"projects": harness(request).projects.list()}

    @r.post("/projects")
    async def create_project(payload: NewProject, request: Request):
        guard(request)
        try:
            return harness(request).projects.create(payload.title, payload.goal, payload.context)
        except ValueError as exc:
            raise _bad(exc) from None

    @r.get("/projects/{project_id}")
    async def project(project_id: str, request: Request):
        guard(request)
        h = harness(request)
        try:
            return {
                **h.projects.get(project_id),
                "tasks": [h.tasks.get(i) for i in h.projects.tasks(project_id)],
            }
        except KeyError:
            raise HTTPException(404, "Unknown project") from None

    @r.patch("/projects/{project_id}")
    async def edit_project(project_id: str, payload: EditProject, request: Request):
        guard(request)
        try:
            return harness(request).projects.update(project_id, **payload.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404, "Unknown project") from None
        except ValueError as exc:
            raise _bad(exc) from None

    @r.get("/projects/{project_id}/plan")
    async def project_plan(project_id: str, request: Request):
        guard(request)
        try:
            return {"plan": harness(request).project_planner.get(project_id)}
        except KeyError:
            raise HTTPException(404, "Unknown project") from None

    @r.post("/projects/{project_id}/plan")
    async def draft_project_plan(project_id: str, request: Request):
        guard(request)
        try:
            return await harness(request).project_planner.draft(project_id)
        except KeyError:
            raise HTTPException(404, "Unknown project") from None
        except ValueError as exc:
            raise _bad(exc) from None

    @r.post("/projects/{project_id}/plan/approve")
    async def approve_project_plan(project_id: str, payload: ApprovePlan, request: Request):
        guard(request)
        try:
            return harness(request).project_planner.approve(project_id, payload.revision)
        except KeyError:
            raise HTTPException(404, "Unknown project") from None
        except ValueError as exc:
            raise _bad(exc) from None

    @r.post("/projects/{project_id}/plan/cancel")
    async def cancel_project_plan(project_id: str, payload: ApprovePlan, request: Request):
        guard(request)
        try:
            return harness(request).project_planner.cancel(project_id, payload.revision)
        except KeyError:
            raise HTTPException(404, "Unknown project") from None
        except ValueError as exc:
            raise _bad(exc) from None

    @r.post("/tasks")
    async def submit(payload: NewTask, request: Request):
        guard(request)
        try:
            return harness(request).tasks.submit(
                payload.goal,
                run_at=time.time() + payload.delay_minutes * 60,
                every_minutes=payload.every_minutes,
                condition=payload.condition or None,
                project_id=payload.project_id,
            )
        except ValueError as exc:
            raise _bad(exc) from None

    @r.get("/tasks/{task_id}")
    async def task(task_id: str, request: Request):
        guard(request)
        h = harness(request)
        try:
            return {**h.tasks.get(task_id), "events": h.tasks.events(task_id)}
        except KeyError:
            raise HTTPException(404, "Unknown task") from None

    @r.post("/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request):
        guard(request)
        try:
            return harness(request).tasks.cancel(task_id)
        except KeyError:
            raise HTTPException(404, "Unknown task") from None

    @r.get("/reviews")
    async def reviews(request: Request):
        guard(request)
        return {"reviews": harness(request).tasks.reviews()}

    @r.post("/reviews/{review_id}")
    async def resolve(review_id: str, payload: Decision, request: Request):
        guard(request)
        try:
            return harness(request).tasks.resolve(review_id, payload.decision, payload.answer)
        except KeyError as exc:
            raise HTTPException(409, str(exc).strip("'")) from None

    @r.get("/notices")
    async def notices(request: Request):
        guard(request)
        return {"notices": harness(request).notices.list()}

    @r.post("/notices/{notice_id}")
    async def mark(notice_id: str, payload: NoticeUpdate, request: Request):
        guard(request)
        try:
            return harness(request).notices.mark(notice_id, payload.status)
        except KeyError:
            raise HTTPException(404, "Unknown notice") from None

    @r.post("/notices/{notice_id}/accept")
    async def accept(notice_id: str, request: Request):
        guard(request)
        try:
            return harness(request).accept(notice_id)
        except KeyError:
            raise HTTPException(404, "Unknown notice") from None
        except ValueError as exc:
            raise _bad(exc) from None

    @r.post("/suggestions/refresh")
    async def refresh(request: Request):
        guard(request)
        return {"created": harness(request).suggest(force=True)}

    @r.get("/connectors")
    async def connectors(request: Request):
        guard(request)
        h = harness(request)
        return {"path": str(h.connectors.path), "connectors": await h.connectors.status()}

    @r.post("/connectors/reload")
    async def reload(request: Request):
        guard(request)
        h = harness(request)
        return {"path": str(h.connectors.path), "connectors": await h.connectors.reload()}

    return r


console_router = build("/api/harness", lambda request: None)  # ConsoleBoundary checks the token.
shell_router = build("/api/panini/harness", lambda request: principal(request))


class NewClient(Strict):
    name: str = Field(min_length=1, max_length=80)


def add_a2a_clients(r, guard):
    @r.get("/a2a/clients")
    async def a2a_clients(request: Request):
        guard(request)
        h = harness(request)
        return {"clients": h.a2a_clients.list(), "remotes": sorted(h.remotes.load())}

    @r.post("/a2a/clients")
    async def a2a_create(payload: NewClient, request: Request):
        guard(request)
        try:
            return harness(request).a2a_clients.create(payload.name)
        except ValueError as exc:
            raise _bad(exc) from None

    @r.delete("/a2a/clients/{client_id}")
    async def a2a_revoke(client_id: str, request: Request):
        guard(request)
        return {"revoked": harness(request).a2a_clients.revoke(client_id)}


add_a2a_clients(console_router, lambda request: None)
add_a2a_clients(shell_router, lambda request: principal(request))

a2a_router = APIRouter()


@a2a_router.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    harness(request)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:3200"
    return a2a.card(f"http://{host}")


@a2a_router.post("/a2a")
async def a2a_endpoint(request: Request):
    h = harness(request)
    client = h.a2a_clients.authenticate(request.headers.get("authorization", ""))
    if not client:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": "A valid A2A token is required."},
            },
            401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        )
    if not isinstance(body, dict):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        )
    return JSONResponse(a2a.handle(h, client, body))


CALLBACK = "/api/harness/connectors/oauth/callback"


@console_router.post("/connectors/{name}/sign-in")
async def connector_sign_in(name: str, request: Request):
    from ..harness.connectors import ConnectorError

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:3200"
    try:
        url = await harness(request).connectors.sign_in(name, f"http://{host}{CALLBACK}")
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    except ConnectorError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"url": url, "signed_in": url is None}


@console_router.post("/connectors/{name}/sign-out")
async def connector_sign_out(name: str, request: Request):
    try:
        harness(request).connectors.sign_out(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from None
    return {"ok": True}


@console_router.get("/connectors/oauth/callback")
async def connector_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    from html import escape

    from fastapi.responses import HTMLResponse

    from ..harness.connectors import ConnectorError

    if error or not code:
        message, ok = f"Sign-in was not completed ({escape(error or 'no code')}).", False
    else:
        try:
            name = await harness(request).connectors.complete(state, code)
            message, ok = f"Signed in to {escape(name)}. PANINI can now use it.", True
        except ConnectorError as exc:
            message, ok = escape(str(exc)), False
    page = (
        "<!doctype html><meta charset='utf-8'><title>PANINI connector</title>"
        "<body style='font-family:Segoe UI,sans-serif;padding:48px;max-width:560px'>"
        f"<h1>{'Connected' if ok else 'Not connected'}</h1><p>{message}</p>"
        "<p><a href='/agent'>Back to PANINI</a></p></body>"
    )
    return HTMLResponse(page, 200 if ok else 400)


def add_mobile(r, guard):
    @r.get("/mobile")
    async def mobile_status(request: Request):
        guard(request)
        return harness(request).mobile.status()

    @r.post("/mobile/enable")
    async def mobile_enable(request: Request):
        guard(request)
        return await harness(request).mobile.enable()

    @r.post("/mobile/disable")
    async def mobile_disable(request: Request):
        guard(request)
        return await harness(request).mobile.disable()

    @r.post("/mobile/code")
    async def mobile_code(request: Request):
        guard(request)
        h = harness(request)
        if not h.mobile.running:
            raise HTTPException(409, "Turn the phone companion on first.")
        return h.mobile.devices.new_code()

    @r.delete("/mobile/devices/{device_id}")
    async def mobile_revoke(device_id: str, request: Request):
        guard(request)
        return {"revoked": harness(request).mobile.devices.revoke(device_id)}


add_mobile(console_router, lambda request: None)
add_mobile(shell_router, lambda request: principal(request))


def add_v2_status(r, guard):
    @r.get("/v2/status")
    async def v2_status(request: Request):
        """Which PANINI v2 components are switched on, and which harness migrations ran."""
        guard(request)
        from .. import v2flags

        h = request.app.state.harness
        applied = (
            [row["id"] for row in h.store.all("SELECT id FROM schema_migrations ORDER BY id")] if h else []
        )
        return {**v2flags.status(), "migrations": applied}


add_v2_status(console_router, lambda request: None)
add_v2_status(shell_router, lambda request: principal(request))


class CapabilityToggle(Strict):
    enabled: bool


def add_registry(r, guard):
    @r.get("/registry")
    async def registry_view(request: Request):
        """Capability manifests, their tools and effect classes, readiness and load errors (V2-02)."""
        guard(request)
        from .. import v2flags

        reg = harness(request).registry
        return {
            "flag": v2flags.enabled("PANINI_V2_REGISTRY"),
            "errors": reg.errors,
            "inventory": await reg.inventory(),
        }

    @r.patch("/registry/{capability}")
    async def registry_toggle(capability: str, payload: CapabilityToggle, request: Request):
        guard(request)
        h = harness(request)
        if capability not in {m.id for m in h.registry.manifests} or capability == "control":
            raise HTTPException(404, "Unknown capability.")
        h.store.execute(
            "INSERT INTO capability_state(id, enabled, updated) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, updated=excluded.updated",
            (capability, int(payload.enabled), time.time()),
        )
        return {"id": capability, "enabled": payload.enabled}


add_registry(console_router, lambda request: None)
add_registry(shell_router, lambda request: principal(request))


class NewGrant(Strict):
    subject: str = Field(default="owner", min_length=1, max_length=120)
    capability: str = Field(default="*", max_length=64)
    tools: list[str] = Field(default_factory=lambda: ["*"], max_length=50)
    levels: dict[str, str] = Field(default_factory=dict)
    expires_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 365)
    note: str = Field(default="", max_length=300)


class PauseRequest(Strict):
    paused: bool


def add_policy(r, guard):
    from ..core_contracts import ApprovalLevel, EffectClass

    @r.get("/policy")
    async def policy_view(request: Request):
        """Profile, the effect x surface matrix, grants, pause state and shadow-mode disagreements (V2-03)."""
        guard(request)
        from .. import v2flags
        from ..policy.engine import PROFILES, TIERS, profile_name

        policy = harness(request).policy
        name = profile_name()
        return {
            "mode": v2flags.value("PANINI_V2_POLICY"),
            "profile": name,
            "tiers": list(TIERS),
            "matrix": {e.value: [lv.value for lv in row] for e, row in PROFILES[name].items()},
            "grants": [
                {**g.__dict__, "tools": list(g.tools), "groups": list(g.groups)} for g in policy.grants()
            ],
            "paused": policy.paused(),
            "epoch": policy.epoch(),
            "shadow": policy.shadow_stats(),
        }

    @r.post("/policy/grants")
    async def policy_grant(payload: NewGrant, request: Request):
        guard(request)
        from ..policy import new_grant

        try:
            levels = {EffectClass(k).value: ApprovalLevel(v).value for k, v in payload.levels.items()}
        except ValueError:
            raise HTTPException(422, "Unknown effect class or approval level.") from None
        expires = time.time() + payload.expires_minutes * 60 if payload.expires_minutes else None
        grant = new_grant(
            subject=payload.subject,
            capability=payload.capability,
            tools=tuple(payload.tools),
            levels=levels,
            expires_at=expires,
            note=payload.note,
        )
        harness(request).policy.put_grant(grant)
        return {"id": grant.id}

    @r.delete("/policy/grants/{grant_id}")
    async def policy_revoke(grant_id: str, request: Request):
        guard(request)
        return {"revoked": harness(request).policy.revoke(grant_id)}

    @r.post("/policy/pause")
    async def policy_pause(payload: PauseRequest, request: Request):
        """Pause every agent action at once (also from the tray and, later, the phone)."""
        guard(request)
        harness(request).policy.pause(payload.paused)
        return {"paused": payload.paused}


add_policy(console_router, lambda request: None)
add_policy(shell_router, lambda request: principal(request))


def add_ledger(r, guard):
    @r.get("/receipts")
    async def receipts(
        request: Request,
        run_id: str = "",
        tool: str = "",
        effect: str = "",
        q: str = "",
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
    ):
        """Every tool call PANINI made, newest first, with redacted arguments (V2-04)."""
        guard(request)
        h = harness(request)
        return {
            "receipts": h.ledger.query(
                run_id=run_id or None,
                tool=tool or None,
                effect=effect or None,
                text=q or None,
                since=since,
                until=until,
                limit=limit,
            )
        }

    @r.get("/receipts/{receipt_id}")
    async def receipt(receipt_id: str, request: Request):
        guard(request)
        found = harness(request).ledger.receipt(receipt_id)
        if found is None:
            raise HTTPException(404, "Unknown receipt.")
        return found

    @r.get("/ledger/verify")
    async def ledger_verify(request: Request):
        guard(request)
        from ..ledger.chain import verify

        h = harness(request)
        roots = h.store.all("SELECT day, last_seq, root_hash FROM ledger_roots ORDER BY day DESC LIMIT 7")
        return {**verify(h.store), "roots": roots}

    @r.post("/artifacts/{artifact_id}/open-trace")
    async def open_trace(artifact_id: str, request: Request):
        """Open a browser trace in Playwright's trace viewer on this PC (V2-10)."""
        guard(request)
        import subprocess
        import tempfile

        from ..hands.playwright_mcp import BrowserDriverError, entry_script, node_executable

        found = harness(request).artifacts.get(artifact_id)
        if found is None or found[0].get("kind") != "trace":
            raise HTTPException(404, "Unknown trace.")
        node = node_executable()
        try:
            cli = entry_script().parents[2] / "cli.js"
        except BrowserDriverError:
            cli = None
        if node is None or cli is None or not cli.exists():
            raise HTTPException(503, "The Playwright trace viewer is not installed.")
        path = Path(tempfile.gettempdir()) / f"panini-trace-{artifact_id}.zip"
        path.write_bytes(found[1])
        subprocess.Popen(
            [node, str(cli), "show-trace", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {"opened": True}

    @r.get("/artifacts/{artifact_id}")
    async def artifact(artifact_id: str, request: Request):
        guard(request)
        from fastapi.responses import Response

        found = harness(request).artifacts.get(artifact_id)
        if found is None:
            raise HTTPException(404, "Unknown artifact.")
        row, data = found
        return Response(
            data,
            media_type=row["mime"],
            headers={
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private",
            },
        )


def add_runs(r, guard):
    @r.get("/runs")
    async def runs(request: Request, thread_id: str = "", task_id: str = "", limit: int = 50):
        """Agent runs, newest first: mode, tier, state, usage and result (V2-06)."""
        guard(request)
        h = harness(request)
        from ..brain import tier_models

        return {
            "runs": h.runs.list(limit=limit, thread_id=thread_id or None, task_id=task_id or None),
            "spent_today_usd": round(h.runs.spent_today(), 4),
            "tiers": tier_models(),
        }

    @r.get("/runs/{run_id}")
    async def run(run_id: str, request: Request):
        guard(request)
        h = harness(request)
        found = h.runs.get(run_id)
        if found is None:
            raise HTTPException(404, "Unknown run.")
        checkpoint = h.runs.load_checkpoint(run_id) or {}
        found["plan"] = checkpoint.get("plan", [])
        found["step"] = checkpoint.get("step")
        if found.get("thread_id"):
            found["bindings"] = h.threads.bindings(found["thread_id"])
        return found


add_ledger(console_router, lambda request: None)
add_runs(console_router, lambda request: None)
add_ledger(shell_router, lambda request: principal(request))
add_runs(shell_router, lambda request: principal(request))


class InterruptDecision(Strict):
    nonce: str = Field(min_length=20, max_length=200)
    action_digest: str | None = Field(default=None, min_length=64, max_length=64)
    status: str = Field(pattern="^(resolved|cancelled)$")
    payload: dict = Field(default_factory=dict)


def add_interrupts(r, guard, surface):
    """Durable approvals and questions (V2-05). Console and shell are on this PC, so they resolve every level."""

    @r.get("/interrupts")
    async def interrupts_list(request: Request, status: str = "open"):
        guard(request)
        service = harness(request).interrupts
        return {"interrupts": service.list(status=status if status != "all" else None)}

    @r.get("/interrupts/{interrupt_id}")
    async def interrupts_get(interrupt_id: str, request: Request):
        guard(request)
        return harness(request).interrupts.get(interrupt_id, include_secret=True)

    @r.post("/interrupts/{interrupt_id}")
    async def interrupts_resolve(interrupt_id: str, payload: InterruptDecision, request: Request):
        guard(request)
        from ..interrupts import decided_by

        return harness(request).interrupts.resolve(
            interrupt_id,
            nonce=payload.nonce,
            action_digest=payload.action_digest,
            status=payload.status,
            payload=payload.payload,
            decided_by=decided_by(surface, "local"),
        )


add_interrupts(console_router, lambda request: None, "console")
add_interrupts(shell_router, lambda request: principal(request), "avatar")
