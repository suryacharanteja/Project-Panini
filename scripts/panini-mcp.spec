from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = []
for package in ('mcp', 'mcp-types', 'jsonschema', 'httpx2'):
    datas += copy_metadata(package)
a = Analysis([str(root / 'scripts/panini-mcp.py')], pathex=[str(root)],
             binaries=[], datas=datas, hiddenimports=collect_submodules('mcp'),
             excludes=['pytest', 'ruff', 'pip', 'tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='panini-mcp',
          icon=str(root / 'assets/panini-cli.ico'), console=True, strip=False, upx=False)
