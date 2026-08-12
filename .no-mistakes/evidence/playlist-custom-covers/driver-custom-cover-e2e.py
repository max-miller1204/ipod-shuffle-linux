#!/usr/bin/env python3
"""A custom playlist cover, chosen and taken off again in the shipped window.

Runs the real IpodWindow against the fixture `tools/demo-library.py` builds -
real MP3s carrying real embedded art, real M3U playlists, and a volume
`ipod-sync.sh` has actually written to - and reaches every step the way a user
does: the Playlists view, the playlist on the rail, the `⋯` on its own page,
and the rows inside the menu that opens.

The one seam that is not a press is the file chooser's answer. `Gtk.FileDialog`
puts a chooser on screen and hands back the file that was picked in it; there
is no way to pick a file in it from here, so the chooser is opened and
photographed, and the answer it would have given is handed to the same callback
the dialog calls - `_playlist_cover_chosen`, with the `Gio.File` the chooser
returns. Everything after that point is the product's own code.

    driver-custom-cover-e2e.py --repo R --demo D --out O

Needs a display. On a machine with no X server, run it under gtk4-broadwayd
with GDK_BACKEND=broadway; the pictures are rendered off the live widget tree
with a Cairo renderer rather than grabbed off a screen.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", required=True)
parser.add_argument("--demo", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

REPO = Path(args.repo).resolve()
DEMO = Path(args.demo).resolve()
OUT = Path(args.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)

HOME = DEMO / "home"
VOLUME = DEMO / "MAX SHUFFLE"
PLAYLISTS_HERE = HOME / "Music" / "Playlists"
PICTURES = HOME / "Pictures"

# Read before HOME moves: the tag reader lives in install.sh's virtualenv, and
# a library with no tags is a library with no artwork to fall back to.
os.environ.setdefault(
    "IPOD_VENV_PYTHON", str(Path.home() / "ipod-tools" / "venv" / "bin" / "python")
)
os.environ["HOME"] = str(HOME)
os.environ["XDG_CONFIG_HOME"] = str(HOME / ".config")
os.environ["XDG_CACHE_HOME"] = str(HOME / ".cache")
os.environ["XDG_DATA_HOME"] = str(HOME / ".local/share")
# The stub findmnt in tests/bin reports this volume as the mounted iPod, so the
# device in these shots is one the app found for itself.
os.environ["FAKE_IPOD_MOUNT"] = str(VOLUME)
# The real encoder, found before tests/bin goes on the PATH: the stub in there
# stands in for ffmpeg and exits without writing anything, and the images this
# chooses as covers have to be images.
FFMPEG = shutil.which("ffmpeg")
if FFMPEG is None:
    raise SystemExit("ffmpeg is needed to draw the images this chooses")
os.environ["PATH"] = f"{REPO / 'tests' / 'bin'}:{os.environ['PATH']}"

sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit("no display: run this under broadway or a nested X server")

# A dialog closes over an animation, and an animation needs a frame clock that
# broadway only runs for a surface something is looking at. Left on, a dialog
# that has been answered stays painted over every picture taken after it.
Gtk.Settings.get_default().props.gtk_enable_animations = False

report = []
transcript = []
said = []

LOCAL_PLAYLIST = "Downloads"
SYNCED_PLAYLIST = "Morning Ride"
RENAMED = "Late Night Drive"


def say(line=""):
    transcript.append(line)


def pump(turns=40):
    context = GLib.MainContext.default()
    for _ in range(turns):
        while context.pending():
            context.iteration(False)
        GLib.usleep(15000)


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


def buttons_under(widget):
    return [found for found in walk(widget) if isinstance(found, Gtk.Button)]


def settle(window, seconds=60):
    for _ in range(int(seconds * 8)):
        pump(2)
        if (
            window.mount_point
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
    """Let a toast time out, so the next scene photographs its own sentence."""
    for _ in range(int(seconds * 4)):
        if toast_text(window) is None:
            return
        pump(17)
    report.append("a toast never went away")


def shot(widget, name, backdrop=None):
    """A PNG of what is on screen, as the window paints it.

    A popover is drawn on a surface of its own, so its own page colour is not
    in the widget it holds; `backdrop` paints that colour first, which is what
    the menu looks like over the page rather than over nothing.
    """
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


def open_page_menu(window, what):
    """Press the ⋯ on the open playlist's page and return what it put up."""
    opener = next(
        (
            found
            for found in walk(window.playlist_actions)
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


def press_in_menu(menu, text, what):
    """Press the row of an open ⋯ menu whose label starts with `text`."""
    for button in buttons_under(menu):
        found = labels(button)
        if found and found[0].startswith(text):
            menu.popdown()
            pump(4)
            button.emit("clicked")
            pump(20)
            return True
    report.append(f"the menu {what} offered no {text!r}: {labels(menu)}")
    menu.popdown()
    return False


def open_playlist(window, name):
    """Reach a playlist the way a user does: the view, then its row."""
    window.show_view("playlists")
    pump(20)
    row = next(
        (
            button
            for button in buttons_under(window.playlist_list)
            if labels(button) and labels(button)[0] == name
        ),
        None,
    )
    if row is None:
        report.append(
            f"no {name!r} on the rail: "
            f"{[labels(b)[:1] for b in buttons_under(window.playlist_list)]}"
        )
        return False
    row.emit("clicked")
    pump(20)
    window.set_focus(None)
    pump(10)
    return True


def dialog_on(window):
    dialog = window.get_visible_dialog()
    return dialog if isinstance(dialog, Adw.Dialog) else None


def entry_in(dialog):
    for found in walk(dialog.get_extra_child()):
        if isinstance(found, Adw.EntryRow):
            return found
    return None


def rail_covers(window):
    """Which rail rows are painted with a loaded image, and which are not.

    `make_cover` puts a Gtk.Image in the row only for a file GTK could decode;
    anything else leaves the placeholder the playlist's name generates, which
    is a style class and no widget at all. So this is the difference between a
    cover that reached the screen and a cover that only reached the disk.
    """
    painted = {}
    for button in buttons_under(window.playlist_list):
        found = labels(button)
        if not found:
            continue
        painted[found[0]] = any(
            isinstance(child, Gtk.Image) for child in walk(button)
        )
    return painted


def store_state():
    """The cover store, read off the folder rather than asked of the app."""
    folder = PLAYLISTS_HERE / ".covers"
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir())


def here_state():
    return sorted(path.name for path in PLAYLISTS_HERE.iterdir())


def staged(window):
    return {
        source: sorted(members)
        for source, members in sorted(window.pending_sources.items())
    }


def art_for(window, name):
    playlist = window._local_playlist(name) or window._shown_playlist(name)
    return window._playlist_art(playlist) if playlist is not None else None


def draw_cover(path, colour, first, second):
    """An image nobody could mistake for one of the demo's own album covers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    text = (
        f"drawtext=fontfile={font}:text='{first}':fontcolor=white:fontsize=88"
        ":x=(w-text_w)/2:y=(h/2)-110,"
        f"drawtext=fontfile={font}:text='{second}':fontcolor=white:fontsize=44"
        ":x=(w-text_w)/2:y=(h/2)+20"
    )
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={colour}:s=600x600",
            "-vf", text, "-frames:v", "1", str(path),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return path


# ------------------------------------------------------------------ the scene


def scene(window):
    real_add_toast = window.toasts.add_toast

    def add_toast(toast):
        said.append(toast.get_title())
        return real_add_toast(toast)

    window.toasts.add_toast = add_toast

    if not settle(window):
        report.append("the device and the library never both arrived")
    say("== a custom cover for a playlist, in the shipped window ==")
    say(f"iPod the app found for itself: {window.mount_point}")
    say(f"playlists here: {[p.name for p in window.local_playlists]}")
    say(f"playlists on the device: {[n for n, _entries in window.playlists]}")
    say()

    chosen_png = draw_cover(
        PICTURES / "night-drive.png", "0x1F1147", "CUSTOM", "chosen PNG"
    )
    chosen_webp = draw_cover(
        PICTURES / "morning-run.webp", "0xB3541E", "CUSTOM", "chosen WebP"
    )
    chosen_jpeg = draw_cover(
        PICTURES / "last-orders.jpg", "0x14532D", "CUSTOM", "chosen JPEG"
    )
    sleeve_notes = PICTURES / "sleeve-notes.txt"
    sleeve_notes.write_text(
        "words about the record rather than a picture of it\n", encoding="utf-8"
    )
    say(
        "images to choose from: "
        f"{[p.name for p in (chosen_png, chosen_webp, chosen_jpeg)]}"
    )
    say(f"and one file that is not an image: {sleeve_notes.name}")
    say()

    # ---------------------------------------------- 01: the artwork it starts with
    if not open_playlist(window, LOCAL_PLAYLIST):
        return
    say("-- before anything is chosen --")
    say(f"{LOCAL_PLAYLIST} is painted with: {art_for(window, LOCAL_PLAYLIST)!r}")
    say(f"cover store beside the playlists: {store_state() or 'no .covers folder'}")
    say(f"queued for the next sync: {staged(window)}")
    shot(window, "01-playlist-wears-its-songs-artwork.png")

    # ---------------------------------------------- 02: what the ⋯ offers
    menu = open_page_menu(window, "on the playlist's page")
    if menu is None:
        return
    say(f"the ⋯ on {LOCAL_PLAYLIST} offers: {labels(menu)}")
    shot(menu, "02-menu-offers-a-custom-cover.png", backdrop="#fafafa")

    # ---------------------------------------------- 03: the chooser it opens
    if not press_in_menu(menu, "Choose custom cover", "on the playlist's page"):
        return
    say("pressed: Choose custom cover…")
    chooser = None
    for _ in range(60):
        pump(5)
        chooser = next(
            (
                top
                for top in Gtk.Window.list_toplevels()
                if top is not window and isinstance(top, Gtk.FileChooser)
            ),
            None,
        )
        if chooser is not None:
            break
    if chooser is None:
        say("no file chooser reached the screen to photograph")
    else:
        # Presented and pointed at the folder the images are in, which is the
        # sidebar click a user makes. Broadway maps no surface for a window
        # nobody is looking at, so it is asked for a size of its own first;
        # everything in it, the filter included, is the chooser the product
        # opened.
        chooser.set_current_folder(Gio.File.new_for_path(str(PICTURES)))
        chooser.set_default_size(940, 620)
        chooser.present()
        pump(60)
        current = chooser.get_filter()
        say(
            "the chooser it opened filters on: "
            f"{current.get_name() if current is not None else None!r}"
        )
        say(f"the folder it is pointed at holds: {sorted(p.name for p in PICTURES.iterdir())}")
        shot(chooser, "03-the-file-chooser-offers-images.png")
        # Dismissed rather than answered: a file cannot be picked in it from
        # here, so the answer is handed to the callback below instead.
        chooser.close()
        pump(20)
    say()

    # ---------------------------------------------- 04: the cover that was chosen
    staged_before = staged(window)
    log_before = window.log_view.get_buffer().get_char_count()
    if not window._playlist_cover_chosen(
        LOCAL_PLAYLIST, Gio.File.new_for_path(str(chosen_png))
    ):
        report.append(f"the chosen PNG was refused: {said[-1:]!r}")
        return
    pump(20)
    say("-- the chooser answered with night-drive.png --")
    say(f"the window said: {said[-1]!r}")
    say(f"cover store now holds: {store_state()}")
    stored = gui.playlist_custom_cover(PLAYLISTS_HERE / f"{LOCAL_PLAYLIST}.m3u")
    say(f"{LOCAL_PLAYLIST} is painted with: {art_for(window, LOCAL_PLAYLIST)!r}")
    say(f"which is the copy in the store: {stored is not None and str(stored)}")
    say(f"the copy matches the file that was chosen: "
        f"{stored is not None and stored.read_bytes() == chosen_png.read_bytes()}")
    say(f"the original is still where it was: {chosen_png.is_file()}")
    say(f"queued for the next sync: {staged(window)}")
    if staged(window) != staged_before:
        report.append("choosing a cover changed what is staged for the sync")
    if window.log_view.get_buffer().get_char_count() != log_before:
        report.append("choosing a cover ran a device command")
    painted = rail_covers(window)
    say(f"rail rows painted with a loaded image: {painted}")
    if not painted.get(LOCAL_PLAYLIST):
        report.append(
            "the rail drew no image for the playlist wearing a custom cover"
        )
    shot(window, "04-the-chosen-cover-on-the-playlist.png")

    # ---------------------------------------------- 05: a WebP on the other list
    wait_for_quiet(window)
    if not open_playlist(window, SYNCED_PLAYLIST):
        return
    if not window._playlist_cover_chosen(
        SYNCED_PLAYLIST, Gio.File.new_for_path(str(chosen_webp))
    ):
        report.append(f"the chosen WebP was refused: {said[-1:]!r}")
        return
    pump(20)
    say()
    say("-- and a WebP for the playlist that is on the iPod --")
    say(f"the window said: {said[-1]!r}")
    say(f"cover store now holds: {store_state()}")
    say(f"{SYNCED_PLAYLIST} is painted with: {art_for(window, SYNCED_PLAYLIST)!r}")
    painted = rail_covers(window)
    if not painted.get(SYNCED_PLAYLIST):
        report.append("the rail drew no image for the WebP cover")
    window.show_view("library")
    pump(30)
    window.set_focus(None)
    shot(window, "05-the-shelf-tiles-wear-them-too.png")

    # ---------------------------------------------- 06: renamed, cover and all
    wait_for_quiet(window)
    if not open_playlist(window, LOCAL_PLAYLIST):
        return
    menu = open_page_menu(window, "to rename it")
    if menu is None:
        return
    if not press_in_menu(menu, "Rename", "to rename it"):
        return
    dialog = dialog_on(window)
    if dialog is None:
        report.append("Rename… opened nothing")
        return
    field = entry_in(dialog)
    if field is None:
        report.append("the rename dialog carries no name field")
        return
    field.set_text(RENAMED)
    pump(10)
    say()
    say("-- renaming the playlist that is wearing the PNG --")
    say(f"typed the new name: {RENAMED!r}")
    shot(window, "06-renaming-it.png")
    dialog.emit("response", "rename")
    dialog.force_close()
    pump(40)
    say(f"the window said: {said[-1]!r}")
    say(f"playlist files here: {here_state()}")
    say(f"cover store now holds: {store_state()}")
    say(f"{RENAMED} is painted with: {art_for(window, RENAMED)!r}")
    renamed_cover = gui.playlist_custom_cover(PLAYLISTS_HERE / f"{RENAMED}.m3u")
    if renamed_cover is None:
        report.append("the rename left the playlist with no custom cover")
    elif renamed_cover.read_bytes() != chosen_png.read_bytes():
        report.append("the renamed playlist is wearing some other image")
    painted = rail_covers(window)
    if not painted.get(RENAMED):
        report.append("the renamed playlist lost its cover on the rail")
    window.show_view("playlists")
    window.set_focus(None)
    shot(window, "07-renamed-and-still-wearing-it.png")

    # ---------------------------------------------- 08: back to song artwork
    wait_for_quiet(window)
    if not open_playlist(window, RENAMED):
        return
    menu = open_page_menu(window, "to take the cover off")
    if menu is None:
        return
    say()
    say(f"the ⋯ now offers: {labels(menu)}")
    shot(menu, "08-menu-offers-use-song-artwork.png", backdrop="#fafafa")
    if not press_in_menu(menu, "Use song artwork", "to take the cover off"):
        return
    pump(20)
    say("pressed: Use song artwork")
    say(f"the window said: {said[-1]!r}")
    say(f"cover store now holds: {store_state() or 'no .covers folder'}")
    say(f"{RENAMED} is painted with: {art_for(window, RENAMED)!r}")
    if gui.playlist_custom_cover(PLAYLISTS_HERE / f"{RENAMED}.m3u") is not None:
        report.append("Use song artwork left the custom cover in place")
    shot(window, "09-back-to-the-songs-own-artwork.png")

    back = open_page_menu(window, "with no cover left to remove")
    if back is not None:
        say(f"and the ⋯ is back to: {labels(back)}")
        if any(text.startswith("Use song artwork") for text in labels(back)):
            report.append("the row that removes a cover stayed with none to remove")
        back.popdown()
        pump(4)

    # ---------------------------------------------- 10: a file that is not an image
    wait_for_quiet(window)
    if window._playlist_cover_chosen(
        RENAMED, Gio.File.new_for_path(str(sleeve_notes))
    ):
        report.append("a text file was accepted as a cover")
    pump(20)
    say()
    say("-- choosing a text file instead of an image --")
    say(f"the window said: {said[-1]!r}")
    say(f"cover store now holds: {store_state() or 'no .covers folder'}")
    shot(window, "10-a-file-that-is-not-an-image-is-refused.png")

    # ---------------------------------------------- 11: deleted, cover and all
    #
    # A JPEG this time, which is the third of the formats the store holds and
    # the one the two scenes above have not been through.
    wait_for_quiet(window)
    if not window._playlist_cover_chosen(
        RENAMED, Gio.File.new_for_path(str(chosen_jpeg))
    ):
        report.append(f"the chosen JPEG was refused: {said[-1:]!r}")
        return
    pump(20)
    say()
    say("-- a JPEG is chosen for it, then the playlist is deleted --")
    say(f"the window said: {said[-1]!r}")
    say(f"cover store before the deletion: {store_state()}")
    if not rail_covers(window).get(RENAMED):
        report.append("the rail drew no image for the JPEG cover")
    wait_for_quiet(window)
    if not open_playlist(window, RENAMED):
        return
    menu = open_page_menu(window, "to delete it")
    if menu is None:
        return
    if not press_in_menu(menu, "Delete", "to delete it"):
        return
    dialog = dialog_on(window)
    if dialog is None:
        report.append("Delete… opened nothing")
        return
    say(f"the confirmation reads: {dialog.get_heading()!r} / {dialog.get_body()!r}")
    shot(window, "11-deleting-it.png")
    dialog.emit("response", "remove")
    dialog.force_close()
    pump(40)
    say(f"the window said: {said[-1]!r}")
    say(f"playlist files here: {here_state()}")
    say(f"cover store now holds: {store_state() or 'no .covers folder'}")
    if gui.playlist_custom_cover(PLAYLISTS_HERE / f"{RENAMED}.m3u") is not None:
        report.append("deleting the playlist left its custom cover behind")
    window.show_view("playlists")
    window.set_focus(None)
    shot(window, "12-deleted-and-the-cover-with-it.png")

    # ---------------------------------------------- and a name reused afterwards
    remade = gui.create_local_playlist(
        PLAYLISTS_HERE, RENAMED, [str(p) for p in sorted(HOME.glob("Music/*/*/*.mp3"))[:1]]
    )
    window._populate_playlist_rail()
    pump(20)
    say()
    say(f"a new playlist called {RENAMED} is made afterwards")
    say(f"it is painted with: {art_for(window, RENAMED)!r}")
    if remade is None or gui.playlist_custom_cover(remade) is not None:
        report.append("a new playlist of the same name inherited the old cover")

    say()
    say(f"nothing on the iPod's volume root changed: "
        f"{sorted(p.name for p in VOLUME.iterdir())}")
    say(f"every toast the run produced: {said}")
    (OUT / "transcript.txt").write_text("\n".join(transcript) + "\n", encoding="utf-8")
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
    application_id="io.github.max_miller1204.IpodShuffle.CustomCover",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(300, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
print("the window did what the transcript says", file=sys.stderr)
