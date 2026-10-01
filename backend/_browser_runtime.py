"""Import helpers for the PANINI Browser capability runtime.

The browser implementation lives under ``panini-core/capabilities/browser``.
Backend modules keep compatibility imports while the product source ownership
stays with the Browser capability.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "panini-core" / "capabilities" / "browser" / "runtime"


def load_browser_module(name: str) -> ModuleType:
    runtime = str(RUNTIME_ROOT)
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    return importlib.import_module(f"panini_browser.{name}")
