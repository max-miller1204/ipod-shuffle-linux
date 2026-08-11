#!/usr/bin/env python3
"""What pressing Rename… does to a playlist the iPod is holding.

Runs the shipped window against a demo device `tools/demo-library.py` built and
`ipod-sync.sh` actually wrote to, and reaches the rename the way a user does:
the Playlists view, the playlist on the rail, the `⋯` on its own page, the
Rename… row inside the menu that opens. Nothing about the speech engine is
stubbed - `has_speech_engine` asks the PATH it is given, so a run under a PATH
with the engines taken out of it is a machine that has none.

Accepting the dialog is left to run: `_remove_device_playlist` spawns the real
`ipod-remove.sh` against the demo volume, so what the volume root holds
afterwards is what an iPod would be left holding.

    driver-rename-refusal.py --repo R --demo D --out O --label L \
        --playlist "Morning Ride" --to "Evening Ride" [--no-accept]

Run it with GDK_BACKEND=x11 under a nested server: a Wayland compositor stops
sending frames to a surface nobody is looking at, and a window that is not
being drawn snapshots to nothing.
"""

import argparse
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", required=True)
parser.add_argument("--demo", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--playlist", required=True)
parser.add_argument("--to", default="")
parser.add_argument("--no-accept", action="store_true")
parser.add_argument(
    "--menu-only",
    action="store_true",
    help="read back what the playlist's ⋯ offers and press nothing",
)
parser.add_argument("--shot", default=None, help="basename of the PNG to write")
args = parser.parse_args()

REPO = Path(args.repo).resolve()
DEMO = Path(args.demo).resolve()
OUT = Path(args.out).resolve()
LABEL = args.label
OUT.mkdir(parents=True, exist_ok=True)

HOME = DEMO / "home"
VOLUME = DEMO / "MAX SHUFFLE"
PLAYLISTS_HERE = HOME / "Music" / "Playlists"

os.environ.setdefault(
    "IPOD_VENV_PYTHON", str(Path.home() / "ipod-tools" / "venv" / "bin" / "python")
)
os.environ["HOME"] = str(HOME)
os.environ["XDG_CONFIG_HOME"] = str(HOME / ".config")
os.environ["XDG_CACHE_HOME"] = str(HOME / ".cache")
os.environ["XDG_DATA_HOME"] = str(HOME / ".local/share")
# The stub findmnt in tests/bin reports this volume as the mounted iPod, which
# is what makes the device in these shots one the app found for itself.
os.environ["FAKE_IPOD_MOUNT"] = str(VOLUME)
os.environ["PATH"] = f"{REPO / 'tests' / 'bin'}:{os.environ['PATH']}"

sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk  # noqa: E402

Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit("no display: run this under a nested X server or xvfb")

report = []
transcript = []
said = []


def say(line=""):
    transcript.append(line)


def pump(turns=40):
    context = GLib.MainContext.default()
    for _ in range(turns):
        while context.pending():
            context.iteration(False)
        GLib.usleep(15000)


def walk(widget):
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


def settle(window, seconds=40):
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


def shot(widget, name):
    """A PNG of what is on screen, as the window paints it."""
    node = None
    for _ in range(40):
        pump(6)
        width, height = widget.get_width(), widget.get_height()
        if not width or not height:
            continue
        paintable = Gtk.WidgetPaintable.new(widget)
        snapshot = Gtk.Snapshot.new()
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


def open_row_menu(row_widget, what):
    opener = next(
        (found for found in walk(row_widget) if isinstance(found, Gtk.MenuButton)),
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
            pump(10)
            return True
    report.append(f"the menu {what} offered no {text!r}: {labels(menu)}")
    menu.popdown()
    return False


def dialog_on(window):
    dialog = window.get_visible_dialog()
    return dialog if isinstance(dialog, Adw.Dialog) else None


def entry_in(dialog):
    for found in walk(dialog.get_extra_child()):
        if isinstance(found, Adw.EntryRow):
            return found
    return None


def volume_state():
    """What the iPod is holding, read off the volume rather than from the app."""
    playlists = sorted(
        path.name for path in VOLUME.iterdir() if path.suffix.lower() == ".m3u"
    )
    spoken = VOLUME / "iPod_Control/Speakable/Playlists"
    recordings = (
        sorted(path.name for path in spoken.iterdir()) if spoken.is_dir() else []
    )
    return playlists, recordings


def here_state():
    return sorted(path.name for path in PLAYLISTS_HERE.iterdir())


def log_text(window):
    buf = window.log_view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def wait_idle(window, seconds=90):
    for _ in range(int(seconds * 8)):
        pump(2)
        if not window.busy:
            pump(10)
            return True
    return False


# ------------------------------------------------------------------ the scene


def scene(window):
    real_add_toast = window.toasts.add_toast

    def add_toast(toast):
        said.append(toast.get_title())
        return real_add_toast(toast)

    window.toasts.add_toast = add_toast

    speaks = window.speech_engine_available
    say(f"== {LABEL} ==")
    say(f"this machine {'has' if speaks else 'has no'} speech engine")
    say(f"has_speech_engine() answered: {speaks}")
    say(f"playlist_unavailable: {window.playlist_unavailable!r}")
    if not settle(window):
        report.append("the device and the library never both arrived")
    say(f"iPod the app found for itself: {window.mount_point}")
    say(f"device identity it resolved: {window.device_identity}")
    say(f"playlists it read off the device: {[n for n, _entries in window.playlists]}")
    say(f"playlists here: {[p.name for p in window.local_playlists]}")

    playlist = window._local_playlist(args.playlist)
    if playlist is None and not args.menu_only:
        report.append(f"no playlist here called {args.playlist!r}")
        return
    if playlist is None:
        say(
            f"{args.playlist!r}: no file here backs it, "
            f"on the iPod: {window._playlist_on_device(args.playlist)}"
        )
    else:
        say(
            f"{args.playlist!r}: lists {len(playlist.entries)} track(s), "
            f"on the iPod: {window._playlist_on_device(args.playlist)}"
        )
    before_volume, before_spoken = volume_state()
    say()
    say("-- before the press --")
    say(f"playlists on the iPod's volume root: {before_volume}")
    say(f"spoken name recordings on the device: {len(before_spoken)}")
    say(f"playlist files here: {here_state()}")
    say(f"queued for the next sync: {sorted(window.pending_sources)}")
    say()

    # The press, reached the way a user reaches it.
    window.show_view("playlists")
    window._populate_playlist_rail()
    pump(60)
    row = next(
        (
            button
            for button in buttons_under(window.playlist_list)
            if labels(button) and labels(button)[0] == args.playlist
        ),
        None,
    )
    if row is None:
        report.append(
            f"no {args.playlist!r} on the rail: "
            f"{[labels(b)[:1] for b in buttons_under(window.playlist_list)]}"
        )
        return
    row.emit("clicked")
    pump(20)
    window.set_focus(None)
    pump(10)

    menu = open_row_menu(window.playlist_actions, "on the playlist's page")
    if menu is None:
        return
    say(f"the ⋯ on {args.playlist!r} offers: {labels(menu)}")
    if args.menu_only:
        # The menu itself is the subject: a popover is its own surface, so it
        # is snapshotted rather than the window behind it.
        shot(menu, args.shot or f"{LABEL}-menu.png")
        menu.popdown()
        pump(4)
        return
    if not press_in_menu(menu, "Rename", "on the playlist's page"):
        return
    say("pressed: Rename…")
    say()

    dialog = dialog_on(window)
    if dialog is None:
        say("-- nothing opened: the press was refused --")
        say(f"the window said: {said[-1]!r}" if said else "the window said nothing")
        shot(window, args.shot or f"{LABEL}-refused.png")
    else:
        say("-- the confirmation it opened --")
        say(f"heading: {dialog.get_heading()!r}")
        say(f"body: {dialog.get_body()!r}")
        shot(window, args.shot or f"{LABEL}-confirmation.png")
        if args.no_accept:
            dialog.emit("response", "cancel")
            dialog.force_close()
            pump(10)
            say("cancelled")
        else:
            field = entry_in(dialog)
            if field is None:
                report.append("the rename dialog carries no name field")
                return
            field.set_text(args.to)
            pump(10)
            say(f"typed the new name: {args.to!r}")
            dialog.emit("response", "rename")
            dialog.force_close()
            pump(20)
            if not wait_idle(window):
                report.append("the window never stopped being busy")
            pump(40)
            say("accepted")
            # What the window shows once the press has finished, which for a
            # playlist the removal reached is the dot beside it changing.
            window.show_view("playlists")
            window.set_focus(None)
            shot(window, f"{LABEL}-outcome.png")
    say()

    after_volume, after_spoken = volume_state()
    say("-- after the press --")
    say(f"playlists on the iPod's volume root: {after_volume}")
    say(f"spoken name recordings on the device: {len(after_spoken)}")
    say(f"playlist files here: {here_state()}")
    say(f"queued for the next sync: {sorted(window.pending_sources)}")
    say(f"what the window said: {said}")
    gone = [name for name in before_volume if name not in after_volume]
    say(f"taken off the iPod by this press: {gone or 'nothing'}")
    output = log_text(window).strip()
    say()
    say("-- the app's own output pane --")
    say(output or "(empty: no script was run)")


def on_activate(app):
    gui.load_css()
    window = gui.IpodWindow(application=app)
    window.set_default_size(1180, 760)
    window.present()

    def go():
        try:
            scene(window)
        except Exception:  # noqa: BLE001
            import traceback

            report.append(traceback.format_exc())
        (OUT / f"{LABEL}-transcript.txt").write_text(
            "\n".join(transcript) + "\n", encoding="utf-8"
        )
        print("\n".join(transcript))
        app.quit()
        return False

    GLib.timeout_add(700, go)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.RenameRefusal",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(240, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
