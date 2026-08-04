#!/usr/bin/env python3
"""Launches the GTK4 front end, which lives in the ipod_gui package.

Kept as a script beside the shell tools because that is what ipod-gui.sh, the
desktop entry and the README all point at, and because the package next to it
is only importable once this directory is on the path. Everything the window
does is in ipod_gui/; see its __init__.py for what each module owns.
"""

import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from ipod_gui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
