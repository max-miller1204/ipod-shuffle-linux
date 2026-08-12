#!/usr/bin/env python3
"""The app deleting a track off the iPod, photographed while it does it.

The window is the case this change must not break. It has no terminal behind
it - it is opened from the app grid - so every deletion it starts is exactly
the non-interactive destructive run the scripts now refuse; it gets through by
doing what a machine caller does, planning the run and returning that plan's
own token, inside the lock it already holds over the device.

Nothing here is staged. The iPod is filled by a real ipod-sync.sh, the window
is the real IpodWindow, the Remove button is pressed the way a finger presses
it, and the removal that follows is ipod-remove.sh reading a token the window
fetched for it. What is photographed is the surface GTK painted.

Needs a GDK display, and something looking at it: this machine has no X
server, so the shell driver beside this file runs it under gtk4-broadwayd and
photographs it through a headless browser.
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

_SANDBOX = tempfile.mkdtemp(prefix="rails-shots-")
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

# A small library, and an iPod that already holds it: the tracks the window
# offers a Remove on are the ones it found on the device.
album = Path(_SANDBOX, "Music", "Kite Season")
album.mkdir(parents=True)
TRACKS = [
    "01 - Harbour Light.mp3",
    "02 - Slow Ferry.mp3",
    "03 - Pier Lights.mp3",
]
for name in TRACKS:
    (album / name).write_bytes(os.urandom(64 * 1024))

ipod = Path(_SANDBOX, "iPod")
for directory in (
    ipod / "iPod_Control" / "iTunes",
    ipod / "iPod_Control" / "Music",
    ipod / "iPod_Control" / "Speakable" / "System",
    ipod / "iPod_Control" / "Device",
):
    directory.mkdir(parents=True)
(ipod / "iPod_Control" / "Speakable" / "System" / "battery.wav").write_text(
    "spoken battery prompt\n"
)
(ipod / "iPod_Control" / "Device" / "SysInfo").write_text("the iPod on the desk\n")
subprocess.run(
    [str(REPO / "ipod-sync.sh"), "--ipod", str(ipod), str(album)],
    check=True,
    capture_output=True,
    text=True,
)

DOOMED = "Kite Season/02 - Slow Ferry.mp3"

gui.find_ipods = lambda: [str(ipod)]
gui.resolve_device = lambda mount, identity, require_block=False: gui.DeviceHandle(
    mount, identity, "/dev/sdz"
)

# No terminal, the way there is none behind a window opened from the app grid.
# This is what makes the removal below a run the scripts refuse without a
# token, and so what makes these pictures worth taking.
_devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(_devnull, 0)
os.close(_devnull)

camera = threading.Lock()
shots = []
window_holder = []
camera_holder = []


def photograph(name):
    """One picture of the window as its display is showing it."""
    path = HERE / name
    with camera:
        time.sleep(1.5)
        camera_holder[0].photograph(path)
        shots.append(path)
        print(name, flush=True)


def settled():
    """Wait for the main loop to have applied everything queued before now."""
    done = threading.Event()
    GLib.idle_add(done.set)
    done.wait(10)


def on_main(function, *args):
    """Run something on the main loop and wait for it to have run."""
    result = {}
    done = threading.Event()

    def call():
        try:
            result["value"] = function(*args)
        finally:
            done.set()
        return False

    GLib.idle_add(call)
    done.wait(30)
    return result.get("value")


def wait_for(predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


app = Adw.Application(application_id="com.example.rails-shots")


def on_activate(application):
    window = gui.IpodWindow(application=application)
    window_holder.append(window)
    window.sync_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
    window.details_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
    # The pane a person opens with the Details button, so the script's own
    # words are in the picture rather than only its result.
    window.details_toggle.set_active(True)
    window.set_default_size(1180, 900)
    window.present()
    camera_holder.append(
        Browser(URL, int(os.environ.get("SHUTTER_PORT", "9422")),
                Path(_SANDBOX, "shutter-profile"), size=(1500, 1180))
    )

    finished = threading.Event()
    finish = window._finish

    def finished_now(code, *args, **kwargs):
        result = finish(code, *args, **kwargs)
        finished.set()
        return result

    window._finish = finished_now

    def script():
        # The window finds the iPod on its own; nothing below runs until it
        # has read the device and put its tracks on screen.
        if not wait_for(lambda: len(window.device_tracks) == len(TRACKS)):
            print("the window never listed the device's tracks", flush=True)
            GLib.idle_add(application.quit)
            return
        # The flat track list rather than the album grid, because what these
        # pictures are about is one track and the button beside it.
        on_main(lambda: window.mode_buttons["list"].set_active(True))
        settled()
        time.sleep(1)
        photograph("20-tracks-on-the-ipod.png")

        # The Remove button, pressed the way a finger presses it.
        on_main(window.on_remove_track, None, DOOMED)
        settled()
        photograph("21-remove-asks-first.png")

        dialog = on_main(window.get_visible_dialog)
        if not isinstance(dialog, Adw.AlertDialog):
            print(f"Remove opened {dialog!r} rather than a dialog", flush=True)
            GLib.idle_add(application.quit)
            return
        # Answering it is the only consent in this run: everything after it -
        # the plan, the token, the identity the script is told to expect - is
        # the window's own doing, between here and ipod-remove.sh.
        on_main(dialog.emit, "response", "remove")
        # And the dialog goes away with the press, as it does under a finger:
        # emitting the response runs the handler, closing it is the other half
        # of what the button does.
        on_main(dialog.close)

        finished.wait(240)
        settled()
        time.sleep(2)
        if not wait_for(lambda: len(window.device_tracks) == len(TRACKS) - 1):
            print("the removal never reached the window's own reading", flush=True)
        settled()
        photograph("22-removed-through-the-handshake.png")

        buffer = window.log_view.get_buffer()
        transcript = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        )
        (HERE / "23-gui-details-pane.txt").write_text(
            "What the window's Details pane held after the removal, which is\n"
            "ipod-remove.sh's own output read back off the app's log view:\n\n"
            + transcript
            + "\nStill in the iPod's music folder afterwards:\n"
            + "".join(
                f"  {path.relative_to(ipod / 'iPod_Control' / 'Music')}\n"
                for path in sorted(
                    (ipod / "iPod_Control" / "Music").rglob("*")
                )
                if path.is_file()
            )
        )
        print("23-gui-details-pane.txt", flush=True)

        with camera:
            camera_holder[0].close()
        GLib.idle_add(application.quit)

    threading.Thread(target=script, daemon=True).start()
    GLib.timeout_add_seconds(400, application.quit)


app.connect("activate", on_activate)
app.run([])
print(f"{len(shots)} picture(s)")
