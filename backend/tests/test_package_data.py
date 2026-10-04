"""Frozen migrations must remain real files, including dynamically loaded Python migrations."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def test_frozen_backend_includes_all_migrations_and_identity(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = lambda _: ([], [], [])
    hooks.collect_submodules = lambda *_args, **_kwargs: []
    hooks.copy_metadata = lambda _: []
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr(sys, "path", list(sys.path))
    captured = {}

    def analysis(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

    scope = {
        "SPECPATH": str(root / "scripts"),
        "Analysis": analysis,
        "PYZ": lambda *_args, **_kwargs: None,
        "EXE": lambda *_args, **_kwargs: None,
        "COLLECT": lambda *_args, **_kwargs: None,
    }
    source = root / "scripts/panini-backend.spec"
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), scope)
    packaged = {
        Path(path).name for path, target in captured["datas"] if target == "backend/harness/migrations"
    }
    expected = {
        path.name
        for path in (root / "backend/harness/migrations").iterdir()
        if path.name[:4].isdigit() and path.suffix in {".sql", ".py"}
    }
    assert packaged == expected
    assert "0024_passkeys.sql" in packaged
    assert any(name.endswith(".py") for name in packaged)
    assert (str(root / "backend/harness/persona.md"), "backend/harness") in captured["datas"]
