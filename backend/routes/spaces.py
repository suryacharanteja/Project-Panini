"""Owner notebook routes, protected by the console or shell boundary."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

from .. import v2flags
from ..spaces import SpacesError, destroy
from ..spaces.export import pdf, save_source
from .harness import Strict, harness
from .panini import principal


class NewSpace(Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class NewPage(Strict):
    space_id: str
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=200000)
    parent_id: str | None = None


class EditPage(Strict):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=200000)
    parent_id: str | None = None
    move: bool = False
    trash: bool = False
    restore: bool = False


class Access(Strict):
    persona_id: str
    allowed: bool


class SaveSource(Strict):
    space_id: str
    kind: str
    source_id: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    parent_id: str | None = None


class RestoreRevision(Strict):
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class DestroyRequest(Strict):
    expected_revision: int = Field(ge=1)
    confirmation: str = Field(max_length=240)


class DestroyConfirm(Strict):
    nonce: str = Field(max_length=200)
    action_digest: str = Field(max_length=200)
    approved: bool


def build(prefix, guard):
    r = APIRouter(prefix=prefix)

    def service(request):
        guard(request)
        if not v2flags.enabled("PANINI_SPACES"):
            raise HTTPException(404, "Spaces is disabled. Enable PANINI_SPACES in Settings.")
        return harness(request).spaces

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SpacesError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc

    @r.get("")
    async def spaces(request: Request):
        return {"spaces": service(request).spaces()}

    @r.post("")
    async def create_space(payload: NewSpace, request: Request):
        return call(service(request).create_space, **payload.model_dump())

    @r.post("/access/{space_id}")
    async def access(space_id: str, payload: Access, request: Request):
        call(service(request).grant, space_id, **payload.model_dump())
        return {"ok": True}

    @r.get("/access/{space_id}")
    async def access_list(space_id: str, request: Request):
        notebook = service(request)
        call(notebook.authorize, space_id)
        return {
            "personas": notebook.store.all(
                "SELECT persona_id FROM space_access WHERE space_id=?", (space_id,)
            )
        }

    @r.post("/save-source")
    async def save(payload: SaveSource, request: Request):
        service(request)
        return call(save_source, harness(request), **payload.model_dump())

    @r.get("/search")
    async def search(request: Request, q: str, space_id: str | None = None):
        return {"pages": call(service(request).search, q, space_id=space_id)}

    @r.get("/{space_id}/pages")
    async def pages(space_id: str, request: Request, deleted: bool = False):
        return {"pages": call(service(request).pages, space_id, deleted=deleted)}

    @r.post("/pages")
    async def create_page(payload: NewPage, request: Request):
        return call(service(request).create, **payload.model_dump())

    @r.get("/pages/{page_id}")
    async def read(page_id: str, request: Request, deleted: bool = False):
        return call(service(request).read, page_id, deleted=deleted)

    @r.patch("/pages/{page_id}")
    async def edit(page_id: str, payload: EditPage, request: Request):
        return call(service(request).edit, page_id, **payload.model_dump())

    @r.get("/pages/{page_id}/revisions")
    async def revisions(page_id: str, request: Request):
        return {"revisions": call(service(request).revisions, page_id)}

    @r.post("/pages/{page_id}/restore-revision")
    async def restore_revision(page_id: str, payload: RestoreRevision, request: Request):
        return call(service(request).restore_revision, page_id, **payload.model_dump())

    @r.get("/pages/{page_id}/pdf")
    async def export_pdf(page_id: str, request: Request):
        notebook = service(request)
        page = call(notebook.read, page_id)
        try:
            output = await pdf(page)
        except SpacesError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc
        current = call(service(request).read, page_id)
        if current["revision"] != page["revision"]:
            raise HTTPException(
                409, {"code": "PAGE_CHANGED", "message": "Page changed during export. Retry."}
            )
        return Response(
            output,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="panini-{page_id}.pdf"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    if prefix == "/api/harness/spaces":

        @r.post("/pages/{page_id}/destroy-request")
        async def destroy_request(page_id: str, payload: DestroyRequest, request: Request):
            service(request)
            return call(destroy.request, harness(request), page_id, **payload.model_dump())

        @r.post("/destroy/{interrupt_id}/confirm")
        async def destroy_confirm(interrupt_id: str, payload: DestroyConfirm, request: Request):
            service(request)
            return call(destroy.confirm, harness(request), interrupt_id, **payload.model_dump())

    @r.post("/pages/{page_id}/conversation")
    async def conversation(page_id: str, request: Request, persona_id: str = "panini"):
        if persona_id != "panini" and not v2flags.enabled("PANINI_PERSONAS"):
            raise HTTPException(409, "Personas is disabled.")
        call(service(request).thread, page_id, persona_id)
        return {"thread_id": f"page:{page_id}:{persona_id}"}

    @r.get("/pages/{page_id}/markdown")
    async def markdown(page_id: str, request: Request):
        page = call(service(request).read, page_id)
        return Response(
            page["content"],
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="panini-{page_id}.md"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    return r


console_router = build("/api/harness/spaces", lambda request: None)
shell_router = build("/api/panini/harness/spaces", principal)
