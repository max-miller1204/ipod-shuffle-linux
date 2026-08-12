#!/usr/bin/env python3
"""What lands on the iPod when the playlist wearing a custom cover is synced.

The claim under test is that artwork is a thing this computer keeps: the cover
chosen for a playlist is copied into the local playlist library, and nothing
carries it onto the device. So this puts a custom cover on a playlist in the
shipped window, queues that playlist with Send to iPod, presses Sync, and then
walks the volume the real `ipod-sync.sh` has just written to and reports every
file on it.

Then the other way in: `ipod-sync.sh` run from a terminal against the whole
music folder, which is the folder the cover store sits inside. A user who
syncs their library wholesale is the case where an image could be dragged
along by accident, and the volume is walked again afterwards.

    driver-nothing-reaches-the-ipod.py --repo R --demo D --out O

Needs a display; run it under gtk4-broadwayd with GDK_BACKEND=broadway on a
machine with no X server.
"""

import argparse
import hashlib
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
MUSIC = HOME / "Music"
VOLUME = DEMO / "MAX SHUFFLE"
PLAYLISTS_HERE = MUSIC / "Playlists"
PICTURES = HOME / "Pictures"

os.environ.setdefault(
    "IPOD_VENV_PYTHON", str(Path.home() / "ipod-tools" / "venv" / "bin" / "python")
)
os.environ.setdefault(
    "IPOD_DB_TOOL",
    str(Path.home() / "ipod-tools" / "IPod-Shuffle-4g" / "ipod-shuffle-4g.py"),
)
os.environ["HOME"] = str(HOME)
os.environ["XDG_CONFIG_HOME"] = str(HOME / ".config")
os.environ["XDG_CACHE_HOME"] = str(HOME / ".cache")
os.environ["XDG_DATA_HOME"] = str(HOME / ".local/share")
os.environ["FAKE_IPOD_MOUNT"] = str(VOLUME)
# The real encoder, before tests/bin's stub shadows it on the PATH.
FFMPEG = shutil.which("ffmpeg")
if FFMPEG is None:
    raise SystemExit("ffmpeg is needed to draw the image this chooses")
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
    raise SystemExit("no display: run this under broadway or a nested X server")
Gtk.Settings.get_default().props.gtk_enable_animations = False

report = []
transcript = []
said = []

PLAYLIST = "Downloads"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


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


def wait_idle(window, seconds=180):
    for _ in range(int(seconds * 8)):
        pump(2)
        if not window.busy:
            pump(10)
            return True
    return False


def shot(widget, name):
    node = None
    for _ in range(40):
        pump(5)
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


def volume_files():
    """Every file on the device, as the paths a user would see on the volume."""
    return sorted(
        str(path.relative_to(VOLUME))
        for path in VOLUME.rglob("*")
        if path.is_file()
    )


def digests(paths):
    found = {}
    for path in paths:
        found.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), []).append(
            str(path)
        )
    return found


def images_on_the_device():
    """Anything on the volume that is an image, by name or by content.

    Both, because a cover that reached the device under some other name would
    pass a check that only reads suffixes; the bytes of the image that was
    chosen are looked for as well.
    """
    by_name = [
        name for name in volume_files() if Path(name).suffix.lower() in IMAGE_SUFFIXES
    ]
    wanted = set(digests(sorted(PICTURES.glob("*.png"))))
    wanted |= set(digests(sorted(PICTURES.glob("*.webp"))))
    by_content = [
        name
        for name in volume_files()
        if hashlib.sha256((VOLUME / name).read_bytes()).hexdigest() in wanted
    ]
    return by_name, by_content


def store_state():
    folder = PLAYLISTS_HERE / ".covers"
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir())


def press_labelled(widget, text, what):
    for button in buttons_under(widget):
        if (button.get_label() or "") == text or text in labels(button):
            if not button.get_sensitive():
                report.append(f"the {text!r} button is insensitive {what}")
                return None
            button.emit("clicked")
            pump(30)
            return button
    report.append(f"no button reading {text!r} to press {what}")
    return None


def open_playlist(window, name):
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
        report.append(f"no {name!r} on the rail")
        return False
    row.emit("clicked")
    pump(20)
    window.set_focus(None)
    pump(10)
    return True


def draw_cover(path, colour, first, second):
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
    say("== what reaches the iPod when a covered playlist is synced ==")
    say(f"iPod the app found for itself: {window.mount_point}")
    before_files = volume_files()
    say(f"files on the volume before anything: {len(before_files)}")
    say()

    cover = draw_cover(
        PICTURES / "night-drive.png", "0x1F1147", "CUSTOM", "chosen PNG"
    )
    if not open_playlist(window, PLAYLIST):
        return
    staged_before = {
        source: sorted(members)
        for source, members in sorted(window.pending_sources.items())
    }
    if not window._playlist_cover_chosen(
        PLAYLIST, Gio.File.new_for_path(str(cover))
    ):
        report.append(f"the cover was refused: {said[-1:]!r}")
        return
    pump(20)
    staged_after = {
        source: sorted(members)
        for source, members in sorted(window.pending_sources.items())
    }
    say("-- the cover, chosen in the window --")
    say(f"the window said: {said[-1]!r}")
    say(f"cover store on this computer: {store_state()}")
    say(f"staged for the next sync before choosing it: {staged_before}")
    say(f"staged for the next sync after choosing it:  {staged_after}")
    if staged_before != staged_after:
        report.append("choosing a cover changed what the sync would carry")
    say()

    # ------------------------------------------------ Send to iPod, then Sync
    if not open_playlist(window, PLAYLIST):
        return
    if press_labelled(window.playlist_actions, "Send to iPod", "on the playlist") is None:
        return
    pump(30)
    say("-- pressed Send to iPod, then Sync --")
    say(f"the window said: {said[-1]!r}")
    say("queued sources, which is exactly what the sync is given:")
    for source in sorted(window.pending_sources):
        say(f"  {source}")
    if any(
        Path(source).suffix.lower() in IMAGE_SUFFIXES
        for source in window.pending_sources
    ):
        report.append("an image was staged for the sync")
    if any(
        gui.PLAYLIST_COVERS_FOLDER in member
        for members in window.pending_sources.values()
        for member in members
    ):
        report.append("something out of the cover store was staged for the sync")

    if not window.sync_button.get_sensitive():
        report.append(f"the Sync button is insensitive: {window.sync_button.get_label()!r}")
        return
    say(f"the Sync button reads: {window.sync_button.get_label()!r}")
    window.sync_button.emit("clicked")
    pump(20)
    if not wait_idle(window):
        report.append("the sync never finished")
        return
    pump(60)
    say(f"the window said: {said[-1]!r}")
    window.show_view("playlists")
    window.set_focus(None)
    pump(20)
    shot(window, "13-synced-with-the-cover-still-here.png")
    say()

    log = window.log_view.get_buffer()
    (OUT / "sync-log-from-the-window.txt").write_text(
        log.get_text(log.get_start_iter(), log.get_end_iter(), False),
        encoding="utf-8",
    )
    say("the script's own output is in sync-log-from-the-window.txt")

    after_files = volume_files()
    named, matching = images_on_the_device()
    say("-- what the iPod is holding afterwards --")
    for name in after_files:
        say(f"  {name}")
    say(f"image files on the device, by suffix: {named}")
    say(f"files on the device matching the chosen image byte for byte: {matching}")
    if named or matching:
        report.append(f"artwork reached the iPod: {named or matching}")
    say(f"the cover is still on this computer: {store_state()}")
    device_list = VOLUME / f"{PLAYLIST}.m3u"
    if device_list.is_file():
        say(f"the playlist the device got, {device_list.name}:")
        for line in device_list.read_text(encoding="utf-8").splitlines():
            say(f"  {line}")
    else:
        report.append(f"the sync put no {PLAYLIST}.m3u on the device")
    say()

    # ------------------------------- and the whole music folder, from a terminal
    say("-- ipod-sync.sh, run against the whole music folder --")
    say(f"which is the folder the cover store lives in: {PLAYLISTS_HERE / '.covers'}")
    command = [
        str(REPO / "ipod-sync.sh"),
        "--ipod", str(VOLUME),
        "--yes",
        "--playlist-voiceover",
        "--",
        str(MUSIC),
    ]
    say(f"$ {' '.join(command)}")
    finished = subprocess.run(
        command, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    (OUT / "sync-the-whole-music-folder.txt").write_text(
        f"$ {' '.join(command)}\n{finished.stdout}{finished.stderr}",
        encoding="utf-8",
    )
    say(f"exit code: {finished.returncode}")
    if finished.returncode != 0:
        report.append(f"the terminal sync failed: {finished.stderr.strip()[-400:]}")
    named, matching = images_on_the_device()
    say(f"files on the device now: {len(volume_files())}")
    say(f"image files on the device, by suffix: {named}")
    say(f"files matching the chosen image byte for byte: {matching}")
    if named or matching:
        report.append(f"a wholesale sync carried artwork onto the iPod: {named or matching}")
    say(f"the cover store is untouched on this computer: {store_state()}")

    (OUT / "device-inventory.txt").write_text(
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
    application_id="io.github.max_miller1204.IpodShuffle.CoverStaysHere",
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
GLib.timeout_add_seconds(600, lambda: report.append("timed out") or app.quit())
app.run([])

if report:
    for line in report:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)
print("nothing on the device is artwork", file=sys.stderr)
