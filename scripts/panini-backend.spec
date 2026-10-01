from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
runtime_paths = [
    str(root),
    str(root / "panini-core/voice/live-spoken-conversation"),
    str(root / "panini-core/capabilities/cli/src"),
    str(root / "panini-core/capabilities/browser/runtime"),
]
# collect_submodules runs before Analysis applies pathex. Dynamic capability
# imports must be discoverable at collection time as well as analysis time.
sys.path[:0] = runtime_paths
datas, binaries, hiddenimports = [(str(root / "backend/tasks.json"), "backend")], [], []
# Built-in agent skills are data files (SKILL.md), not modules.
datas += [
    (str(p), str(p.parent.relative_to(root)))
    for p in (root / "backend/harness/builtin_skills").rglob("SKILL.md")
]
# The phone companion's static app.
datas += [
    (str(p), "backend/harness/mobile_app")
    for p in (root / "backend/harness/mobile_app").iterdir()
    if p.is_file()
]
for package in ("browser_use", "panini", "uvicorn", "bubus", "cdp_use"):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += imports
hiddenimports += collect_submodules("backend", filter=lambda name: ".tests" not in name)
hiddenimports += collect_submodules("panini_cli")
browser_root = root / "panini-core/capabilities/browser/runtime"
hiddenimports += [
    ".".join(p.relative_to(browser_root).with_suffix("").parts).removesuffix(".__init__")
    for p in (browser_root / "panini_browser").rglob("*.py")
]
for package in ("browser-use", "pydantic", "openai", "httpx", "fastapi"):
    datas += copy_metadata(package)
a = Analysis(
    [str(root / "scripts/panini-backend-entry.py")],
    pathex=runtime_paths,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["backend.tests", "pytest", "ruff", "pip", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Panini.Backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Panini.Backend")
