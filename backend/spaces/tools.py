"""Read tools are scoped to the caller's persona, even when policy runs in shadow mode."""

from .. import v2flags
from ..core_contracts import Readiness, ToolResult
from .store import SpacesError


class SpacesHand:
    def __init__(self, manifest, runtime):
        self.runtime = runtime

    async def readiness(self):
        return Readiness("ready" if v2flags.enabled("PANINI_SPACES") else "unavailable")

    async def call(self, ctx, spec, args):
        if (
            not v2flags.enabled("PANINI_SPACES")
            or not ctx.view.private
            or not ctx.principal.verified
            or ctx.principal.kind not in {"owner", "persona"}
        ):
            return ToolResult("blocked", "Spaces is disabled or this session is not private.")
        h = self.runtime["harness"]
        persona = getattr(ctx.agent, "persona_id", None) or (
            ctx.principal.client_id if ctx.principal.kind == "persona" else "panini"
        )
        try:
            if not h.personas.allows(persona, spec):
                return ToolResult("blocked", "Persona lacks this permission.")
            if spec.name in {"pages_create", "pages_edit"}:
                from .reviews import review

                if not ctx.agent or not ctx.agent.durable:
                    return ToolResult("blocked", "Use a durable conversation to review page drafts.")
                operation = {"kind": "create" if spec.name == "pages_create" else "edit", **args}
                operation.pop("review", None)
                if spec.name == "pages_create" and not operation.get("space_id"):
                    operation["space_id"] = h.personas.get(persona).get("default_space_id")
                    if not operation["space_id"]:
                        raise SpacesError("SPACE_REQUIRED", "Choose a destination space first.")
                result = review(
                    h,
                    operation,
                    persona_id=persona,
                    run_id=ctx.run_id,
                    tool_call_id=ctx.agent.persona_call_id,
                    thread_id=ctx.agent.thread_id,
                )
                page = result.get("page")
                return ToolResult(
                    "ok",
                    data=result,
                    evidence=[{"id": f"page:{page['id']}:{page['revision']}", "kind": "page_revision"}]
                    if page
                    else [],
                )
            with h.spaces.transaction():
                if spec.name == "spaces_list":
                    data = {"spaces": h.spaces.spaces(persona)}
                elif spec.name == "pages_list":
                    data = {"pages": h.spaces.pages(args["space_id"], persona)}
                elif spec.name == "pages_read":
                    data = h.spaces.read(args["page_id"], persona)
                elif spec.name == "pages_search":
                    data = {"pages": h.spaces.search(args["query"], persona, args.get("space_id"))}
                else:
                    return ToolResult("failed", "Unknown Spaces tool.")
            return ToolResult("ok", data=data)
        except (SpacesError, KeyError) as exc:
            return ToolResult("blocked", str(exc).strip("'"))
