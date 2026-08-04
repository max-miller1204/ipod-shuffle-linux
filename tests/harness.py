"""Loads the GUI package for the smoke checks, and lets them replace parts.

The window used to be one module, so a check could rebind one of its globals
and every caller saw the stand-in. It is a package now, and `from .device
import find_ipods` copies that name into each importing module: rebinding it
in one of them would leave the others calling the real function, and a check
written to run against a stand-in would quietly pass against the real code
instead. Passing for the wrong reason is worse than failing, so the checks go
through `gui` rather than touching the package directly.

Assigning `gui.name` writes to every module of the package that holds that
name, which is what the single module used to give for free. Assigning a name
no module holds raises instead of silently doing nothing, so a helper that
moves or is renamed breaks the check that depended on it rather than turning
it into a check of the real implementation.

Reading `gui.name` answers from the module that defines it - the first one in
ipod_gui.__all__ that has it, and that list is ordered innermost first. That
matters for state the package changes itself: scan_tracks resolves TAG_PYTHON
on first use, and only the defining module's binding sees that.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import ipod_gui  # noqa: E402

# A module missing from __all__ would be one this file never writes to, which
# is the silent half-patch the whole arrangement exists to prevent.
_declared = set(ipod_gui.__all__)
_present = {
    path.stem
    for path in (REPO / "ipod_gui").glob("*.py")
    if path.stem != "__init__"
}
if _declared != _present:
    raise ImportError(
        "ipod_gui.__all__ does not list every module of the package: "
        f"missing {sorted(_present - _declared)}, "
        f"stale {sorted(_declared - _present)}"
    )


class _Facade:
    """The package as one namespace, the way the single module was."""

    @staticmethod
    def _modules():
        return [getattr(ipod_gui, name) for name in ipod_gui.__all__]

    def __getattr__(self, name):
        for module in self._modules():
            if name in vars(module):
                return getattr(module, name)
        raise AttributeError(f"no ipod_gui module defines {name!r}")

    def __setattr__(self, name, value):
        holders = [module for module in self._modules() if name in vars(module)]
        if not holders:
            raise AttributeError(
                f"no ipod_gui module holds {name!r}, so replacing it here "
                f"would leave every caller running the real one"
            )
        for module in holders:
            setattr(module, name, value)


gui = _Facade()
