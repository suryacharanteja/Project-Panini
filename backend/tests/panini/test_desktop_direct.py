import asyncio
from types import SimpleNamespace

import pytest

from backend.capabilities import desktop


class Context:
    def __init__(self):
        self.task = SimpleNamespace(task_id="t1")
        self.evidence = []

    def record_evidence(self, kind, summary, detail):
        self.evidence.append((kind, summary, detail))
        return "ev1"


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "report.pdf").write_bytes(b"x" * 10)
    (folder / "photos").mkdir()
    monkeypatch.setattr(desktop, "known_folder", lambda name: folder)
    monkeypatch.setattr(desktop.os, "startfile", lambda path: None, raising=False)
    return folder


def direct(text):
    adapter = desktop.DesktopAdapter.__new__(desktop.DesktopAdapter)
    return asyncio.run(adapter.direct(Context(), text))


def test_listing_a_known_folder_is_answered_from_the_file_system(downloads):
    outcome = direct("Open the Downloads folder and list all files currently present.")
    assert outcome.outcome == "verified_complete" and "2 items" in outcome.message
    assert {r["title"] for r in outcome.results} == {"report.pdf", "photos\\"}
    assert outcome.evidence_ids == ["ev1"]


@pytest.mark.parametrize(
    "text",
    ["Delete the files in my Downloads folder", "Set the draft in Notepad", "Move report.pdf to Documents"],
)
def test_changes_and_other_tasks_use_the_normal_path(downloads, text):
    assert direct(text) is None
