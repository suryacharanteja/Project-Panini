"""Package a committed local increment: source patch, built surfaces and explicit evidence."""

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Verified commit the source patch applies to.")
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--evidence", required=True, help="Workspace artifact directory with release-check.log."
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,60}", args.label):
        parser.error("Use a short lowercase label.")
    if git("status", "--porcelain").strip():
        parser.error("Commit the source before creating the bundle.")
    base = git("rev-parse", "--verify", args.base + "^{commit}").decode().strip()
    commit = git("rev-parse", "HEAD").decode().strip()
    evidence = (ROOT / args.evidence).resolve()
    if not evidence.is_relative_to((ROOT / "artifacts").resolve()):
        parser.error("Evidence must be in this workspace's artifacts directory.")
    included = [
        "release-check.log",
        "browser-smoke.log",
        "admin-build.log",
        "shell-build.log",
        "admin-desktop.png",
        "admin-mobile.png",
        "final-targeted.log",
    ]
    if any(not (evidence / name).is_file() for name in included):
        parser.error("Build and verify the surfaces before packaging their evidence.")
    output = evidence / f"panini-{args.label}-dev-{commit[:7]}.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("source.patch", git("diff", "--binary", base, commit))
        bundle.writestr(
            "qualification.json",
            json.dumps(
                {
                    "kind": "local development increment",
                    "commit": commit,
                    "patch_base": base,
                    "production_qualified": False,
                    "red_team": "disabled and deferred",
                },
                indent=2,
            ),
        )
        bundle.writestr(
            "README.txt",
            f"PANINI local development increment {commit}\nPatch base: {base}\n\nApply source.patch to a checkout at the patch base, run npm ci, and use the existing Python development environment. Built Admin, shell and Android PWA assets are included at their repository paths. Enable PANINI_SPACES, PANINI_V2_AGUI and PANINI_V2_INTERRUPTS in Settings for notebook conversations and reviews. Read the included qualification document for remaining requirements.\n\nThis is not a standalone installer, signed production release, physical Android qualification or full LLD completion. No credentials or databases are included.\n",
        )
        for directory in ("frontend/public/admin", "panini-core/apps/shell/dist", "panini-core/apps/phone"):
            for path in sorted((ROOT / directory).rglob("*")):
                if path.is_file() and path.suffix.lower() in {
                    ".html",
                    ".js",
                    ".mjs",
                    ".css",
                    ".png",
                    ".svg",
                    ".ico",
                    ".json",
                    ".webmanifest",
                    ".md",
                    ".woff",
                    ".woff2",
                }:
                    bundle.write(path, path.relative_to(ROOT).as_posix())
        for name in included:
            bundle.write(evidence / name, "evidence/" + name)
        for path in sorted((ROOT / "docs/panini-enhancements").rglob("*.md")):
            bundle.write(path, path.relative_to(ROOT).as_posix())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(checksum + "  " + output.name + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "sha256": checksum, "commit": commit}))


if __name__ == "__main__":
    main()
