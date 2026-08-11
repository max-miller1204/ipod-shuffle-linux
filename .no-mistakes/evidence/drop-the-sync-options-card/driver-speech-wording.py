#!/usr/bin/env python3
"""The pages and dialogs that say what a missing speech engine costs.

Run twice against the same demo device - once on this machine, which has
pico2wave, and once with a PATH those engines have been taken out of. Nothing
about the speech engine is stubbed: `has_speech_engine` asks the PATH it is
given, exactly as it does for a user, so the two runs are two machines.

Reaches the dialogs by pressing what a user presses - the sidebar's footer
button, a playlist on the rail, the `⋯` on its page, the row inside the menu
that opens - so the sentences read back are the ones those presses produced.

    driver-speech-wording.py <repo> <demo-root> <out-dir> <label>

Run it with GDK_BACKEND=x11 under a nested server: a Wayland compositor stops
sending frames to a surface nobody is looking at, and a window that is not
being drawn snapshots to nothing.
"""

import os
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
DEMO = Path(sys.argv[2]).resolve()
OUT = Path(sys.argv[3]).resolve()
LABEL = sys.argv[4]
OUT.mkdir(parents=True, exist_ok=True)

HOME = DEMO / "home"
VOLUME = DEMO / "MAX SHUFFLE"
# The tags are what put a cover on an album and on the playlist that carries
# it, so the reader has to keep finding the virtualenv install.sh keeps mutagen
# in. Read before HOME moves, and left alone if the environment already names
# one.
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


def settle(window, seconds=30):
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


def press_labelled(widget, text, what):
    for button in buttons_under(widget):
        if (button.get_label() or "") == text or text in labels(button):
            button.emit("clicked")
            pump()
            return button
    report.append(f"no button reading {text!r} to press {what}")
    return None


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


def close_dialog(dialog, response="cancel"):
    dialog.emit("response", response)
    dialog.force_close()
    pump(10)


def dialog_text(dialog):
    heading = dialog.get_heading() if hasattr(dialog, "get_heading") else ""
    body = dialog.get_body() if hasattr(dialog, "get_body") else ""
    return heading, body


# ------------------------------------------------------------------ the scene


def scene(window):
    speaks = window.speech_engine_available
    say(f"== this machine {'has' if speaks else 'has no'} speech engine ==")
    say(f"has_speech_engine() answered: {speaks}")
    say(f"the flags every sync is run with: {window._sync_options()}")
    if not settle(window):
        report.append("the device and the library never both arrived")
    say(f"iPod the app found for itself: {window.mount_point}")
    say(f"playlists it read off the device: {sorted(window.spoken)}")
    say()

    # Device & Settings, reached from the sidebar's footer the way it is
    # reached in the app. This is the page the Sync options card and the three
    # add buttons were on. The rail is drawn first because its covers are read
    # off the files on a worker, and a shot taken before that lands shows a
    # sidebar of grey squares the user only sees for an instant.
    window.show_view("playlists")
    window._populate_playlist_rail()
    pump(60)
    footer_button = window.nav_buttons["settings"]
    footer_button.emit("clicked")
    pump(20)
    # The search entry takes the focus when the window opens, and a focus ring
    # in the shot reads as something the user clicked on.
    window.set_focus(None)
    pump(10)
    page = window.views.get_child_by_name("settings")
    say("== Device & Settings, pressed in the sidebar ==")
    say(f"cards on the page: {[t for t in card_titles(page)]}")
    say(f"buttons on the page: {sorted(page_buttons(page))}")
    shot(window, f"{LABEL}-01-device-settings.png")

    warning = warning_card(page)
    if speaks and warning is not None:
        report.append("a machine that can speak was warned that it cannot")
    if not speaks:
        if warning is None:
            report.append("the page of a speechless machine carries no warning")
        else:
            say()
            say("== the warning card, on its own ==")
            say(f"it reads: {labels(warning)[0]!r}")
            shot(warning, f"{LABEL}-02-speech-warning-card.png")
    say()

    # Every dialog whose answer rebuilds the device, reached by pressing.
    window.show_view("playlists")
    pump(10)
    row = next(
        (
            button
            for button in buttons_under(window.playlist_list)
            if labels(button) and labels(button)[0] == "Morning Ride"
        ),
        None,
    )
    if row is None:
        report.append(
            "no Morning Ride on the rail: "
            f"{[labels(b)[:1] for b in buttons_under(window.playlist_list)]}"
        )
        return
    row.emit("clicked")
    pump(20)

    scenes = (
        ("Rename", "03-rename-a-playlist-on-the-ipod"),
        ("Delete", "04-delete-a-playlist-that-is-on-the-ipod"),
    )
    for offered, name in scenes:
        menu = open_row_menu(window.playlist_actions, "on a playlist page")
        if menu is None or not press_in_menu(menu, offered, "on a playlist page"):
            continue
        dialog = dialog_on(window)
        if dialog is None:
            report.append(f"{offered} opened no dialog")
            continue
        heading, body = dialog_text(dialog)
        say(f"== {offered}, pressed on a playlist that is on the iPod ==")
        say(f"heading: {heading!r}")
        say(f"body: {body!r}")
        shot(window, f"{LABEL}-{name}.png")
        close_dialog(dialog)
        say()

    # The track's own Remove, which is a button on the row rather than a menu
    # entry: the album this device was synced from, opened from the grid.
    window.show_view("library")
    pump(10)
    card = next(
        (
            button
            for button in buttons_under(window.album_flow)
            if "Warm Ridge" in labels(button)
        ),
        None,
    )
    if card is None:
        report.append("no Warm Ridge card in the grid to open")
        return
    card.emit("clicked")
    pump(20)
    if press_labelled(window.album_tracks, "Remove", "on a track on the iPod"):
        dialog = dialog_on(window)
        if dialog is None:
            report.append("Remove opened no dialog")
        else:
            heading, body = dialog_text(dialog)
            say("== Remove, pressed on a track that is on the device ==")
            say(f"heading: {heading!r}")
            say(f"body: {body!r}")
            shot(window, f"{LABEL}-05-remove-a-track-from-the-ipod.png")
            close_dialog(dialog)


def card_titles(page):
    for widget in walk(page):
        if isinstance(widget, Gtk.Label) and widget.has_css_class("sf-row-title"):
            yield widget.get_text()


def page_buttons(page):
    return {
        (button.get_label() or (labels(button) or [""])[0]).strip()
        for button in buttons_under(page)
    }


def warning_card(page):
    for widget in walk(page):
        if not isinstance(widget, Gtk.Label):
            continue
        card = widget.get_parent()
        if card is None or not card.has_css_class("sf-warn-card"):
            continue
        if card.get_visible() and "speech engine" in widget.get_text().lower():
            return card
    return None


def on_activate(app):
    # The app's own stylesheet, loaded here because IpodApp.do_activate is
    # what loads it and this driver stands in for that. Without it the cards
    # and the warning lose their backgrounds, and the shot is of a window the
    # user never sees.
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
    application_id="io.github.max_miller1204.IpodShuffle.SpeechWording",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(180, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
