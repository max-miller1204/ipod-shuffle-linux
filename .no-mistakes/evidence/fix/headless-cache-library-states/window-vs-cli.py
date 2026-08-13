#!/usr/bin/env python3
"""Compare what the real GTK window shows with what the display-free CLI says.

Both surfaces are pointed at the same canonical demo fixture: four albums in
the music folder, an iPod holding one of them, and one download waiting in the
preview cache. The window is the real Adw application on a private display and
bus; the CLI is `python3 -m ipod_gui.cli library` in a subprocess. The counts
the window paints its pills from - the ones a machine client reads back with
dump-state - have to be the counts the CLI writes down, or "the same merged
view" is two views that agree by luck.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(os.environ["SHUFFLE_REPO"]).resolve()
FIXTURE = Path(os.environ["SHUFFLE_FIXTURE"]).resolve()

if not os.environ.get("SHUFFLE_HEADLESS_TEST"):
    raise SystemExit(
        subprocess.run(
            [sys.executable, REPO / "tools/headless-run.py", sys.executable, __file__]
        ).returncode
    )

sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit("no display: tools/headless-run.py started none to build on")


def settle(condition, seconds=60):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        if not context.iteration(False):
            time.sleep(0.01)
    return bool(condition())


app = gui.IpodApp()
if not app.register(None):
    raise SystemExit("the application would not register")
if app.get_is_remote():
    raise SystemExit("another instance already owns this application's bus name")
app.activate()
window = app.props.active_window
if window is None:
    raise SystemExit("activating the application built no window")

if not settle(lambda: window.probe_answered):
    raise SystemExit("the window's first device probe never landed")
if not settle(lambda: not window._library_scan_running):
    raise SystemExit("the window's first library scan never finished")
# The device tag scan lands separately from the probe, and the "On iPod"
# badges are what it decides, so the window is only comparable once it has.
settle(lambda: window.device_tracks and not window._device_scan_active)

app.activate_action("dump-state", None)
state = json.loads(app.lookup_action("dump-state").get_state().get_string())

cli = subprocess.run(
    [sys.executable, "-m", "ipod_gui.cli", "library"],
    cwd=REPO,
    env=dict(os.environ, PYTHONPATH=str(REPO)),
    capture_output=True,
    text=True,
)
if cli.returncode != 0:
    raise SystemExit(f"the CLI refused: {cli.stderr}")
document = json.loads(cli.stdout)

window_rows = sorted(
    (track.state, track.artist, track.album, track.title)
    for track in window.library.all_tracks()
)
cli_rows = sorted(
    (track["state"], track["artist"], track["album"], track["title"])
    for track in document["result"]["tracks"]
)

report = {
    "fixture": str(FIXTURE),
    "windowVisibleCounts": state["visibleCounts"],
    "cliCounts": document["result"]["counts"],
    "windowTracks": window_rows,
    "cliTracks": cli_rows,
    "cliComplete": document["result"]["complete"],
}
print(json.dumps(report, indent=2))


# The one place the two are meant to differ, and the reason the merge rule
# takes the queue as an argument: staging a track is a thing a window with a
# person in front of it has, and a headless run has no queue to have. The
# window counts it; the CLI, reading the same three places a moment later,
# still answers zero rather than inventing one.
staged = Path(FIXTURE, "Elsewhere", "01 - Highway.mp3")
queued_report = None
if staged.is_file():
    app.activate_action("queue", GLib.Variant("s", str(staged)))
    settle(lambda: window.dump_state()["visibleCounts"]["queued"] == 1)
    queued_state = json.loads(
        (app.activate_action("dump-state", None) or True)
        and app.lookup_action("dump-state").get_state().get_string()
    )
    queued_cli = subprocess.run(
        [sys.executable, "-m", "ipod_gui.cli", "library"],
        cwd=REPO,
        env=dict(os.environ, PYTHONPATH=str(REPO)),
        capture_output=True,
        text=True,
    )
    queued_document = json.loads(queued_cli.stdout)
    queued_report = {
        "stagedPath": str(staged),
        "windowVisibleCountsAfterQueue": queued_state["visibleCounts"],
        "windowStagedTracks": queued_state["staged"]["tracks"],
        "cliCountsAfterQueue": queued_document["result"]["counts"],
    }
    print(json.dumps({"queue": queued_report}, indent=2))

failures = []
if queued_report is not None:
    if queued_report["windowVisibleCountsAfterQueue"]["queued"] != 1:
        failures.append("the window did not count the staged track")
    if queued_report["cliCountsAfterQueue"]["queued"] != 0:
        failures.append("a headless run reported a queue it cannot have")
    for key in ("ipod", "library", "preview"):
        if (
            queued_report["windowVisibleCountsAfterQueue"][key]
            != queued_report["cliCountsAfterQueue"][key]
        ):
            failures.append(f"the two disagree about {key} once something is staged")
if state["visibleCounts"] != document["result"]["counts"]:
    failures.append(
        f"the window counts {state['visibleCounts']} and the CLI counts "
        f"{document['result']['counts']}"
    )
if window_rows != cli_rows:
    failures.append(
        f"the window shows {window_rows} and the CLI writes {cli_rows}"
    )
if document["result"]["counts"]["queued"] != 0:
    failures.append("a headless run has no window queue, so queued must be 0")
if failures:
    raise SystemExit("\n".join(failures))
print("window and CLI agree")
