"""The backend's parameterless GET routes, as JSON, for the whole-product test (scripts/panini-product-test.mjs)."""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "panini-core/capabilities/cli/src")]
os.environ["PANINI_DATA_DIR"] = tempfile.mkdtemp(prefix="panini-routes-")
for key in ("JEV_REV_DESKTOP_ENABLED", "PANINI_CLI_ENABLED", "PANINI_CAPABILITY_ROUTER_ENABLED"):
    os.environ[key] = "0"  # Routes are registered either way; this only skips the capability coordinator.
os.environ.setdefault("PANINI_JEV_GATES", "false")

from backend.app import create_app  # noqa: E402
from backend.panini.settings import Settings  # noqa: E402

app = create_app(panini_settings=Settings(enabled=True, bootstrap_token="r" * 40))
paths = sorted(
    path for path, operations in app.openapi()["paths"].items() if "get" in operations and "{" not in path
)
print(json.dumps(paths))
