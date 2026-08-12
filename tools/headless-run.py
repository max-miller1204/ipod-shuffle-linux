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
    dbus_run = shutil.which("dbus-run-session")
    if dbus_run is None:
        sys.exit("dbus-run-session is required to isolate the test application")
    env = dict(os.environ)
    env["SHUFFLE_HEADLESS_TEST"] = "1"
    env.update(
        # Unsetting WAYLAND_DISPLAY does not take GTK off the desktop. GDK
        # tries the Wayland backend before the X11 one, and a Wayland client
        # given no socket name falls back to $XDG_RUNTIME_DIR/wayland-0 - the
        # developer's own compositor - so the private display below would be
        # started, paid for and never drawn on. Naming the backend is what
        # actually decides it, and pinning it to the one server this runner
        # controls also means a failure to start that server is a failure to
        # run, rather than a window opening on the desktop.
        GDK_BACKEND="x11",
        GTK_USE_PORTAL="0",
        GIO_USE_VFS="local",
        NO_AT_BRIDGE="1",
    )
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    return subprocess.run(
        [
            dbus_run,
            "--",
            xvfb_run,
            "-a",
            "--server-args=-screen 0 2400x1800x24",
            *sys.argv[1:],
        ],
        env=env,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
