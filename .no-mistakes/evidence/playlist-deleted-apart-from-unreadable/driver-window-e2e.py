#!/usr/bin/env python3
"""Drives the real IpodWindow through the two ways a playlist stops reading.

No stand-in: an Adw.Application builds the actual window under a real GDK
display, the playlist folder is a real folder, and the deletion is a real
unlink - which is what another program deleting a playlist while its rows are
on screen looks like from here. What is read back afterwards is what a user
would see: the rows on the playlist rail and the sentence in the toast.

Two scenes, because the whole point of the change is telling them apart:

  1. Road Trip is deleted behind the window's back, then Add to playlist is
     pressed on it. Nothing failed and there is nothing to write.
  2. On A Drive is left in the folder pointing at a drive that is not plugged
     in, then Add to playlist is pressed on it. The read genuinely failed and
     the playlist is still there.

Takes the repository to import from, so the same script can be pointed at the
commit before the fix and at the commit after it.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
LABEL = sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)

SANDBOX = tempfile.mkdtemp(prefix="ipod-deleted-playlist-")
os.environ["HOME"] = SANDBOX
os.environ["XDG_CACHE_HOME"] = str(Path(SANDBOX, "cache"))
os.environ["XDG_CONFIG_HOME"] = str(Path(SANDBOX, "config"))
MUSIC = Path(SANDBOX, "Music")
MUSIC.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit("no display: run under broadway or xvfb")

# Detection would otherwise go looking for real removable drives.
gui.find_ipods = lambda: []

PLAYLISTS = Path(gui.PLAYLIST_LIBRARY)
PLAYLISTS.mkdir(parents=True, exist_ok=True)


def song(name, artist="The Coast Road"):
    path = MUSIC / artist / f"{name}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(name.encode("utf-8"))
    return path


def track_for(path):
    return gui.Track(
        path,
        {"title": Path(path).stem, "artist": Path(path).parent.name, "size": 8},
        gui.STATE_LIBRARY,
    )


HIGHWAY = song("Highway")
COASTLINE = song("Coastline")
SUNRISE = song("Sunrise Drive")
BARBELL = song("Barbell", artist="Reps")

gui.write_playlist_entries(
    PLAYLISTS / "Road Trip.m3u", [str(HIGHWAY), str(COASTLINE)]
)
gui.write_playlist_entries(PLAYLISTS / "Gym.m3u", [str(BARBELL)])
gui.write_playlist_entries(PLAYLISTS / "On A Drive.m3u", [str(HIGHWAY)])

report = []
transcript = []


def walk(widget):
    if widget is None:
        return
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def rail_names(window):
    """The playlist names the playlists page is offering right now."""
    names = []
    for row in walk(window.playlist_list):
        if isinstance(row, Gtk.Button):
            labels = [
                found.get_text()
                for found in walk(row)
                if isinstance(found, Gtk.Label)
            ]
            if labels:
                names.append(labels[0])
    return names


def toast_text(window):
    """The sentence the toast on screen is showing, read off the widget.

    Found by the toast's own widget rather than by the first label under the
    overlay: the overlay wraps the whole window, so every label on the page is
    somewhere below it.
    """
    for found in walk(window.toasts):
        if found is window.toasts or "Toast" not in type(found).__name__:
            continue
        for inner in walk(found):
            if isinstance(inner, Gtk.Label) and inner.get_text().strip():
                return inner.get_text()
    return None


def wait_for_quiet(window, seconds=12):
    """Let the toast on screen time out, so the next scene reads its own.

    The overlay shows one at a time and the app never dismisses one early, so
    a scene that toasted a moment ago would otherwise have the previous
    sentence read back to it.
    """
    for _ in range(int(seconds * 4)):
        if toast_text(window) is None:
            return
        pump(17)
    report.append("a toast never went away")


def pump(turns=40):
    context = GLib.MainContext.default()
    for _ in range(turns):
        while context.pending():
            context.iteration(False)
        GLib.usleep(15000)


def shot(widget, name):
    pump(30)
    width = widget.get_width()
    height = widget.get_height()
    if not width or not height:
        report.append(f"{name} was asked for before the window had a size")
        return
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        report.append(f"nothing rendered for {name}")
        return
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(None)
    renderer.render_texture(node, None).save_to_png(str(OUT / name))
    renderer.unrealize()
    transcript.append(f"screenshot: {(OUT / name).name}")


def scene(window):
    window.show_view("playlists")
    window._populate_playlist_rail()
    window.current_playlist = "Road Trip"
    window._show_playlist("Road Trip")
    pump()

    before = rail_names(window)
    transcript.append(f"rail as the user sees it: {before}")
    if "Road Trip" not in before:
        report.append(f"the rail never showed Road Trip: {before}")
    shot(window, f"{LABEL}-1-before.png")

    # Another program deletes the playlist while its rows are on screen. The
    # window is told nothing: it is still painted from its own last listing.
    (PLAYLISTS / "Road Trip.m3u").unlink()
    transcript.append("--- another program deletes Road Trip.m3u ---")
    transcript.append(f"rail still offering: {rail_names(window)}")

    # Add to playlist, taken exactly as a track row's menu takes it.
    window._add_tracks_to_playlist("Road Trip", [track_for(SUNRISE)])
    pump()
    transcript.append(f"Add to playlist -> Road Trip says: {toast_text(window)!r}")
    transcript.append(f"rail afterwards: {rail_names(window)}")
    if (PLAYLISTS / "Road Trip.m3u").exists():
        report.append("the edit wrote back a playlist that had been deleted")
    shot(window, f"{LABEL}-2-deleted-playlist.png")

    # The other half: a playlist that is still sitting in the folder, pointing
    # at a drive that is not plugged in. Its read fails with the same error a
    # deleted one fails with, and it has not gone anywhere.
    wait_for_quiet(window)
    (PLAYLISTS / "On A Drive.m3u").unlink()
    (PLAYLISTS / "On A Drive.m3u").symlink_to("/nowhere/mounted/On A Drive.m3u")
    transcript.append("--- On A Drive is left pointing at an unplugged drive ---")
    window._add_tracks_to_playlist("On A Drive", [track_for(SUNRISE)])
    pump()
    transcript.append(f"Add to playlist -> On A Drive says: {toast_text(window)!r}")
    transcript.append(f"rail afterwards: {rail_names(window)}")
    if not (PLAYLISTS / "On A Drive.m3u").is_symlink():
        report.append("the edit replaced the playlist on the unplugged drive")
    shot(window, f"{LABEL}-3-unreadable-playlist.png")

    # Dragging a track from one playlist onto another writes twice, and the
    # destination is the one that has gone. The track must stay where it was:
    # the source is only ever rewritten once the target is holding it, so an
    # edit that misreads the target's refusal empties a playlist for nothing.
    wait_for_quiet(window)
    gui.write_playlist_entries(PLAYLISTS / "Road Trip.m3u", [str(HIGHWAY)])
    window._populate_playlist_rail()
    (PLAYLISTS / "Road Trip.m3u").unlink()
    transcript.append("--- Road Trip is listed again, then deleted again ---")
    transcript.append(
        f"Gym holds before the drag: {gui.read_playlist_entries(PLAYLISTS / 'Gym.m3u')}"
    )
    window._move_track_between("Gym", "Road Trip", track_for(BARBELL))
    pump()
    transcript.append(f"drag Gym -> Road Trip says: {toast_text(window)!r}")
    kept = gui.read_playlist_entries(PLAYLISTS / "Gym.m3u")
    transcript.append(f"Gym holds after the drag: {kept}")
    if kept != [str(BARBELL)]:
        report.append(f"the drag emptied the playlist it came from: {kept}")
    shot(window, f"{LABEL}-4-drag-onto-deleted-playlist.png")

    transcript.append(
        f"playlist folder now holds: {sorted(p.name for p in PLAYLISTS.iterdir())}"
    )
    (OUT / f"{LABEL}-transcript.txt").write_text(
        "\n".join(transcript) + "\n", encoding="utf-8"
    )
    print("\n".join(transcript))


def on_activate(app):
    window = gui.IpodWindow(application=app)
    window.present()

    def go():
        try:
            scene(window)
        except Exception:  # noqa: BLE001
            import traceback

            report.append(traceback.format_exc())
        app.quit()
        return False

    GLib.timeout_add(600, go)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.DeletedPlaylist",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(90, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
