#!/usr/bin/env python3
"""The report, driven by pressing the controls a user would press.

The fixture is the reported machine: five playlists on the rail - "Inspo",
made in the app, and "2000", "2016", "More alt shii" and "YN", written into
the music folder by a Spotify export and put on the device by ipod-sync.sh -
and a song's `⋯` offering one of them.

The other driver in this repository reaches the window's handlers by name.
This one only ever emits a press on the widget the pointer would be over: the
album card, the `⋯` on a track row, the row inside the menu that opens, the
playlist on the rail, "Copy to this computer", the dialog's own response, and
the refresh button in the header. Nothing about the copy is called directly,
so what is read back afterwards is what the presses did.

Takes the repository to import from and a directory to write into.

Run it with `GDK_BACKEND=x11`, or under xvfb. A Wayland compositor stops
sending frame callbacks to a surface nobody is looking at, and a window that
is not being drawn snapshots to nothing - so on Wayland the pictures come out
empty as soon as the terminal is the focused window.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)

SANDBOX = tempfile.mkdtemp(prefix="ipod-copy-here-")
# The tags are what matches a copy on the device to the file it was made from,
# so the reader has to keep finding the virtualenv install.sh keeps mutagen in.
# Read before HOME moves, and left alone if the environment already names one.
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
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit("no display: run this on a desktop session or under xvfb")

report = []
transcript = []


def say(line=""):
    transcript.append(line)


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
    subprocess.run(
        [TAGGER, "-c", _TAG_SCRIPT, str(path), title, artist, album], check=True
    )
    return path


EXPORTED = [
    ("Enrique Iglesias", "Tonight", "Euphoria"),
    ("Taio Cruz", "Break Your Heart", "Rokstarr"),
    ("Flo Rida", "Wild Ones", "Wild Ones"),
    ("Grimes", "Oblivion", "Visions"),
]
exported = [track_file(EXPORT, *entry) for entry in EXPORTED]
downloaded = track_file(YOUTUBE, "kobzx2z", "i just wanna be loved", "Singles")

# One song that reached the iPod from a machine this computer cannot see: the
# file is built here only so it can be copied onto the device and deleted.
STRANGER = track_file(Path(SANDBOX, "elsewhere"), "OT7 Quanny", "Ghost", "Singles")

# What the export tool wrote into the music folder. These are the files
# ipod-sync.sh was handed; they are not the app's playlist folder, and the app
# never reads them.
EXPORT_LISTS = EXPORT / "playlists"
EXPORT_LISTS.mkdir(parents=True, exist_ok=True)
for name, members in (
    ("2000", exported[:3]),
    ("2016", exported[2:3]),
    ("More alt shii", exported[3:]),
):
    (EXPORT_LISTS / f"{name}.m3u").write_text(
        "#EXTM3U\n" + "\n".join(str(p) for p in members) + "\n", encoding="utf-8"
    )

# The device, as a sync left it: every track under iPod_Control/Music keeping
# its artist folder and filename, and one list per playlist at the volume root
# naming those copies.
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


ON_DEVICE = {
    "2000": exported[:3],
    "2016": exported[2:3],
    "More alt shii": exported[3:],
    # The one that cannot be copied whole: half of it is a song this computer
    # does not hold.
    "YN": [exported[1], STRANGER],
    "Inspo": [downloaded],
}
for name, members in ON_DEVICE.items():
    (VOLUME / f"{name}.m3u").write_text(
        "#EXTM3U\n" + "\n".join(device_entry(p) for p in members) + "\n",
        encoding="utf-8",
    )

# Spoken names for all of them, so nothing in the note reads as a device that
# cannot announce its playlists - the report is not about voiceover.
SPEAKABLE = VOLUME / "iPod_Control" / "Speakable" / "Playlists"
SPEAKABLE.mkdir(parents=True, exist_ok=True)
for name in ON_DEVICE:
    (SPEAKABLE / f"{gui.speakable_id(name)}.wav").write_bytes(b"RIFF")

# The one playlist made in the app.
PLAYLISTS = Path(gui.PLAYLIST_LIBRARY)
PLAYLISTS.mkdir(parents=True, exist_ok=True)
gui.write_playlist_entries(PLAYLISTS / "Inspo.m3u", [str(downloaded)])

# Detection would otherwise go looking for real removable drives.
attached = [str(VOLUME)]
gui.find_ipods = lambda: list(attached)


# ------------------------------------------------------- reading it back


def walk(widget):
    """Every widget under this one, minus any tooltip standing open.

    A tooltip is a child of the widget it describes in GTK4's tree, and the
    pointer has to rest somewhere while this runs, so without this the ⋯ menu
    reads back with the row's tooltip in the middle of it - a sentence the
    menu does not contain.
    """
    if widget is None:
        return
    name = type(widget).__name__
    if "Tooltip" in name or widget.get_css_name() == "tooltip":
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
            and not window._library_scan_running
            and window._device_snapshot_ready
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


def wait_for_quiet(window, seconds=25):
    """Let the toast time out, so the next scene reads its own sentence."""
    for _ in range(int(seconds * 4)):
        if toast_text(window) is None:
            return
        pump(17)
    report.append("a toast never went away")


def shot(widget, name, backdrop=None):
    """A PNG of what is on screen, as the window paints it.

    A menu is drawn on a surface of its own, so its own background is not in
    the widget the popover holds and rendering that alone gives pale text on
    nothing. `backdrop` is the page colour it sits over, painted first so the
    picture reads the way the screen does.
    """
    # Both waited for rather than assumed: a popover is a surface of its own
    # and is allocated a frame or two after it is asked to open, and a widget
    # mid-repaint renders an empty node. An empty picture would be worse than
    # none at all, so nothing is written until there is something in it.
    node = None
    for _ in range(40):
        pump(5)
        width, height = widget.get_width(), widget.get_height()
        if not width or not height:
            continue
        paintable = Gtk.WidgetPaintable.new(widget)
        snapshot = Gtk.Snapshot.new()
        if backdrop is not None:
            colour = Gdk.RGBA()
            colour.parse(backdrop)
            snapshot.append_color(colour, Graphene.Rect().init(0, 0, width, height))
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is not None:
            break
    if node is None:
        report.append(f"nothing rendered for {name}")
        return
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(None)
    renderer.render_texture(node, None).save_to_png(str(OUT / name))
    renderer.unrealize()
    say(f"screenshot: {name}")


# --------------------------------------------------------------- pressing it


def buttons_under(widget):
    return [found for found in walk(widget) if isinstance(found, Gtk.Button)]


def press_labelled(widget, text, what):
    """Press the one button under `widget` reading `text`."""
    for button in buttons_under(widget):
        if (button.get_label() or "") == text or text in labels(button):
            button.emit("clicked")
            pump()
            return button
    report.append(f"no button reading {text!r} to press {what}")
    return None


def rail_row(window, name):
    for button in buttons_under(window.playlist_list):
        found = labels(button)
        if found and found[0] == name:
            return button
    return None


def open_row_menu(row_widget, what):
    """Press a ⋯ and return the popover it puts on screen."""
    opener = next(
        (
            found
            for found in walk(row_widget)
            if isinstance(found, Gtk.MenuButton)
        ),
        None,
    )
    if opener is None:
        report.append(f"no ⋯ to press {what}")
        return None
    opener.popup()
    popover = None
    for _ in range(40):
        pump(5)
        popover = opener.get_popover()
        if popover is not None and popover.get_width():
            break
    if popover is None:
        report.append(f"the ⋯ {what} opened nothing")
    return popover


def album_row(window, title):
    """A song's ⋯ on its album's page, reached by pressing the card.

    The way a song is actually reached: the grid, then the record, then the
    row. What comes back is the ⋯ on the first track of it.
    """
    window.show_view("library")
    pump()
    card = next(
        (
            button
            for button in buttons_under(window.album_flow)
            if title in labels(button)
        ),
        None,
    )
    if card is None:
        report.append(
            f"no album card reading {title!r} in the grid: "
            f"{[labels(b) for b in buttons_under(window.album_flow)]}"
        )
        return None
    card.emit("clicked")
    pump(20)
    openers = [
        found for found in walk(window.album_tracks) if isinstance(found, Gtk.MenuButton)
    ]
    if not openers:
        report.append(f"the page for {title!r} drew no track row carrying a ⋯")
        return None
    return openers[0]


# ------------------------------------------------------------------ the scene


def scene(window):
    if not settle(window):
        report.append("the device and the library never both arrived")

    window.show_view("playlists")
    pump()
    say("== the machine in the report ==")
    say(f"playlist folder holds: {sorted(p.name for p in PLAYLISTS.iterdir())}")
    say(f"volume root holds: {sorted(p.name for p in VOLUME.glob('*.m3u'))}")
    say(f"rail as the user sees it: {[labels(b)[0] for b in buttons_under(window.playlist_list) if labels(b)]}")
    shot(window, "01-five-playlists-on-the-rail.png")
    say()

    # The report: the ⋯ beside a song, which is how a song joins a playlist.
    row = album_row(window, "Euphoria")
    if row is None:
        report.append("the library never drew a row to press ⋯ on")
        return
    menu = open_row_menu(row, "on a song in the library")
    if menu is None:
        return
    offered = labels(menu)
    say("== the ⋯ on a song, pressed ==")
    say(f"Add to playlist offers: {offered}")
    shot(menu, "02-add-menu-names-what-it-leaves-out.png", backdrop="#131314")
    for name in ("2000", "2016", "More alt shii", "YN"):
        if name in offered:
            report.append(f"{name} is offered before it has been copied here")
    if not any("Only on the iPod" in line for line in offered):
        report.append("the menu leaves four playlists out and says nothing")
    menu.popdown()
    pump()
    say()

    # The way out, pressed on the rail row rather than called by name.
    window.show_view("playlists")
    pump()
    row_2000 = rail_row(window, "2000")
    if row_2000 is None:
        report.append("2000 is not on the rail")
        return
    row_2000.emit("clicked")
    pump()
    say("== 2000, opened from the rail ==")
    say(f"page note: {labels(window.playlist_voice_note)}")
    say(f"page buttons: {[b.get_label() for b in buttons_under(window.playlist_actions) if b.get_label()]}")
    shot(window, "03-a-device-playlist-offers-a-copy.png")

    # Where "Remove from iPod" went.
    under_it = open_row_menu(window.playlist_actions, "on the 2000 page")
    if under_it is not None:
        say(f"the ⋯ on that page holds: {labels(under_it)}")
        shot(under_it, "04-remove-from-ipod-under-the-dots.png", backdrop="#131314")
        if not any("Remove from iPod" in line for line in labels(under_it)):
            report.append("the page offers no way to take the playlist off the iPod")
        under_it.popdown()
        pump()
    say()

    staged_before = dict(window.pending_sources)
    press_labelled(window.playlist_actions, "Copy to this computer", "on 2000")
    pump(20)
    say("== Copy to this computer, pressed ==")
    say(f"says: {toast_text(window)!r}")
    copied = PLAYLISTS / "2000.m3u"
    if not copied.is_file():
        report.append("the press wrote no playlist")
        return
    say(f"playlist folder now holds: {sorted(p.name for p in PLAYLISTS.iterdir())}")
    say("2000.m3u holds:")
    for entry in gui.read_playlist_entries(copied):
        say(f"    {entry.replace(SANDBOX, '~')}")
    for entry in gui.read_playlist_entries(copied):
        if str(VOLUME) in entry:
            report.append(f"the copy names the iPod's own file: {entry}")
        if not Path(entry).is_file():
            report.append(f"the copy names a file that is not here: {entry}")
    say(f"page buttons now: {[b.get_label() for b in buttons_under(window.playlist_actions) if b.get_label()]}")
    if window.pending_sources != staged_before:
        report.append(
            f"copying staged a sync the press never asked for: {window.pending_sources}"
        )
    else:
        say("staged for sync by the copy: nothing")
    shot(window, "05-copied-here-and-now-editable.png")
    say()

    # And the whole point: the song can now be put into it, from the same menu
    # that used to leave it out.
    wait_for_quiet(window)
    row = album_row(window, "Visions")
    menu = open_row_menu(row, "on a song after the copy")
    if menu is None:
        return
    say("== the same ⋯, after the copy ==")
    say(f"Add to playlist now offers: {labels(menu)}")
    shot(menu, "06-the-copied-playlist-is-offered.png", backdrop="#131314")
    if "2000" not in labels(menu):
        report.append("2000 is still not offered after being copied here")
        return
    press_labelled(menu, "2000", "to add the song to the copied playlist")
    pump(20)
    say(f"adding it says: {toast_text(window)!r}")
    say("2000.m3u now holds:")
    for entry in gui.read_playlist_entries(copied):
        say(f"    {entry.replace(SANDBOX, '~')}")
    if str(exported[3]) not in gui.read_playlist_entries(copied):
        report.append("the song never reached the copied playlist's file")
    say()

    # That edit stages a change the sync copies no bytes for, because the song
    # it added is already over there. Read off the sidebar, attached and not.
    say("== the sidebar, for a staged change that copies nothing ==")
    say(f"Sync button: {window.sync_button.get_label()!r}")
    say(f"sidebar says: {window.queued_label.get_text()!r}")
    if "0 B" in window.queued_label.get_text():
        report.append(
            f"the sidebar calls a change that copies nothing {window.queued_label.get_text()!r}"
        )
    shot(window, "07-sidebar-queued-nothing-to-copy.png")

    attached.clear()
    window.refresh_button.emit("clicked")
    for _ in range(80):
        pump(4)
        if not window.mount_point:
            break
    if window.mount_point:
        report.append("the iPod never went away when the refresh found none")
    say(f"unplugged, sidebar says: {window.queued_label.get_text()!r}")
    if "0 B" in window.queued_label.get_text():
        report.append(
            f"the unplugged sidebar reads {window.queued_label.get_text()!r}"
        )
    attached.append(str(VOLUME))
    window.refresh_button.emit("clicked")
    settle(window)
    say()

    # The half copy: YN names a song this computer does not hold, so the press
    # asks first, and what it would leave behind is on the page before that.
    wait_for_quiet(window)
    row_yn = rail_row(window, "YN")
    if row_yn is None:
        report.append("YN is not on the rail")
        return
    row_yn.emit("clicked")
    pump()
    say("== YN, half of which is not here ==")
    say(f"page note: {labels(window.playlist_voice_note)}")
    if not any("does not have" in line for line in labels(window.playlist_voice_note)):
        report.append("the page never counts what a copy of YN would leave behind")
    shot(window, "08-a-partial-copy-is-counted-on-the-page.png")

    press_labelled(window.playlist_actions, "Copy to this computer", "on YN")
    pump(20)
    dialog = window.get_visible_dialog()
    if not isinstance(dialog, Adw.AlertDialog):
        report.append(f"a copy that leaves a track behind asked {dialog!r}")
        return
    say(f"dialog heading: {dialog.get_heading()!r}")
    say(f"dialog body: {dialog.get_body()!r}")
    say(f"default response: {dialog.get_default_response()!r}")
    shot(window, "09-a-partial-copy-asks-first.png")

    dialog.emit("response", "cancel")
    dialog.force_close()
    pump(20)
    if (PLAYLISTS / "YN.m3u").exists():
        report.append("Cancel copied the playlist anyway")
    say(f"after Cancel the folder holds: {sorted(p.name for p in PLAYLISTS.iterdir())}")

    press_labelled(window.playlist_actions, "Copy to this computer", "on YN again")
    pump(20)
    dialog = window.get_visible_dialog()
    if not isinstance(dialog, Adw.AlertDialog):
        report.append(f"the second press asked {dialog!r}")
        return
    dialog.emit("response", "copy")
    dialog.force_close()
    pump(20)
    say(f"after Copy it says: {toast_text(window)!r}")
    yn = PLAYLISTS / "YN.m3u"
    if not yn.is_file():
        report.append("confirming the dialog wrote nothing")
        return
    say("YN.m3u holds:")
    for entry in gui.read_playlist_entries(yn):
        say(f"    {entry.replace(SANDBOX, '~')}")
    if gui.read_playlist_entries(yn) != [str(exported[1])]:
        report.append(
            f"the confirmed copy holds {gui.read_playlist_entries(yn)} rather than "
            "the one track this computer answers for"
        )
    if device_entry(STRANGER) in " ".join(gui.read_playlist_entries(yn)):
        report.append("the copy wrote the iPod's own path for the missing song")
    say(f"the device's own YN still holds: {gui.read_playlist_entries(VOLUME / 'YN.m3u')}")
    shot(window, "10-yn-copied-what-it-could.png")
    say()

    window.show_view("playlists")
    pump()
    say(f"rail now: {[labels(b)[0] for b in buttons_under(window.playlist_list) if labels(b)]}")
    row = album_row(window, "Euphoria")
    menu = open_row_menu(row, "on a song at the end")
    if menu is not None:
        say(f"Add to playlist finally offers: {labels(menu)}")
        shot(menu, "11-every-playlist-copied-here-is-offered.png", backdrop="#131314")
        menu.popdown()
    say()

    # And the copy is editable in the other direction too: a song can be taken
    # back out of it, from the ⋯ on its own row.
    wait_for_quiet(window)
    rail = rail_row(window, "2000")
    if rail is None:
        report.append("2000 left the rail")
        return
    rail.emit("clicked")
    pump(20)
    openers = [
        found for found in walk(window.playlist_tracks) if isinstance(found, Gtk.MenuButton)
    ]
    if not openers:
        report.append("the copied playlist drew no row carrying a ⋯")
        return
    inside = open_row_menu(openers[-1], "on a row inside the copied playlist")
    if inside is None:
        return
    say("== a row inside the copied playlist ==")
    say(f"its ⋯ holds: {labels(inside)}")
    press_labelled(inside, "Remove from 2000", "to take a song back out of it")
    pump(20)
    say(f"says: {toast_text(window)!r}")
    say("2000.m3u now holds:")
    for entry in gui.read_playlist_entries(copied):
        say(f"    {entry.replace(SANDBOX, '~')}")
    if str(exported[3]) in gui.read_playlist_entries(copied):
        report.append("the song was not taken out of the copied playlist's file")
    shot(window, "12-the-copy-edits-like-any-other-playlist.png")

    (OUT / "transcript.txt").write_text("\n".join(transcript) + "\n", encoding="utf-8")
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
        (OUT / "transcript.txt").write_text(
            "\n".join(transcript) + "\n", encoding="utf-8"
        )
        app.quit()
        return False

    GLib.timeout_add(800, go)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.CopyHere",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(180, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
