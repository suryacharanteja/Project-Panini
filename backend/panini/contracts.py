"""Versioned, strict public contracts. IDs never confer authority."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Capability(StrEnum):
    BROWSER = "browser"
    SEARCH = "search"
    DESKTOP = "desktop"
    CODE = "code"
    AGENT = "agent"


class Control(StrictModel):
    command_id: UUID


class Register(StrictModel):
    client_name: str = Field(default="PANINI", min_length=1, max_length=80)
    profile_id: str = Field(default="panini", min_length=1, max_length=80)


class Renew(Control):
    renderer_revision: int = Field(ge=0)


class Turn(Control):
    schema_version: Literal[1] = 1
    kind: Literal["turn", "replace"] = "turn"
    source: Literal["typed"] = "typed"
    text: str = Field(min_length=1, max_length=5000)
    capability_hint: Capability | None = None
    target_ref: str | None = Field(default=None, max_length=120)
    expected_task_id: str | None = Field(default=None, max_length=100)
    expected_task_revision: int | None = Field(default=None, ge=1)
    selected_result_id: str | None = Field(default=None, max_length=100)
    resume_choice_id: str | None = Field(default=None, max_length=200)


class SelectTarget(Control):
    capability: Literal["browser", "desktop", "code"]
    choice_id: str = Field(min_length=1, max_length=200)


class Stop(Control):
    expected_task_id: str | None = Field(default=None, max_length=100)


class Feedback(Control):
    task_id: str = Field(min_length=1, max_length=100)
    rating: Literal["up", "down"]
    note: str = Field(default="", max_length=1000)


class ObserverRequest(Control):
    share_captions: bool = False


class TicketExchange(StrictModel):
    ticket: str = Field(min_length=20, max_length=200)


class MediaOffer(Control):
    # SDP is a line-oriented protocol; its final CRLF must not be stripped.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    media_generation: int = Field(ge=1)
    sdp: str = Field(min_length=20, max_length=100000)
    persona_id: str = Field(default="panini", pattern=r"^[a-z][a-z0-9-]{0,40}$")


class MediaClose(Control):
    media_generation: int = Field(ge=1)


class ConfirmationDecision(Control):
    nonce: str = Field(min_length=20, max_length=200)
    digest: str = Field(min_length=64, max_length=64)
    decision: Literal["approve", "reject"]


class Fault(Exception):
    def __init__(self, code: str, message: str, status: int = 409, recovery: str = "retry"):
        super().__init__(message)
        self.code, self.message, self.status, self.recovery = code, message, status, recovery

    def payload(self, request_id=""):
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": False,
                "recovery": self.recovery,
                "request_id": request_id,
            }
        }
