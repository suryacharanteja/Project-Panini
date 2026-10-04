from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.brain.threads import Threads
from backend.harness.sessions import Sessions
from backend.harness.store import Store
from backend.spaces import Spaces, SpacesError


@pytest.fixture
def notebook(tmp_path):
    store = Store(tmp_path / "spaces.db")
    spaces = Spaces(store, Threads(store, Sessions(store)))
    yield spaces
    store.close()


def test_access_search_and_revocation(notebook):
    a = notebook.create_space("Research")
    b = notebook.create_space("Private")
    notebook.create(a["id"], "Launch", "A nebula observation")
    notebook.create(b["id"], "Secret", "nebula private")
    notebook.grant(a["id"], "researcher", True)
    assert [p["title"] for p in notebook.search('nebula OR "', "researcher")] == []
    assert [p["title"] for p in notebook.search("nebula", "researcher")] == ["Launch"]
    notebook.grant(a["id"], "researcher", False)
    assert notebook.search("nebula", "researcher") == []
    with pytest.raises(SpacesError, match="cannot access"):
        notebook.pages(a["id"], "researcher")


def test_cas_has_one_winner_and_no_lost_revision(notebook):
    s = notebook.create_space("Work")
    p = notebook.create(s["id"], "Plan", "original")

    def save(text):
        try:
            return notebook.edit(p["id"], 1, content=text)["revision"]
        except SpacesError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, ["one", "two"]))
    assert sorted(map(str, results)) == ["2", "PAGE_CHANGED"]
    assert len(notebook.revisions(p["id"])) == 2


def test_parent_cycles_cross_space_and_trash(notebook):
    s = notebook.create_space("Work")
    other = notebook.create_space("Other")
    p = notebook.create(s["id"], "Parent")
    child = notebook.create(s["id"], "Child", parent_id=p["id"])
    for operation in [
        lambda: notebook.edit(p["id"], 1, move=True, parent_id=child["id"]),
        lambda: notebook.create(other["id"], "Wrong", parent_id=p["id"]),
        lambda: notebook.edit(p["id"], 1, trash=True),
    ]:
        with pytest.raises(SpacesError):
            operation()
    assert notebook.read(p["id"])["revision"] == 1
    notebook.edit(child["id"], 1, move=True, parent_id=None)
    trashed = notebook.edit(p["id"], 1, trash=True)
    assert not notebook.search("Parent")
    with pytest.raises(SpacesError):
        notebook.read(p["id"])
    notebook.edit(p["id"], trashed["revision"], restore=True)
    assert notebook.read(p["id"])["revision"] == 3


def test_retention_expiry_and_validation_are_atomic(notebook):
    s = notebook.create_space("Work")
    p = notebook.create(s["id"], "Page", "kept")
    with pytest.raises(SpacesError):
        notebook.edit(p["id"], 1, content="x" * 200001)
    assert notebook.read(p["id"])["content"] == "kept"
    for revision in range(1, 56):
        notebook.edit(p["id"], revision, content=str(revision))
    assert len(notebook.revisions(p["id"])) == 50
    p = notebook.edit(p["id"], 56, trash=True)
    notebook.clock = lambda: p["deleted_at"] + 30 * 86400 + 1
    with pytest.raises(SpacesError, match="cannot be restored"):
        notebook.edit(p["id"], 57, restore=True)


def test_thread_concurrency_and_restart(notebook):
    s = notebook.create_space("Work")
    p = notebook.create(s["id"], "Page")
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda _: notebook.thread(p["id"]), range(24)))
    assert len(set(ids)) == 1
    restarted = Spaces(notebook.store, Threads(notebook.store, Sessions(notebook.store)))
    assert restarted.thread(p["id"]) == ids[0]
    assert notebook.store.one("SELECT count(*) AS n FROM sessions")["n"] == 1
    notebook.grant(s["id"], "specialist", True)
    assert notebook.thread(p["id"], "specialist") != ids[0]
    notebook.grant(s["id"], "specialist", False)
    with pytest.raises(SpacesError):
        notebook.thread(p["id"], "specialist")
