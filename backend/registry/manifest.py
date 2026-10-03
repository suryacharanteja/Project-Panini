"""Capability manifests (`panini.capability/1`): what a capability's tools are, what they do to the world,
and how the brain may use them (docs/panini-enhancements/lld/V2-02-capability-registry.md §5.2)."""

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core_contracts import EffectClass, ToolSpec

BUILTIN = Path(__file__).parent / "manifests"
ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
TOOL = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
REQUIREMENTS = {
    "private",
    "foreground",
    "top",
    "tasks",
    "connectors",
    "remotes",
    "ledger",
    "managed_browser",
    "claw_cdp",
    "browser_v2",
}


class ManifestError(ValueError):
    pass


class ToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    hand: str
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4000)
    input_schema: dict
    effect: EffectClass
    dynamic_effect: bool = False
    group: str = "core"
    evidence: list[str] = []
    taints: list[str] = []
    idempotent: bool = False
    timeout_ms: int = Field(default=300_000, ge=1000, le=3_600_000)
    concurrency: str | None = None
    background: bool = False
    owner_only: bool = False
    hand_from_run: bool = False
    budget_per_run: int | None = Field(default=None, ge=1, le=100)
    requires: list[str] = []
    strict: bool = True
    meta: bool = False
    control: bool = False
    offered: bool = True
    order: int = 1000
    adapter: str | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not TOOL.match(value):
            raise ValueError(f"tool name {value!r} must match {TOOL.pattern}")
        return value

    @field_validator("requires")
    @classmethod
    def known_requirements(cls, value):
        unknown = set(value) - REQUIREMENTS
        if unknown:
            raise ValueError(f"unknown requirements {sorted(unknown)}")
        return value

    @field_validator("input_schema")
    @classmethod
    def valid_schema(cls, value):
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as exc:
            raise ValueError(f"invalid input_schema: {exc.message}") from None
        if value.get("type") != "object" or value.get("additionalProperties") is not False:
            raise ValueError("input_schema must be an object with additionalProperties: false")
        return value

    def spec(self, capability: str) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            capability=capability,
            hand=self.hand,
            title=self.title,
            description=self.description,
            input_schema=self.input_schema,
            effect=self.effect,
            dynamic_effect=self.dynamic_effect,
            group=self.group,
            evidence=tuple(self.evidence),
            taints=tuple(self.taints),
            idempotent=self.idempotent,
            timeout_ms=self.timeout_ms,
            concurrency=self.concurrency,
            background=self.background,
            owner_only=self.owner_only,
            hand_from_run=self.hand_from_run,
            budget_per_run=self.budget_per_run,
            requires=tuple(self.requires),
            strict=self.strict,
            meta=self.meta,
        )


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_: str = Field(alias="schema")
    id: str
    title: str
    version: str
    summary: str = Field(min_length=1, max_length=400)
    entry: str | None = None
    kind: str = "builtin"  # builtin | mcp | a2a (user manifests may only be mcp or a2a)
    tools: list[ToolManifest] = []
    prompt: dict = {}
    source: str = ""

    @field_validator("schema_")
    @classmethod
    def known_schema(cls, value):
        if value != "panini.capability/1":
            raise ValueError("schema must be panini.capability/1")
        return value

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        if not ID.match(value):
            raise ValueError(f"id must match {ID.pattern}")
        return value

    @model_validator(mode="after")
    def safe_entry(self):
        if self.entry and not self.entry.startswith("backend."):
            raise ValueError("entry must name a module under backend.")
        return self

    @property
    def control(self) -> set[str]:
        return {t.name for t in self.tools if t.control}

    def principles(self) -> list[str]:
        return list(self.prompt.get("principles", []))


def load_file(path: Path, *, user: bool = False) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"{path.name}: {exc}") from None
    if user and (data.get("entry") or data.get("kind") not in {"mcp", "a2a"}):
        raise ManifestError(
            f"{path.name}: user manifests may only declare mcp or a2a capabilities (no entry)"
        )
    try:
        manifest = Manifest.model_validate({**data, "source": str(path)})
    except ValueError as exc:
        raise ManifestError(f"{path.name}: {exc}") from None
    return manifest


def load_all(sources: list[tuple[Path, bool]]) -> tuple[list[Manifest], list[str]]:
    """Manifests from (folder, user) pairs in precedence order; duplicates and invalid files are reported."""
    manifests, errors, ids, tools = [], [], set(), set()
    for folder, user in sources:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                manifest = load_file(path, user=user)
            except ManifestError as exc:
                errors.append(str(exc))
                continue
            if manifest.id in ids:
                errors.append(f"{path.name}: duplicate capability id {manifest.id!r}")
                continue
            clash = {t.name for t in manifest.tools} & tools
            if clash:
                errors.append(f"{path.name}: duplicate tool names {sorted(clash)}")
                continue
            ids.add(manifest.id)
            tools |= {t.name for t in manifest.tools}
            manifests.append(manifest)
    return manifests, errors
