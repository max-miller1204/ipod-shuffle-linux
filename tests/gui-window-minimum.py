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
the end of check().

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


def children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def walk(widget):
    if widget is None:
        return
    yield widget
    for child in children(widget):
        yield from walk(child)


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

    # The search page, which measures as an empty heading until a search has
    # run in it. Both halves, because they are two lists of the same kind of
    # long title and either one can be the wider.
    window.search_query = "track"
    window.search_results = [
        gui.SearchResult(
            "A Reasonably Long Video Title (Official Audio)",
            "A Channel With A Long Name",
            245,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "dQw4w9WgXcQ",
        )
    ]
    # Two strips nothing produces at rest, and a hidden child measures as
    # nothing: the header naming the playlist a pasted link resolved to, and
    # the clipboard offer under the window's own header. Left as they are, the
    # first time either appeared in a narrow window would be the first time
    # anything measured whether it fitted.
    window.search_playlist = gui.LinkedPlaylist(
        "A Playlist With A Long Name",
        40,
        "https://www.youtube.com/playlist?list=PLAnUnaBFhOM0Y3nVQFwq6b3lFm0",
        len(window.search_results),
    )
    window._paint_local_results()
    window._paint_youtube_section()
    window.clipboard_offer_label.set_text(
        "On your clipboard: youtube.com/playlist?list=PLAnUnaBFhOM0Y3nVQFwq6b3lFm0"
    )
    window.clipboard_offer.set_reveal_child(True)

    window._populate_albums()
    window._populate_playlist_rail()
    # The shelf along the top of the library page, which carries a tile per
    # playlist: without it that page measures as if the library held none.
    window._populate_playlist_shelf()
    window._show_playlist(name)
    collections = window.library.collections()
    if collections:
        window._show_album(collections[0])


def device_playlist_page(window):
    """The playlists page while showing a playlist only the iPod has.

    Left in place afterwards rather than put back: this is the wider of the
    two states, so the pane measured further down should be measured in it,
    and every page above has already been read.

    One of its two entries is claimed by a track in the library, the way a
    finished scan claims it, and the other names a song this computer does not
    hold - which is what puts the longest sentence this page has into the note
    under its heading, and leaves the copy on offer rather than refused.
    """
    # Both readings the page quotes have landed, which is the state a settled
    # window is in. Left as a fresh window has them, the copy is refused and
    # the note says so instead of stating the count - so the page measured
    # would be the narrow refusal rather than the widest real one, and the two
    # entries chosen below would decide nothing that gets drawn.
    window._library_scan_running = False
    window._device_snapshot_ready = True
    here = window.library.tracks[0]
    window.device_tracks = [
        gui.Track(
            "/media/max/MAX SHUFFLE/iPod_Control/Music/an-artist/track.mp3",
            {
                "title": here.title,
                "artist": here.artist,
                "album": here.album,
                "duration": here.duration,
            },
            gui.STATE_IPOD,
            relpath="an-artist/track.mp3",
        )
    ]
    window._merge_states()
    window.playlists = [
        (
            "A Playlist With A Long Name That Is Only On The iPod",
            ["an-artist/track.mp3", "another-artist/a track from elsewhere.mp3"],
        )
    ]
    window._populate_playlist_rail()
    window._show_playlist(window.playlists[0][0])
    # Read back rather than assumed. With either reading still outstanding the
    # page refuses the copy and the note drops the count for a shorter
    # sentence, so what is measured below would be the narrow state wearing
    # this one's name - and the minimum the window advertises rests on it.
    offered = [
        found
        for found in walk(window.playlist_actions)
        if isinstance(found, Gtk.Button)
        and found.get_label() == "Copy to this computer"
    ]
    if not offered or not offered[0].get_sensitive():
        failures.append(
            "the playlists page measured for a playlist only on the iPod is "
            "the one refusing the copy, not the one this fixture is for"
        )
    return minimum_width(window.views.get_child_by_name("playlists"))


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

    # The playlists page again, showing the other kind of playlist. The
    # fixture above shows one made here, holding tracks that are not on the
    # device; this is one that is only on the iPod, holding tracks that are.
    #
    # It is the wider of the two, and not by its buttons: every row of a
    # playlist whose tracks are on the device carries a Remove where a library
    # track carries an Add, and that column is what sets the page's width. It
    # went unmeasured for as long as this fixture only ever painted the other
    # state, and the window was advertising a minimum eight pixels under what
    # this page needs.
    device_page = device_playlist_page(window)
    measured.append(("page playlists on iPod", device_page))
    if device_page > limit:
        failures.append(
            f"the playlists page needs {device_page}px while showing a playlist "
            f"that is only on the iPod, but the window offers to be {limit}px "
            f"wide; widest thing in it: "
            f"{widest(window.views.get_child_by_name('playlists'))}"
        )

    # Named on its own as well as counted inside the search page. It is hidden
    # at rest like the clipboard offer, so a paint that stopped putting it up
    # would leave that page's number vouching for a strip it had never
    # included, and the check above would pass by measuring nothing.
    header = minimum_width(window.search_playlist_row)
    measured.append(("playlist header", header))
    if header <= 0:
        failures.append(
            "the playlist header measured nothing while shown, so nothing here "
            "has checked that it fits the window's minimum"
        )

    # The bars along the bottom, which span the window rather than sitting
    # inside a page. Taken from the toolbar view's own children rather than by
    # style class: a class names one bar, so the sync bar went unmeasured for
    # as long as this looked for the now-playing bar's, and the next bar added
    # would go the same way. Everything the toolbar view holds beside its
    # content spans the window by construction, whichever bars those are.
    #
    # The header is not here because it is not a toolbar-view bar: it sits at
    # the top of the split's content, so the content pane already carries it.
    spanning = [("content pane", window.split.get_content())]
    # Named on its own as well as counted inside the pane. It is the one thing
    # here that is hidden at rest, so a revealer that measured as nothing while
    # revealed would leave the pane's number vouching for a strip it had never
    # included, and this check would pass by measuring nothing at all.
    offer = minimum_width(window.clipboard_offer)
    if offer <= 0:
        failures.append(
            "the clipboard offer measured nothing while revealed, so nothing "
            "here has checked that it fits the window's minimum"
        )
    else:
        spanning.append(("clipboard offer", window.clipboard_offer))
    bars = next(
        (found for found in walk(window) if isinstance(found, Adw.ToolbarView)),
        None,
    )
    if bars is None:
        failures.append(
            "the window has no Adw.ToolbarView, so its bars were never measured"
        )
    else:
        spanning += [
            (" ".join(bar.get_css_classes()) or type(bar).__name__, bar)
            for bar in children(bars)
            if bar is not window.split
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
    #
    # Measured with the view title shown, which it is not at the moment. The
    # window is up at its own minimum here, so the narrowest breakpoint is the
    # one applied and it hides that title - and a hidden child measures as
    # nothing. But the band being asked about is the one where the sidebar is
    # back, hundreds of pixels above where the title goes away, so up there the
    # title is on screen and takes its own width plus the header's spacing out
    # of the pane. Left hidden it comes out about thirty pixels light, and a
    # heading that stopped ellipsising would put the real figure over the
    # threshold with this check still green.
    # The sidebar's queue row, hidden at rest and so measured as nothing by
    # everything above - including the two numbers below it. It carries the
    # longest sentences in the window, each of which has to say both what is
    # staged and what to do about it, inside a card in a sidebar pinned to a
    # width the split view never negotiates. Painted here through the two
    # methods that actually write it, in both the states each is reached in,
    # rather than trusting that a sentence someone adds later will fit.
    was_mount = window.mount_point
    bare = minimum_width(window.split.get_sidebar())
    staged = {
        "tracks to copy": {window.library.tracks[0].path},
        "nothing to copy": {"/music/A Playlist With A Long Name.m3u"},
    }
    for what, members in staged.items():
        window.pending = set(members)
        window.pending_sources = {"/music/A Playlist With A Long Name.m3u": members}
        window.pending_records = {}
        for state in ("attached", "unplugged"):
            window.mount_point = was_mount if state == "attached" else None
            window._populate_device_summary()
            if not window.queued_row.get_visible():
                failures.append(
                    f"the {state} queue row stayed hidden with {what} staged, "
                    "so nothing here measured the sentence it carries"
                )
                continue
            painted = minimum_width(window.split.get_sidebar())
            measured.append((f"sidebar {state[:3]} {what}", painted))
            if painted > bare:
                failures.append(
                    f"with {what} staged and the iPod {state}, the sidebar "
                    f"needs {painted}px against {bare}px with the queue row "
                    f"hidden: {window.queued_label.get_text()!r} does not fit "
                    f"the card it sits in; widest thing in it: "
                    f"{widest(window.split.get_sidebar())}"
                )
    window.mount_point = was_mount
    window.pending = set()
    window.pending_sources = {}
    window._populate_device_summary()

    was_titled = window.view_title.get_visible()
    window.view_title.set_visible(True)
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
    window.view_title.set_visible(was_titled)


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
