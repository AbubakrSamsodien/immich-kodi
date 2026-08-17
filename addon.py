"""Addon entry point.

Kept deliberately thin. With `<reuselanguageinvoker>` Kodi keeps the
interpreter alive between navigations, so this module must not cache anything
derived from `sys.argv` — `router.run()` reads it fresh on every call.
"""

import os
import sys

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

# Import the lib modules flat (`import router`), never as `resources.lib.router`.
# Kodi puts the addon root on sys.path, so PEP 420 also makes `resources.lib`
# importable as a namespace package. That second path yields separate module
# objects with their own empty route registry. It cannot be prevented, only
# avoided.

from router import run  # noqa: E402 - import needs the path above

if __name__ == "__main__":
    run()
