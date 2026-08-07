#!/usr/bin/env python3
"""Holds the window to the size it advertises it can be.

A window asks GTK for a minimum size and the compositor honours it: tile the
app to half a small screen and that is exactly the width it gets. If the
contents cannot actually fit in it, GTK has no way to refuse - it allocates
widgets a rectangle smaller than they asked for and then paints them at the
size they wanted, so what is on screen is no longer where the window thinks it
is. Clicks land beside the control under the pointer, hover redraws flicker,
and a tooltip can open over the menu it belongs to. That is not a look; it is
the window being unusable at a width it offered.

So this measures rather than looks: every page, and every bar that spans the
window, has to fit the minimum the window advertises.

The sidebar is deliberately not counted. It folds away into an overlay at and
below gui.SIDEBAR_COLLAPSE_WIDTH, which is well above the advertised minimum,
so at any width where the minimum matters the sidebar is not taking space from
the content. The widths just above that breakpoint are checked separately, at
the end of measure().

Needs a display, like tests/gui-window-build.py, and fails rather than skips
without one for the same reason.
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="ipod-gui-minimum-")
os.environ["HOME"] = _SANDBOX
os.environ["XDG_CACHE_HOME"] = str(Path(_SANDBOX, "cache"))
os.environ["XDG_CONFIG_HOME"] = str(Path(_SANDBOX, "config"))
Path(_SANDBOX, "Music").mkdir(parents=True, exist_ok=True)

from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit(
        "no display: run this under `xvfb-run -a`, or on a desktop session"
    )

gui.find_ipods = lambda: []

failures = []
measured = []

# How much wider the same window runs on a machine that is not this one. The
# fixture below builds every page's content, so what is left over is font
# metrics: the pages that set the pane's width are lists of text, and the
# widest of them moves about twenty pixels either side of the default across
# the font sizes a desktop offers. This is that spread with room over it, so
# clearing the sidebar threshold in CI means clearing it on the user's screen.
#
# It does not stand in for content any more. It used to, and it was set from a
# pane the playlist fixture never reached - so keep it below what the fixture
# leaves unmeasured, or a page that stops being built goes back to passing.
FIXTURE_ALLOWANCE = 50


def walk(widget):
    if widget is None:
        return
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def minimum_width(widget):
    return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]


def widest(widget):
    """The deepest widget whose own minimum is what drives this subtree's.

    Reported with a failure so it names the widget to fix rather than the page
    that happens to contain it.
    """
    worst, culprit = minimum_width(widget), widget
    for found in walk(widget):
        if not found.get_visible():
            continue
        width = minimum_width(found)
        children = [
            child
            for child in walk(found)
            if child is not found and child.get_visible()
        ]
        below = max((minimum_width(child) for child in children), default=0)
        if width >= worst and width > below:
            worst, culprit = width, found
    name = type(culprit).__name__
    classes = " ".join(culprit.get_css_classes())
    if isinstance(culprit, Gtk.Label):
        name += f" {culprit.get_text()[:40]!r}"
    return f"{name}{f' [{classes}]' if classes else ''}"


def populate(window):
    """Fill the window with the kind of content that sets its width.

    Measured empty, every page is narrower than it will ever be in use: the
    tables have no rows, the grid no covers and the playlist rail no names.
    The widths this file exists to hold are the ones content produces, so the
    content has to be here - long titles and all, since a name that fits is
    not the one that decides a minimum.
    """
    tracks = [
        gui.Track(
            f"/music/artist-{index}/album/{index:02d} track.mp3",
            {
                "title": "A Reasonably Long Track Title",
                "artist": "An Artist With A Long Name",
                "album": "An Album With A Long Name",
                "duration": 245,
                "track": str(index),
                "size": 8 * 1024 * 1024,
            },
            gui.STATE_LIBRARY,
        )
        for index in range(1, 13)
    ]
    window.library.tracks = tracks

    # Written to the sandbox rather than assigned onto the window: the files
    # are the playlists, and every paint here starts by re-reading the folder,
    # so a list handed straight to the attribute is gone before it is measured.
    name = "A Playlist With A Long Name"
    for title in (name, "Another Long Playlist Name"):
        gui.write_playlist_entries(
            gui.local_playlist_file(gui.PLAYLIST_LIBRARY, title),
            [track.path for track in tracks],
        )

    # A device, because the settings page is the widest of them and most of
    # what it shows is only there once one is attached: its name, its mount
    # path and the figures beside them. Measured with no iPod plugged in, that
    # page reports a width nobody ever sees.
    window.mount_point = "/media/max/MAX SHUFFLE"
    window.device_track_count = len(tracks)
    window._populate_device_summary()

    window._populate_albums()
    window._populate_playlist_rail()
    # The shelf along the top of the library page, which carries a tile per
    # playlist: without it that page measures as if the library held none.
    window._populate_playlist_shelf()
    window._show_playlist(name)
    collections = window.library.collections()
    if collections:
        window._show_album(collections[0])


def check(window):
    limit, limit_height = window.get_size_request()
    if limit <= 0:
        failures.append("the window does not ask for a minimum width at all")
        return

    # Every page of the view stack. The stack hands each page the width of the
    # widest one, so a single page over the limit holds the whole window there.
    for name in ("library", "search", "album", "playlists", "settings"):
        page = window.views.get_child_by_name(name)
        if page is None:
            failures.append(f"the view stack has no {name!r} page")
            continue
        width = minimum_width(page)
        measured.append((f"page {name}", width))
        if width > limit:
            failures.append(
                f"the {name} page needs {width}px but the window offers to be "
                f"{limit}px wide; widest thing in it: {widest(page)}"
            )

    # The header, and the bars along the bottom, which span the window rather
    # than sitting inside a page.
    spanning = [("content pane", window.split.get_content())]
    spanning += [
        ("bottom bar", found)
        for found in walk(window)
        if "sf-bottom-bar" in found.get_css_classes()
    ]
    for name, widget in spanning:
        width = minimum_width(widget)
        measured.append((name, width))
        if width > limit:
            failures.append(
                f"the {name} needs {width}px but the window offers to be "
                f"{limit}px wide; widest thing in it: {widest(widget)}"
            )

    content = window.get_content()
    height = content.measure(Gtk.Orientation.VERTICAL, -1)[0]
    measured.append(("window height", height))
    if limit_height > 0 and height > limit_height:
        failures.append(
            f"the window needs {height}px of height but offers to be "
            f"{limit_height}px tall"
        )

    # What the window actually does at that width, rather than what its parts
    # measure. The sidebar is only affordable because a breakpoint folds it
    # away, and libadwaita applies exactly one breakpoint - the last one that
    # matches - so a narrower breakpoint added later silently drops the setters
    # of a wider one. Nothing in the widths above notices that; the window is
    # simply allocated less than it needs and paints over the edge.
    width = window.get_width()
    measured.append(("allocated width", width))
    if width > limit:
        failures.append(
            f"the window came up {width}px wide, so it was never measured at "
            f"the {limit}px minimum this check exists to hold it to"
        )
    elif window.split.get_collapsed():
        whole = content.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        measured.append(("whole window", whole))
        if whole > width:
            failures.append(
                f"at {width}px wide the window still needs {whole}px; "
                f"widest thing in it: {widest(content)}"
            )
    else:
        sidebar = window.split.get_sidebar()
        failures.append(
            f"at {width}px wide the sidebar has not folded away, so the window "
            f"needs {content.measure(Gtk.Orientation.HORIZONTAL, -1)[0]}px: the "
            f"sidebar's {minimum_width(sidebar)}px on top of the content. Check "
            "that every breakpoint narrow enough to match here sets "
            "'collapsed' on the split view."
        )

    # The other end of the same problem, and the one no single width finds: the
    # widths just above the breakpoint, where the sidebar comes back. The
    # window is legal at every size from its minimum upwards, so the moment the
    # sidebar is shown there has to be room for it beside the content.
    sidebar_width = minimum_width(window.split.get_sidebar())
    pane = minimum_width(window.split.get_content())
    together = sidebar_width + pane
    measured.append(("sidebar + pane", together))
    # With room to spare, so the threshold is not sitting on the exact number
    # this machine's fonts happen to produce.
    if together + FIXTURE_ALLOWANCE > gui.SIDEBAR_COLLAPSE_WIDTH:
        failures.append(
            f"the sidebar is shown from {gui.SIDEBAR_COLLAPSE_WIDTH + 1}px up, "
            f"but it needs {together}px to sit beside the content "
            f"({sidebar_width}px of sidebar and {pane}px of pane). Every width "
            f"between the two is a window showing a sidebar it has no room "
            f"for. Raise SIDEBAR_COLLAPSE_WIDTH, or make the pane narrower; "
            f"widest thing in the pane: {widest(window.split.get_content())}"
        )


def on_activate(app):
    try:
        window = gui.IpodWindow(application=app)
    except Exception:  # noqa: BLE001 - the construction is the subject
        failures.append(f"IpodWindow(...) raised:\n{traceback.format_exc()}")
        app.quit()
        return

    # Brought up at the size it advertises rather than its comfortable default,
    # because half of what is checked here is what the window does at that
    # width, and a window that never gets there cannot be asked.
    limit, limit_height = window.get_size_request()
    window.set_default_size(limit, limit_height)
    window.present()

    waited = [0]

    def look():
        # Widths are nothing until the window has been allocated one; before
        # that get_width() answers 0 and every comparison below is vacuous.
        if window.get_width() <= 0 and waited[0] < 40:
            waited[0] += 1
            return True
        try:
            populate(window)
            check(window)
        except Exception:  # noqa: BLE001
            failures.append(f"measuring the window raised:\n{traceback.format_exc()}")
        app.quit()
        return False

    GLib.timeout_add(50, look)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.MinimumCheck",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(
    60, lambda: failures.append("the window did not finish building") or app.quit()
)
app.run([])

for name, width in measured:
    print(f"  {name:16} {width}px")

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("every page and bar fits the minimum the window advertises")
