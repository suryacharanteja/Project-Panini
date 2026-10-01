"""Verify the frozen CLI never resolves the sandbox DLL from its workspace."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "panini-core/capabilities/cli/tests/fixtures/native_packaged_smoke.json"


def main(executable):
    with tempfile.TemporaryDirectory(prefix="panini-dll-workspace-") as directory:
        workspace = Path(directory)
        (workspace / "processmodel.dll").write_bytes(b"not a Windows DLL")
        result = subprocess.run(
            [
                str(executable.resolve()),
                "-C",
                str(workspace),
                "--sandbox",
                "native",
                "--approve-all",
                "exec",
                "Run the DLL boundary smoke test",
            ],
            cwd=workspace,
            env={**os.environ, "PANINI_CLI_SCRIPTED_MODEL": str(MODEL)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
        if result.returncode or "PANINI_NATIVE_PACKAGED_SMOKE" not in result.stdout:
            raise AssertionError(
                f"Packaged CLI failed the System32-only DLL test: {(result.stdout + result.stderr)[-1500:]}"
            )
    print("PANINI_PACKAGED_SYSTEM32_DLL_VERIFIED")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
