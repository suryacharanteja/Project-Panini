from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import config, private_mode
from ..task_catalog import TASKS

router = APIRouter(prefix="/api")


class CompareRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=1, max_length=config.MAX_TEXT_LENGTH)
    task: str

    @field_validator("task")
    @classmethod
    def known_task(cls, value):
        if value not in TASKS:
            raise ValueError("Unknown task")
        return value


@router.get("/tasks")
async def list_tasks():
    return {"tasks": [task.model_dump(exclude={"questions"}) for task in TASKS.values()]}


@router.post("/compare")
async def compare(payload: CompareRequest, request: Request):
    if private_mode.active():
        raise HTTPException(503, private_mode.reason())
    return await request.app.state.comparison_work.run(
        lambda: request.app.state.compare_service(payload.text, payload.task)
    )
