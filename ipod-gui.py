#!/usr/bin/env python3
"""GTK4 front end for the iPod shuffle 4G scripts.

Drives ipod-sync.sh and ipod-wipe.sh rather than reimplementing their logic,
so the command line and the GUI cannot drift apart. Launch it via
./ipod-gui.sh, which picks an interpreter that has the GTK bindings.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

APP_ID = "io.github.max_miller1204.IpodShuffle"
SCRIPT_DIR = Path(__file__).resolve().parent
SYNC_SCRIPT = SCRIPT_DIR / "ipod-sync.sh"
WIPE_SCRIPT = SCRIPT_DIR / "ipod-wipe.sh"


def _tag_interpreter():
    """Pick an interpreter that can import mutagen, or None.

    Inside a Flatpak everything shares one environment, so this interpreter
    will do. On a normal install PyGObject belongs to the system Python while
    mutagen lives in install.sh's virtualenv, and tag reading has to cross
    over to it.
    """
    try:
        import mutagen  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    venv = Path(
        os.environ.get("IPOD_VENV_PYTHON")
        or Path.home() / "ipod-tools" / "venv" / "bin" / "python"
    )
    return str(venv) if venv.exists() else None


TAG_PYTHON = None  # resolved lazily on first use

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav"}

_TAG_READER = """
import json, sys
from pathlib import Path
try:
    import mutagen
except ImportError:
    print("{}")
    sys.exit(0)

root = Path(sys.argv[1])
tags = {}
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        continue
    if audio is None:
        continue
    title = (audio.get("title") or [None])[0]
    artist = (audio.get("artist") or [None])[0]
    if title or artist:
        tags[str(path.relative_to(root))] = [title, artist]
print(json.dumps(tags))
"""


def read_tags(mount_point):
    """Map each track's relative path to (title, artist).

    Returns an empty dict when the venv or mutagen is unavailable, in which
    case the interface falls back to showing filenames.
    """
    global TAG_PYTHON
    if TAG_PYTHON is None:
        TAG_PYTHON = _tag_interpreter()

    music = Path(mount_point, "iPod_Control", "Music")
    if TAG_PYTHON is None or not music.is_dir():
        return {}
    try:
        result = subprocess.run(
            [TAG_PYTHON, "-c", _TAG_READER, str(music)],
            capture_output=True, text=True, timeout=60,
        )
        return json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def find_ipod():
    """Return the mount point of a connected iPod, or None.

    Uses findmnt's JSON output because raw mode escapes spaces as \\x20 and
    iPod names very often contain one.
    """
    try:
        out = subprocess.run(
            ["findmnt", "-no", "TARGET", "-t", "vfat", "--json"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        filesystems = json.loads(out).get("filesystems", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None

    for entry in filesystems:
        target = entry.get("target")
        if target and Path(target, "iPod_Control").is_dir():
            return target
    return None


def device_for(mount_point):
    """Block device backing a mount point, e.g. /dev/sda."""
    try:
        return subprocess.run(
            ["findmnt", "-rno", "SOURCE", "--target", mount_point],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def udisks_filesystem_call(device, method):
    """Invoke a UDisks2 Filesystem method on a block device.

    Speaks D-Bus directly rather than shelling out to udisksctl, which the
    Flatpak runtime does not ship. Both reach the same daemon and the same
    polkit check, which grants removable media to the logged-in user.

    Returns (ok, detail), where detail is the mount path for Mount and the
    error message on failure.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = bus.call_sync(
            "org.freedesktop.UDisks2",
            f"/org/freedesktop/UDisks2/block_devices/{Path(device).name}",
            "org.freedesktop.UDisks2.Filesystem", method,
            GLib.Variant("(a{sv})", ({},)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
    except GLib.Error as exc:
        return False, exc.message
    unpacked = result.unpack()
    return True, (unpacked[0] if unpacked else "")


def unmounted_vfat_devices():
    """Removable vfat volumes that are not currently mounted.

    HintSystem filters out internal disks, so a click on Mount cannot reach
    for the EFI system partition, which is also vfat.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = bus.call_sync(
            "org.freedesktop.UDisks2", "/org/freedesktop/UDisks2",
            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, None, Gio.DBusCallFlags.NONE, -1, None,
        )
    except GLib.Error:
        return []

    devices = []
    for path, interfaces in result.unpack()[0].items():
        filesystem = interfaces.get("org.freedesktop.UDisks2.Filesystem")
        block = interfaces.get("org.freedesktop.UDisks2.Block")
        if filesystem is None or block is None:
            continue
        if filesystem.get("MountPoints"):
            continue
        if block.get("IdType") != "vfat" or block.get("HintSystem"):
            continue
        devices.append(f"/dev/{path.rsplit('/', 1)[-1]}")
    return devices


def has_speech_engine():
    """Whether any text-to-speech engine the builder recognises is installed."""
    return any(shutil.which(engine) for engine in ("pico2wave", "espeak", "say"))


def count_tracks(mount_point):
    music = Path(mount_point, "iPod_Control", "Music")
    if not music.is_dir():
        return 0
    return sum(1 for p in music.rglob("*") if p.is_file())


def list_tracks(mount_point, limit=500):
    """Relative paths of the tracks on the device, sorted."""
    music = Path(mount_point, "iPod_Control", "Music")
    if not music.is_dir():
        return []
    files = sorted(p for p in music.rglob("*") if p.is_file())
    return [str(p.relative_to(music)) for p in files[:limit]]


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


class IpodWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("iPod Shuffle")
        self.set_default_size(620, 720)

        self.mount_point = None
        self.busy = False
        self.track_rows = {}
        self.tag_generation = 0

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        toolbar = Adw.ToolbarView()
        self.toasts.set_child(toolbar)

        header = Adw.HeaderBar()
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Rescan for a connected iPod")
        self.refresh_button.connect("clicked", lambda _b: self.refresh())
        header.pack_start(self.refresh_button)
        toolbar.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)

        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_device_page(), "device")

        # React to the device being plugged in or unplugged, rather than making
        # the user press refresh. Polling would work but wastes wakeups.
        self.monitor = Gio.VolumeMonitor.get()
        for signal in ("mount-added", "mount-removed"):
            self.monitor.connect(signal, lambda *_a: GLib.idle_add(self.refresh))

        self.refresh()

    def _build_empty_page(self):
        page = Adw.StatusPage(
            icon_name="multimedia-player-symbolic",
            title="No iPod Connected",
            description="Plug in an iPod shuffle and it will appear here.\n"
                        "If it is already connected, it may need mounting.",
        )
        button = Gtk.Button(label="Mount Connected iPod")
        button.set_halign(Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.add_css_class("suggested-action")
        button.connect("clicked", self.on_mount_clicked)
        page.set_child(button)
        return page

    def _build_device_page(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=18,
            margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
        )
        # Keep the content column readable when the window is wide, instead of
        # stretching rows across the whole screen. Standard libadwaita layout.
        clamp = Adw.Clamp(maximum_size=680, tightening_threshold=600)
        clamp.set_child(box)
        scroller.set_child(clamp)

        info_group = Adw.PreferencesGroup(title="Device")
        self.name_row = Adw.ActionRow(title="Name")
        self.tracks_row = Adw.ActionRow(title="Tracks")
        self.space_row = Adw.ActionRow(title="Storage")
        for row in (self.name_row, self.tracks_row, self.space_row):
            row.add_css_class("property")
            info_group.add(row)
        box.append(info_group)

        actions = Adw.PreferencesGroup(title="Actions")

        self.add_row = Adw.ActionRow(
            title="Add Music",
            subtitle="Copy a folder onto the iPod and rebuild its database",
        )
        add_button = Gtk.Button(label="Choose Folder…", valign=Gtk.Align.CENTER)
        add_button.add_css_class("suggested-action")
        add_button.connect("clicked", self.on_add_music)
        self.add_row.add_suffix(add_button)
        self.add_row.set_activatable_widget(add_button)
        actions.add(self.add_row)

        self.rebuild_row = Adw.ActionRow(
            title="Rebuild Database",
            subtitle="Re-scan the iPod if tracks are not playing",
        )
        rebuild_button = Gtk.Button(label="Rebuild", valign=Gtk.Align.CENTER)
        rebuild_button.connect("clicked", self.on_rebuild)
        self.rebuild_row.add_suffix(rebuild_button)
        self.rebuild_row.set_activatable_widget(rebuild_button)
        actions.add(self.rebuild_row)

        self.wipe_row = Adw.ActionRow(
            title="Wipe iPod",
            subtitle="Remove every track, with an optional backup first",
        )
        wipe_button = Gtk.Button(label="Wipe…", valign=Gtk.Align.CENTER)
        wipe_button.add_css_class("destructive-action")
        wipe_button.connect("clicked", self.on_wipe)
        self.wipe_row.add_suffix(wipe_button)
        self.wipe_row.set_activatable_widget(wipe_button)
        actions.add(self.wipe_row)

        self.eject_row = Adw.ActionRow(
            title="Eject",
            subtitle="Unmount safely before unplugging",
        )
        eject_button = Gtk.Button(label="Eject", valign=Gtk.Align.CENTER)
        eject_button.connect("clicked", self.on_eject)
        self.eject_row.add_suffix(eject_button)
        self.eject_row.set_activatable_widget(eject_button)
        actions.add(self.eject_row)

        box.append(actions)

        options = Adw.PreferencesGroup(
            title="Options",
            description="Applied when adding music or rebuilding the database",
        )

        # Playlist names are stored only as spoken audio, never as text, since
        # the device has no screen. Choosing a grouping therefore implies
        # wanting the names read aloud, so that switch follows along.
        self.playlist_mode = Adw.ComboRow(
            title="Playlists",
            subtitle="How to group tracks into playlists",
            model=Gtk.StringList.new(
                ["None", "One per folder", "By artist", "By genre"]
            ),
        )
        self.playlist_mode.connect("notify::selected", self._on_playlist_mode_changed)
        options.add(self.playlist_mode)

        self.track_voiceover = Adw.SwitchRow(
            title="Speak track names",
            subtitle="Announce the current track when you press the VoiceOver button",
        )
        options.add(self.track_voiceover)

        self.playlist_voiceover = Adw.SwitchRow(
            title="Speak playlist names",
            subtitle="Without this, playlists cannot be told apart on a screenless device",
        )
        options.add(self.playlist_voiceover)

        if not has_speech_engine():
            for row in (self.track_voiceover, self.playlist_voiceover):
                row.set_sensitive(False)
                row.set_subtitle("No speech engine installed")

        box.append(options)

        self.progress = Gtk.ProgressBar(visible=False, show_text=True)
        box.append(self.progress)

        self.tracks_group = Adw.PreferencesGroup(title="On the iPod")
        self.tracks_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.tracks_list.add_css_class("boxed-list")
        self.tracks_group.add(self.tracks_list)
        box.append(self.tracks_group)

        self.log_expander = Adw.ExpanderRow(title="Output", subtitle="Details of the last operation")
        log_group = Adw.PreferencesGroup()
        log_group.add(self.log_expander)
        self.log_view = Gtk.TextView(
            editable=False, monospace=True, cursor_visible=False,
            left_margin=12, right_margin=12, top_margin=8, bottom_margin=8,
        )
        log_scroller = Gtk.ScrolledWindow(min_content_height=180, vexpand=False)
        log_scroller.set_child(self.log_view)
        self.log_expander.add_row(log_scroller)
        box.append(log_group)

        return scroller

    # ---------------------------------------------------------------- state

    def refresh(self):
        """Re-detect the iPod and repaint. Safe to call from the main loop."""
        if self.busy:
            return False

        self.mount_point = find_ipod()
        if self.mount_point is None:
            self.stack.set_visible_child_name("empty")
            return False

        self.stack.set_visible_child_name("device")

        label = Path(self.mount_point).name
        self.name_row.set_subtitle(label)

        track_total = count_tracks(self.mount_point)
        self.tracks_row.set_subtitle(str(track_total))

        try:
            usage = shutil.disk_usage(self.mount_point)
            # Report used rather than free. On a nearly empty 2 GB device both
            # free and total round to the same figure, which reads as a bug.
            self.space_row.set_subtitle(
                f"{human_size(usage.used)} used of {human_size(usage.total)}"
            )
        except OSError:
            self.space_row.set_subtitle("unknown")

        self._populate_tracks()
        return False

    def _populate_tracks(self):
        child = self.tracks_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.tracks_list.remove(child)
            child = nxt

        self.track_rows = {}
        tracks = list_tracks(self.mount_point)
        if not tracks:
            empty = Adw.ActionRow(title="No tracks", subtitle="Use Add Music to copy some over")
            self.tracks_list.append(empty)
            self.tracks_group.set_description(None)
            return

        for relpath in tracks:
            path = Path(relpath)
            row = Adw.ActionRow(title=path.name)
            folder = str(path.parent)
            if folder and folder != ".":
                row.set_subtitle(folder)
            self.tracks_list.append(row)
            self.track_rows[relpath] = row

        total = count_tracks(self.mount_point)
        if total > len(tracks):
            self.tracks_group.set_description(f"Showing {len(tracks)} of {total}")
        else:
            self.tracks_group.set_description(None)

        self._load_tags_async()

    def _load_tags_async(self):
        """Replace filenames with real titles once tags have been read.

        Reading tags means spawning the venv interpreter and touching every
        file over USB, which is far too slow for the main loop, so the rows
        show filenames immediately and are upgraded in place afterwards.
        """
        self.tag_generation += 1
        generation = self.tag_generation
        mount_point = self.mount_point

        def worker():
            tags = read_tags(mount_point)
            GLib.idle_add(self._apply_tags, generation, tags)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_tags(self, generation, tags):
        # Discard results from a scan the device has already moved on from.
        if generation != self.tag_generation:
            return False
        for relpath, row in self.track_rows.items():
            entry = tags.get(relpath)
            if not entry:
                continue
            title, artist = entry
            if title:
                row.set_title(GLib.markup_escape_text(title))
            if artist:
                row.set_subtitle(GLib.markup_escape_text(artist))
        return False

    def _on_playlist_mode_changed(self, *_args):
        if self.playlist_mode.get_selected() != 0 and has_speech_engine():
            self.playlist_voiceover.set_active(True)

    def _sync_options(self):
        """Flags for the selected playlist and voiceover options."""
        args = []
        mode = self.playlist_mode.get_selected()
        if mode == 1:
            args.append("--dir-playlists")
        elif mode == 2:
            args.append("--id3-playlists={artist}")
        elif mode == 3:
            args.append("--id3-playlists={genre}")
        if self.track_voiceover.get_active():
            args.append("--voiceover")
        if self.playlist_voiceover.get_active():
            args.append("--playlist-voiceover")
        return args

    def _set_busy(self, busy, message=""):
        self.busy = busy
        for row in (self.add_row, self.rebuild_row, self.wipe_row, self.eject_row):
            row.set_sensitive(not busy)
        self.refresh_button.set_sensitive(not busy)
        self.progress.set_visible(busy)
        if busy:
            self.progress.set_text(message)
            self.progress.pulse()
            self._pulse_id = GLib.timeout_add(120, self._pulse)
        else:
            pulse_id = getattr(self, "_pulse_id", None)
            if pulse_id:
                GLib.source_remove(pulse_id)
                self._pulse_id = None

    def _pulse(self):
        if not self.busy:
            return False
        self.progress.pulse()
        return True

    def _log(self, text):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), text)
        return False

    def _clear_log(self):
        self.log_view.get_buffer().set_text("")

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))
        return False

    # ------------------------------------------------------------- commands

    def _run(self, argv, busy_message, done_message):
        """Run a script in a worker thread, streaming output into the log."""
        self._clear_log()
        self._set_busy(True, busy_message)
        self.log_expander.set_expanded(True)

        def worker():
            code = -1
            try:
                proc = subprocess.Popen(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                if proc.stdout is not None:
                    for line in proc.stdout:
                        GLib.idle_add(self._log, line)
                code = proc.wait()
            except OSError as exc:
                GLib.idle_add(self._log, f"failed to run: {exc}\n")
            GLib.idle_add(self._finish, code, done_message)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, code, done_message):
        self._set_busy(False)
        if code == 0:
            self._toast(done_message)
        else:
            self._toast(f"Failed (exit {code}) - see Output")
        self.refresh()
        return False

    def on_add_music(self, _button):
        dialog = Gtk.FileDialog(title="Choose a music folder")

        def chosen(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path()
            if not path:
                self._toast("That location is not a local folder")
                return
            if not self._has_audio(path):
                self._toast("No supported audio found in that folder")
                return
            self._run(
                [str(SYNC_SCRIPT), "--ipod", self.mount_point,
                 *self._sync_options(), path],
                "Copying music…", "Music added",
            )

        dialog.select_folder(self, None, chosen)

    @staticmethod
    def _has_audio(path):
        for _root, _dirs, files in os.walk(path):
            for name in files:
                if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                    return True
        return False

    def on_rebuild(self, _button):
        # --rebuild-only rather than passing the Music directory as a source,
        # which would copy the iPod's own library into a subfolder of itself.
        self._run(
            [str(SYNC_SCRIPT), "--ipod", self.mount_point, "--rebuild-only",
             *self._sync_options()],
            "Rebuilding database…", "Database rebuilt",
        )

    def on_wipe(self, _button):
        total = count_tracks(self.mount_point)
        dialog = Adw.AlertDialog(
            heading="Wipe this iPod?",
            body=(f"All {total} track(s) will be removed from the device.\n\n"
                  "Backing up first is strongly recommended: iPod filenames are "
                  "scrambled codes, and the database that maps them back to real "
                  "song titles is deleted too."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("wipe", "Wipe Without Backup")
        dialog.add_response("backup", "Back Up and Wipe")
        dialog.set_response_appearance("wipe", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("backup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("backup")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_wipe_response)
        dialog.present(self)

    def _on_wipe_response(self, _dialog, response):
        if response == "cancel":
            return
        argv = [str(WIPE_SCRIPT), "--ipod", self.mount_point, "--yes"]
        if response == "backup":
            target = Path(os.path.expanduser("~"), "ipod-backup")
            argv += ["--backup", str(target)]
        self._run(argv, "Wiping…", "iPod wiped")

    def on_eject(self, _button):
        device = device_for(self.mount_point)
        if not device:
            self._toast("Could not determine the device to unmount")
            return

        self._set_busy(True, "Ejecting…")

        def worker():
            ok, message = udisks_filesystem_call(device, "Unmount")
            GLib.idle_add(self._finish_dbus, ok, "Safe to unplug", message)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_dbus(self, ok, success_message, error_message):
        self._set_busy(False)
        self._toast(success_message if ok else f"Failed: {error_message}")
        self.refresh()
        return False

    def on_mount_clicked(self, _button):
        """Mount an iPod that is plugged in but not mounted."""
        candidates = unmounted_vfat_devices()
        if not candidates:
            self._toast("No unmounted iPod found")
            return

        self._set_busy(True, "Mounting…")

        def worker():
            for device in candidates:
                ok, message = udisks_filesystem_call(device, "Mount")
                if not ok:
                    continue
                if Path(message, "iPod_Control").is_dir():
                    GLib.idle_add(self._finish_dbus, True, "iPod mounted", "")
                    return
                # Something else on the bus. Put it back as it was rather
                # than leaving an unrelated volume mounted.
                udisks_filesystem_call(device, "Unmount")
            GLib.idle_add(self._finish_dbus, False, "", "no iPod among the connected volumes")

        threading.Thread(target=worker, daemon=True).start()


class IpodApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.props.active_window or IpodWindow(application=self)
        window.present()


if __name__ == "__main__":
    sys.exit(IpodApp().run(sys.argv))
