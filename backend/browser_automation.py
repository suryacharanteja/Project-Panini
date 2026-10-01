import sys

from ._browser_runtime import load_browser_module

_module = load_browser_module("browser_automation")
sys.modules[__name__] = _module
