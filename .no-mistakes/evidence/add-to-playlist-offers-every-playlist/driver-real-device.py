#!/usr/bin/env python3
"""Reads the real window against the real iPod, and changes nothing.

Nothing is pressed: what is printed is what the Playlists page would offer for
each playlist that is only on the device, and what the Add to playlist menu on
a real library track says. The point is whether this computer's own files can
be matched to the copies on the attached device at all, which no fixture can
answer.
"""

import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402


def walk(widget):
    if widget is None:
        return
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def labels(widget):
    return [
        found.get_text()
        for found in walk(widget)
        if isinstance(found, Gtk.Label) and found.get_text().strip()
    ]


def controls(widget):
    """Every control in a row, including the ones carrying no label.

    A reading taken from labels alone cannot see the ⋯: a Gtk.MenuButton is an
    icon and no Gtk.Label, so a row that had moved a button into that menu
    still printed as though the button were sitting in the row. The menu is
    recorded here as the control it is, and not walked into - what is inside
    it is built when it is opened, and this driver opens nothing.
    """
    found = []

    def visit(node):
        if node is None:
            return
        if isinstance(node, Gtk.MenuButton):
            found.append(f"⋯ [{node.get_icon_name() or 'menu'}]")
            return
        if isinstance(node, Gtk.Label) and node.get_text().strip():
            found.append(node.get_text())
        elif (
            isinstance(node, Gtk.Button)
            and not node.get_label()
            and node.get_icon_name()
        ):
            found.append(f"[{node.get_icon_name()}]")
        child = node.get_first_child()
        while child is not None:
            visit(child)
            child = child.get_next_sibling()

    visit(widget)
    return found


def pump(turns=40):
    context = GLib.MainContext.default()
    for _ in range(turns):
        while context.pending():
            context.iteration(False)
        GLib.usleep(15000)


def settle(window, seconds=90):
    """Wait for both readings the page quotes, not merely for their first batch.

    library.tracks is republished in batches of 25 and device_tracks is empty
    from the probe until the tag read over USB finishes, so "not empty" is
    somewhere in the middle of a scan rather than the end of one. The window
    now refuses to state a figure from that, and a driver that reads it there
    would be recording the refusal rather than the answer.
    """
    for _ in range(int(seconds * 8)):
        pump(2)
        if (
            window.mount_point
            and window.device_tracks
            and window.library.tracks
            and window.playlists
            and not window._library_scan_running
            and window._device_snapshot_ready
        ):
            pump(20)
            return True
    return False


def on_activate(app):
    window = gui.IpodWindow(application=app)
    window.present()

    def go():
        try:
            ready = settle(window)
            print(f"device: {window.mount_point} · settled: {ready}")
            print(f"library holds {len(window.library.tracks)} local tracks, "
                  f"{len(window.device_tracks)} on the device")
            print(f"playlists on the device: {[n for n, _ in window.playlists]}")
            print(f"playlists made here: {[p.name for p in window.local_playlists]}")
            for playlist in window._playlists_only_on_device():
                resolved = window._entries_here_for(playlist)
                if resolved is None:
                    print(
                        f"  {playlist.name}: {len(playlist.entries)} entries · "
                        "still being read, so no figure to give"
                    )
                else:
                    here, missing = resolved
                    print(
                        f"  {playlist.name}: {len(playlist.entries)} entries · "
                        f"{len(here)} here · {missing} only on the iPod"
                    )
                window._select_playlist(playlist.name)
                pump(5)
                print(f"    note: {labels(window.playlist_voice_note)}")
                print(f"    row : {controls(window.playlist_actions)}")
            track = window.library.tracks[0] if window.library.tracks else None
            if track is not None:
                print(f"Add to playlist on {track.title!r}:")
                for line in labels(window.track_menu(track).get_child()):
                    print(f"    {line}")
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()
        app.quit()
        return False

    GLib.timeout_add(800, go)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.Inspect",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(240, lambda: app.quit())
app.run([])
