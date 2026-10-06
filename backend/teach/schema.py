"""Bounded, declarative replay data; no executable source or model-selected code."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..harness.memory import _SECRET

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
PARAM = re.compile(r"\{\{([a-z][a-z0-9_]{0,39})\}\}")
FRAME = re.compile(r"^iframe\[(?:name|title)=(['\"])([^'\"\]\\]{1,200})\1\]$")


def frame_name(selector):
    match = FRAME.fullmatch(selector)
    if not match:
        raise ValueError("Only a named iframe can be replayed; correct this frame in review.")
    return match[2]


SENSITIVE = re.compile(
    r"password|passwd|passcode|one.time|\bpin\b|\b(?:cvv|cvc|otp)\b|credit.card|card.number|security.code|verification.code",
    re.I,
)
ACTIONS = {
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_press_key",
    "browser_select_option",
    "browser_hover",
    "browser_file_upload",
    "manual",
}
ASSERTIONS = {"browser_verify_text_visible", "browser_verify_element_visible", "browser_verify_value"}
DESKTOP_ACTIONS = {
    "desktop_open_application",
    "desktop_click",
    "desktop_type",
    "desktop_select",
    "desktop_toggle",
    "desktop_scroll",
    "desktop_press",
    "desktop_drag",
    "manual",
}
DESKTOP_ASSERTIONS = {"desktop_verify_text", "desktop_verify_value", "desktop_verify_element"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ancestor(Strict):
    role: str = Field(max_length=80)
    name: str = Field(max_length=500)


class Target(Strict):
    role: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=500)
    name_regex: str = Field(default="", max_length=500)
    locator: str = Field(default="", max_length=1500)
    frame: str = Field(default="", max_length=500)
    ancestors: list[Ancestor] = Field(default_factory=list, max_length=8)
    dialog: str = Field(default="", max_length=200)  # Desktop: the owned dialog's title (Save As, Open, …).

    @model_validator(mode="after")
    def bounded(self):
        if self.frame:
            frame_name(self.frame)
        if self.name_regex and not self.name_regex.startswith("^"):
            raise ValueError("Name patterns must be a literal anchored prefix.")
        if self.name_regex and re.search(r"(?<!\\)[.*+?{}\[\]()|$]", PARAM.sub("input", self.name_regex[1:])):
            raise ValueError("Name patterns only support a literal anchored prefix.")
        return self


class Step(Strict):
    id: str = Field(pattern=r"^s[1-9][0-9]{0,2}$")
    tool: str
    args: dict = Field(default_factory=dict)
    target: Target | None = None
    checked: bool | None = None
    note: str = Field(default="", max_length=500)
    crop_artifact: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{6,64}$"
    )  # Recorded visual context.
    drop: Target | None = None  # desktop_drag only: the reviewed control the drag ends on.


class Input(Strict):
    default: str = Field(default="", max_length=2000)
    kind: Literal["text", "date", "email", "number", "file"] = "text"
    required: bool = True


class Replay(Strict):
    version: Literal[1] = 1
    kind: Literal["browser", "desktop"] = "browser"
    desktop_application: str = Field(default="", max_length=100)
    steps: list[Step] = Field(min_length=1, max_length=100)
    expect: list[Step] = Field(default_factory=list, max_length=3)
    inputs: dict[str, Input] = Field(default_factory=dict, max_length=30)
    recording_artifacts: dict[str, str] = Field(default_factory=dict, max_length=103)

    @model_validator(mode="after")
    def safe(self):
        if self.kind == "desktop" and not self.desktop_application:
            raise ValueError("Desktop replay needs a reviewed application.")
        if self.kind == "browser" and self.desktop_application:
            raise ValueError("Browser replay cannot bind a Windows application.")
        ids = [s.id for s in self.steps + self.expect]
        if len(ids) != len(set(ids)):
            raise ValueError("Step IDs must be unique.")
        for step in self.steps + self.expect:
            allowed = (
                (DESKTOP_ASSERTIONS if step in self.expect else DESKTOP_ACTIONS)
                if self.kind == "desktop"
                else (ASSERTIONS if step in self.expect else ACTIONS)
            )
            if step.tool not in allowed or len(json.dumps(step.args)) > 8000:
                raise ValueError("Unsupported or oversized replay step.")
            if any(k in step.args for k in ("target", "ref", "element", "function", "code")):
                raise ValueError("Refs, page code and element descriptions are resolved at replay time.")
            if (
                step.tool
                in {
                    "browser_click",
                    "browser_type",
                    "browser_select_option",
                    "browser_hover",
                    "browser_press_key",
                    "browser_file_upload",
                }
                or step.tool in DESKTOP_ACTIONS - {"manual", "desktop_open_application"}
            ) and not step.target:
                raise ValueError("This step needs a reviewed target.")
            if step.tool == "desktop_open_application" and (
                step.target is not None
                or step.args.keys() != {"application_id"}
                or not isinstance(step.args["application_id"], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{1,39}", step.args["application_id"])
            ):
                raise ValueError("App launch needs one reviewed installed application ID.")
            if self.kind == "browser" and any(t and t.dialog for t in (step.target, step.drop)):
                raise ValueError("Dialog targets are for Windows replays.")
            if step.drop and step.target and step.drop.dialog != step.target.dialog:
                raise ValueError("A drag starts and ends in the same window.")
            if self.kind == "desktop" and step.target and (step.target.frame or step.target.locator):
                raise ValueError("Desktop targets use roles, names and ancestry, not browser locators.")
            if step.target and not (step.target.name or step.target.name_regex):
                raise ValueError("Targets need an accessible name, not a raw selector alone.")
            if step.target and SENSITIVE.search(str(step.target.model_dump())):
                raise ValueError("Sensitive controls cannot be replay targets.")
            if (step.tool == "desktop_drag") != (step.drop is not None):
                raise ValueError("A drag needs exactly one reviewed drop target, and only drags have one.")
            if step.drop and (
                not (step.drop.name or step.drop.name_regex)
                or step.drop.frame
                or step.drop.locator
                or SENSITIVE.search(str(step.drop.model_dump()))
            ):
                raise ValueError("Drop targets need an accessible name and cannot be sensitive controls.")
            if step.checked is not None and (
                step.tool not in {"browser_click", "desktop_toggle", "desktop_click"}
                or not step.target
                or step.target.role not in {"checkbox", "radio"}
            ):
                raise ValueError("Checked-state replay needs a checkbox or radio control.")
        data = json.dumps(self.model_dump(), ensure_ascii=False)
        launches = [step for step in self.steps if step.tool == "desktop_open_application"]
        if launches and (self.kind != "desktop" or len(launches) != 1 or self.steps[0] != launches[0]):
            raise ValueError("An application launch must be the first and only launch step.")
        if len(data) > 100000 or _SECRET.search(data):
            raise ValueError("Replay data contains a secret pattern or exceeds the size limit.")
        for key in self.inputs:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
                raise ValueError("Invalid input name.")
        for key, value in self.recording_artifacts.items():
            if key not in set(ids) | {"start", "end"} or not re.fullmatch(r"\d{13}[a-f0-9]{12}", value):
                raise ValueError("Invalid recording artifact reference.")
        if set(PARAM.findall(data)) - self.inputs.keys():
            raise ValueError("A parameter has no input definition.")
        return self


def substitute(value, inputs):
    if isinstance(value, str):
        return PARAM.sub(lambda m: inputs[m[1]], value)
    if isinstance(value, list):
        return [substitute(v, inputs) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, inputs) for k, v in value.items()}
    return value


def input_values(replay, supplied):
    if not isinstance(supplied, dict) or supplied.keys() - replay.inputs.keys():
        raise ValueError("Unknown skill input.")
    values = {}
    for name, spec in replay.inputs.items():
        value = supplied.get(name, spec.default)
        if not isinstance(value, str) or len(value) > 2000 or (spec.required and not value):
            raise ValueError(f"Provide a bounded value for {name}.")
        if _SECRET.search(value):
            raise ValueError("Secrets cannot be supplied to replay.")
        values[name] = value
    return values
