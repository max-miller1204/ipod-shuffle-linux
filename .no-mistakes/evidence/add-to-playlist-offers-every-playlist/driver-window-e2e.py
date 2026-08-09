#!/usr/bin/env python3
"""Drives the real IpodWindow through the report: only the newest playlist is
offered when a song is added to a playlist.

The fixture is the reported machine in miniature. Four playlists were built
somewhere else - a Spotify export wrote them into the music folder and
ipod-sync.sh put them on the device - and one, "Inspo", was made in the app.
So the app's own playlist folder holds one file, the volume root holds five
lists, and the tracks all five name are sitting in the music folder.

What is read back is what the user sees: the rows the rail paints, the rows
the Add to playlist menu offers, and the buttons each playlist's page carries.

Takes the repository to import from, so the same script runs against the
commit before the fix and the commit after it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
LABEL = sys.argv[3]
OUT.mkdir(parents=True, exist_ok=True)

SANDBOX = tempfile.mkdtemp(prefix="ipod-add-to-playlist-")
# The tags are the whole point of the fixture - a copy on the device is matched
# to the file it was made from by what it was tagged with - so the reader has to
# keep finding the virtualenv install.sh keeps mutagen in, which is under the
# home directory the next line moves away from. Read before the move, and left
# alone if the environment already names one.
os.environ.setdefault(
    "IPOD_VENV_PYTHON", str(Path.home() / "ipod-tools" / "venv" / "bin" / "python")
)
os.environ["HOME"] = SANDBOX
os.environ["XDG_CACHE_HOME"] = str(Path(SANDBOX, "cache"))
os.environ["XDG_CONFIG_HOME"] = str(Path(SANDBOX, "config"))
os.environ["XDG_DATA_HOME"] = str(Path(SANDBOX, ".local/share"))

MUSIC = Path(SANDBOX, "Music")
EXPORT = MUSIC / "spotify-final"
YOUTUBE = MUSIC / "youtube"
VOLUME = Path(SANDBOX, "MAX_SHUFFLE")

sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit("no display: run this on a desktop session or under xvfb")

report = []
transcript = []


# --------------------------------------------------------------- the fixture

TAGGER = os.environ["IPOD_VENV_PYTHON"]

_TAG_SCRIPT = """
import sys
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
path, title, artist, album = sys.argv[1:5]
try:
    tags = EasyID3(path)
except ID3NoHeaderError:
    tags = EasyID3()
tags["title"] = title
tags["artist"] = artist
tags["album"] = album
tags.save(path)
"""


def track_file(folder, artist, title, album):
    """One second of silence, tagged the way a ripped file is tagged."""
    path = folder / artist / f"{artist} - {title}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "1", "-q:a", "9", str(path),
        ],
        check=True,
    )
    subprocess.run([TAGGER, "-c", _TAG_SCRIPT, str(path), title, artist, album],
                   check=True)
    return path


EXPORTED = [
    ("Enrique Iglesias", "Tonight", "Euphoria"),
    ("Taio Cruz", "Break Your Heart", "Rokstarr"),
    ("Flo Rida", "Wild Ones", "Wild Ones"),
]
exported = [track_file(EXPORT, *entry) for entry in EXPORTED]
downloaded = track_file(YOUTUBE, "kobzx2z", "i just wanna be loved", "Singles")

# One song that reached the iPod from somewhere this computer cannot see: the
# file is built here only so it can be copied onto the device and deleted.
STRANGER = track_file(Path(SANDBOX, "elsewhere"), "OT7 Quanny", "Ghost", "Singles")

# The export tool's own playlist folder, inside the music folder: these are the
# files ipod-sync.sh was handed, and they are not the app's playlist folder.
EXPORT_LISTS = EXPORT / "playlists"
EXPORT_LISTS.mkdir(parents=True, exist_ok=True)
(EXPORT_LISTS / "2000.m3u").write_text(
    "#EXTM3U\n" + "\n".join(str(p) for p in exported) + "\n", encoding="utf-8"
)
(EXPORT_LISTS / "2016.m3u").write_text(
    "#EXTM3U\n" + str(exported[2]) + "\n", encoding="utf-8"
)

# The device, as a sync left it: every track copied under iPod_Control/Music
# keeping its artist folder and filename, and one list per playlist at the
# volume root naming those copies.
MUSIC_ON_DEVICE = VOLUME / "iPod_Control" / "Music"
for source in [*exported, downloaded, STRANGER]:
    target = MUSIC_ON_DEVICE / source.parent.name / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
shutil.rmtree(STRANGER.parent.parent)
Path(VOLUME, "iPod_Control", "Device").mkdir(parents=True, exist_ok=True)
Path(VOLUME, "iPod_Control", "Device", "SysInfo").write_text(
    "BoardHwName: N12\n", encoding="utf-8"
)


def device_entry(source):
    return f"iPod_Control/Music/{source.parent.name}/{source.name}"


(VOLUME / "2000.m3u").write_text(
    "#EXTM3U\n" + "\n".join(device_entry(p) for p in exported) + "\n",
    encoding="utf-8",
)
(VOLUME / "2016.m3u").write_text(
    "#EXTM3U\n" + device_entry(exported[2]) + "\n", encoding="utf-8"
)
(VOLUME / "Inspo.m3u").write_text(
    "#EXTM3U\n" + device_entry(downloaded) + "\n", encoding="utf-8"
)
# The one that cannot be copied whole: half of it is a song this computer
# does not hold.
(VOLUME / "YN.m3u").write_text(
    "#EXTM3U\n"
    + device_entry(exported[1]) + "\n"
    + device_entry(STRANGER) + "\n",
    encoding="utf-8",
)

# Spoken names for all three, so nothing in the note reads as a device that
# cannot announce them - the report is not about voiceover.
SPEAKABLE = VOLUME / "iPod_Control" / "Speakable" / "Playlists"
SPEAKABLE.mkdir(parents=True, exist_ok=True)
for name in ("2000", "2016", "Inspo", "YN"):
    (SPEAKABLE / f"{gui.speakable_id(name)}.wav").write_bytes(b"RIFF")

# The one playlist made in the app.
PLAYLISTS = Path(gui.PLAYLIST_LIBRARY)
PLAYLISTS.mkdir(parents=True, exist_ok=True)
gui.write_playlist_entries(PLAYLISTS / "Inspo.m3u", [str(downloaded)])

# Detection would otherwise go looking for real removable drives.
gui.find_ipods = lambda: [str(VOLUME)]


# ------------------------------------------------------------- reading it back


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


def pump(turns=40):
    context = GLib.MainContext.default()
    for _ in range(turns):
        while context.pending():
            context.iteration(False)
        GLib.usleep(15000)


def settle(window, seconds=25):
    """Wait for the device probe and both scans to land."""
    for _ in range(int(seconds * 8)):
        pump(2)
        if (
            window.mount_point
            and window.device_tracks
            and window.library.tracks
            and window.playlists
        ):
            pump(10)
            return True
    return False


def toast_text(window):
    for found in walk(window.toasts):
        if found is window.toasts or "Toast" not in type(found).__name__:
            continue
        for inner in walk(found):
            if isinstance(inner, Gtk.Label) and inner.get_text().strip():
                return inner.get_text()
    return None


def wait_for_quiet(window, seconds=12):
    """Let the toast time out, so the next scene reads its own sentence."""
    for _ in range(int(seconds * 4)):
        if toast_text(window) is None:
            return
        pump(17)
    report.append("a toast never went away")


def rail_names(window):
    names = []
    for row in walk(window.playlist_list):
        if isinstance(row, Gtk.Button):
            found = labels(row)
            if found:
                names.append(found[0])
    return names


def menu_rows(popover):
    """Every row the menu offers, heading first, as the user reads it."""
    return labels(popover)


def action_labels(window):
    return [
        child.get_label() or "⋯"
        for child in walk(window.playlist_actions)
        if isinstance(child, Gtk.Button) and child.get_parent() is window.playlist_actions
    ]


def shot(widget, name):
    pump(30)
    width, height = widget.get_width(), widget.get_height()
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
    transcript.append(f"screenshot: {name}")


def scene(window):
    if not settle(window):
        report.append("the device and the library never both arrived")
    window.show_view("playlists")
    pump()

    transcript.append(f"playlist folder holds: {sorted(p.name for p in PLAYLISTS.iterdir())}")
    transcript.append(f"volume root holds: {sorted(p.name for p in VOLUME.iterdir() if p.suffix == '.m3u')}")
    transcript.append(f"rail as the user sees it: {rail_names(window)}")
    transcript.append("")

    # The report itself: the ⋯ beside a track in the library, which is how a
    # song is put into a playlist.
    track = None
    for candidate in window.library.tracks:
        if candidate.title.lower().startswith("tonight"):
            track = candidate
    if track is None:
        report.append("the library never indexed the fixture's tracks")
        return
    menu = window.track_menu(track)
    offered = menu_rows(menu)
    transcript.append(f"Add to playlist offers: {offered}")
    if "2000" not in offered:
        transcript.append("  -> 2000 is not offered, though the rail lists it")
    transcript.append("")

    # The menu as it is actually seen, popped up over the window it belongs to.
    window.show_view("library")
    pump()
    menu.set_parent(window.view_title)
    menu.popup()
    pump(20)
    shot(menu.get_child(), f"{LABEL}-add-menu.png")
    menu.popdown()
    menu.unparent()
    window.show_view("playlists")
    pump()

    for name in ("Inspo", "2000"):
        window._select_playlist(name)
        pump()
        transcript.append(f"{name} page note: {labels(window.playlist_voice_note)}")
        transcript.append(f"{name} page buttons: {action_labels(window)}")
        shot(window, f"{LABEL}-{name.lower()}-page.png")
    transcript.append("")

    window._select_playlist("2000")
    pump()
    shot(window, f"{LABEL}-playlists.png")

    # Press the way out, if this build has one.
    if not hasattr(window, "on_copy_playlist_here"):
        transcript.append("this build offers no way to copy a device playlist here")
        (OUT / f"{LABEL}-transcript.txt").write_text(
            "\n".join(transcript) + "\n", encoding="utf-8"
        )
        print("\n".join(transcript))
        return

    transcript.append("--- Copy to this computer, pressed on 2000 ---")
    window.on_copy_playlist_here("2000")
    pump()
    transcript.append(f"says: {toast_text(window)!r}")
    copied = PLAYLISTS / "2000.m3u"
    transcript.append(f"playlist folder now holds: {sorted(p.name for p in PLAYLISTS.iterdir())}")
    transcript.append(f"2000.m3u holds: {gui.read_playlist_entries(copied)}")
    if not copied.is_file():
        report.append("pressing Copy to this computer wrote no playlist")
    transcript.append(f"2000 page buttons: {action_labels(window)}")
    transcript.append(f"rail: {rail_names(window)}")
    transcript.append("")

    offered = menu_rows(window.track_menu(track))
    transcript.append(f"Add to playlist now offers: {offered}")
    if "2000" not in offered:
        report.append("2000 is still not offered after being copied here")
    transcript.append("")

    # And the whole point of being offered: the song actually lands in it.
    wait_for_quiet(window)
    newcomer = None
    for candidate in window.library.tracks:
        if candidate.title.startswith("i just wanna"):
            newcomer = candidate
    window._add_tracks_to_playlist("2000", [newcomer])
    pump()
    transcript.append(f"adding {newcomer.title} to 2000 says: {toast_text(window)!r}")
    transcript.append(f"2000.m3u holds: {gui.read_playlist_entries(copied)}")
    shot(window, f"{LABEL}-2000-copied.png")
    transcript.append("")

    # The half-copy: YN names a song this computer does not hold, so the press
    # asks before it writes a list the next sync would shorten the device's by.
    wait_for_quiet(window)
    window._select_playlist("YN")
    pump()
    transcript.append(f"YN page note: {labels(window.playlist_voice_note)}")
    transcript.append(f"YN page buttons: {action_labels(window)}")
    transcript.append(f"YN page caption: {labels(window.playlist_actions)}")
    dialog = window.on_copy_playlist_here("YN")
    pump()
    if dialog is None:
        report.append("a copy that would leave tracks behind asked nothing")
    else:
        transcript.append(f"dialog: {dialog.get_heading()!r}")
        transcript.append(f"        {dialog.get_body()!r}")
        shot(window, f"{LABEL}-yn-dialog.png")
        window._on_copy_here_response(dialog, "cancel", "YN", [], 1)
        dialog.close()
        pump()
        if (PLAYLISTS / "YN.m3u").exists():
            report.append("cancelling the dialog copied the playlist anyway")
        transcript.append(f"after Cancel, folder holds: {sorted(p.name for p in PLAYLISTS.iterdir())}")

    (OUT / f"{LABEL}-transcript.txt").write_text(
        "\n".join(transcript) + "\n", encoding="utf-8"
    )
    print("\n".join(transcript))


def on_activate(app):
    window = gui.IpodWindow(application=app)
    window.set_default_size(1180, 760)
    window.present()

    def go():
        try:
            scene(window)
        except Exception:  # noqa: BLE001
            import traceback

            report.append(traceback.format_exc())
        app.quit()
        return False

    GLib.timeout_add(800, go)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.AddToPlaylist",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(120, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
