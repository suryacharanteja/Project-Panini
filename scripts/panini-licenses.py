"""Collect installed dependency notices alongside the packaged dependency inventories."""

import importlib.metadata
import json
import re
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1]).resolve()
if output != root / "artifacts" / "panini-runtime" / "licenses":
    raise SystemExit("License output must stay in this project's runtime staging directory.")


def copy(source, folder):
    if source.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, folder / source.name)


packages = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name", "unknown")
    identifier = re.sub(r"[^\w.-]", "_", name)
    packages.append(
        {
            "name": name,
            "version": distribution.version,
            "license": distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License", ""),
        }
    )
    for file in distribution.files or []:
        if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts):
            copy(Path(distribution.locate_file(file)), output / "python" / identifier)
(output / "python-packages.json").write_text(json.dumps(packages, indent=2), encoding="utf-8")


# Include direct and transitive package notices; development notices are harmless extras.
def node_packages(directory):
    if not directory.is_dir():
        return
    for package in directory.iterdir():
        if not package.is_dir() or package.is_symlink() or package.name.startswith("."):
            continue
        if package.name.startswith("@"):
            node_packages(package)
            continue
        manifest = package / "package.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            identifier = re.sub(r"[^\w.-]", "_", data.get("name", package.name))
            for item in package.iterdir():
                if item.name.lower().startswith(("license", "copying", "notice", "third-party-notices")):
                    copy(item, output / "npm" / identifier)
        node_packages(package / "node_modules")


node_packages(root / "node_modules")
for name in ("frontend", "panini-core/capabilities/search", "panini-core/apps/shell"):
    node_packages(root / name / "node_modules")
for source in (
    root / "panini-core/voice/live-spoken-conversation" / "LICENSE",
    root / "panini-core/capabilities/search" / "LICENSE",
):
    copy(source, output / "upstream" / source.parent.name)
print(f"Collected notices for {len(packages)} Python distributions and installed npm packages.")
vendor_root = root / "panini-core/capabilities/desktop" / "vendor"
if vendor_root.is_dir():
    for vendor in vendor_root.iterdir():
        for name in ("LICENSE", "PROVENANCE.md"):
            copy(vendor / name, output / "upstream" / vendor.name)
