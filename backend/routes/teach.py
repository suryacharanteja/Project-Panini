"""Owner console controls; remote clients and models cannot begin a recording."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from ..core_contracts import Principal
from ..interrupts import InterruptSpec
from ..registry.context import ToolContext, ToolView
from ..teach.schema import Strict
from .panini import principal

router = APIRouter(prefix="/api/harness/teach")
shell_router = APIRouter(prefix="/api/panini/harness/teach")


class Start(Strict):
    browser: str = Field(pattern="^(managed|chrome)$")


class Proposal(Strict):
    name: str = Field(max_length=63)
    description: str = Field(max_length=300)
    replay: dict
    base_version: int = Field(default=0, ge=0)


class Run(Strict):
    name: str = Field(max_length=63)
    inputs: dict[str, str] = Field(default_factory=dict, max_length=30)
    browser: str = Field(default="managed", pattern="^(managed|chrome)$")


def service(request):
    h = request.app.state.harness
    if not h:
        raise HTTPException(404, "The harness is disabled.")
    return h.teach


def context(teach):
    async def confirm(title, detail, policy_decision=None):
        review = teach.h.interrupts.open(
            InterruptSpec(
                "approval",
                "teach_effect",
                title,
                "Approve this replay step?",
                preview={"detail": detail},
                action={"title": title, "detail": detail},
                ttl_s=120,
                level=policy_decision.level.value if policy_decision else "tap",
            ),
            run_id=identifier,
            owner_principal={"surface": "avatar"},
        )
        try:
            decision = await teach.h.interrupts.wait(review["id"], timeout=120)
            return decision["status"] == "resolved" and bool(
                (decision.get("decision") or {}).get("payload", {}).get("approved")
            )
        finally:
            teach.h.interrupts.cancel(review["id"], "The replay is no longer waiting for this decision.")

    identifier = uuid4().hex
    ctx = ToolContext(
        principal=Principal(surface="avatar"),
        view=ToolView(managed_browser=True, claw_cdp=True, teach=True, ledger=True),
        run_id=identifier,
        confirm=confirm,
    )
    ctx.teach_replay = True
    ctx.confirm_decision = confirm
    return ctx


@router.get("")
async def status(request: Request):
    teach = service(request)
    return {
        "enabled": teach.allowed(),
        "recording": teach.recorder.status(),
        "replaying": bool(teach.replayer.active),
        "skills": teach.bundles.list(),
        "recordings": teach.h.store.all(
            "SELECT id,browser,state,created FROM teach_recordings ORDER BY created DESC LIMIT 20"
        ),
    }


@router.post("/record/start")
async def start(payload: Start, request: Request):
    teach = service(request)
    try:
        teach.require()
        return await teach.recorder.start(context(teach), payload.browser)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/stop")
async def stop(request: Request):
    teach = service(request)
    await teach.replayer.stop()
    return await teach.recorder.stop()


@shell_router.post("/stop")
async def shell_stop(request: Request):
    principal(request)
    return await stop(request)


@router.get("/recordings/{identifier}")
async def recording(identifier: str, request: Request):
    import json

    row = service(request).h.store.one("SELECT * FROM teach_recordings WHERE id=?", (identifier,))
    if not row:
        raise HTTPException(404, "Unknown recording.")
    return {**row, "replay": json.loads(row["data"])}


@router.post("/propose")
async def propose(payload: Proposal, request: Request):
    teach = service(request)
    try:
        teach.require()
        return teach.bundles.propose(
            payload.name, payload.description, payload.replay, base_version=payload.base_version
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:500]) from None


@router.get("/skills/{name}")
async def skill(name: str, request: Request):
    teach = service(request)
    teach.bundles.reconcile()
    try:
        row, replay = teach.bundles.current(name)
        return {
            "name": name,
            "version": row["version"],
            "description": row["description"],
            "replay": replay.model_dump(),
        }
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/run")
async def run(payload: Run, request: Request):
    teach = service(request)
    try:
        teach.require()
        result = await teach.replayer.run(context(teach), payload.name, payload.inputs, payload.browser)
        return result.model_output()
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:500]) from None
