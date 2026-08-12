"""GTK4 front end for the iPod shuffle 4G scripts.

Drives ipod-sync.sh, ipod-remove.sh, ipod-wipe.sh and ipod-fetch.sh rather
than reimplementing their device-changing logic, so their copy and database
rules stay shared with the command line. Launch it via ./ipod-gui.sh, which
picks an interpreter that has the GTK bindings.

The interface is library-first: your music is the app, and the device
operations live in one Device & Settings view rather than leading the window.
A track is in one of four states everywhere it appears - on the iPod, staged
for the next sync, in your local library, or previewed only - and that state
is what the coloured dot next to it means.

This was one module until it outgrew being readable as one. The split follows
what each part talks to: the shell scripts, the device over USB, YouTube, the
tag reader in the other interpreter, the design's tokens, the widgets shared
between views, and the window that assembles them.

The window itself then outgrew being one module too. It stays one
Adw.ApplicationWindow, because its views share a stack, a toast overlay and a
set of bottom bars, so it is split into mixins instead: one per view, one for
what is staged for the next sync, and one for the scripts that carry it out.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

# Imported eagerly, and in this order, because the versions above have to be
# pinned before anything reaches gi.repository, and a caller that imports one
# submodule directly gets that pinning through this file either way.
from . import (  # noqa: E402
    app,
    cli,
    commands,
    config,
    device,
    device_view,
    library_view,
    model,
    playback_view,
    player,
    playlist_view,
    playlists,
    previews,
    queue,
    search_view,
    shell,
    tags,
    text,
    theme,
    widgets,
    window,
    youtube,
)

# Innermost first. Every module imports only from ones earlier in this list,
# so a name is defined exactly once and the module that defines it is the
# first one here to hold it. tests/harness.py depends on that: it is how a
# check knows which binding to read, and which ones a stand-in has to replace.
__all__ = [
    "config",
    "text",
    "tags",
    "device",
    "shell",
    "youtube",
    "previews",
    "model",
    "playlists",
    "theme",
    "widgets",
    "player",
    # The window's mixins. None of them imports another, so their order among
    # themselves is only the order the window reads in.
    "library_view",
    "search_view",
    "playlist_view",
    "playback_view",
    "device_view",
    "queue",
    "commands",
    "window",
    "app",
    # Last, because nothing imports it: the display-free entry point reads the
    # same model the window does, so `main` here is still the window's.
    "cli",
]
