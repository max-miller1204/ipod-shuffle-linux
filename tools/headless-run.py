#!/usr/bin/env python3
"""Run a GTK check on a private Xvfb display, never the user's desktop."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} COMMAND [ARG ...]")
    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run is None:
        sys.exit(
            "xvfb-run is required for local GUI checks; install the xvfb package. "
            "The check was not run on your desktop."
        )
    env = dict(os.environ)
    env["SHUFFLE_HEADLESS_TEST"] = "1"
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    return subprocess.run(
        [xvfb_run, "-a", "--server-args=-screen 0 2400x1800x24", *sys.argv[1:]],
        env=env,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
