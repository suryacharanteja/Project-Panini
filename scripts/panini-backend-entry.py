"""Entry point for the private, frozen backend sidecar."""

import os
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["panini-cli"]:
        # The frozen backend doubles as PANINI-CLI's runtime (panini-cli app-server --stdio).
        from panini_cli.agent.cli import main

        raise SystemExit(main(sys.argv[2:]))
    import uvicorn

    from backend.app import app

    if sys.argv[1:2] == ["--check-runtime"]:
        from backend.harness.runtime import Harness  # noqa: F401

        print("PANINI_BACKEND_RUNTIME_IMPORTS_OK", flush=True)
        raise SystemExit(0)

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BACKEND_PORT", "8200")), access_log=False)
