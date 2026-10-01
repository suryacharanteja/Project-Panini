"""Harness API: memories, skills, background tasks and reviews.

/api/harness/* are console routes (the gateway signs the person in). The shell
reads status and manages the same data through /api/panini/harness/* with its
own runtime credential.
"""

import time

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
