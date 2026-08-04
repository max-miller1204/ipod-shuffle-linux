"""GTK4 front end for the iPod shuffle 4G scripts.

Drives ipod-sync.sh, ipod-remove.sh, ipod-wipe.sh and ipod-fetch.sh rather
than reimplementing their device-changing logic, so their copy and database
rules stay shared with the command line. Launch it via ./ipod-gui.sh, which
picks an interpreter that has the GTK bindings.

The interface is library-first: your music is the app, and the device
operations live in one Device & Settings view rather than leading the window.
A track is in one of three states everywhere it appears - on the iPod, in your
local library, or previewed only - and that state is what the coloured dot
next to it means.

This was one module until it outgrew being readable as one. The split follows
what each part talks to: the shell scripts, the device over USB, YouTube, the
tag reader in the other interpreter, the design's tokens, the widgets shared
between views, and the window that assembles them.
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
    config,
    device,
    model,
    player,
    previews,
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
    "theme",
    "widgets",
    "player",
    "window",
    "app",
]
