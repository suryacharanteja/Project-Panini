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


# Slack, WhatsApp and email (V2-17). Secrets are write-only; omit a field to keep what is saved.
class SlackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    owner_ids: list[str] = Field(min_length=1, max_length=20)
    group_ids: list[str] = Field(default_factory=list, max_length=20)
    team_id: str = Field(min_length=1, max_length=40)
    enabled: bool = False
    token: str | None = Field(default=None, max_length=260)
    app_token: str | None = Field(default=None, max_length=260)


class WhatsAppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    owner_ids: list[str] = Field(min_length=1, max_length=20)
    phone_number_id: str = Field(min_length=1, max_length=40)
    enabled: bool = False
    token: str | None = Field(default=None, max_length=520)
    app_secret: str | None = Field(default=None, max_length=140)
    verify_token: str | None = Field(default=None, max_length=140)


class EmailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    owner_ids: list[str] = Field(min_length=1, max_length=20)
    address: str = Field(min_length=3, max_length=254)
    username: str = Field(min_length=1, max_length=200)
    imap_host: str = Field(min_length=1, max_length=253)
    smtp_host: str = Field(min_length=1, max_length=253)
    imap_port: str = Field(default="", max_length=5)
    smtp_port: str = Field(default="", max_length=5)
    enabled: bool = False
    token: str | None = Field(default=None, max_length=200)


SHAPES = {
    "slack": (SlackConfig, ("team_id",), ("app_token",)),
    "whatsapp": (WhatsAppConfig, ("phone_number_id",), ("app_secret", "verify_token")),
    "email": (EmailConfig, ("address", "username", "imap_host", "smtp_host", "imap_port", "smtp_port"), ()),
}


async def save_channel(channel, payload, request, identifier=None):
    _, settings, secret_names = SHAPES[channel]
    data = payload.model_dump()
    service = manager(request)
    try:
        result = service.accounts.save(
            channel=channel,
            label=data["label"],
            owner_ids=data["owner_ids"],
            group_ids=data.get("group_ids", []),
            enabled=data["enabled"],
            token=data["token"],
            identifier=identifier,
            settings={name: data[name] for name in settings},
            secrets={name: data[name] for name in secret_names},
        )
    except KeyError:
        raise HTTPException(404, "This channel account is unavailable.") from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    await service.apply()
    return result


@router.post("/slack")
async def create_slack(payload: SlackConfig, request: Request):
    return await save_channel("slack", payload, request)


@router.put("/slack/{identifier}")
async def update_slack(identifier: str, payload: SlackConfig, request: Request):
    return await save_channel("slack", payload, request, identifier)


@router.post("/whatsapp")
async def create_whatsapp(payload: WhatsAppConfig, request: Request):
    return await save_channel("whatsapp", payload, request)


@router.put("/whatsapp/{identifier}")
async def update_whatsapp(identifier: str, payload: WhatsAppConfig, request: Request):
    return await save_channel("whatsapp", payload, request, identifier)


@router.post("/email")
async def create_email(payload: EmailConfig, request: Request):
    return await save_channel("email", payload, request)


@router.put("/email/{identifier}")
async def update_email(identifier: str, payload: EmailConfig, request: Request):
    return await save_channel("email", payload, request, identifier)
