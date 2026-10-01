import sys

from ._browser_runtime import load_browser_module

_module = load_browser_module("jev_browser_loop")
sys.modules[__name__] = _module
