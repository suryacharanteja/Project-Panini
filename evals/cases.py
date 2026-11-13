"""Golden-task cases: one YAML file per case under evals/cases/<hand>/ (see evals/README.md)."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CASES = Path(__file__).parent / "cases"
HANDS = {
    "chat",
    "computer",
    "search",
    "browser",
    "desktop",
    "code",
    "memory",
    "background",
    "safety",
    "web",
    "personas",
}
PERSONA_FIELDS = {"id", "name", "role", "instructions", "color", "voice"}
MODES = {"offline", "live", "local"}
APPROVALS = {"decline", "approve", "timeout"}
KNOWN_EXPECT = {
    "outcome_in",
    "message_contains_all",
    "message_contains_any",
    "message_contains_newest_download",
    "message_contains_file_count",
    "file_exists",
    "file_absent",
    "file_contains",
    "approvals_requested_min",
    "approvals_requested_max",
    "max_seconds",
    "evidence_min",
    "task_status_in",
    "personas_ran",
    "personas_not_ran",
}


class CaseError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    hand: str
    modes: tuple[str, ...]
    turn: str
    kind: str = "turn"  # turn | background
    files: dict = field(default_factory=dict)
    script: tuple = ()
    approvals: str = "decline"
    expect: dict = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()  # live prerequisites: desktop, chrome, docker, phone ...
    personas: tuple = ()  # specialists created in the case's data folder (PANINI_PERSONAS on)
    path: str = ""


def parse(path: Path) -> Case:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    hand = path.parent.name
    if hand not in HANDS:
        raise CaseError(f"{path}: unknown hand folder {hand!r}")
    for key in ("id", "title", "turn", "modes", "expect"):
        if key not in data:
            raise CaseError(f"{path}: missing {key}")
    if not str(data["id"]).startswith(hand + "."):
        raise CaseError(f"{path}: id must start with {hand!r}.")
    modes = tuple(data["modes"])
    if not modes or not set(modes) <= MODES:
        raise CaseError(f"{path}: modes must be a subset of {sorted(MODES)}")
    if "offline" in modes and not data.get("script"):
        raise CaseError(f"{path}: offline cases need a scripted model 'script'")
    if data.get("approvals", "decline") not in APPROVALS:
        raise CaseError(f"{path}: approvals must be one of {sorted(APPROVALS)}")
    unknown = set(data["expect"]) - KNOWN_EXPECT
    if unknown:
        raise CaseError(f"{path}: unknown expectations {sorted(unknown)}")
    if data.get("kind", "turn") not in {"turn", "background"}:
        raise CaseError(f"{path}: kind must be turn or background")
    for persona in data.get("personas") or ():
        if not isinstance(persona, dict) or "id" not in persona or set(persona) - PERSONA_FIELDS:
            raise CaseError(f"{path}: personas need an id and only {sorted(PERSONA_FIELDS)}")
    return Case(
        id=data["id"],
        title=data["title"],
        hand=hand,
        modes=modes,
        turn=data["turn"],
        kind=data.get("kind", "turn"),
        files=dict(data.get("files") or {}),
        script=tuple(data.get("script") or ()),
        approvals=data.get("approvals", "decline"),
        expect=dict(data["expect"]),
        tags=tuple(data.get("tags") or ()),
        needs=tuple(data.get("needs") or ()),
        personas=tuple(data.get("personas") or ()),
        path=str(path),
    )


def load(root: Path = CASES, *, mode: str | None = None, only: set[str] | None = None) -> list[Case]:
    cases, seen = [], set()
    for path in sorted(root.rglob("*.yaml")):
        case = parse(path)
        if case.id in seen:
            raise CaseError(f"Duplicate case id {case.id}")
        seen.add(case.id)
        if mode and mode not in case.modes:
            continue
        if only and case.id not in only:
            continue
        cases.append(case)
    return cases


def expand(text, work: Path):
    """Placeholders: {work} is the case's private working folder."""
    if isinstance(text, str):
        return text.replace("{work}", str(work))
    if isinstance(text, list):
        return [expand(item, work) for item in text]
    if isinstance(text, dict):
        return {expand(key, work): expand(value, work) for key, value in text.items()}
    return text
