from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH).parent
hiddenimports = collect_submodules('panini_cli') + collect_submodules('flatbuffers')
datas = []
for package in ('openai', 'flatbuffers'):
    datas += copy_metadata(package)

a = Analysis(
    [str(root / 'scripts/panini-cli-entry.py')],
    pathex=[str(root / 'panini-core/capabilities/cli/src')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['pytest', 'ruff', 'pip', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PANINI-CLI',
    icon=str(root / 'assets/panini-cli.ico'),
    console=True,
    strip=False,
    upx=False,
)


