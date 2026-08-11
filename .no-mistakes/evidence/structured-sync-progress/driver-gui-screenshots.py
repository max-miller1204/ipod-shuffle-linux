#!/usr/bin/env python3
"""The window, running a real sync, photographed while the bar follows it.

Builds the whole `IpodWindow` the way tests/gui-window-build.py does, points it
at a synthetic iPod and starts a sync through the same `_run` the Sync button
goes through. Nothing about the bar is staged: every number, name and stage in
these pictures arrived as JSON, written by ipod-sync.sh on the descriptor the
window opened for it and read back on the window's own reader thread.

Two things are added for the camera, and neither changes what is reported. The
window's two reading threads are slowed to a fifth of a second per line, since
a synthetic iPod on a local disk finishes a copy in under half a second and
there is nothing to photograph in that; and the progress reader is held at four
points of the run while the shutter fires.

Needs a GDK display, and something looking at it: this machine has no X server,
so the shell driver beside this file runs it under gtk4-broadwayd and takes the
photographs through a headless browser, which is that display's client.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
URL = os.environ.get("BROADWAY_URL", "http://127.0.0.1:8087/")

_SANDBOX = tempfile.mkdtemp(prefix="progress-shots-")
os.environ["HOME"] = _SANDBOX
os.environ["XDG_CACHE_HOME"] = str(Path(_SANDBOX, "cache"))
os.environ["XDG_CONFIG_HOME"] = str(Path(_SANDBOX, "config"))
os.environ["XDG_DATA_HOME"] = str(Path(_SANDBOX, ".local/share"))
os.environ["IPOD_DB_TOOL"] = str(REPO / "tests" / "fake-db-builder.py")
os.environ["IPOD_VENV_PYTHON"] = "/usr/bin/python3"
os.environ["FAKE_DB_RECORD"] = str(Path(_SANDBOX, "database-invocations.jsonl"))

sys.path.insert(0, str(REPO / "tests"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from harness import gui  # noqa: E402
from shutter import Browser  # noqa: E402

# The library this sync is carrying, and the iPod it is going to.
music = Path(_SANDBOX, "Music")
album = music / "Kite Season"
album.mkdir(parents=True)
ipod = Path(_SANDBOX, "iPod")
for directory in (
    ipod / "iPod_Control" / "iTunes",
    ipod / "iPod_Control" / "Music",
    ipod / "iPod_Control" / "Speakable",
    ipod / "iPod_Control" / "Device",
):
    directory.mkdir(parents=True)
(ipod / "iPod_Control" / "Device" / "SysInfo").write_text("device identity\n")

TRACKS = [
    "01 - Harbour Light.mp3",
    '02 - Say "hello" again.mp3',
    "03 - Slow Ferry.mp3",
    "04 - Weather Balloon.mp3",
    "05 - Pier Lights.mp3",
    "06 - Long Way Round.mp3",
]
for name in TRACKS:
    (album / name).write_bytes(os.urandom(96 * 1024))
# Neither planned nor reported on the stream: the firmware cannot play it, so
# it stays in the human output's count of unsupported files.
(album / "cover.flac").write_bytes(b"artwork")
playlist = music / "Kite Season.m3u"
playlist.write_text(
    "\n".join([str(album / TRACKS[0]), "/nowhere/not-on-this-computer.mp3"]) + "\n"
)

gui.find_ipods = lambda: []
gui.resolve_device = lambda mount, identity, require_block=False: gui.DeviceHandle(
    mount, identity, "/dev/sdz"
)


# One shutter, and what the reading threads wait on while it is open.
camera = threading.Lock()


class PacedGLib:
    """GLib, with the window's two reading threads slowed to a copy's speed.

    The terminal output and the progress stream are read on threads of their
    own and handed to the main loop through idle_add, which is where this
    holds each of them back for a moment. A real copy onto a shuffle runs at
    USB 2.0 speeds; a synthetic one on this disk is over before it can be
    photographed, and the two panes would race each other to the end.

    Both threads also stop for the camera, so the log pane beside the bar
    shows what the script had said by the moment in the run being photographed
    rather than running on to the end while the shutter is open.

    Only the reading threads wait. Holding the main loop would stop the window
    painting, which is the one thing these pictures need it to do.
    """

    def __getattr__(self, name):
        return getattr(GLib, name)

    def idle_add(self, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            with camera:
                pass
            time.sleep(0.2)
        return GLib.idle_add(*args, **kwargs)


gui.GLib = PacedGLib()

shots = []
window_holder = []
camera_holder = []


def photograph(name):
    """One picture of the window as its display is actually showing it.

    Taken by the browser that has been looking at this display since before the
    run started, so what is saved is the frame GTK has painted by now rather
    than the one the display was holding when a fresh browser connected.

    Never called on the main thread: the app has to keep answering the display
    while the picture is being taken.
    """
    path = HERE / name
    with camera:
        # The window paints on its own clock; a picture asked for in the same
        # breath as the change can arrive a frame early.
        time.sleep(1.5)
        camera_holder[0].photograph(path)
        shots.append(path)
        print(name, flush=True)


def settled():
    """Wait for the main loop to have applied everything queued before now."""
    done = threading.Event()
    GLib.idle_add(done.set)
    done.wait(10)


def wanted(event):
    """The picture this event leaves the bar in a state to be in, if any."""
    kind = event.get("event")
    if kind == "file" and event.get("done") == 1:
        return "10-bar-first-file.png"
    if kind == "file" and event.get("done") == 4:
        return "11-bar-mid-copy.png"
    if kind == "playlist":
        return "12-bar-playlist-written.png"
    if (
        kind == "stage"
        and event.get("name") == "rebuild"
        and event.get("state") == "start"
    ):
        return "13-bar-rebuilding.png"
    return ""


real_progress_event = gui.progress_event


def paced_progress_event(line):
    """The real parser, with the reader held where a picture is wanted.

    Called on the window's reader thread, once per line of the stream. An
    event a picture is wanted of is handed to the main loop here and then
    reported as unparseable, so the reader does not deliver it twice; every
    other event goes back to the reader untouched.
    """
    event = real_progress_event(line)
    if event is None:
        return None
    name = wanted(event)
    if not name:
        return event
    GLib.idle_add(window_holder[0]._note_progress, event)
    settled()
    print(f"    [bar says {window_holder[0].sync_count.get_text()!r} "
          f"{window_holder[0].sync_current.get_text()!r}]", flush=True)
    photograph(name)
    return None


gui.progress_event = paced_progress_event

app = Adw.Application(application_id="com.example.progress-shots")


def on_activate(application):
    window = gui.IpodWindow(application=application)
    window_holder.append(window)
    # The details pane a person opens with the Details button, opened before
    # the window is on screen because this display only lays out what it is
    # about to paint. The slide is taken off for the same reason.
    window.sync_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
    window.details_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
    window.details_toggle.set_active(True)
    # Tall enough for the details pane the bar opens under it: the window asks
    # for 860 px with that pane open, and one shorter than its own contents
    # clips them.
    window.set_default_size(1180, 900)
    window._set_busy(True, "Copying to iPod")
    window.mount_point = str(ipod)
    window.device_identity = "uuid:synthetic"
    window.present()
    camera_holder.append(
        Browser(URL, int(os.environ.get("SHUTTER_PORT", "9422")),
                Path(_SANDBOX, "shutter-profile"), size=(1400, 1020))
    )

    finished = threading.Event()
    finish = window._finish

    def finished_now(code, *args, **kwargs):
        result = finish(code, *args, **kwargs)
        finished.set()
        return result

    window._finish = finished_now
    window._run(
        [
            str(gui.SYNC_SCRIPT),
            "--ipod",
            str(ipod),
            "--playlist-voiceover",
            "--yes",
            "--",
            str(album),
            str(playlist),
        ],
        "Copying to iPod",
        "6 changes synced",
    )

    def watch():
        """Close the camera once the run is over, and stop.

        Nothing is photographed here: the bar's last state is the one above,
        and _finish takes the bar off the screen as its first act.
        """
        finished.wait(240)
        settled()
        # A picture of the last stage can still be being taken: the run is
        # over the moment the script is, and the shutter is slower than that.
        time.sleep(3)
        with camera:
            camera_holder[0].close()
        GLib.idle_add(application.quit)

    threading.Thread(target=watch, daemon=True).start()
    GLib.timeout_add_seconds(300, application.quit)


app.connect("activate", on_activate)
app.run([])
print(f"{len(shots)} picture(s)")
