#!/usr/bin/env python3
"""GTK4 front end for the iPod shuffle 4G scripts.

Drives ipod-sync.sh, ipod-remove.sh, ipod-wipe.sh and ipod-fetch.sh rather
than reimplementing their logic, so the command line and the GUI cannot drift
apart. Launch it via ./ipod-gui.sh, which picks an interpreter that has the
GTK bindings.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

APP_ID = "io.github.max_miller1204.IpodShuffle"
SCRIPT_DIR = Path(__file__).resolve().parent
LIB_SCRIPT = SCRIPT_DIR / "lib.sh"
SYNC_SCRIPT = SCRIPT_DIR / "ipod-sync.sh"
WIPE_SCRIPT = SCRIPT_DIR / "ipod-wipe.sh"
REMOVE_SCRIPT = SCRIPT_DIR / "ipod-remove.sh"
FETCH_SCRIPT = SCRIPT_DIR / "ipod-fetch.sh"

# Mirrors ipod-fetch.sh's default --output. Named here as well because the GUI
# syncs out of the same folder once the download has finished.
YOUTUBE_LIBRARY = Path.home() / "Music" / "youtube"


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

# Kept in step with SUPPORTED_EXT in lib.sh, which is the canonical list; a
# test asserts the two agree, because a stale copy of this silently miscounts
# the library rather than failing outright.
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav"}

# The scripts colour their output for a terminal. A text view has no idea what
# to do with the escape sequences, so every line arrived as literal noise:
# "[36m==>[0m Removed 1 track(s)".
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text):
    """Text as a terminal would show it, without the colour escapes."""
    return ANSI_ESCAPE.sub("", text)


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
            capture_output=True,
            text=True,
            timeout=60,
        )
        return json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def find_ipods():
    """Return the mount points of connected iPods.

    Uses findmnt's JSON output because raw mode escapes spaces as \\x20 and
    iPod names very often contain one.
    """
    try:
        out = subprocess.run(
            ["findmnt", "-no", "TARGET", "-t", "vfat", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        filesystems = json.loads(out).get("filesystems", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []

    candidates = []
    for entry in filesystems:
        target = entry.get("target")
        if target and Path(target, "iPod_Control").is_dir():
            candidates.append(target)
    return candidates


def saved_sync_options(mount_point):
    """Return the GUI state and equivalent CLI playlist arguments."""
    options_file = Path(mount_point, "iPod_Control", ".sync-options")
    try:
        options = options_file.read_text().splitlines()
    except OSError:
        options = []

    mode = 0
    playlist_args = []
    track_voiceover = False
    playlist_voiceover = False
    index = 0
    while index < len(options):
        option = options[index]
        if option == "--auto-dir-playlists" and index + 1 < len(options):
            value = options[index + 1]
            playlist_args.append(f"--dir-playlists={value}")
            mode = 1
            index += 2
        elif option == "--auto-id3-playlists" and index + 1 < len(options):
            value = options[index + 1]
            playlist_args.append(f"--id3-playlists={value}")
            mode = 3 if value == "{genre}" else 2
            index += 2
        elif option == "--track-voiceover":
            track_voiceover = True
            index += 1
        elif option == "--playlist-voiceover":
            playlist_voiceover = True
            index += 1
        else:
            index += 1

    return mode, playlist_args, track_voiceover, playlist_voiceover


def device_for(mount_point):
    """Block device backing a mount point, e.g. /dev/sda."""
    try:
        return (
            subprocess.run(
                ["findmnt", "-rno", "SOURCE", "--target", mount_point],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or None
        )
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
            "org.freedesktop.UDisks2.Filesystem",
            method,
            GLib.Variant("(a{sv})", ({},)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
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
            "org.freedesktop.UDisks2",
            "/org/freedesktop/UDisks2",
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
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


def lib_function_succeeds(name):
    """Run a helper from lib.sh and report whether it succeeded.

    Asking the shared library rather than repeating its checks here keeps the
    GUI's idea of an installed dependency identical to the scripts'. The
    supported runtime versions in particular are a moving target, and a second
    copy of those rules would eventually disagree with the one that matters.
    """
    if not LIB_SCRIPT.is_file():
        return False
    try:
        return (
            subprocess.run(
                ["bash", "-c", f'source "$1" && {name}', "_", str(LIB_SCRIPT)],
                capture_output=True,
                timeout=20,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def youtube_unavailable_reason():
    """Why downloading from YouTube is not possible here, or None if it is.

    Checked before offering the button because every one of these failures
    surfaces late and misleadingly: without a JavaScript runtime the download
    dies with HTTP 403 on everything but the oldest uploads, and without
    ffmpeg yt-dlp hands back the Opus file the shuffle silently ignores.
    """
    if not FETCH_SCRIPT.is_file():
        return "Not available in this build"
    if not lib_function_succeeds("yt_dlp_bin"):
        return "yt-dlp is not installed - run ./install.sh"
    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not installed - run ./install.sh"
    if not lib_function_succeeds("js_runtime"):
        return "Needs Deno, Node, or Bun - see the README"
    return None


def home_relative(path):
    """~/Music/youtube rather than the full path, which reads as noise."""
    try:
        return str(Path("~", Path(path).relative_to(Path.home())))
    except ValueError:
        return str(path)


def is_downloadable_url(text):
    """Whether text looks like a link that can be handed to yt-dlp."""
    try:
        parsed = urllib.parse.urlsplit((text or "").strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def fetched_sources(list_path, library):
    """What to copy onto the iPod after a download.

    ipod-fetch.sh writes the path of each newly downloaded track, so this is
    normally those tracks alone and nothing else the library already held.
    It deletes the file instead when its yt-dlp is too old to report them, and
    the artist folders are then the closest honest answer. An empty list is
    the third case and means the video had been downloaded before.
    """
    try:
        lines = Path(list_path).read_text().splitlines()
    except OSError:
        return sorted(str(p) for p in Path(library).glob("*") if p.is_dir())
    return [line.strip() for line in lines if line.strip()]


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
            return (
                f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
            )
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
        self.track_names = {}
        self.tag_generation = 0
        self.loading_options = False
        self.loaded_playlist_mode = 0
        self.loaded_playlist_args = []
        self.speech_engine_available = has_speech_engine()
        self.youtube_unavailable = youtube_unavailable_reason()

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
        self.empty_page = Adw.StatusPage(
            icon_name="multimedia-player-symbolic",
            title="No iPod Connected",
            description="Plug in an iPod shuffle and it will appear here.\n"
            "If it is already connected, it may need mounting.",
        )
        self.mount_button = Gtk.Button(label="Mount Connected iPod")
        self.mount_button.set_halign(Gtk.Align.CENTER)
        self.mount_button.add_css_class("pill")
        self.mount_button.add_css_class("suggested-action")
        self.mount_button.connect("clicked", self.on_mount_clicked)
        self.empty_page.set_child(self.mount_button)
        return self.empty_page

    def _build_device_page(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            hexpand=True,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        scroller.set_child(box)

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

        self.youtube_row = Adw.ActionRow(
            title="Add from YouTube",
            subtitle="Paste a link; it is converted to a format the iPod plays",
        )
        youtube_button = Gtk.Button(label="Paste Link…", valign=Gtk.Align.CENTER)
        youtube_button.connect("clicked", self.on_add_youtube)
        self.youtube_row.add_suffix(youtube_button)
        self.youtube_row.set_activatable_widget(youtube_button)
        # Better to say why than to let the user paste a link and watch the
        # download fail several steps later for a reason the log explains only
        # to someone who already knows what to look for.
        if self.youtube_unavailable:
            self.youtube_row.set_subtitle(self.youtube_unavailable)
            self.youtube_row.set_sensitive(False)
        actions.add(self.youtube_row)

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

        for row in (self.track_voiceover, self.playlist_voiceover):
            row.connect("notify::active", self._on_voiceover_changed)

        if not self.speech_engine_available:
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

        self.log_expander = Adw.ExpanderRow(
            title="Output", subtitle="Details of the last operation"
        )
        log_group = Adw.PreferencesGroup()
        log_group.add(self.log_expander)
        self.log_view = Gtk.TextView(
            editable=False,
            monospace=True,
            cursor_visible=False,
            # The lines carry mount points and library paths, which are long
            # enough that without wrapping the interesting half of the message
            # sits off the right edge behind a scrollbar nobody drags.
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=12,
            right_margin=12,
            top_margin=8,
            bottom_margin=8,
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

        candidates = find_ipods()
        if len(candidates) != 1:
            self.mount_point = None
            if len(candidates) > 1:
                self.empty_page.set_title("Multiple iPods Connected")
                self.empty_page.set_description(
                    "Disconnect all but the iPod you want to manage."
                )
                self.mount_button.set_visible(False)
            else:
                self.empty_page.set_title("No iPod Connected")
                self.empty_page.set_description(
                    "Plug in an iPod shuffle and it will appear here.\n"
                    "If it is already connected, it may need mounting."
                )
                self.mount_button.set_visible(True)
            self.stack.set_visible_child_name("empty")
            return False

        self.mount_point = candidates[0]
        self.stack.set_visible_child_name("device")
        self._load_sync_options()

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
        self.track_names = {}
        tracks = list_tracks(self.mount_point)
        if not tracks:
            empty = Adw.ActionRow(
                title="No tracks", subtitle="Use Add Music to copy some over"
            )
            self.tracks_list.append(empty)
            self.tracks_group.set_description(None)
            return

        for relpath in tracks:
            path = Path(relpath)
            row = Adw.ActionRow(title=path.name)
            folder = str(path.parent)
            if folder and folder != ".":
                row.set_subtitle(folder)
            remove_button = Gtk.Button(
                icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER
            )
            remove_button.add_css_class("flat")
            remove_button.set_tooltip_text("Remove this track from the iPod")
            remove_button.connect("clicked", self.on_remove_track, relpath)
            row.add_suffix(remove_button)
            self.tracks_list.append(row)
            self.track_rows[relpath] = row
            # Kept unescaped alongside the row, whose title is markup. The
            # confirmation dialog shows plain text and would otherwise ask
            # about a song called "Me &amp; You".
            self.track_names[relpath] = path.name

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
                self.track_names[relpath] = title
            if artist:
                row.set_subtitle(GLib.markup_escape_text(artist))
        return False

    def _on_playlist_mode_changed(self, *_args):
        if self.loading_options:
            return
        if self.playlist_mode.get_selected() != 0 and self.speech_engine_available:
            self.playlist_voiceover.set_active(True)

    def _on_voiceover_changed(self, row, *_args):
        if not self.speech_engine_available:
            row.set_sensitive(row.get_active())

    def _load_sync_options(self):
        mode, playlist_args, track_voiceover, playlist_voiceover = saved_sync_options(
            self.mount_point
        )
        self.loading_options = True
        try:
            self.playlist_mode.set_selected(mode)
            self.track_voiceover.set_active(track_voiceover)
            self.playlist_voiceover.set_active(playlist_voiceover)
        finally:
            self.loading_options = False
        self.loaded_playlist_mode = mode
        self.loaded_playlist_args = playlist_args

    def _sync_options(self):
        """Flags for the selected playlist and voiceover options."""
        args = []
        mode = self.playlist_mode.get_selected()
        if mode == self.loaded_playlist_mode and self.loaded_playlist_args:
            args.extend(self.loaded_playlist_args)
        elif mode == 1:
            args.append("--dir-playlists")
        elif mode == 2:
            args.append("--id3-playlists={artist}")
        elif mode == 3:
            args.append("--id3-playlists={genre}")
        if self.track_voiceover.get_active():
            args.append("--voiceover")
        if self.playlist_voiceover.get_active():
            args.append("--playlist-voiceover")
        return args or ["--forget-options"]

    def _set_busy(self, busy, message=""):
        self.busy = busy
        for row in (self.add_row, self.rebuild_row, self.wipe_row, self.eject_row):
            row.set_sensitive(not busy)
        self.youtube_row.set_sensitive(not busy and not self.youtube_unavailable)
        # Also the per-track remove buttons, which are otherwise a way to start
        # a second script against the same device while one is still running.
        self.tracks_list.set_sensitive(not busy)
        self.refresh_button.set_sensitive(not busy)
        self.progress.set_visible(busy)

        # Cleared unconditionally: one operation can hand over to the next
        # without ever going idle, and a timeout left behind would keep
        # pulsing a bar nothing is driving.
        pulse_id = getattr(self, "_pulse_id", None)
        if pulse_id:
            GLib.source_remove(pulse_id)
        self._pulse_id = None

        if busy:
            self.progress.set_text(message)
            self.progress.pulse()
            self._pulse_id = GLib.timeout_add(120, self._pulse)

    def _pulse(self):
        if not self.busy:
            return False
        self.progress.pulse()
        return True

    def _log(self, text):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), strip_ansi(text))
        # Follow the output as it arrives. A pane that stays at the first line
        # of a copy shows the least useful part of a running operation.
        end = buf.create_mark(None, buf.get_end_iter(), False)
        self.log_view.scroll_to_mark(end, 0, False, 0, 0)
        buf.delete_mark(end)
        return False

    def _clear_log(self):
        self.log_view.get_buffer().set_text("")

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))
        return False

    # ------------------------------------------------------------- commands

    def _run(self, argv, busy_message, done_message, then=None, clear=True):
        """Run a script in a worker thread, streaming output into the log.

        then, when given, is called on success and returns either the next
        command as (argv, busy_message, done_message) or a string to report as
        the outcome when there is nothing further to do. That is how the
        YouTube flow runs a download and a sync as one operation from the
        user's point of view, deciding what to copy only once the download has
        said what it produced.
        """
        if clear:
            self._clear_log()
        self._set_busy(True, busy_message)
        self.log_expander.set_expanded(True)

        def worker():
            code = -1
            try:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if proc.stdout is not None:
                    for line in proc.stdout:
                        GLib.idle_add(self._log, line)
                code = proc.wait()
            except OSError as exc:
                GLib.idle_add(self._log, f"failed to run: {exc}\n")
            GLib.idle_add(self._finish, code, done_message, then)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, code, done_message, then=None):
        if code == 0 and then is not None:
            outcome = then()
            if isinstance(outcome, tuple):
                # Straight into the next command, staying busy, so the two
                # read as one action rather than appearing to finish twice.
                self._run(*outcome, clear=False)
                return False
            done_message = outcome

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
                [
                    str(SYNC_SCRIPT),
                    "--ipod",
                    self.mount_point,
                    *self._sync_options(),
                    path,
                ],
                "Copying music…",
                "Music added",
            )

        dialog.select_folder(self, None, chosen)

    def on_add_youtube(self, _button):
        url_entry = Adw.EntryRow(title="YouTube link")
        url_entry.set_activates_default(True)
        whole_playlist = Adw.SwitchRow(
            title="Whole playlist",
            subtitle="Off downloads only the linked video",
        )

        fields = Adw.PreferencesGroup()
        fields.add(url_entry)
        fields.add(whole_playlist)

        dialog = Adw.AlertDialog(
            heading="Add from YouTube",
            body=(
                "The audio is converted to MP3, tagged with its artist and "
                f"title, kept in {home_relative(YOUTUBE_LIBRARY)}, and copied "
                "onto the iPod."
            ),
        )
        dialog.set_extra_child(fields)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("download", "Download")
        dialog.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("download")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_youtube_response, url_entry, whole_playlist)
        dialog.present(self)

        # Pasting a link is the entire interaction, so offer the one already on
        # the clipboard rather than making the user paste it by hand.
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().read_text_async(
                None, self._offer_clipboard_url, url_entry
            )

    @staticmethod
    def _offer_clipboard_url(clipboard, result, url_entry):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if is_downloadable_url(text) and not url_entry.get_text():
            url_entry.set_text(text.strip())

    def _on_youtube_response(self, _dialog, response, url_entry, whole_playlist):
        if response != "download":
            return

        url = url_entry.get_text().strip()
        if not is_downloadable_url(url):
            self._toast("Enter a link starting with http:// or https://")
            return

        # Written by the download and read by the sync that follows it, so
        # only what this run actually fetched is copied over. Without it the
        # whole library would go back onto the device every time.
        handle, new_tracks = tempfile.mkstemp(prefix="ipod-fetch-", suffix=".list")
        os.close(handle)

        fetch = [
            str(FETCH_SCRIPT),
            "--output",
            str(YOUTUBE_LIBRARY),
            "--new-tracks",
            new_tracks,
        ]
        if not whole_playlist.get_active():
            fetch.append("--single")
        fetch.append(url)

        # Read here rather than in the callback below, which runs after the
        # download and must not touch widgets from outside the main loop.
        options = self._sync_options()

        self._run(
            fetch,
            "Downloading from YouTube…",
            "Downloaded",
            then=lambda: self._sync_downloaded(new_tracks, options),
        )

    def _sync_downloaded(self, new_tracks, options):
        """Copy what the download produced, or say why there is nothing to."""
        sources = fetched_sources(new_tracks, YOUTUBE_LIBRARY)
        try:
            os.unlink(new_tracks)
        except OSError:
            pass

        if not sources:
            return "Already downloaded - nothing new to add"
        return (
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                *options,
                # A YouTube title can start with a dash, and the shell script
                # would read that as a flag rather than a path.
                "--",
                *sources,
            ],
            "Copying onto the iPod…",
            "Music added",
        )

    def on_remove_track(self, _button, relpath):
        name = self.track_names.get(relpath, Path(relpath).name)
        dialog = Adw.AlertDialog(
            heading="Remove this track?",
            body=(
                f"{name}\n\n"
                "It is deleted from the iPod and the database is rebuilt. "
                "Any copy in your own music folder is left alone."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_response, relpath)
        dialog.present(self)

    def _on_remove_response(self, _dialog, response, relpath):
        if response != "remove":
            return
        self._run(
            [
                str(REMOVE_SCRIPT),
                "--ipod",
                self.mount_point,
                "--yes",
                # Track names are whatever the tags said, dashes included.
                "--",
                relpath,
            ],
            "Removing track…",
            "Track removed",
        )

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
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                "--rebuild-only",
                *self._sync_options(),
            ],
            "Rebuilding database…",
            "Database rebuilt",
        )

    def on_wipe(self, _button):
        total = count_tracks(self.mount_point)
        dialog = Adw.AlertDialog(
            heading="Wipe this iPod?",
            body=(
                f"All {total} track(s) will be removed from the device.\n\n"
                "Backing up first is strongly recommended: iPod filenames are "
                "scrambled codes, and the database that maps them back to real "
                "song titles is deleted too."
            ),
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
            GLib.idle_add(
                self._finish_dbus, False, "", "no iPod among the connected volumes"
            )

        threading.Thread(target=worker, daemon=True).start()


class IpodApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

    def do_activate(self):
        window = self.props.active_window or IpodWindow(application=self)
        window.present()


if __name__ == "__main__":
    sys.exit(IpodApp().run(sys.argv))
