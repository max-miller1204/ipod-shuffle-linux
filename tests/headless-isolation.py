#!/usr/bin/env python3
"""Holds tools/headless-run.py to the isolation every GUI check routes through.

The promise is that a check never lands on the desktop the developer is looking
at, and that promise cannot be read off the variables the runner scrubs. GDK
chooses its own backend, and a Wayland client handed no socket name falls back
to the compositor's default one, so a runner that unsets everything it knows
about can still hand the check the real screen - which is what happened, and
what this pins. What settles it is where the process actually ended up, so that
is what is read back: the display GDK connected to, the screen that display
advertises, and the bus the process was given.

The second half is the other half of the same promise. The runner refuses when
either server is missing rather than letting the check through onto whatever is
already there, so it is run with each of them taken off PATH and the wrapped
command is given a file to write: a refusal that still ran the command is a
window on the desktop, and the file is how that would show.

This process needs no display of its own and imports no GTK; only the child it
starts does.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "tools/headless-run.py"

# The screen tools/headless-run.py asks Xvfb for. Reading it back off the
# display is what distinguishes the server the runner started from any other
# one that happened to answer.
SCREEN = [2400, 1800]

REPORT = "SHUFFLE_ISOLATION_REPORT"


if os.environ.get(REPORT):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk  # noqa: E402

    Gtk.init_check()
    display = Gdk.Display.get_default()
    geometry = None
    if display is not None:
        monitors = display.get_monitors()
        if monitors.get_n_items():
            box = monitors.get_item(0).get_geometry()
            geometry = [box.width, box.height]
    Path(os.environ[REPORT]).write_text(
        json.dumps(
            {
                "connected": display.get_name() if display else None,
                "backend": type(display).__name__ if display else None,
                "geometry": geometry,
                "DISPLAY": os.environ.get("DISPLAY"),
                "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
                "bus": os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
            }
        )
    )
    raise SystemExit(0)


def refuses(workspace, path):
    """Run the runner with `path` as PATH, and say whether it let anything run.

    The wrapped command writes a file, so a refusal that is only a message on
    stderr - the runner exiting non-zero after the check has already opened a
    window - is told apart from one that actually stopped it.
    """
    ran = Path(tempfile.mkdtemp(dir=workspace)) / "ran"
    env = dict(os.environ)
    env["PATH"] = path
    result = subprocess.run(
        [
            sys.executable,
            RUNNER,
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(ran)!r}).write_text('ran')",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    return result, ran


with tempfile.TemporaryDirectory(prefix="headless-isolation-") as workspace:
    report = Path(workspace) / "report.json"
    environment = dict(os.environ)
    environment[REPORT] = str(report)
    started = subprocess.run([sys.executable, RUNNER, sys.executable, __file__],
                             env=environment)
    assert started.returncode == 0, (
        f"{RUNNER.name} exited {started.returncode} rather than running the "
        "check on a display of its own"
    )
    observed = json.loads(report.read_text())

    assert observed["connected"] is not None, "the check reached no display at all"
    assert observed["DISPLAY"], "the check was given no DISPLAY"
    assert observed["DISPLAY"] != os.environ.get("DISPLAY"), (
        f"the check ran on {observed['DISPLAY']}, the display this process was "
        "started with, rather than one of its own"
    )
    # The regression this exists for: with GDK left to choose, it took the
    # Wayland backend and connected to the desktop compositor's default socket
    # while the private X server it was handed sat unused, so the name it
    # reports and the display it was given came apart.
    assert observed["connected"] == observed["DISPLAY"], (
        f"the check was handed {observed['DISPLAY']} and connected to "
        f"{observed['connected']} instead, through {observed['backend']}: it "
        "is on a display server the runner does not control"
    )
    assert observed["geometry"] == SCREEN, (
        f"the check's display is {observed['geometry']}, not the {SCREEN} "
        f"screen {RUNNER.name} asks Xvfb for: it is not that server"
    )
    assert observed["WAYLAND_DISPLAY"] is None, (
        "the check kept WAYLAND_DISPLAY and can still reach the compositor"
    )
    assert observed["bus"], "the check was given no session bus"
    assert observed["bus"] != os.environ.get("DBUS_SESSION_BUS_ADDRESS"), (
        "the check shares this process's session bus, so it would meet the "
        "application already running on it"
    )

    empty = Path(workspace) / "no-servers"
    empty.mkdir()
    result, ran = refuses(workspace, str(empty))
    assert result.returncode != 0 and not ran.exists(), (
        f"with no xvfb-run on PATH {RUNNER.name} ran the command anyway "
        f"(exit {result.returncode}, wrote {ran.exists()})"
    )

    xvfb_only = Path(workspace) / "xvfb-only"
    xvfb_only.mkdir()
    which = subprocess.run(["sh", "-c", "command -v xvfb-run"],
                           capture_output=True, text=True)
    assert which.returncode == 0, "xvfb-run is required to run this check"
    (xvfb_only / "xvfb-run").symlink_to(which.stdout.strip())
    result, ran = refuses(workspace, str(xvfb_only))
    assert result.returncode != 0 and not ran.exists(), (
        f"with no dbus-run-session on PATH {RUNNER.name} ran the command "
        f"anyway (exit {result.returncode}, wrote {ran.exists()})"
    )

print("headless isolation ok")
