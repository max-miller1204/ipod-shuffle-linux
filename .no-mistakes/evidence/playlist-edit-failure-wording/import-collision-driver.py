#!/usr/bin/env python3
"""Drives the real window through the import-collision case, for a screenshot.

The Import button opens the desktop's own file chooser, and that chooser needs
a portal the nested X server the screenshots are taken in has none of. So the
shot is taken by calling what the chooser calls with the path it would have
handed back - `_import_playlist(path)` - and everything from there on is the
app's own: the name it lands on, the toast it raises, the shelf it repaints.

    import-collision-driver.py <demo root> <foreign playlist>

The window is left up on a schedule so a grabber outside can take the shot:
Import is pressed at t=5s and again at t=11s, and the toast each press raises
stands for five seconds after it.
"""

import os
import sys
from pathlib import Path

DEMO = Path(sys.argv[1]).resolve()
FOREIGN = Path(sys.argv[2]).resolve()
HOME = DEMO / "home"

# Before the package is imported: it reads Path.home() to find the music and
# playlist folders, so the demo has to be this process's home from the start.
os.environ["HOME"] = str(HOME)
os.environ["XDG_CONFIG_HOME"] = str(HOME / ".config")
os.environ["XDG_CACHE_HOME"] = str(HOME / ".cache")
os.environ["FAKE_IPOD_MOUNT"] = str(DEMO / "MAX SHUFFLE")

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from ipod_gui.config import PLAYLIST_LIBRARY  # noqa: E402
from ipod_gui.theme import load_css  # noqa: E402
from ipod_gui.window import IpodWindow  # noqa: E402

SQUATTER = PLAYLIST_LIBRARY / "Road Trip.m3u"
SAID = []


def stage(window):
    """Another program writes a playlist while the window is not looking.

    Nothing refreshes here: the shelf on screen was painted before the file
    landed, which is the whole of the case - the name the import picks itself
    is free as far as the window knows and taken as far as the folder is.
    """
    window.show_view("playlists")
    SQUATTER.write_text(
        "#EXTM3U\n"
        f"{HOME}/Music/Elle Marchetti/Warm Ridge/01 - Low Sun.mp3\n",
        encoding="utf-8",
    )
    print(f"another program wrote {SQUATTER.name}", flush=True)
    print(f"the window still shows: {[p.name for p in window._shown_playlists()]}",
          flush=True)
    return False


def press_import(window, which):
    print(f"Import pressed ({which})", flush=True)
    window._import_playlist(str(FOREIGN))
    return False


def on_activate(app):
    # The stylesheet the app loads on activation, so the shot is of the app
    # rather than of the same widgets in GTK's defaults.
    load_css()
    window = IpodWindow(application=app)
    said = window._toast

    def remember(message):
        SAID.append(message)
        print(f"  toast: {message}", flush=True)
        return said(message)

    window._toast = remember
    window.present()

    GLib.timeout_add(4000, stage, window)
    GLib.timeout_add(5000, press_import, window, "first")
    GLib.timeout_add(11000, press_import, window, "second")
    GLib.timeout_add(18000, lambda: app.quit())


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.ImportShot",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
app.run([])

print("\nplaylist folder afterwards:")
for entry in sorted(PLAYLIST_LIBRARY.iterdir()):
    print(f"  {entry.name}: {entry.read_text(encoding='utf-8').splitlines()[1:]}")
print(f"\ntoasts: {SAID}")
