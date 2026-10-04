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
        "--profile",
        choices=("spaces", "phone", "channels", "outbound", "android", "teach", "desktop"),
        default="spaces",
    )
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
        "live-citation.json",
        "export.pdf",
    ]
    if args.profile == "phone":
        included = [
            "release-check.log",
            "browser-smoke.log",
            "admin-build.log",
            "shell-build.log",
            "final-targeted.log",
            "android-home.png",
            "android-passkeys.png",
            "android-notebook.png",
            "team-live.json",
        ]
    if args.profile in {"channels", "outbound"}:
        included = [
            "release-check.log",
            "browser-smoke.log",
            "admin-build.log",
            "admin-desktop.png",
            "admin-mobile.png",
            "final-targeted.log",
        ]
        if args.profile == "outbound":
            included.append("android-calls.png")
            included.extend(
                name
                for name in ("final-backend.log", "program-qualification.json")
                if (evidence / name).is_file()
            )
    licenses = [
        ("node_modules/marked/LICENSE.md", "marked-LICENSE.txt"),
        ("node_modules/dompurify/LICENSE", "dompurify-LICENSE.txt"),
        ("node_modules/dompurify/LICENSE-MPL", "dompurify-LICENSE-MPL.txt"),
    ]
    if args.profile == "android":
        included = [
            "release-check.log",
            "browser-smoke.log",
            "admin-build.log",
            "admin-desktop.png",
            "admin-mobile.png",
            "admin-live.png",
            "android-home.png",
            "final-targeted.log",
        ]
        licenses += [
            ("node_modules/playwright-core/LICENSE", "playwright-core-LICENSE.txt"),
            ("node_modules/playwright-core/NOTICE", "playwright-core-NOTICE.txt"),
            ("node_modules/playwright-core/ThirdPartyNotices.txt", "playwright-core-ThirdPartyNotices.txt"),
        ]
    if args.profile == "teach":
        included = [
            "release-check.log",
            "browser-smoke.log",
            "admin-build.log",
            "shell-build.log",
            "admin-desktop.png",
            "admin-mobile.png",
            "review-desktop.png",
            "final-targeted.log",
            "real-browser-evidence.json",
            "normalized-recording.json",
        ]
        licenses += [
            ("node_modules/playwright-core/LICENSE", "playwright-core-LICENSE.txt"),
            ("node_modules/playwright-core/NOTICE", "playwright-core-NOTICE.txt"),
        ]
    if args.profile == "desktop":
        included = [
            "release-check.log",
            "final-targeted.log",
            "native-build.log",
            "native-wpf.log",
            "native-win32.log",
            "notepad-smoke.log",
            "native-wpf-evidence.json",
            "native-win32-evidence.json",
            "notepad-evidence.json",
            "native-wpf-snapshot.txt",
            "native-win32-snapshot.txt",
            "notepad-before.txt",
            "notepad-after.txt",
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
                    "profile": args.profile,
                },
                indent=2,
            ),
        )
        setup = (
            "Enable PANINI_CHANNELS=telegram in Settings, then add your bot and numeric owner IDs in Admin → Build → Channels. Telegram text runs use durable background permissions; approve effects on the PC or paired phone."
            if args.profile == "channels"
            else "Enable PANINI_SPACES, PANINI_V2_AGUI and PANINI_V2_INTERRUPTS in Settings for notebook conversations and reviews."
        )
        if args.profile == "outbound":
            setup = "Configure the outbound TLS SIP trunk and owner checklist in Admin → Build → Phone line, then opt in with PANINI_OUTBOUND_CALLS. Prepare a brief and approve its exact replacement-safe review in Inbox. Read the outbound qualification document; no real SIP or physical Android result is claimed."
        if args.profile == "android":
            setup = "Enable PANINI_ANDROID and PANINI_V2_LEDGER, install Android SDK Platform Tools and run the pinned Playwright CLI's install android command for source development. In Admin → Build → Android hands, pair/connect or authorize USB debugging, select the phone and acknowledge driver installation. The unsigned installer includes the pinned driver APKs. Read the Android qualification report; no physical Android result is claimed."
        if args.profile == "teach":
            setup = "Enable PANINI_TEACH, PANINI_V2_LEDGER and PANINI_V2_INTERRUPTS with the Playwright browser engine. In Admin → Build → Skills, click Watch me, perform the browser task, then Stop. Review the defaults and a specific expected result, then approve the version in Inbox. Replay requests action approvals separately. Desktop recording and frame-scoped replay remain pending; read the teach-browser qualification report."
        if args.profile == "desktop":
            setup = "Set PANINI_DESKTOP_TOOLS=fine and enable PANINI_V2_LEDGER in Settings. Start a private foreground desktop task; list Windows applications, snapshot a selected window and use only its current refs. Actions invalidate refs, and every claimed result needs an assertion. Coarse remains the default; read the Windows checkpoint qualification report for pending macros, desktop recording, other hand normalizers and golden-task comparison."
        bundle.writestr(
            "README.txt",
            f"PANINI local development increment {commit}\nPatch base: {base}\n\nApply source.patch to a checkout at the patch base, run npm ci, and use the existing Python development environment. Built Admin, shell and Android PWA assets are included at their repository paths. {setup} Read the included qualification document for remaining requirements.\n\nThis is not a standalone installer, signed production release, physical Android qualification or full LLD completion. No credentials or databases are included.\n",
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
        for source, name in licenses:
            bundle.write(ROOT / source, "licenses/" + name)
        for path in sorted((ROOT / "docs/panini-enhancements").rglob("*.md")):
            bundle.write(path, path.relative_to(ROOT).as_posix())
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(checksum + "  " + output.name + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "sha256": checksum, "commit": commit}))


if __name__ == "__main__":
    main()
