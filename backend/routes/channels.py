"""Owner-only channel configuration. Credentials are write-only."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field


class WriteOnlyValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request):
            try:
                return await handler(request)
            except RequestValidationError as error:
                # Pydantic's default response includes rejected input, including bot tokens.
                raise HTTPException(
                    422,
                    [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()],
                ) from None

        return checked


router = APIRouter(prefix="/api/admin/v1/channels", route_class=WriteOnlyValidationRoute)


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    owner_ids: list[str] = Field(min_length=1, max_length=20)
    group_ids: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = False
    token: str | None = Field(default=None, max_length=250)


def manager(request):
    harness = request.app.state.harness
    if not harness:
        raise HTTPException(404, "The PANINI harness is disabled.")
    return harness.channels


@router.get("")
async def status(request: Request):
    return manager(request).status()


async def save(payload, request, identifier=None):
    service = manager(request)
    try:
        result = service.accounts.save(**payload.model_dump(), identifier=identifier)
    except KeyError:
        raise HTTPException(404, "This channel account is unavailable.") from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    await service.apply()
    return result


@router.post("/telegram")
async def create(payload: TelegramConfig, request: Request):
    return await save(payload, request)


@router.put("/telegram/{identifier}")
async def update(identifier: str, payload: TelegramConfig, request: Request):
    return await save(payload, request, identifier)
