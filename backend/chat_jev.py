import sys

from ._browser_runtime import load_browser_module

_module = load_browser_module("chat_jev")
sys.modules[__name__] = _module
