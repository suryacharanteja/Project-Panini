"""With every application a valid target, the instruction itself says which window it means."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.panini.contracts import Fault
from backend.panini.events import EventHub
from backend.panini.targets import pick_from_instruction, resolve_target

WINDOWS = [
    {"choice_id": "paint", "label": "Untitled - Paint", "application": "mspaint"},
    {"choice_id": "claude", "label": "Claude", "application": "claude"},
    {"choice_id": "code", "label": "Welcome - project-PANINI - Visual Studio Code", "application": "code"},
    {
        "choice_id": "chrome",
        "label": "Cheap flights to Bengaluru | Skyscanner - Google Chrome",
        "application": "chrome",
    },
    {"choice_id": "term", "label": "PANINI-CLI", "application": "windowsterminal"},
    {"choice_id": "explorer", "label": "assets - File Explorer", "application": "explorer"},
    {"choice_id": "manager", "label": "Program Manager", "application": "explorer"},
    {"choice_id": "nvidia", "label": "NVIDIA GeForce Overlay", "application": "nvcontainer"},
]


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("In Paint, draw a simple elephant", "paint"),
        ("Open MS Paint and sketch a tree", "paint"),
        ("In 'Untitled - Paint', draw a big oval for the body", "paint"),
        ("Type the command git status in the terminal", "term"),
        ("run `dir` in PowerShell", "term"),
        ("In VS Code open the settings", "code"),
        ("Scroll down in Chrome", "chrome"),
        ("Show me the folder in File Explorer", "explorer"),
    ],
)
def test_the_instruction_names_the_window(instruction, expected):
    assert pick_from_instruction(instruction, WINDOWS)["choice_id"] == expected


@pytest.mark.parametrize("instruction", ["click the button", "draw a simple elephant", "", "do the thing"])
def test_no_hint_picks_nothing_rather_than_guessing(instruction):
    assert pick_from_instruction(instruction, WINDOWS) is None


def test_a_tie_picks_nothing():
    twins = [
        {"choice_id": "a", "label": "Notes - Notepad", "application": "notepad"},
        {"choice_id": "b", "label": "Todo - Notepad", "application": "notepad"},
    ]
    assert pick_from_instruction("type hello in Notepad", twins) is None


class Adapter:
    def __init__(self, windows):
        self.windows, self.selected = windows, None

    async def targets(self):
        return [dict(w) for w in self.windows]

    async def select(self, choice_id):
        self.selected = choice_id
        return next(dict(w) for w in self.windows if w["choice_id"] == choice_id)


def context():
    session = SimpleNamespace(selected={}, hub=EventHub("instance", "session"))
    return SimpleNamespace(session=session, task=SimpleNamespace(capability="desktop"), check=lambda: None)


def test_resolve_target_uses_the_instruction_when_many_windows_are_open():
    adapter = Adapter(WINDOWS)
    target = asyncio.run(resolve_target(context(), adapter, "", "In Paint, draw a simple elephant"))
    assert target["label"] == "Untitled - Paint" and adapter.selected == "paint"


def test_resolve_target_still_asks_when_the_instruction_names_no_window():
    with pytest.raises(Fault) as error:
        asyncio.run(resolve_target(context(), Adapter(WINDOWS), "", "click the button"))
    assert error.value.code == "TARGET_AMBIGUOUS" and "Untitled - Paint" in error.value.message


def test_windows_windows_are_never_offered_as_choices():
    noise_only = [WINDOWS[0], WINDOWS[6], WINDOWS[7]]
    target = asyncio.run(resolve_target(context(), Adapter(noise_only), "", "anything"))
    assert target["label"] == "Untitled - Paint"


def test_an_explicit_target_query_still_wins():
    target = asyncio.run(resolve_target(context(), Adapter(WINDOWS), "Visual Studio Code", "In Paint, draw"))
    assert target["choice_id"] == "code"
