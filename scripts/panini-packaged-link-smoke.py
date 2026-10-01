"""Verify the frozen CLI rejects a pre-existing hard-link escape."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "panini-core/capabilities/cli/tests/fixtures/native_packaged_smoke.json"


def main(executable):
    with tempfile.TemporaryDirectory(prefix="panini-link-workspace-") as workspace_dir:
        with tempfile.TemporaryDirectory(prefix="panini-link-outside-") as outside_dir:
            workspace = Path(workspace_dir)
            outside = Path(outside_dir) / "private.txt"
            outside.write_text("PANINI_PRIVATE_SENTINEL", encoding="utf-8")
            os.link(outside, workspace / "alias.txt")
            env = {**os.environ, "PANINI_CLI_SCRIPTED_MODEL": str(MODEL)}
            result = subprocess.run(
                [
                    str(executable),
                    "-C",
                    str(workspace),
                    "--sandbox",
                    "native",
                    "--approve-all",
                    "exec",
                    "Run the hard-link boundary smoke test",
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=90,
                check=False,
            )
            transcript = result.stdout + result.stderr
            if outside.read_text(encoding="utf-8") != "PANINI_PRIVATE_SENTINEL":
                raise AssertionError("Packaged CLI modified an outside hard-linked file")
            if "workspace hard link" not in transcript:
                raise AssertionError(f"Packaged CLI did not report hard-link rejection: {transcript[-1500:]}")
    print("PANINI_PACKAGED_HARDLINK_REJECTED")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
