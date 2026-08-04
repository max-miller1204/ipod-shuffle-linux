#!/usr/bin/env python3
"""GTK4 front end for the iPod shuffle 4G scripts.

Drives ipod-sync.sh, ipod-remove.sh, ipod-wipe.sh and ipod-fetch.sh rather
than reimplementing their device-changing logic, so their copy and database
rules stay shared with the command line. Launch it via ./ipod-gui.sh, which
picks an interpreter that has the GTK bindings.

The interface is library-first: your music is the app, and the device
operations live in one Device & Settings view rather than leading the window.
A track is in one of three states everywhere it appears - on the iPod, in your
local library, or previewed only - and that state is what the coloured dot
next to it means.
"""

import hashlib
import http.client
import json

import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

ELLIPSIZE_END = Pango.EllipsizeMode.END

APP_ID = "io.github.max_miller1204.IpodShuffle"
SCRIPT_DIR = Path(__file__).resolve().parent
LIB_SCRIPT = SCRIPT_DIR / "lib.sh"
SYNC_SCRIPT = SCRIPT_DIR / "ipod-sync.sh"
WIPE_SCRIPT = SCRIPT_DIR / "ipod-wipe.sh"
REMOVE_SCRIPT = SCRIPT_DIR / "ipod-remove.sh"
FETCH_SCRIPT = SCRIPT_DIR / "ipod-fetch.sh"

# Mirrors ipod-fetch.sh's default --output. Named here as well because the GUI
# queues newly downloaded tracks from that folder once the download finishes.
YOUTUBE_LIBRARY = Path.home() / "Music" / "youtube"

CACHE_DIR = Path(
    os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
) / "ipod-shuffle-linux"
ART_CACHE = CACHE_DIR / "art"
CONFIG_FILE = Path(
    os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
) / "ipod-shuffle-linux" / "config.json"

# Hearing a YouTube result costs a file: a yt-dlp media URL is short-lived and
# bound to the address that asked for it, seeking over one is unreliable, and
# streaming would throw the work away so that adding the track fetched it a
# second time. The file lands here rather than in a music folder, because
# previewing twenty songs must not add twenty of them to the library.
PREVIEW_CACHE = CACHE_DIR / "previews"

# Each download runs into a directory of its own under here and is moved into
# the cache once it has finished. Downloading straight into the cache would
# leave yt-dlp's --download-archive recording a video as fetched in a folder
# it can be pruned out of, and the next preview of that video would then
# download nothing at all.
PREVIEW_INCOMING = PREVIEW_CACHE / ".incoming"

# About seventy previews at the 256k bitrate this project downloads. Large
# enough that an evening of listening never evicts anything, small enough that
# a folder nobody ever looks at cannot grow without bound.
PREVIEW_CACHE_LIMIT = 512 * 1024 * 1024

# Where a track lives, which decides its marker in every view. "preview" is a
# track downloaded purely so it could be heard: it sits in the preview cache
# rather than in a music folder, and stays there until it is added, which moves
# it into the library and out of the cache.
STATE_IPOD = "ipod"
STATE_LIBRARY = "library"
STATE_PREVIEW = "preview"

STATE_LABELS = {
    STATE_IPOD: "On iPod",
    STATE_LIBRARY: "In library",
    STATE_PREVIEW: "Previewed",
}


def _tag_interpreter():
    """Pick an interpreter that can import mutagen, or None.

    PyGObject belongs to the system Python while mutagen lives in
    install.sh's virtualenv, so tag reading has to cross over to it unless
    this interpreter happens to have mutagen itself.
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

# Kept in step with the canonical SUPPORTED_EXT in lib.sh. The GUI cannot
# source shell, so a test asserts that this necessary copy has not drifted.
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav"}
PLAYLIST_EXTENSIONS = {".m3u", ".pls"}

# The scripts colour their output for a terminal. A text view has no idea what
# to do with the escape sequences, so every line arrived as literal noise:
# "[36m==>[0m Removed 1 track(s)".
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# ipod-sync.sh prints one of these per file as it copies. Parsed to drive the
# sync bar, which would otherwise have nothing but an indeterminate pulse to
# show for an operation that can run for minutes.
COPIED_LINE = re.compile(r"^\s*\+ (?P<name>.+?) -> (?P<dest>.+?)\s*$")


def strip_ansi(text):
    """Text as a terminal would show it, without the colour escapes."""
    return ANSI_ESCAPE.sub("", text)


# Streams one JSON object per line rather than a single blob at exit. The scan
# touches every file over USB and now extracts cover art as well, so a device
# holding two gigabytes of music would sit behind one timeout with nothing to
# show; per-line output lets the grid fill in as results arrive.
_TAG_READER = r"""
import base64, hashlib, json, os, stat, sys
from pathlib import Path
try:
    import mutagen
except ImportError:
    mutagen = None

root = Path(sys.argv[1])
art_dir = Path(sys.argv[2])
suffixes = set(sys.argv[3].split(","))


def first(audio, key):
    value = audio.get(key)
    if not value:
        return None
    text = str(value[0]).strip()
    return text or None


def cover_bytes(path):
    # Cover art is unreachable through easy=True - EasyID3 has no APIC
    # mapping - so this is a second parse, done only for files we are
    # keeping and only until one picture is found.
    try:
        raw = mutagen.File(path)
    except Exception:
        return None
    if raw is None or not getattr(raw, "tags", None):
        return None
    tags = raw.tags
    try:
        pictures = tags.getall("APIC")
        if pictures:
            return bytes(pictures[0].data)
    except AttributeError:
        pass
    try:
        covr = tags.get("covr")
        if covr:
            return bytes(covr[0])
    except Exception:
        pass
    pictures = getattr(raw, "pictures", None)
    if pictures:
        return bytes(pictures[0].data)
    return None


def art_path(path):
    data = cover_bytes(path)
    if not data:
        return None
    name = hashlib.sha1(data).hexdigest() + ".img"
    target = art_dir / name
    if not target.exists():
        try:
            art_dir.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
        except OSError:
            return None
    return str(target)


def emit(path, key, size=None):
    if size is None:
        size = path.stat().st_size
    fallback = {"path": key, "title": path.stem, "size": size}
    sys.stdout.write(json.dumps(fallback) + "\n")
    sys.stdout.flush()
    if mutagen is None:
        return
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return
    if audio is None:
        return
    record = {
        "path": key,
        "title": first(audio, "title"),
        "artist": first(audio, "artist"),
        "album": first(audio, "album"),
        "albumartist": first(audio, "albumartist"),
        "genre": first(audio, "genre"),
        "track": first(audio, "tracknumber"),
        "duration": float(getattr(getattr(audio, "info", None), "length", 0) or 0),
        "size": size,
        "art": art_path(path),
    }
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def report_symlink(path):
    sys.stdout.write(json.dumps({"event": "symlink", "path": str(path)}) + "\n")
    sys.stdout.flush()


def walk_error(error):
    raise error


try:
    if len(sys.argv) > 4 and sys.argv[4] == "exact":
        for line in sys.stdin:
            value = json.loads(line)
            if not isinstance(value, str):
                raise ValueError
            path = Path(value)
            if path.suffix.lower() not in suffixes:
                continue
            if not path.is_file():
                raise OSError
            emit(path, value)
    else:
        skip_symlinks = sys.argv[4] == "recursive-no-symlinks"
        root_info = os.lstat(root)
        if skip_symlinks and stat.S_ISLNK(root_info.st_mode):
            report_symlink(root)
            sys.exit(0)
        if not root.is_dir():
            raise OSError
        for current, dirs, files in os.walk(
            root, onerror=walk_error, followlinks=False
        ):
            dirs.sort()
            if skip_symlinks:
                for name in dirs[:]:
                    path = Path(current, name)
                    info = os.lstat(path)
                    if stat.S_ISLNK(info.st_mode):
                        report_symlink(path)
                        dirs.remove(name)
            for name in sorted(files):
                path = Path(current, name)
                if path.suffix.lower() not in suffixes:
                    continue
                if skip_symlinks:
                    info = os.lstat(path)
                    if stat.S_ISLNK(info.st_mode):
                        report_symlink(path)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    emit(path, str(path.relative_to(root)), info.st_size)
                else:
                    emit(path, str(path.relative_to(root)))
except (OSError, TypeError, ValueError):
    sys.exit(2)
"""


def scan_tracks(
    root=None,
    on_record=None,
    timeout=900,
    cancelled=None,
    files=None,
    skip_symlinks=False,
):
    """Read tags for every supported file under root.

    Returns (records, complete, skipped_symlinks), with each record path
    relative to root.
    on_record, when given, is called with each record as it arrives so a caller
    can show progress rather than waiting for the whole tree. Partial records
    are returned with complete false after cancellation, timeout, or failure.
    """
    global TAG_PYTHON
    if TAG_PYTHON is None:
        TAG_PYTHON = _tag_interpreter()

    exact_files = (
        None
        if files is None
        else list(dict.fromkeys(str(path) for path in files))
    )
    exact_allowed = set(exact_files or ())
    root = Path(root) if root is not None else Path(".")
    records = {}
    skipped_symlinks = 0
    reader_mode = (
        "exact"
        if exact_files is not None
        else "recursive-no-symlinks" if skip_symlinks else "recursive"
    )

    try:
        proc = subprocess.Popen(
            [
                TAG_PYTHON or sys.executable,
                "-c",
                _TAG_READER,
                str(root),
                str(ART_CACHE),
                ",".join(sorted(AUDIO_EXTENSIONS)),
                reader_mode,
            ],
            stdin=(
                subprocess.PIPE
                if exact_files is not None
                else subprocess.DEVNULL
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError:
        return [], False, 0

    output = queue.Queue()
    output_done = object()
    input_failed = threading.Event()

    def read_output():
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    output.put(line)
        finally:
            output.put(output_done)

    threading.Thread(target=read_output, daemon=True).start()
    if exact_files is not None:
        def write_input():
            try:
                for path in exact_files:
                    proc.stdin.write(json.dumps(path) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                input_failed.set()
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        threading.Thread(target=write_input, daemon=True).start()
    deadline = time.monotonic() + timeout
    complete = False
    try:
        while True:
            if cancelled is not None and cancelled():
                raise subprocess.SubprocessError
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try:
                wait = min(remaining, 0.1) if cancelled is not None else remaining
                line = output.get(timeout=wait)
            except queue.Empty as exc:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(proc.args, timeout) from exc
                continue
            if line is output_done:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("event") == "symlink":
                    skipped_symlinks += 1
                    continue
                relpath = record["path"]
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if not isinstance(relpath, str):
                continue
            relative = Path(relpath)
            invalid_recursive = (
                exact_files is None
                and (relative.is_absolute() or ".." in relative.parts)
            )
            invalid_exact = exact_files is not None and relpath not in exact_allowed
            if (
                invalid_recursive
                or invalid_exact
                or relative.suffix.lower() not in AUDIO_EXTENSIONS
            ):
                continue
            records[relpath] = record
            if on_record is not None:
                on_record(record)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        complete = proc.wait(timeout=remaining) == 0 and not input_failed.is_set()
    except subprocess.SubprocessError:
        if proc.poll() is None:
            proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.SubprocessError:
            pass
    return list(records.values()), complete, skipped_symlinks


def read_tags(mount_point):
    """Map each device track's relative path to its tag record.

    Returns an empty dict when the venv or mutagen is unavailable, in which
    case the interface falls back to showing filenames.
    """
    music = Path(mount_point, "iPod_Control", "Music")
    records, complete, _skipped_symlinks = scan_tracks(music)
    if not complete:
        return {}
    return {record["path"]: record for record in records}


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


def volume_identity(mount_point):
    try:
        uuid = subprocess.run(
            ["findmnt", "-rno", "UUID", "--target", mount_point],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        uuid = ""
    if uuid and uuid != "-":
        return f"uuid:{uuid}"

    try:
        sysinfo = Path(
            mount_point, "iPod_Control", "Device", "SysInfo"
        ).read_bytes()
    except OSError:
        return None
    if not sysinfo:
        return None
    return f"sysinfo:{hashlib.sha256(sysinfo).hexdigest()}"


class DeviceHandle:
    __slots__ = ("mount_point", "identity", "block_device")

    def __init__(self, mount_point, identity, block_device):
        self.mount_point = mount_point
        self.identity = identity
        self.block_device = block_device


def resolve_device(mount_point, expected_identity, require_block=False):
    if mount_point is None or expected_identity is None:
        return None
    mount_point = str(mount_point)
    if volume_identity(mount_point) != expected_identity:
        return None
    block_device = device_for(mount_point) if require_block else None
    if require_block and block_device is None:
        return None
    return DeviceHandle(mount_point, expected_identity, block_device)


def udisks_filesystem_call(device, method):
    """Invoke a UDisks2 Filesystem method on a block device.

    Speaks D-Bus directly rather than shelling out to udisksctl, which
    minimal systems do not ship. Both reach the same daemon and the same
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


def lib_function_output(name):
    """Run a helper from lib.sh and return what it printed, or None.

    lib_function_succeeds answers whether a dependency is installed; this
    answers where it is. Searching runs yt-dlp directly rather than through one
    of the scripts, and the virtualenv copy install.sh keeps current is not on
    PATH, so the path has to come from the same helper the scripts use.
    """
    if not LIB_SCRIPT.is_file():
        return None
    try:
        result = subprocess.run(
            ["bash", "-c", f'source "$1" && {name}', "_", str(LIB_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


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


def youtube_search_unavailable_reason():
    """Why searching YouTube is not possible here, or None if it is.

    Deliberately weaker than youtube_unavailable_reason, which is about the
    download. Searching only reads metadata, and yt-dlp does that without a
    JavaScript runtime and without ffmpeg: it is the media URL that is signed
    and the audio that has to be converted. Gating the field on the download's
    requirements would blank out the half of the window that still works and
    leave the user no way to find out what they could have downloaded.
    """
    if not lib_function_succeeds("yt_dlp_bin"):
        return "yt-dlp is not installed - run ./install.sh"
    return None


GSTREAMER_UNAVAILABLE = (
    "GStreamer is not installed - see Preview playback in the README"
)

# Said in the bar, where the track that will not arrive is already named, and
# pointing at the log rather than repeating yt-dlp's own diagnosis badly.
PREVIEW_FAILED = (
    "Could not download that preview. Details has what yt-dlp reported; "
    "./ipod-fetch.sh --update is the usual fix when downloads stop working."
)
_GST = None
_GST_LOADED = False


def gst():
    """The GStreamer bindings, or None when they are not installed.

    Imported here rather than beside Gtk at the top of the file, because
    gi.require_version raises when the typelib is absent and that exception
    would take away an entire working window to withhold the one feature of it
    that is optional by design. Loaded once and remembered: Gst.init scans the
    plugin registry, which is not work to repeat per track.
    """
    global _GST, _GST_LOADED
    if _GST_LOADED:
        return _GST
    _GST_LOADED = True
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
    except (ImportError, ValueError, GLib.Error):
        return None
    _GST = Gst
    return _GST


def preview_unavailable_reason():
    """Why preview playback is not possible here, or None if it is.

    Asks lib.sh for the same reason the YouTube checks do: the scripts and the
    window have to agree about what counts as installed.
    The shell probe is also the stricter of the two available answers, because
    it makes the elements rather than only importing the module, and a
    GStreamer with no decoders imports perfectly and then plays silence.
    """
    if not lib_function_succeeds("gst_available"):
        return GSTREAMER_UNAVAILABLE
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


# Three, which is exactly what the reserved skeleton makes room for. The
# section is a shortlist to add from rather than a browser - for a song the
# answer is almost always the first row - and a skeleton shorter than the
# result set would let the layout jump at the moment the results land, which
# is the moment the user is reading it.
YOUTUBE_SEARCH_RESULTS = 3

# A flat search answers in about a second. This is the point at which waiting
# longer is worse than saying it did not come back.
YOUTUBE_SEARCH_TIMEOUT = 20

# Below this a query matches most of a library and none of it usefully, and
# every keystroke of a real query would spend a round trip to YouTube.
SEARCH_MIN_QUERY = 2

# Long enough that typing a phrase costs one search rather than one per
# letter, short enough that it still reads as a response to the typing. On top
# of the ~150ms GtkSearchEntry already waits before it reports a change at all,
# which is why this is shorter than a debounce written from nothing would be.
SEARCH_DEBOUNCE_MS = 300


class SearchResult:
    """One YouTube hit, before anything has been downloaded.

    Not a Track: it has no file, no size and none of the three states, and
    giving it one would put a thing that does not exist yet into the album
    grid and into the storage meter.
    """

    __slots__ = ("title", "uploader", "duration", "url", "video_id", "thumbnail")

    def __init__(self, title, uploader, duration, url, video_id="", thumbnail=""):
        self.title = title
        self.uploader = uploader
        self.duration = duration
        self.url = url
        self.video_id = video_id
        # Where the artwork can be fetched from, not the artwork itself: the
        # search answers in about a second and downloading three images before
        # showing anything would trade that away for decoration.
        self.thumbnail = thumbnail


def youtube_search_target(query, limit=YOUTUBE_SEARCH_RESULTS):
    """What to hand yt-dlp for this query.

    A pasted link is looked up as itself rather than searched for: searching
    for a URL returns whatever YouTube makes of that text as a phrase, which
    is never the video that was on the clipboard.
    """
    query = (query or "").strip()
    if is_downloadable_url(query):
        return query
    return f"ytsearch{max(1, int(limit))}:{query}"


def youtube_search_command(yt_dlp, query, limit=YOUTUBE_SEARCH_RESULTS):
    """The argv that lists candidates without downloading any of them.

    --flat-playlist is the difference between an answer in about a second and
    one in half a minute: without it yt-dlp resolves every hit's media URLs,
    which is the expensive half of the work and the half a list of titles does
    not need. --playlist-items caps a linked playlist to the same shortlist the
    search returns, so pasting an album link cannot flood the section.
    """
    limit = max(1, int(limit))
    return [
        str(yt_dlp),
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--playlist-items",
        f"1-{limit}",
        "--",
        youtube_search_target(query, limit),
    ]


def parse_search_results(lines, limit=YOUTUBE_SEARCH_RESULTS):
    """Turn --dump-json output into results, skipping anything unusable.

    yt-dlp prints one JSON object per line, and a line that is neither is a
    notice that escaped --no-warnings rather than a reason to show nothing.
    """
    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        video_id = video_id if isinstance(video_id, str) else ""
        url = entry.get("webpage_url") or entry.get("url")
        if not isinstance(url, str) or not is_downloadable_url(url):
            # A flat search entry sometimes carries only the id, and the watch
            # URL built from one is what ipod-fetch.sh would be given anyway.
            url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        if not is_downloadable_url(url):
            continue
        try:
            duration = float(entry.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        results.append(
            SearchResult(
                title=str(entry.get("title") or video_id or "Untitled"),
                uploader=str(
                    entry.get("uploader") or entry.get("channel") or "YouTube"
                ),
                duration=duration,
                url=url,
                video_id=video_id,
                thumbnail=thumbnail_from_entry(entry),
            )
        )
        if len(results) >= limit:
            break
    return results


def search_youtube(
    query, limit=YOUTUBE_SEARCH_RESULTS, timeout=YOUTUBE_SEARCH_TIMEOUT
):
    """Ask YouTube for candidates. Returns (results, reached_youtube).

    A search that finds nothing exits 0 having printed nothing, and one that
    could not reach YouTube exits non-zero, so the empty case and the offline
    or rate-limited case are told apart by the exit code. Without that they
    both look like no results, and the user retypes a query that was fine.
    """
    yt_dlp = lib_function_output("yt_dlp_bin")
    if yt_dlp is None:
        return [], False
    try:
        finished = subprocess.run(
            youtube_search_command(yt_dlp, query, limit),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return [], False
    if finished.returncode != 0:
        return [], False
    return parse_search_results(finished.stdout.splitlines(), limit), True


# ------------------------------------------------------------ youtube artwork

# A search result has no file to read a cover out of, so its artwork is the
# video's thumbnail, fetched separately into the same cache embedded covers
# land in. ipod-fetch.sh keeps passing --no-embed-thumbnail: cover art really
# is pure waste on a 2GB device with no screen, and embedding it would put it
# on the iPod while still leaving a result that has not been downloaded yet
# with nothing to show.
#
# Cached under the video id rather than under the image's own checksum, which
# is what the tag reader uses, because the id is what a result carries before
# anything has been downloaded. It is also in the name of every file
# ipod-fetch.sh writes, so a previewed track, the copy that keeping it moves
# into the music folder, and a library rescanned days later all find the
# artwork the search already fetched.

# The largest square artwork is drawn at is the album page's 180px, and a
# 16:9 thumbnail is cropped to its short edge, so 360 is the first standard
# size with pixels to spare at every size the window uses.
YOUTUBE_ART_SIZE = 360

# Bounded because this writes a file from a URL that came off the network. A
# YouTube thumbnail is tens of kilobytes; nothing this size is one.
THUMBNAIL_MAX_BYTES = 4 * 1024 * 1024

# Short, because artwork decorates a list that is already on screen. A slow
# image must not outlive the search it belongs to.
THUMBNAIL_TIMEOUT = 10

# The only thing standing between an id that arrived over the network and a
# filename, so it is a whitelist of what yt-dlp ids are made of rather than an
# attempt to escape whatever turns up.
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

# The id ipod-fetch.sh writes into every filename, read as the last bracketed
# group: a title can carry brackets of its own - "Song [Live] [dQw4w9WgXcQ]" -
# and --output puts the id last.
VIDEO_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{1,64})\]")


def youtube_art_file(video_id, root):
    """Where this video's thumbnail belongs in the art cache, or None."""
    video_id = (video_id or "").strip()
    if not VIDEO_ID.fullmatch(video_id):
        return None
    # The prefix cannot collide with the tag reader's names in the same
    # directory, which are the SHA-1 of the image: forty hex characters.
    return Path(root) / f"yt-{video_id}.img"


def youtube_art_path(video_id, root):
    """The cached thumbnail for a video id, or None if it is not cached."""
    target = youtube_art_file(video_id, root)
    if target is None or not target.is_file():
        return None
    return str(target)


def video_id_from_name(name):
    """The YouTube id in a downloaded file's name, or ""."""
    found = VIDEO_ID_IN_NAME.findall(Path(name).stem)
    return found[-1] if found else ""


def cached_thumbnail_for(path):
    """The cached YouTube artwork for a file on disk, or None."""
    return youtube_art_path(video_id_from_name(Path(path).name), ART_CACHE)


def thumbnail_from_entry(entry, want=YOUTUBE_ART_SIZE):
    """The thumbnail to cache for one search hit, or "".

    yt-dlp offers several sizes, worst first. The smallest one that still
    covers the largest square the artwork is drawn at is the right trade:
    below that it is visibly soft on the album page, and the maximum-
    resolution version is a quarter of a megabyte to show at 36 pixels in a
    list of three.
    """
    sized = []
    unsized = []
    for item in entry.get("thumbnails") or ():
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not is_downloadable_url(url):
            continue
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        # The short edge, because a 16:9 thumbnail is cropped to a square and
        # the sides are what the crop throws away.
        edge = min(width, height) if width and height else max(width, height)
        (sized if edge > 0 else unsized).append((edge, url))
    if sized:
        # The smallest that is big enough, and failing that the largest there
        # is, which is what the two keys order.
        return min(
            sized,
            key=lambda found: (0, found[0]) if found[0] >= want else (1, -found[0]),
        )[1]
    if unsized:
        # Nothing said how big any of them are, so the last one, which is the
        # best yt-dlp knows of.
        return unsized[-1][1]
    fallback = entry.get("thumbnail")
    if isinstance(fallback, str) and is_downloadable_url(fallback):
        return fallback
    return ""


def fetch_thumbnail(url, destination, timeout=THUMBNAIL_TIMEOUT):
    """Download one thumbnail to destination. True if it landed.

    Never raises. Artwork is the one part of a result that is allowed to be
    missing, so a network that half-answers has to leave the row showing its
    placeholder rather than an error the user cannot do anything about.
    """
    # Checked rather than trusted: this URL came off the network, and urlopen
    # is as happy to read file:// as https://.
    if not is_downloadable_url(url):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = response.read(THUMBNAIL_MAX_BYTES + 1)
    except (OSError, ValueError, http.client.HTTPException):
        return False
    if not data or len(data) > THUMBNAIL_MAX_BYTES:
        return False

    destination = Path(destination)
    staging = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Written under a name of its own and renamed into place, so neither a
        # fetch cut off halfway nor a second fetch of the same image running
        # beside it can leave a half-written file behind for every later paint
        # to try to load.
        handle, staging = tempfile.mkstemp(dir=destination.parent, suffix=".part")
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(staging, destination)
    except OSError:
        if staging is not None:
            try:
                os.unlink(staging)
            except OSError:
                pass
        return False
    return True


def cache_thumbnail(video_id, url, root):
    """Put this video's thumbnail in the art cache. Its path, or None.

    Already cached counts as cached: an image is fetched once and then belongs
    to every later search, preview and library scan naming the same video.
    """
    target = youtube_art_file(video_id, root)
    if target is None:
        return None
    if target.is_file():
        return str(target)
    return str(target) if fetch_thumbnail(url, target) else None


def local_search_matches(tracks, query):
    """Library tracks matching every word of the query.

    Every word rather than the whole phrase, so "queen rhapsody" finds a track
    tagged "Bohemian Rhapsody" by "Queen"; a phrase match would need the words
    in whatever order the tagger happened to use.
    """
    terms = [term for term in (query or "").lower().split() if term]
    if not terms:
        return []
    matched = [
        track
        for track in tracks
        if all(
            term in f"{track.title} {track.artist} {track.album}".lower()
            for term in terms
        )
    ]
    return sorted(
        matched,
        key=lambda t: (
            t.artist.lower(),
            t.album.lower(),
            t.track_no or 999,
            t.title.lower(),
        ),
    )


def fetched_sources(list_path, library):
    """What to queue after a download.

    ipod-fetch.sh writes the path of each newly downloaded track, so this is
    normally those tracks alone and nothing else the library already held.
    It deletes the file instead when its yt-dlp is too old to report them, and
    the artist folders are then the closest honest source set. An empty list
    is the third case and means the video had been downloaded before.
    """
    try:
        lines = Path(list_path).read_text().splitlines()
    except OSError:
        return sorted(str(p) for p in Path(library).glob("*") if p.is_dir())
    return [line.strip() for line in lines if line.strip()]


def fetch_command(url, output, new_tracks=None, single=True):
    """The ipod-fetch.sh invocation behind every download the GUI starts.

    One builder keeps the shared flags consistent for the link dialog, a
    search result's Add and a preview while letting each supply its output,
    new-track manifest and playlist scope.
    """
    command = [str(FETCH_SCRIPT), "--output", str(output)]
    if new_tracks is not None:
        command += ["--new-tracks", str(new_tracks)]
    if single:
        command.append("--single")
    command.append(url)
    return command


def preview_cache_entries(root):
    """Every finished preview under root, as (path, size, mtime).

    Oldest first, which is the order they are pruned in: a preview is written
    once and never rewritten, so its mtime is when it was downloaded.

    Read from the filesystem rather than from an index kept alongside it. The
    files are the cache, so a cache that was pruned, cleared, or emptied by
    hand cannot disagree with a record of what it is supposed to hold.
    """
    root = Path(root)
    try:
        candidates = sorted(root.rglob("*"))
    except OSError:
        return []
    entries = []
    for path in candidates:
        # A download still in flight lives under .incoming and is not a
        # preview yet; it is moved into place only once it has finished.
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        entries.append((path, info.st_size, info.st_mtime))
    entries.sort(key=lambda entry: (entry[2], str(entry[0])))
    return entries


def cached_preview_path(video_id, root):
    """The already-downloaded file for a video id, or None.

    ipod-fetch.sh puts the id in every filename it writes, so the cache can be
    asked whether it holds a search result without keeping a second record of
    which id became which file.
    """
    video_id = (video_id or "").strip()
    if not video_id:
        return None
    marker = f"[{video_id}]"
    for path, _size, _mtime in preview_cache_entries(root):
        if marker in path.name:
            return path
    return None


def prunable_previews(entries, limit, keep=()):
    """Which cached previews to drop to bring the cache back under limit.

    Oldest first, excluding paths the caller marks to keep. The window uses
    that exclusion for the track being played and the one that just arrived.
    """
    kept = {str(path) for path in keep}
    total = sum(size for _path, size, _mtime in entries)
    dropped = []
    for path, size, _mtime in entries:
        if total <= limit:
            break
        if str(path) in kept:
            continue
        dropped.append(path)
        total -= size
    return dropped


def promote_destination(source, cache_root, library_root):
    """Where a previewed file belongs once it is kept.

    The cache mirrors the artist folders ipod-fetch.sh writes, so keeping a
    preview is a move that lands it exactly where downloading it in the first
    place would have put it.
    """
    source = Path(source)
    try:
        relative = source.relative_to(cache_root)
    except ValueError:
        relative = Path(source.name)
    return Path(library_root) / relative


def read_local_playlist_tracks(list_path):
    path = Path(list_path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], False
    if path.suffix.lower() == ".pls":
        numbered = []
        for line in lines:
            key, separator, value = line.partition("=")
            match = re.fullmatch(r"File([0-9]+)", key.strip(), re.IGNORECASE)
            if separator and match:
                numbered.append((int(match.group(1)), value.strip()))
        entries = [value for _index, value in sorted(numbered)]
    else:
        entries = [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]

    tracks = []
    for entry in entries:
        if entry.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(entry)
            if parsed.netloc not in ("", "localhost"):
                continue
            entry = urllib.parse.unquote(parsed.path)
        elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", entry):
            continue
        track = Path(entry) if Path(entry).is_absolute() else path.parent / entry
        if not track.exists() and "\\" in entry:
            slashed = entry.replace("\\", "/")
            candidate = (
                Path(slashed) if Path(slashed).is_absolute() else path.parent / slashed
            )
            if candidate.exists():
                track = candidate
        if track.is_file() and track.suffix.lower() in AUDIO_EXTENSIONS:
            tracks.append(str(track.absolute()))
    return tracks, True


def local_playlist_tracks(list_path):
    return read_local_playlist_tracks(list_path)[0]


def count_tracks(mount_point):
    music = Path(mount_point, "iPod_Control", "Music")
    if not music.is_dir():
        return 0
    return sum(1 for p in music.rglob("*") if p.is_file())


def list_tracks(mount_point, limit=None):
    """Relative paths of the tracks on the device, sorted."""
    music = Path(mount_point, "iPod_Control", "Music")
    if not music.is_dir():
        return []
    files = sorted(p for p in music.rglob("*") if p.is_file())
    if limit is not None:
        files = files[:limit]
    return [str(p.relative_to(music)) for p in files]


MUSIC_PREFIX = "iPod_Control/Music/"


def list_playlists(mount_point):
    """The playlists the sync keeps at the volume root, as (name, entries).

    Entries are stored relative to the volume root; the ones under the music
    folder come back relative to it instead, matching list_tracks, so playlist
    rows can share the tag-derived titles. Hand-written lines that point
    elsewhere are kept as written.
    """
    playlists = []
    root = Path(mount_point)
    paths = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".m3u", ".pls"}
    )
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if path.suffix.lower() == ".pls":
            numbered = []
            for line in lines:
                key, separator, value = line.partition("=")
                match = re.fullmatch(r"File([0-9]+)", key.strip(), re.IGNORECASE)
                if separator and match:
                    numbered.append((int(match.group(1)), value.strip()))
            lines = [value for _index, value in sorted(numbered)]
        entries = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(MUSIC_PREFIX):
                line = line[len(MUSIC_PREFIX):]
            entries.append(line)
        playlists.append((path.stem, entries))
    return playlists


def playlist_file(mount_point, name):
    """The file backing a playlist, whichever of the two formats it uses."""
    root = Path(mount_point)
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".m3u", ".pls"} and path.stem == name:
            return path
    return None


def write_playlist(mount_point, device_identity, path, entries):
    """Rewrite a playlist in place, keeping the format it already used.

    Entries arrive as list_playlists returns them: the ones under the music
    folder have had that prefix stripped, and anything hand-written was left
    alone. Which is which cannot be recovered from the string, so each is
    tested against the device rather than guessed at - restoring the prefix
    blindly would rewrite a hand-written absolute path into a broken one.

    Written beside the target and renamed, because a half-written playlist on
    a volume that can be unplugged at any moment is one the firmware drops.
    """
    device = resolve_device(mount_point, device_identity)
    if device is None:
        return False
    music = Path(device.mount_point, "iPod_Control", "Music")
    lines = []
    for entry in entries:
        if not Path(entry).is_absolute() and (music / entry).exists():
            entry = MUSIC_PREFIX + entry
        lines.append(entry)

    if path.suffix.lower() == ".pls":
        body = ["[playlist]", f"NumberOfEntries={len(lines)}"]
        body += [f"File{index}={line}" for index, line in enumerate(lines, start=1)]
        body.append("Version=2")
    else:
        body = ["#EXTM3U"] + lines

    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text("\n".join(body) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False
    return True


def spoken_playlists(mount_point):
    """Names of the playlists the device can say out loud.

    With no screen, a playlist the shuffle cannot announce is one the user has
    no way to identify, so the interface says which are which rather than
    listing them all as though they were equal.
    """
    speakable = Path(mount_point, "iPod_Control", "Speakable", "Playlists")
    if not speakable.is_dir():
        return set()
    return {p.stem.lower() for p in speakable.iterdir() if p.is_file()}


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}".replace(".0 ", " ")
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def plural(count, singular, suffix="s"):
    return f"{count} {singular}" + ("" if count == 1 else suffix)


def human_duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def music_roots():
    """Folders searched for local music, newest configuration winning."""
    try:
        stored = json.loads(CONFIG_FILE.read_text()).get("music_roots")
    except (OSError, ValueError, AttributeError):
        stored = None
    if isinstance(stored, list) and stored:
        return [Path(p).expanduser() for p in stored]
    return [Path.home() / "Music"]


def save_music_roots(roots):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps({"music_roots": [str(p) for p in roots]}, indent=2)
        )
    except OSError:
        pass


# --------------------------------------------------------------------- model


class Track:
    """One audio file, wherever it happens to live."""

    __slots__ = (
        "path",
        "relpath",
        "title",
        "artist",
        "album",
        "genre",
        "duration",
        "track_no",
        "art",
        "size",
        "state",
        "on_ipod",
    )

    def __init__(self, path, record, state, relpath=None):
        self.path = str(path)
        self.relpath = relpath if relpath is not None else str(path)
        self.title = record.get("title") or Path(path).stem
        self.artist = record.get("artist") or "Unknown artist"
        self.album = record.get("album") or "Unknown album"
        self.genre = record.get("genre") or ""
        self.duration = float(record.get("duration") or 0)
        # Embedded art first, then the thumbnail cached for whichever video
        # this file was downloaded from. The fallback is what carries a search
        # result's artwork through into the track it becomes, and it is here
        # rather than at one producer because every producer needs it: the
        # preview that has just finished, the preview cache rescanned at
        # startup, and the copy in the music folder that keeping it made.
        self.art = record.get("art") or cached_thumbnail_for(path)
        self.size = int(record.get("size") or 0)
        self.state = state
        self.on_ipod = state == STATE_IPOD
        try:
            self.track_no = int(str(record.get("track") or "0").split("/")[0])
        except ValueError:
            self.track_no = 0

    @property
    def tagged(self):
        """Whether the file carried real tags, rather than being named only."""
        return self.album != "Unknown album" or self.artist != "Unknown artist"

    def identity(self):
        """A key that survives the trip onto the device.

        iPod_Control/Music filenames are scrambled four-letter codes, so a
        track copied there cannot be recognised by path. Tags plus a rounded
        duration are what remain of its identity.
        """
        return (
            (self.title or "").strip().lower(),
            (self.artist or "").strip().lower(),
            (self.album or "").strip().lower(),
            round(self.duration),
        )


class Album:
    __slots__ = ("title", "artist", "tracks", "art")

    def __init__(self, title, artist):
        self.title = title
        self.artist = artist
        self.tracks = []
        self.art = None

    def add(self, track):
        self.tracks.append(track)
        if self.art is None and track.art:
            self.art = track.art

    @property
    def state(self):
        """An album is only 'on iPod' when all of it is.

        A half-synced album badged "On iPod" would be a lie, and the shuffle
        gives you no way to discover which half is missing. The album view
        shows the ratio for the partial case.
        """
        states = {track.state for track in self.tracks}
        if states == {STATE_IPOD}:
            return STATE_IPOD
        if states == {STATE_PREVIEW}:
            return STATE_PREVIEW
        return STATE_LIBRARY

    @property
    def on_ipod_count(self):
        return sum(1 for track in self.tracks if track.on_ipod)

    def sorted_tracks(self):
        return sorted(self.tracks, key=lambda t: (t.track_no or 999, t.title.lower()))


class LibraryIndex:
    """Everything the app knows about, local folders and device combined."""

    def __init__(self):
        self.tracks = []
        # Tracks that exist on the device and nowhere else on this computer.
        # Kept apart from the scan results so a rescan can replace one without
        # disturbing the other.
        self.device_only = []
        # Downloads sitting in the preview cache. A third list rather than
        # tracks with a different state in the first, because the music folders
        # and the cache are rescanned independently and neither may drop the
        # other's tracks when it finishes.
        self.previews = []
        self.roots = music_roots()
        self.generation = 0

    def all_tracks(self):
        return self.tracks + self.device_only + self.previews

    def albums(self):
        grouped = {}
        for track in self.all_tracks():
            key = (track.album.lower(), track.artist.lower())
            album = grouped.get(key)
            if album is None:
                album = Album(track.album, track.artist)
                grouped[key] = album
            album.add(track)
        return sorted(
            grouped.values(), key=lambda a: (a.artist.lower(), a.title.lower())
        )

    def artists(self):
        """The library grouped by artist rather than by album.

        Reuses Album because the detail view only ever needs a title, a
        summary line, some artwork and a track list, and an artist is that
        same shape. The summary line carries the album count instead of a
        performer's name.
        """
        grouped = {}
        for track in self.all_tracks():
            key = track.artist.lower()
            collection = grouped.get(key)
            if collection is None:
                collection = Album(track.artist, "")
                grouped[key] = collection
            collection.add(track)
        for collection in grouped.values():
            albums = {track.album.lower() for track in collection.tracks}
            collection.artist = plural(len(albums), "album")
        return sorted(grouped.values(), key=lambda a: a.title.lower())

    def collections(self, by_artist=False):
        return self.artists() if by_artist else self.albums()

    def counts(self, by_artist=False):
        counts = {STATE_IPOD: 0, STATE_LIBRARY: 0, STATE_PREVIEW: 0}
        for collection in self.collections(by_artist):
            counts[collection.state] = counts.get(collection.state, 0) + 1
        return counts


# ---------------------------------------------------------------- appearance

# Every value here is from the design's token sheet. GTK has no CSS custom
# properties, so the light theme is expressed as overrides under .light rather
# than as a second set of variables.
PALETTE = {
    "dark": {
        "window": "#101011",
        "sidebar": "#0a0a0b",
        "content": "#131314",
        "card": "#1b1b1d",
        "line": "#26262a",
        "text": "#f4f2f0",
        "dim": "#8b8884",
        "accent": "#ff6b3d",
        "danger": "#c0341f",
        "ok": "#3ddc84",
        "warn": "#e0a33c",
        "queued": "#5a4034",
        "meter": "#26262a",
    },
    "light": {
        "window": "#ffffff",
        "sidebar": "#f1efec",
        "content": "#ffffff",
        "card": "#f7f5f2",
        "line": "#e3dfd9",
        "text": "#1a1917",
        "dim": "#6f6c67",
        "accent": "#e0521f",
        "danger": "#b02d18",
        "ok": "#1f9d55",
        "warn": "#a8721a",
        "queued": "#e8c3ad",
        "meter": "#e3dfd9",
    },
}

# Covers with no embedded art get one of these, chosen by hashing the album so
# a given record always looks the same. The design draws them as diagonal
# stripes, which reads as deliberate rather than as a missing image.
COVER_TINTS = [
    ("#3a2c26", "#2a201c"),
    ("#2b3340", "#1f2630"),
    ("#38302b", "#282320"),
    ("#2f3a34", "#232b27"),
    ("#3b2b33", "#2b1f26"),
    ("#333037", "#25232a"),
    ("#2a3436", "#1e2628"),
    ("#3a332a", "#2a251f"),
]

STYLE_CSS = """
window.shuffle,
window.shuffle > * {
  font-family: Manrope, Cantarell, sans-serif;
}
window.shuffle { background: #101011; color: #f4f2f0; }
window.shuffle.light { background: #ffffff; color: #1a1917; }

.sf-sidebar { background: #0a0a0b; border-right: 1px solid #1f1f22; }
window.shuffle.light .sf-sidebar {
  background: #f1efec; border-right: 1px solid #e3dfd9;
}
.sf-content { background: #131314; }
window.shuffle.light .sf-content { background: #ffffff; }

.sf-card {
  background: #1b1b1d; border: 1px solid #26262a; border-radius: 11px;
}
window.shuffle.light .sf-card { background: #f7f5f2; border-color: #e3dfd9; }

.sf-danger-card { background: #1a1416; border: 1px solid #4a2a24; border-radius: 11px; }
window.shuffle.light .sf-danger-card { background: #fdf3f1; border-color: #f0cec6; }

.sf-warn-card { background: #1e1a13; border: 1px solid #4a3d22; border-radius: 9px; }
window.shuffle.light .sf-warn-card { background: #fdf7ea; border-color: #e8d7a8; }

.sf-brand {
  background: #ff6b3d; color: #1a0d07; border-radius: 7px;
  font-weight: 800; font-size: 12px; min-width: 24px; min-height: 24px;
}
window.shuffle.light .sf-brand { background: #e0521f; color: #ffffff; }

.sf-nav-row { border-radius: 8px; padding: 9px 11px; font-size: 14px; }
.sf-nav-row:hover { background: alpha(#ffffff, 0.05); }
window.shuffle.light .sf-nav-row:hover { background: alpha(#000000, 0.04); }
.sf-nav-row.selected { background: #1f1f22; font-weight: 600; }
window.shuffle.light .sf-nav-row.selected { background: #e5e1db; font-weight: 600; }

.sf-heading { font-size: 26px; font-weight: 800; letter-spacing: -0.02em; }
.sf-album-title { font-size: 38px; font-weight: 800; letter-spacing: -0.03em; }
.sf-section-heading { font-size: 16px; font-weight: 700; }
.sf-row-title { font-size: 13.5px; font-weight: 600; }
.sf-body { font-size: 12.5px; color: #8b8884; }
.sf-caption { font-size: 11.5px; font-weight: 500; color: #8b8884; }
window.shuffle.light .sf-body,
window.shuffle.light .sf-caption { color: #6f6c67; }
.sf-section-label {
  font-family: "IBM Plex Mono", monospace; font-size: 10.5px;
  letter-spacing: 0.12em; color: #5c5955;
}
.sf-mono { font-family: "IBM Plex Mono", monospace; font-size: 11.5px; }
.sf-dim { color: #8b8884; }
window.shuffle.light .sf-dim { color: #6f6c67; }
.sf-accent { color: #ff6b3d; }
window.shuffle.light .sf-accent { color: #e0521f; }
.sf-alert { color: #ff8f6b; }
window.shuffle.light .sf-alert { color: #b02d18; }

.sf-pill {
  border-radius: 99px; padding: 4px 11px; font-size: 12px; font-weight: 500;
  background: #1b1b1d; border: 1px solid #2a2a2d; color: #c9c6c2;
  min-height: 0; box-shadow: none;
}
window.shuffle.light .sf-pill {
  background: #ffffff; border-color: #e3dfd9; color: #46433f;
}
.sf-pill.selected {
  background: #ff6b3d; color: #1a0d07; border-color: #ff6b3d; font-weight: 600;
}
window.shuffle.light .sf-pill.selected {
  background: #e0521f; color: #ffffff; border-color: #e0521f;
}

.sf-button {
  border-radius: 7px; font-size: 12px; font-weight: 600;
  background: transparent; border: 1px solid #3a3a40; color: #f4f2f0;
  box-shadow: none;
}
window.shuffle.light .sf-button { border-color: #d5d0c8; color: #1a1917; }
.sf-button:hover { background: alpha(#ffffff, 0.06); }
window.shuffle.light .sf-button:hover { background: alpha(#000000, 0.04); }
.sf-button.accent {
  background: #ff6b3d; border-color: #ff6b3d; color: #1a0d07; font-weight: 700;
}
window.shuffle.light .sf-button.accent {
  background: #e0521f; border-color: #e0521f; color: #ffffff;
}
.sf-button.accent:hover { background: #ff8355; }
.sf-button.danger { background: #c0341f; border-color: #c0341f; color: #ffffff; }
.sf-button.danger:hover { background: #d4402a; }

.sf-cover { border-radius: 9px; background: #26262a; }
.sf-cover.small { border-radius: 6px; }
.sf-cover.tiny { border-radius: 5px; }
.sf-cover-0 { background: repeating-linear-gradient(135deg,#3a2c26 0px,#3a2c26 10px,#2a201c 10px,#2a201c 20px); }
.sf-cover-1 { background: repeating-linear-gradient(135deg,#2b3340 0px,#2b3340 10px,#1f2630 10px,#1f2630 20px); }
.sf-cover-2 { background: repeating-linear-gradient(135deg,#38302b 0px,#38302b 10px,#282320 10px,#282320 20px); }
.sf-cover-3 { background: repeating-linear-gradient(135deg,#2f3a34 0px,#2f3a34 10px,#232b27 10px,#232b27 20px); }
.sf-cover-4 { background: repeating-linear-gradient(135deg,#3b2b33 0px,#3b2b33 10px,#2b1f26 10px,#2b1f26 20px); }
.sf-cover-5 { background: repeating-linear-gradient(135deg,#333037 0px,#333037 10px,#25232a 10px,#25232a 20px); }
.sf-cover-6 { background: repeating-linear-gradient(135deg,#2a3436 0px,#2a3436 10px,#1e2628 10px,#1e2628 20px); }
.sf-cover-7 { background: repeating-linear-gradient(135deg,#3a332a 0px,#3a332a 10px,#2a251f 10px,#2a251f 20px); }
window.shuffle.light .sf-cover-0 { background: repeating-linear-gradient(135deg,#e0cec4 0px,#e0cec4 10px,#d3bdb1 10px,#d3bdb1 20px); }
window.shuffle.light .sf-cover-1 { background: repeating-linear-gradient(135deg,#ccd6e2 0px,#ccd6e2 10px,#b9c7d8 10px,#b9c7d8 20px); }
window.shuffle.light .sf-cover-2 { background: repeating-linear-gradient(135deg,#ded4cb 0px,#ded4cb 10px,#d0c3b8 10px,#d0c3b8 20px); }
window.shuffle.light .sf-cover-3 { background: repeating-linear-gradient(135deg,#cddcd3 0px,#cddcd3 10px,#bacfc3 10px,#bacfc3 20px); }
window.shuffle.light .sf-cover-4 { background: repeating-linear-gradient(135deg,#e2ced6 0px,#e2ced6 10px,#d4bcc7 10px,#d4bcc7 20px); }
window.shuffle.light .sf-cover-5 { background: repeating-linear-gradient(135deg,#d7d3dc 0px,#d7d3dc 10px,#c7c2d1 10px,#c7c2d1 20px); }
window.shuffle.light .sf-cover-6 { background: repeating-linear-gradient(135deg,#ccd8d9 0px,#ccd8d9 10px,#b9cacc 10px,#b9cacc 20px); }
window.shuffle.light .sf-cover-7 { background: repeating-linear-gradient(135deg,#ded8cb 0px,#ded8cb 10px,#d0c8b7 10px,#d0c8b7 20px); }

.sf-dot { min-width: 8px; min-height: 8px; border-radius: 99px; }
.sf-dot.ipod { background: #ff6b3d; border: 1.5px solid #ff6b3d; }
window.shuffle.light .sf-dot.ipod { background: #e0521f; border-color: #e0521f; }
.sf-dot.library { background: transparent; border: 1.5px solid #8b8884; }
.sf-dot.preview { background: transparent; border: 1.5px dashed #a8a5a1; }

.sf-badge {
  background: alpha(#0a0a0b, 0.78); border-radius: 99px;
  padding: 3px 8px; font-size: 10.5px; font-weight: 600;
}
window.shuffle.light .sf-badge { background: alpha(#ffffff, 0.86); color: #1a1917; }

.sf-bottom-bar { background: #0a0a0b; border-top: 1px solid #1f1f22; }
window.shuffle.light .sf-bottom-bar {
  background: #f1efec; border-top: 1px solid #e3dfd9;
}
.sf-sync-bar { background: #151517; border-top: 1px solid #1f1f22; }
window.shuffle.light .sf-sync-bar {
  background: #f7f5f2; border-top: 1px solid #e3dfd9;
}

.sf-idle-art {
  border: 1px dashed #2f2f33; border-radius: 8px; color: #3a3a40;
}
window.shuffle.light .sf-idle-art { border-color: #d5d0c8; color: #b8b2aa; }

.sf-log {
  font-family: "IBM Plex Mono", monospace; font-size: 11px;
  background: transparent; color: #8b8884;
}
window.shuffle.light .sf-log { color: #6f6c67; }

/* The play affordance on a row's artwork. Transparent until the row is under
   the pointer, so a list at rest reads as covers rather than as a column of
   identical glyphs. Focus reveals it too, because a control that only exists
   while a mouse hovers it cannot be reached from the keyboard at all. */
.sf-cover-play {
  opacity: 0; transition: opacity 90ms ease-out;
  padding: 0; min-width: 0; min-height: 0;
  border: none; box-shadow: none; border-radius: 6px;
  background: alpha(#000000, 0.55); color: #ffffff;
}
.sf-tracks row:hover .sf-cover-play,
.sf-track-row:hover .sf-cover-play,
.sf-cover-play:hover,
.sf-cover-play:focus { opacity: 1; }
.sf-cover-play:disabled { color: alpha(#ffffff, 0.45); }

.sf-track-row { border-radius: 9px; padding: 6px 8px; }
.sf-track-row:hover { background: alpha(#ffffff, 0.04); }
window.shuffle.light .sf-track-row:hover { background: alpha(#000000, 0.03); }
.sf-track-row.previewed { opacity: 0.72; }
/* A YouTube result sits directly under a GtkColumnView of local results, and
   that view insets its last column by 4px less than a plain row's padding
   does. Matched here so the two lists' Add buttons share one right edge
   rather than nearly sharing one. */
.sf-track-row.sf-result-row { padding-right: 4px; }

/* Placeholder blocks holding the shape of a YouTube result while it loads.
   Flat rather than animated: a shimmer would be the only moving thing in a
   window that is otherwise still, and it would read as progress that the
   search has no way to actually report. */
.sf-skeleton { background: alpha(#ffffff, 0.06); border-radius: 5px; }
window.shuffle.light .sf-skeleton { background: alpha(#000000, 0.05); }

.sf-search { border-radius: 8px; font-size: 12.5px; min-height: 30px; }

.sf-new-tile {
  border: 1px dashed #33333a; border-radius: 10px;
  background: transparent; color: #8b8884; box-shadow: none;
}
window.shuffle.light .sf-new-tile { border-color: #d5d0c8; color: #6f6c67; }

.sf-tracks { background: transparent; }
.sf-tracks > listview > row,
.sf-tracks row { background: transparent; padding: 4px 2px; }
.sf-tracks row:hover { background: alpha(#ffffff, 0.04); border-radius: 9px; }
window.shuffle.light .sf-tracks row:hover { background: alpha(#000000, 0.03); }
.sf-tracks > header > button {
  background: transparent; border: none; box-shadow: none;
  font-size: 11.5px; font-weight: 600; color: #8b8884;
}
window.shuffle.light .sf-tracks > header > button { color: #6f6c67; }
.sf-tracks > header > button:hover { color: #f4f2f0; }
window.shuffle.light .sf-tracks > header > button:hover { color: #1a1917; }

.sf-meter { border-radius: 99px; background-color: #26262a; }
window.shuffle.light .sf-meter { background-color: #e3dfd9; }

.sf-flow { background: transparent; }
.sf-flow > flowboxchild { padding: 0; border-radius: 10px; }
.sf-flow > flowboxchild:selected { background: alpha(#ff6b3d, 0.14); }
"""


def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(STYLE_CSS, -1)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    return provider


def cover_class(seed):
    digest = hashlib.sha1((seed or "").encode("utf-8", "replace")).digest()
    return f"sf-cover-{digest[0] % len(COVER_TINTS)}"


ALBUM_COVER = 140


def cover_pixel_size(width, height, size):
    """How large to draw artwork so it fills a square of this size.

    Gtk.Image fits a paintable inside the square it is given, which letterboxes
    anything that is not square - and a YouTube thumbnail is 16:9, so it would
    be a strip of artwork floating in a band of placeholder colour. Scaling by
    the long edge instead lands the short edge exactly on the square and lets
    the frame clip what hangs over, which is the centre crop every music player
    shows a video thumbnail as.
    """
    if width <= 0 or height <= 0:
        return size
    long_edge, short_edge = max(width, height), min(width, height)
    # Rounded up, because a pixel short of the square would show a hairline of
    # the frame's own background down one edge.
    return (size * long_edge + short_edge - 1) // short_edge


def make_cover(art_path, size, seed, extra_class=""):
    """A rounded square cover, from embedded art or a generated placeholder."""
    # An Overlay rather than a Box because artwork wider than the square is
    # what fills it: an overlay child is not measured, so a cropped cover
    # cannot widen the row it sits in, while a Box would grow to its child. It
    # is left without a main child for the same reason - the size request below
    # is the whole of the square, and an Overlay measures nothing else.
    frame = Gtk.Overlay()
    frame.add_css_class("sf-cover")
    if extra_class:
        frame.add_css_class(extra_class)
    # border-radius alone paints rounded corners but does not clip children,
    # so the artwork would square them off again.
    frame.set_overflow(Gtk.Overflow.HIDDEN)
    frame.set_size_request(size, size)
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.CENTER)

    texture = None
    if art_path and Path(art_path).exists():
        try:
            texture = Gdk.Texture.new_from_filename(str(art_path))
        except GLib.Error:
            texture = None

    if texture is not None:
        # Gtk.Image rather than Gtk.Picture: a Picture reports the texture's
        # intrinsic size as its natural width, so a 600px cover would make
        # every cell of the album grid 600px wide and collapse it to two
        # columns. Image honours pixel-size instead.
        image = Gtk.Image.new_from_paintable(texture)
        image.set_pixel_size(
            cover_pixel_size(texture.get_width(), texture.get_height(), size)
        )
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        frame.add_overlay(image)
    else:
        frame.add_css_class(cover_class(seed))

    return frame


def state_dot(state):
    dot = Gtk.Box(valign=Gtk.Align.CENTER)
    dot.add_css_class("sf-dot")
    dot.add_css_class(state)
    return dot


def label(text, *classes, **kwargs):
    kwargs.setdefault("xalign", 0)
    widget = Gtk.Label(label=text, **kwargs)
    for name in classes:
        widget.add_css_class(name)
    return widget


class TrackItem(GObject.Object):
    """A Track in a form Gio.ListStore will hold."""

    __gtype_name__ = "ShuffleTrackItem"

    def __init__(self, track, number):
        super().__init__()
        self.track = track
        self.number = number


def playable_cover(window, track, view):
    """A track's artwork, which becomes a play button under the pointer.

    On the cover rather than in a column of its own: a row already carries an
    Add or Remove button, and a second permanently visible button beside it
    would make the more consequential one harder to pick out. Hovering the
    artwork is also where every other music player puts this.
    """
    cover = make_cover(track.art, 36, track.album, "small")
    if view is None:
        return cover

    overlay = Gtk.Overlay(valign=Gtk.Align.CENTER)
    overlay.set_child(cover)
    play = Gtk.Button(icon_name="media-playback-start-symbolic")
    play.add_css_class("sf-cover-play")
    play.set_size_request(36, 36)
    if window.preview_unavailable:
        play.set_sensitive(False)
        play.set_tooltip_text(window.preview_unavailable)
    else:
        play.set_tooltip_text(f"Preview {track.title} on this computer")
    play.connect("clicked", lambda _b, t=track: window.play_from(view, t))
    overlay.add_overlay(play)
    return overlay


def track_cell(window, track, number, column, view=None):
    """One cell of a track row, for whichever column asked for it."""
    if column == "number":
        return label(str(number), "sf-caption", "sf-mono", width_chars=3, xalign=1.0)

    if column == "title":
        row = Gtk.Box(spacing=12)
        row.append(playable_cover(window, track, view))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(label(track.title, "sf-row-title", ellipsize=ELLIPSIZE_END))
        subtitle = track.artist if track.tagged else "No tags — filename shown"
        text.append(label(subtitle, "sf-body", ellipsize=ELLIPSIZE_END))
        row.append(text)
        return row

    if column == "album":
        return label(track.album, "sf-body", ellipsize=ELLIPSIZE_END, hexpand=True)

    if column == "state":
        marker = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        marker.append(state_dot(track.state))
        marker.append(label(STATE_LABELS[track.state], "sf-caption"))
        return marker

    if column == "duration":
        return label(
            human_duration(track.duration),
            "sf-caption",
            "sf-mono",
            width_chars=5,
            xalign=1.0,
        )

    action = Gtk.Button()
    action.add_css_class("sf-button")
    action.set_valign(Gtk.Align.CENTER)
    if track.state == STATE_PREVIEW:
        # Add still means "I want this track", but a previewed file is sitting
        # in a cache that gets pruned, so here it has to move the file first.
        # Offered with no iPod attached, unlike every other Add: keeping a
        # download is something you do to your own music folder.
        action.set_label("Add")
        action.add_css_class("accent")
        action.set_tooltip_text(
            f"Move into {home_relative(YOUTUBE_LIBRARY)} and out of the "
            "preview cache"
        )
        action.connect("clicked", lambda _b, t=track: window._promote_preview(t))
        return action
    if track.on_ipod:
        action.set_label("Remove")
        action.connect(
            "clicked", lambda b, t=track: window.on_remove_track(b, t.relpath)
        )
        action.set_sensitive(bool(window.mount_point))
    elif track.path in window.pending:
        action.set_label("Queued")
        action.connect("clicked", lambda _b, t=track: window._unqueue_track(t))
    else:
        action.set_label("Add")
        action.add_css_class("accent")
        action.connect("clicked", lambda _b, t=track: window._queue_tracks([t]))
        action.set_sensitive(bool(window.mount_point))
    return action


TRACK_COLUMNS = (
    # key, title, expand, sort key
    ("number", "#", False, None),
    ("title", "Title", True, lambda t: t.title.lower()),
    ("album", "Album", True, lambda t: t.album.lower()),
    ("state", "", False, lambda t: t.state),
    ("duration", "Time", False, lambda t: t.duration),
    ("action", "", False, None),
)


def track_sorter(get):
    """A column sorter keyed on one field of the track.

    A closure rather than a lambda with a default argument: GtkCustomSorter
    calls the comparison with user_data as a third positional argument, which
    would land on that default and replace the key function with None.
    """

    def compare(left, right, _user_data=None):
        first, second = get(left.track), get(right.track)
        return (first > second) - (first < second)

    return Gtk.CustomSorter.new(compare)


def track_column_view(window, columns=None):
    """A sortable track table.

    GtkColumnView rather than a box of rows: it recycles widgets, so a flat
    view of a whole library stays responsive, and each column can carry a
    sorter without the rows knowing anything about it.
    """
    store = Gio.ListStore.new(TrackItem)
    sort_model = Gtk.SortListModel.new(store, None)
    view = Gtk.ColumnView.new(Gtk.NoSelection.new(sort_model))
    view.add_css_class("sf-tracks")
    sort_model.set_sorter(view.get_sorter())

    wanted = columns or [key for key, *_ in TRACK_COLUMNS]
    for key, title, expand, sort_key in TRACK_COLUMNS:
        if key not in wanted:
            continue
        factory = Gtk.SignalListItemFactory()

        def bind(_factory, item, key=key):
            entry = item.get_item()
            item.set_child(track_cell(window, entry.track, entry.number, key, view))

        factory.connect("bind", bind)
        column = Gtk.ColumnViewColumn.new(title, factory)
        column.set_expand(expand)
        if sort_key is not None:
            column.set_sorter(track_sorter(sort_key))
        view.append_column(column)
    view.store = store
    return view


def track_list_view(window, on_reorder):
    """An ordered, drag-reorderable track list.

    A GtkListView rather than the sortable table above, because a playlist's
    order is the whole point of it: offering to sort one by title would throw
    away the only thing the user put there by hand.
    """
    store = Gio.ListStore.new(TrackItem)
    view = Gtk.ListView.new(Gtk.NoSelection.new(store), Gtk.SignalListItemFactory())
    view.add_css_class("sf-tracks")
    factory = view.get_factory()

    def setup(_factory, item):
        row = Gtk.Box(spacing=12)
        row.add_css_class("sf-track-row")

        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda _s, _x, _y, i=item: Gdk.ContentProvider.new_for_value(
                GObject.Value(GObject.TYPE_UINT, i.get_position())
            ),
        )
        row.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_UINT, Gdk.DragAction.MOVE)
        target.connect(
            "drop",
            lambda _t, value, _x, _y, i=item: on_reorder(value, i.get_position()),
        )
        row.add_controller(target)
        item.set_child(row)

    def bind(_factory, item):
        row = item.get_child()
        child = row.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            row.remove(child)
            child = nxt
        entry = item.get_item()
        for key in ("number", "title", "state", "duration", "action"):
            row.append(track_cell(window, entry.track, entry.number, key, view))
        if entry.track.state == STATE_PREVIEW:
            row.add_css_class("previewed")
        else:
            row.remove_css_class("previewed")

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    view.store = store
    return view


def fill_tracks(view, tracks):
    view.store.remove_all()
    for number, track in enumerate(tracks, start=1):
        view.store.append(TrackItem(track, number))


class StorageMeter(Gtk.Box):
    """Used, queued and free space as one bar.

    A CSS gradient with hard colour stops rather than a drawn widget: Cairo
    drawing from Python needs python3-gi-cairo, which is a system package this
    project would otherwise never ask for, and the segments have to meet
    exactly at a fraction that no box layout expresses cleanly.

    Every meter shares one provider, rebuilt whenever any of them changes.
    There are three in the window, so regenerating the lot is cheaper than
    tracking which one moved.
    """

    _provider = None
    _registry = {}
    _counter = 0

    def __init__(self, height=5):
        super().__init__()
        StorageMeter._counter += 1
        self._id = f"sfmeter{StorageMeter._counter}"
        self.set_name(self._id)
        self.add_css_class("sf-meter")
        self.set_size_request(-1, height)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("destroy", lambda *_a: self._forget())
        self.set_fractions(0.0, 0.0)

    def _forget(self):
        StorageMeter._registry.pop(self._id, None)

    def set_fractions(self, used, queued, over=False):
        used = max(0.0, min(1.0, used))
        queued = max(0.0, min(1.0 - used, queued))
        StorageMeter._registry[self._id] = (used, queued, over)
        StorageMeter.restyle()

    @classmethod
    def restyle(cls):
        if cls._provider is None:
            cls._provider = Gtk.CssProvider()
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    cls._provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
                )
        rules = []
        for theme, selector in (
            ("dark", "window.shuffle"),
            ("light", "window.shuffle.light"),
        ):
            colours = PALETTE[theme]
            for name, (used, queued, over) in cls._registry.items():
                # Only the queued segment reddens when the queue will not fit.
                # Reddening the used segment too would erase the one thing the
                # bar is being asked: how much of this is already on the device
                # and how much is the part that overflows.
                filled = colours["accent"]
                pending = colours["danger"] if over else colours["queued"]
                first = used * 100
                second = (used + queued) * 100
                rules.append(
                    f"{selector} #{name} {{ background-image: linear-gradient("
                    f"to right, {filled} 0%, {filled} {first:.2f}%, "
                    f"{pending} {first:.2f}%, {pending} {second:.2f}%, "
                    f"{colours['meter']} {second:.2f}%, "
                    f"{colours['meter']} 100%); }}"
                )
        cls._provider.load_from_data("\n".join(rules), -1)


# --------------------------------------------------------------------- player

# What the now-playing bar is showing. "loading" is its own state rather than a
# flavour of playing because the gap is real and visible: a flushing seek or a
# track read over USB from the device leaves the bar with a title and no
# timeline yet, and a seek bar that accepts drags during that gap would
# silently drop them.
#
# "fetching" is the longer gap in front of that one, and separate from it
# because there is no pipeline at all yet: the file is still being downloaded,
# so there is nothing to pause, seek or step past for several seconds.
PLAY_IDLE = "idle"
PLAY_FETCHING = "fetching"
PLAY_LOADING = "loading"
PLAY_PLAYING = "playing"
PLAY_PAUSED = "paused"

# GStreamer has no position-changed signal, so the bar polls. 250ms is the
# coarsest interval at which the timeline still reads as tracking the audio
# rather than stepping along behind it.
PLAYER_POLL_MS = 250

# Polls to leave the sought position alone after a seek. A flushing seek
# usually reports its new position immediately, but a pipeline that has to
# pre-roll first answers with the old one for a frame or two, and the thumb
# springing back to where it was dragged from reads as the seek being refused.
SEEK_SETTLE_POLLS = 2

# Pressing previous this far into a track restarts it instead of stepping back,
# which is what every other player does and what the gesture usually means.
RESTART_WINDOW = 3.0

# What the bar has painted before it has painted anything. A sentinel rather
# than None, because None is also what "idle, showing the placeholder" looks
# like, and the first paint of a freshly built bar is exactly that - so
# comparing against None skipped it and left the artwork slot empty.
UNPAINTED = object()


class PreviewPlayer:
    """playbin3 driving the now-playing bar, on this computer's speakers.

    Preview only, and deliberately so: the shuffle has no way to be told what
    to play, so nothing here reaches the device beyond reading a file that
    happens to be mounted on it.

    The window owns the widgets and this owns the pipeline; the two meet at
    on_change, which fires whenever the bar would look different. Pushing a
    repaint rather than having the window poll keeps the transport, the end of
    a track and a decoding failure on one path.
    """

    def __init__(self, on_change):
        self.on_change = on_change
        self.track = None
        self.state = PLAY_IDLE
        self.position = 0.0
        self.duration = 0.0
        # The last playback failure, shown in the bar until something else is
        # played. Not a toast: the bar is where the user is already looking,
        # and it is the thing that stopped.
        self.error = None
        # The list the current track was started from, so previous and next
        # move through what the user was looking at rather than through the
        # whole library.
        self.queue = []
        self.index = -1
        self._pipeline = None
        self._poll = None
        self._settle = 0
        self._prerolled = False

    @property
    def seekable(self):
        return (
            self._pipeline is not None
            and self._prerolled
            and self.state in (PLAY_PLAYING, PLAY_PAUSED)
            and self.duration > 0
        )

    # --------------------------------------------------------------- control

    def play(self, tracks, index):
        """Start one track, with the rest of its list as the queue."""
        tracks = list(tracks)
        if not 0 <= index < len(tracks):
            return
        self.queue = tracks
        self.index = index
        self._start(tracks[index])

    def fetch(self, track):
        """Show a track that is still being downloaded before it can play.

        The wait belongs in the bar rather than in a spinner elsewhere: the
        download is happening because the user pressed play, and the bar is
        where the result of pressing play appears.
        """
        self._teardown()
        self.track = track
        self.state = PLAY_FETCHING
        self.error = None
        self.position = 0.0
        # From the search result, so the timeline is the right length before
        # there is a file to ask. Corrected once the pipeline opens it.
        self.duration = float(track.duration or 0)
        self.queue = [track]
        self.index = 0
        self._changed()

    def fail(self, track, message):
        """Give up on a track that never became playable at all.

        The queue goes with it: leaving the track in it would leave the play
        button offering to start a file that was never downloaded.
        """
        self.queue = []
        self.index = -1
        self._fail(track, message)

    def toggle(self):
        """Pause what is playing, resume what is paused, restart what ended."""
        if self.state == PLAY_FETCHING:
            # Nothing exists to pause yet. The transport is insensitive while a
            # download runs, so this is only reachable from the keyboard.
            return
        if self.state in (PLAY_PLAYING, PLAY_LOADING):
            # Loading counts as playing here. The pipeline is already on its
            # way to PLAYING, pressing the button again means "no, stop", and
            # a button that visibly does nothing for the second a track takes
            # to open reads as the transport being broken.
            self._set_pipeline_state("PAUSED", PLAY_PAUSED)
        elif self.state == PLAY_PAUSED:
            self._set_pipeline_state("PLAYING", PLAY_PLAYING)
        elif self.queue and 0 <= self.index < len(self.queue):
            self._start(self.queue[self.index])

    def previous(self):
        """Restart the track, or step back if it only just started."""
        if self.position > RESTART_WINDOW or self.index <= 0:
            self.seek(0.0)
            return
        self.index -= 1
        self._start(self.queue[self.index])

    def next(self):
        """Step forward, or stop at the end of the queue rather than wrap.

        Wrapping would make a queue started from one album play forever, and
        the bar gives no hint that it had looped.
        """
        if self.index + 1 >= len(self.queue):
            self.stop()
            return
        self.index += 1
        self._start(self.queue[self.index])

    def seek(self, fraction):
        """Jump to a fraction of the track, 0 to 1."""
        if not self.seekable:
            return
        module = gst()
        if module is None:
            return
        target = max(0.0, min(1.0, fraction)) * self.duration
        self._pipeline.seek_simple(
            module.Format.TIME,
            module.SeekFlags.FLUSH | module.SeekFlags.KEY_UNIT,
            int(target * module.SECOND),
        )
        self.position = target
        self._settle = SEEK_SETTLE_POLLS
        self._changed()

    def forget(self, paths):
        """Drop tracks whose files have gone.

        The queue is what previous and next walk and what the play button
        resumes, so a pruned or cleared preview left in it is a control that
        fails on every press.
        """
        paths = {str(path) for path in paths}
        if self.track is not None and self.track.path in paths:
            self.stop()
        remaining = [track for track in self.queue if track.path not in paths]
        if len(remaining) == len(self.queue):
            return
        current = self.queue[self.index] if 0 <= self.index < len(self.queue) else None
        self.queue = remaining
        if current in remaining:
            self.index = remaining.index(current)
        else:
            # Whatever was current has gone. Play then starts the queue from
            # the top rather than being a button that does nothing.
            self.index = 0 if remaining else -1
        self._changed()

    def stop(self):
        """Return to idle, keeping the queue so play can resume it."""
        self._teardown()
        self.track = None
        self.state = PLAY_IDLE
        self.position = 0.0
        self.duration = 0.0
        self._changed()

    def shutdown(self):
        """Release the pipeline on the way out of the window."""
        self._teardown()
        self._pipeline = None

    # -------------------------------------------------------------- internals

    def _fail(self, track, message):
        """Give up on a track, stopping whatever was playing before it.

        The teardown matters: without it a start that fails halfway leaves the
        previous track audible behind a bar that has already moved on to the
        one that did not open.
        """
        self._teardown()
        self.track = track
        self.state = PLAY_IDLE
        self.position = 0.0
        self.duration = 0.0
        self.error = message
        self._changed()

    def _start(self, track):
        module = gst()
        if module is None:
            self._fail(track, GSTREAMER_UNAVAILABLE)
            return

        pipeline = self._ensure_pipeline()
        if pipeline is None:
            self._fail(track, "GStreamer cannot build a playback pipeline")
            return

        try:
            uri = module.filename_to_uri(str(Path(track.path).absolute()))
        except GLib.Error:
            self._fail(track, "That file's location cannot be opened")
            return

        # NULL before the new URI, not just READY: playbin3 keeps the previous
        # stream's decoders around otherwise, and starting an m4a straight
        # after an mp3 then fails inside the old decoder rather than building
        # the right one.
        self._prerolled = False
        pipeline.set_state(module.State.NULL)
        pipeline.set_property("uri", uri)

        self.track = track
        self.error = None
        self.position = 0.0
        # Whatever the tags claimed, until the pipeline can be asked. Tagged
        # durations and decoded ones disagree often enough that starting from
        # the tag and correcting is better than an empty timeline.
        self.duration = float(track.duration or 0)
        self._settle = 0
        self._set_pipeline_state("PLAYING", PLAY_LOADING)
        self._start_polling()

    def _ensure_pipeline(self):
        module = gst()
        if module is None:
            return None
        if self._pipeline is None:
            pipeline = module.ElementFactory.make("playbin3", "preview")
            if pipeline is None:
                return None
            # Video would open a window of its own for a YouTube preview the
            # user asked to hear, so the sink is refused outright rather than
            # merely left unset.
            pipeline.set_property("video-sink", module.ElementFactory.make("fakesink"))
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            bus.connect("message::error", self._on_error)
            bus.connect("message::state-changed", self._on_state_changed)
            bus.connect("message::duration-changed", self._on_duration_changed)
            self._pipeline = pipeline
        return self._pipeline

    def _set_pipeline_state(self, name, state):
        """Move the pipeline, naming its state rather than passing the enum.

        Gst.State cannot be referenced from a call site that has to work on a
        machine where importing Gst fails, which is every call site here.
        """
        module = gst()
        if self._pipeline is None or module is None:
            return
        self._pipeline.set_state(getattr(module.State, name))
        self.state = state
        if state == PLAY_PLAYING:
            self._start_polling()
        elif state == PLAY_PAUSED:
            self._stop_polling()
        self._changed()

    def _start_polling(self):
        if self._poll is None:
            self._poll = GLib.timeout_add(PLAYER_POLL_MS, self._tick)

    def _stop_polling(self):
        if self._poll is not None:
            GLib.source_remove(self._poll)
            self._poll = None

    def _teardown(self):
        module = gst()
        if self._pipeline is not None and module is not None:
            self._pipeline.set_state(module.State.NULL)
        self._prerolled = False
        self._stop_polling()

    def _tick(self):
        if self._pipeline is None or self.state not in (PLAY_LOADING, PLAY_PLAYING):
            self._poll = None
            return False
        module = gst()
        if module is None:
            self._poll = None
            return False

        found, duration = self._pipeline.query_duration(module.Format.TIME)
        if found and duration > 0:
            self.duration = duration / module.SECOND
        if self._settle > 0:
            self._settle -= 1
        else:
            found, position = self._pipeline.query_position(module.Format.TIME)
            if found and position >= 0:
                self.position = position / module.SECOND
        self._changed()
        return True

    def _on_state_changed(self, _bus, message):
        # Every element in the pipeline reports its own transitions; only the
        # pipeline's own says whether audio is actually coming out.
        if message.src is not self._pipeline:
            return
        module = gst()
        if module is None:
            return
        _old, new, _pending = message.parse_state_changed()
        changed = False
        if (
            new in (module.State.PAUSED, module.State.PLAYING)
            and self.state in (PLAY_LOADING, PLAY_PLAYING, PLAY_PAUSED)
            and not self._prerolled
        ):
            self._prerolled = True
            changed = True
        if new == module.State.PLAYING and self.state in (PLAY_LOADING, PLAY_PLAYING):
            self.state = PLAY_PLAYING
            changed = True
        if changed:
            self._changed()

    def _on_duration_changed(self, _bus, _message):
        # Emitted once the demuxer knows better than the tag did. The next
        # poll reads the new value; this only makes sure a paused track still
        # gets a correct timeline.
        module = gst()
        if self._pipeline is None or module is None:
            return
        found, duration = self._pipeline.query_duration(module.Format.TIME)
        if found and duration > 0:
            self.duration = duration / module.SECOND
            self._changed()

    def _on_eos(self, _bus, _message):
        self.next()

    def _on_error(self, _bus, message):
        error, _debug = message.parse_error()
        self._teardown()
        self.state = PLAY_IDLE
        self.position = 0.0
        # GStreamer's own wording, which names the file and the missing
        # decoder. Rewriting it into something friendlier would throw away the
        # only part that says which plugin to install.
        self.error = error.message
        self._changed()

    def _changed(self):
        if self.on_change is not None:
            self.on_change()


# --------------------------------------------------------------------- window


class IpodWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Shuffle")
        self.set_default_size(1180, 760)
        # Below this the sidebar has already folded away and the now-playing
        # bar has nothing left to give, so stop rather than clipping.
        self.set_size_request(640, 560)
        self.add_css_class("shuffle")

        self.mount_point = None
        self.device_identity = None
        self.busy = False
        self.track_names = {}
        self.tag_generation = 0
        self.scan_generation = 0
        self.source_generation = 0
        self.discovering_sources = False
        self.loading_options = False
        self.loaded_playlist_mode = 0
        self.loaded_playlist_args = []
        self.speech_engine_available = has_speech_engine()
        self.playlist_unavailable = (
            None if self.speech_engine_available else "No speech engine installed"
        )
        self.youtube_unavailable = youtube_unavailable_reason()
        self.youtube_search_unavailable = youtube_search_unavailable_reason()
        self.preview_unavailable = preview_unavailable_reason()

        # Built before the device page, which mounts the bar the player drives.
        self.player = PreviewPlayer(self._update_now_playing)
        # Which cover the bar is showing, so a 250ms poll does not rebuild a
        # texture four times a second to draw the same artwork.
        self._painted_art = UNPAINTED

        self.search_query = ""
        self.search_generation = 0
        self.search_results = []
        self.search_loading = False
        # What the YouTube half of the search is currently unable to give, said
        # in place of the rows rather than in a toast: a toast has gone by the
        # time the eye returns to the empty space it was explaining.
        self.search_note = ""
        self.search_add_buttons = []
        self._search_timeout = None

        # Bumped whenever a preview is asked for, so a download that is still
        # running when the next one starts cannot land in the bar behind it.
        self.preview_generation = 0
        self._preview_process = None
        self._preview_lock = threading.Lock()
        self._preview_closed = False

        self.library = LibraryIndex()
        self.device_tracks = []
        self._device_scan_tracks = {}
        self._device_scan_active = False
        self._device_snapshot_ready = False
        self._library_scan_tracks = {}
        self._library_by_path = {}
        self.pending = set()
        self.pending_sources = {}
        self.pending_records = {}
        self.pending_skipped_symlinks = {}
        self._pending_track_index = {}
        self.pending_device_identity = None
        self.sync_files = []
        self.sync_total = 0
        # Setting a filter pill active while the view is still being built
        # fires its handler, which would repaint a grid that does not exist
        # yet. Flipped once the last widget is in place.
        self._library_ready = False
        self.current_album = None
        self.current_playlist = None
        self.playlists = []
        self.spoken = set()
        self.album_filter = "all"
        self.view_mode = "grid"

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)
        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_device_page(), "device")

        # Every widget an in-flight script must not be able to race. Collected
        # once rather than named individually in _set_busy, which previously
        # had to be edited every time the window grew a control.
        self._busy_widgets = [
            self.refresh_button,
            self.sync_button,
            self.add_button,
            self.playlist_button,
            self.youtube_button,
            self.rebuild_button,
            self.wipe_button,
            self.eject_button,
            self.sidebar_eject,
            self.new_playlist_button,
            # Whole views, because every track and playlist row carries a
            # button that would otherwise start a second script against the
            # same device while one is still running.
            self.library_view,
            self.search_view,
            self.album_view,
            self.playlists_view,
            # Typing while a script runs would repaint a view that is already
            # insensitive, which reads as the search being broken rather than
            # as the window being busy.
            self.search_entry,
        ]

        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_a: self._apply_theme())
        self._apply_theme()

        # React to the device being plugged in or unplugged, rather than making
        # the user press refresh. Polling would work but wastes wakeups.
        self.monitor = Gio.VolumeMonitor.get()
        for signal in ("mount-added", "mount-removed"):
            self.monitor.connect(signal, lambda *_a: GLib.idle_add(self.refresh))

        # A pipeline left in PLAYING outlives the window it was started from,
        # so closing the window would keep playing audio nothing on screen
        # could stop.
        self.connect("close-request", self._on_close_request)

        self._populate_cache_card()
        self.refresh()
        self._rescan_library()

    def _on_close_request(self, _window):
        self.player.shutdown()
        # A download outlives the window that started it otherwise, writing
        # into a cache nothing is left to show or prune.
        with self._preview_lock:
            self._preview_closed = True
        self._supersede_preview_fetch()
        return False

    def _apply_theme(self):
        if Adw.StyleManager.get_default().get_dark():
            self.remove_css_class("light")
        else:
            self.add_css_class("light")

    # ------------------------------------------------------------ empty page

    def _build_empty_page(self):
        self.empty_page = Adw.StatusPage(
            icon_name="multimedia-player-symbolic",
            title="No iPod Connected",
            description="Plug in an iPod shuffle and it will appear here.\n"
            "If it is already connected, it may need mounting.",
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )
        self.mount_button = Gtk.Button(label="Mount Connected iPod")
        self.mount_button.set_halign(Gtk.Align.CENTER)
        self.mount_button.add_css_class("pill")
        self.mount_button.add_css_class("suggested-action")
        self.mount_button.connect("clicked", self.on_mount_clicked)
        box.append(self.mount_button)
        box.append(
            label(
                "Your library, search and preview playback do not need a device.",
                "sf-caption",
                xalign=0.5,
            )
        )
        self.empty_page.set_child(box)
        return self.empty_page

    # ----------------------------------------------------------- device page

    def _build_device_page(self):
        toolbar = Adw.ToolbarView()

        self.split = Adw.OverlaySplitView(
            sidebar_width_fraction=0.2,
            min_sidebar_width=236,
            max_sidebar_width=236,
        )
        self.split.set_sidebar(self._build_sidebar())
        toolbar.set_content(self.split)

        self.views = Adw.ViewStack()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.add_css_class("sf-content")
        content.append(self._build_header())
        content.append(self.views)
        self.views.set_vexpand(True)
        self.split.set_content(content)

        self.views.add_named(self._build_library_view(), "library")
        self.views.add_named(self._build_search_view(), "search")
        self.views.add_named(self._build_album_view(), "album")
        self.views.add_named(self._build_playlists_view(), "playlists")
        self.views.add_named(self._build_settings_view(), "settings")

        toolbar.add_bottom_bar(self._build_sync_bar())
        toolbar.add_bottom_bar(self._build_now_playing_bar())

        # Below this width the sidebar stops being furniture and starts being
        # most of the window, so it folds away behind the toggle instead.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 780px")
        )
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        return toolbar

    def _build_header(self):
        header = Gtk.Box(spacing=12)
        header.set_size_request(-1, 48)
        header.set_margin_start(18)
        header.set_margin_end(12)

        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.add_css_class("flat")
        self.sidebar_toggle.set_valign(Gtk.Align.CENTER)
        # Only worth showing once the sidebar has folded away; while it is
        # visible the button would toggle something already on screen.
        self.split.bind_property(
            "collapsed",
            self.sidebar_toggle,
            "visible",
            GObject.BindingFlags.SYNC_CREATE,
        )
        self.split.bind_property(
            "show-sidebar",
            self.sidebar_toggle,
            "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        header.append(self.sidebar_toggle)

        self.view_title = label("Your Library", "sf-section-heading")
        self.view_title.set_valign(Gtk.Align.CENTER)
        header.append(self.view_title)

        header.append(Gtk.Box(hexpand=True))

        # One field over both sources. Which of them a result came from is
        # said by the section it lands in, not by asking the user to choose a
        # source before they have typed anything.
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search your library and YouTube"
        )
        self.search_entry.add_css_class("sf-search")
        self.search_entry.set_size_request(260, -1)
        self.search_entry.set_valign(Gtk.Align.CENTER)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", lambda _e: self._clear_search())
        header.append(self.search_entry)

        # Only meaningful on the library, which is the only view with a grid
        # to group or to swap for a table.
        self.library_controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)

        self.group_mode = Gtk.DropDown.new_from_strings(["Album", "Artist"])
        self.group_mode.add_css_class("flat")
        self.group_mode.set_tooltip_text("Group the library by album or by artist")
        self.group_mode.connect("notify::selected", lambda *_a: self._populate_albums())
        self.library_controls.append(self.group_mode)

        modes = Gtk.Box()
        modes.add_css_class("linked")
        self.mode_buttons = {}
        for mode, icon, tip in (
            ("grid", "view-grid-symbolic", "Show covers as a grid"),
            ("list", "view-list-symbolic", "Show every track as a sortable table"),
        ):
            button = Gtk.ToggleButton(icon_name=icon)
            button.set_tooltip_text(tip)
            button.connect("toggled", self._on_view_mode_toggled, mode)
            modes.append(button)
            self.mode_buttons[mode] = button
        self.mode_buttons["grid"].set_active(True)
        self.library_controls.append(modes)
        header.append(self.library_controls)

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_button.add_css_class("flat")
        self.refresh_button.set_valign(Gtk.Align.CENTER)
        self.refresh_button.set_tooltip_text("Rescan the device and your music folders")
        self.refresh_button.connect("clicked", self.on_refresh_clicked)
        header.append(self.refresh_button)

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrapper.append(header)
        line = Gtk.Separator()
        wrapper.append(line)
        return wrapper

    # -------------------------------------------------------------- sidebar

    def _build_sidebar(self):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.add_css_class("sf-sidebar")
        sidebar.set_size_request(236, -1)

        brand = Gtk.Box(spacing=9)
        brand.set_margin_start(14)
        brand.set_margin_end(14)
        brand.set_margin_top(12)
        brand.set_margin_bottom(6)
        badge = label("S", "sf-brand", xalign=0.5, yalign=0.5)
        badge.set_size_request(24, 24)
        brand.append(badge)
        brand.append(label("Shuffle", "sf-row-title", valign=Gtk.Align.CENTER))
        sidebar.append(brand)

        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        nav.set_margin_start(10)
        nav.set_margin_end(10)
        nav.set_margin_top(6)
        self.nav_buttons = {}
        for name, icon, title in (
            ("library", "view-grid-symbolic", "Your Library"),
            ("playlists", "view-list-symbolic", "Playlists"),
        ):
            button = self._nav_button(name, icon, title)
            nav.append(button)
            self.nav_buttons[name] = button
        sidebar.append(nav)

        sidebar.append(
            self._sidebar_label("Playlists", margin_top=14)
        )
        self.playlist_rail = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, vexpand=True
        )
        self.playlist_rail.set_margin_start(10)
        self.playlist_rail.set_margin_end(10)
        rail_scroll = Gtk.ScrolledWindow(vexpand=True)
        rail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroll.set_child(self.playlist_rail)
        sidebar.append(rail_scroll)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)

        self.device_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.device_card.add_css_class("sf-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        inner.set_margin_top(11)
        inner.set_margin_bottom(11)
        self.device_card.append(inner)

        top = Gtk.Box(spacing=8)
        self.device_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.device_dot.add_css_class("sf-dot")
        self.device_dot.add_css_class("library")
        top.append(self.device_dot)
        self.device_name = label("No iPod", "sf-row-title", hexpand=True, ellipsize=ELLIPSIZE_END)
        top.append(self.device_name)
        self.sidebar_eject = Gtk.Button(icon_name="media-eject-symbolic")
        self.sidebar_eject.add_css_class("flat")
        self.sidebar_eject.set_valign(Gtk.Align.CENTER)
        self.sidebar_eject.set_tooltip_text("Unmount safely before unplugging")
        self.sidebar_eject.connect("clicked", self.on_eject)
        top.append(self.sidebar_eject)
        inner.append(top)

        self.sidebar_meter = StorageMeter()
        inner.append(self.sidebar_meter)

        figures = Gtk.Box()
        self.device_free = label("", "sf-caption", hexpand=True)
        figures.append(self.device_free)
        self.device_count = label("", "sf-caption", "sf-mono")
        figures.append(self.device_count)
        inner.append(figures)

        self.queued_row = Gtk.Box(spacing=6)
        queued_swatch = Gtk.Box(valign=Gtk.Align.CENTER)
        queued_swatch.add_css_class("sf-dot")
        queued_swatch.add_css_class("ipod")
        self.queued_row.append(queued_swatch)
        self.queued_label = label("", "sf-caption", "sf-accent")
        self.queued_row.append(self.queued_label)
        self.queued_row.set_visible(False)
        inner.append(self.queued_row)

        footer.append(self.device_card)
        settings_button = self._nav_button(
            "settings", "emblem-system-symbolic", "Device & Settings"
        )
        self.nav_buttons["settings"] = settings_button
        footer.append(settings_button)
        sidebar.append(footer)

        return sidebar

    def _sidebar_label(self, text, margin_top=0):
        widget = label(text.upper(), "sf-section-label")
        widget.set_margin_start(20)
        widget.set_margin_end(20)
        widget.set_margin_top(margin_top)
        widget.set_margin_bottom(6)
        return widget

    def _nav_button(self, name, icon, title):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-nav-row")
        box = Gtk.Box(spacing=11)
        image = Gtk.Image.new_from_icon_name(icon)
        box.append(image)
        box.append(label(title))
        button.set_child(box)
        button.connect("clicked", lambda _b, n=name: self._navigate(n))
        return button

    def _navigate(self, name):
        """Follow a sidebar row, ending the search that row is leaving.

        Leaving the results up behind a field that still holds the query would
        make the next keystroke reopen a view the user had just navigated out
        of, and the sidebar would show a destination that is not on screen.
        """
        self._clear_search()
        self.show_view(name)

    # ---------------------------------------------------------- library view

    def _build_library_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.library_view = scroller

        shelf_head = Gtk.Box(spacing=10)
        shelf_head.append(label("Playlists", "sf-section-heading"))
        shelf_head.append(
            label(
                "Names exist on the device only as spoken audio",
                "sf-body",
                valign=Gtk.Align.BASELINE,
                wrap=True,
            )
        )
        self.shelf_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        self.shelf_section.append(shelf_head)
        self.playlist_shelf = Gtk.Box(spacing=12)
        shelf_scroll = Gtk.ScrolledWindow()
        shelf_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        shelf_scroll.set_child(self.playlist_shelf)
        self.shelf_section.append(shelf_scroll)
        box.append(self.shelf_section)

        # Built before the filter pills, whose set_active below fires the
        # toggled handler immediately and repaints the grid.
        self.album_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=8,
            min_children_per_line=2,
            column_spacing=16,
            row_spacing=16,
            homogeneous=True,
            valign=Gtk.Align.START,
        )
        self.album_flow.add_css_class("sf-flow")

        albums_head = Gtk.Box(spacing=8)
        self.collection_heading = label("Albums", "sf-section-heading")
        albums_head.append(self.collection_heading)
        self.album_filters = {}
        for key, text in (
            ("all", "All"),
            (STATE_IPOD, "On iPod"),
            (STATE_LIBRARY, "In library"),
            (STATE_PREVIEW, "Previewed"),
        ):
            pill = Gtk.ToggleButton()
            pill.add_css_class("sf-pill")
            pill.set_valign(Gtk.Align.CENTER)
            pill.set_child(label(text, xalign=0.5))
            pill.connect("toggled", self._on_filter_toggled, key)
            albums_head.append(pill)
            self.album_filters[key] = pill
        self.album_filters["all"].add_css_class("selected")
        self.album_filters["all"].set_active(True)
        box.append(albums_head)

        # The grid and the flat table are two ways of reading the same
        # library, so they share a stack rather than a scroll position.
        self.library_modes = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE
        )
        self.library_modes.add_named(self.album_flow, "grid")
        self.library_table = track_column_view(self)
        self.library_table.set_vexpand(True)
        self.library_modes.add_named(self.library_table, "list")
        box.append(self.library_modes)

        self.library_status = label("Reading your music folders…", "sf-body")
        box.append(self.library_status)
        self._library_ready = True
        return scroller

    def _on_view_mode_toggled(self, button, mode):
        if not button.get_active():
            # Refuse to leave neither selected; this is a choice of two.
            if self.view_mode == mode:
                button.set_active(True)
            return
        self.view_mode = mode
        # The header is built before the views it controls, so the initial
        # set_active arrives before there is a stack to switch.
        if not self._library_ready:
            return
        for name, other in self.mode_buttons.items():
            if name != mode:
                other.set_active(False)
        self.library_modes.set_visible_child_name(mode)
        # Grouping is a property of the grid; the table is always every track.
        self.group_mode.set_sensitive(mode == "grid")
        self._populate_albums()

    def _on_filter_toggled(self, button, key):
        if not button.get_active():
            if self.album_filter == key:
                button.set_active(True)
            return
        self.album_filter = key
        for name, pill in self.album_filters.items():
            if name == key:
                pill.add_css_class("selected")
            else:
                pill.remove_css_class("selected")
                pill.set_active(False)
        self._populate_albums()

    # ----------------------------------------------------------- search view

    def _build_search_view(self):
        """Both sources on one page, local first because it is instant.

        Two sections rather than one merged list: a file you already have and
        a video you would have to download are not the same offer, and sorting
        them together would bury the free half under the expensive one.
        """
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.search_view = scroller

        local = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        head = Gtk.Box(spacing=8)
        head.append(label("In your library", "sf-section-heading"))
        self.search_local_count = label(
            "", "sf-caption", valign=Gtk.Align.BASELINE
        )
        head.append(self.search_local_count)
        local.append(head)
        self.search_local_note = label("", "sf-body", wrap=True)
        local.append(self.search_local_note)
        self.search_local_table = track_column_view(
            self, columns=("title", "album", "state", "duration", "action")
        )
        local.append(self.search_local_table)
        box.append(local)

        remote = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        head = Gtk.Box(spacing=8)
        head.append(label("From YouTube", "sf-section-heading"))
        self.search_youtube_count = label(
            "", "sf-caption", valign=Gtk.Align.BASELINE
        )
        head.append(self.search_youtube_count)
        remote.append(head)
        self.search_youtube_note = label("", "sf-body", wrap=True)
        remote.append(self.search_youtube_note)
        self.search_youtube_rows = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        remote.append(self.search_youtube_rows)
        box.append(remote)
        return scroller

    @staticmethod
    def _skeleton_row():
        """A placeholder exactly as tall as the result that replaces it.

        The two lines are real labels carrying a result's own text classes with
        the grey bar drawn over them, rather than bars of a chosen height: the
        row has to come out the same number of pixels tall as what lands in its
        place, and that number belongs to the font rather than to anything this
        code could hardcode and keep correct.
        """
        row = Gtk.Box(spacing=12)
        row.add_css_class("sf-track-row")
        row.add_css_class("sf-result-row")
        art = Gtk.Box(valign=Gtk.Align.CENTER)
        art.add_css_class("sf-skeleton")
        art.set_size_request(36, 36)
        row.append(art)

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            hexpand=True,
        )
        for width, style in ((210, "sf-row-title"), (120, "sf-body")):
            line = Gtk.Overlay()
            line.set_child(label(" ", style))
            bar = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
            bar.add_css_class("sf-skeleton")
            bar.set_size_request(width, 9)
            line.add_overlay(bar)
            text.append(line)
        row.append(text)
        return row

    def _youtube_row(self, result):
        row = Gtk.Box(spacing=12)
        row.add_css_class("sf-track-row")
        row.add_css_class("sf-result-row")
        row.append(self._preview_cover(result))

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        text.append(label(result.title, "sf-row-title", ellipsize=ELLIPSIZE_END))
        text.append(label(result.uploader, "sf-body", ellipsize=ELLIPSIZE_END))
        row.append(text)

        row.append(
            label(
                human_duration(result.duration) if result.duration else "--:--",
                "sf-caption",
                "sf-mono",
                width_chars=5,
                xalign=1.0,
                valign=Gtk.Align.CENTER,
            )
        )

        add = Gtk.Button(label="Add")
        add.add_css_class("sf-button")
        add.add_css_class("accent")
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda _b, r=result: self._download_result(r))
        add.set_sensitive(self._can_download())
        add.set_tooltip_text(self._youtube_download_tooltip())
        row.append(add)
        self.search_add_buttons.append(add)
        return row

    def _preview_cover(self, result):
        """A result's artwork, which plays it under the pointer.

        The video's thumbnail once it has been fetched, and until then the
        same generated placeholder a local track with no embedded cover gets.
        Behind the same hover-to-play button a library row has, so the two
        halves of the page read as one list rather than as two widgets that
        happen to be stacked.
        """
        overlay = Gtk.Overlay(valign=Gtk.Align.CENTER)
        overlay.set_child(
            make_cover(
                youtube_art_path(result.video_id, ART_CACHE),
                36,
                result.video_id or result.title,
                "small",
            )
        )
        play = Gtk.Button(icon_name="media-playback-start-symbolic")
        play.add_css_class("sf-cover-play")
        play.set_size_request(36, 36)
        reason = self._preview_unavailable_reason(result)
        if reason:
            play.set_sensitive(False)
            play.set_tooltip_text(reason)
        else:
            play.set_tooltip_text(f"Preview {result.title} on this computer")
        play.connect("clicked", lambda _b, r=result: self.preview_result(r))
        overlay.add_overlay(play)
        return overlay

    def _preview_unavailable_reason(self, result):
        """Why this result cannot be previewed, or None.

        A result already in the cache needs nothing but GStreamer to play. One
        that is not has to be downloaded first, so it needs everything a
        download needs - and saying so on the button is the difference between
        a disabled control and one that fails several steps later.
        """
        if self.preview_unavailable:
            return self.preview_unavailable
        if cached_preview_path(result.video_id, PREVIEW_CACHE) is not None:
            return None
        return self.youtube_unavailable

    def _youtube_download_tooltip(self):
        if self.youtube_unavailable:
            return self.youtube_unavailable
        if not self.mount_point:
            return "Connect an iPod to download and queue a track"
        return None

    def _can_download(self):
        """Whether a search result could be fetched and queued right now."""
        return bool(
            self.mount_point
            and self.device_identity is not None
            and not self.busy
            and not self.discovering_sources
            and not self.youtube_unavailable
        )

    def _paint_local_results(self):
        matches = local_search_matches(
            self.library.all_tracks(), self.search_query
        )
        fill_tracks(self.search_local_table, matches)
        self.search_local_count.set_text(
            plural(len(matches), "track") if matches else ""
        )
        self.search_local_table.set_visible(bool(matches))
        self.search_local_note.set_text(
            "" if matches else "Nothing in your music folders matches this search."
        )
        self.search_local_note.set_visible(not matches)

    def _paint_youtube_section(self):
        """Repaint the YouTube half from whatever state it is in.

        One function for the skeleton, the results and every failure, because
        all of them are alternatives for the same space: the section shows rows
        or it shows the sentence saying why it cannot, and the sentence lives
        in search_note rather than in an argument so that a repaint triggered
        by something else - a device appearing, a sync finishing - cannot
        quietly erase it.
        """
        self.search_add_buttons = []
        child = self.search_youtube_rows.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.search_youtube_rows.remove(child)
            child = nxt

        if self.search_loading:
            for _ in range(YOUTUBE_SEARCH_RESULTS):
                self.search_youtube_rows.append(self._skeleton_row())
            self.search_youtube_count.set_text("Searching…")
            self.search_youtube_note.set_visible(False)
            return

        for result in self.search_results:
            self.search_youtube_rows.append(self._youtube_row(result))
        self.search_youtube_count.set_text(
            plural(len(self.search_results), "result") if self.search_results else ""
        )

        note = self.search_note
        # Results can be listed and still not be addable. Metadata needs no
        # JavaScript runtime and no ffmpeg; the download needs both, and
        # letting the user press Add and watch it fail several steps later is
        # exactly the confusion the capability checks exist to prevent.
        if not note and self.search_results and self.youtube_unavailable:
            note = (
                f"Downloads are unavailable: {self.youtube_unavailable}. "
                "These are search results only."
            )
        self.search_youtube_note.set_text(note)
        self.search_youtube_note.set_visible(bool(note))

    def _set_search_note(self, text):
        self.search_note = text
        self._paint_youtube_section()

    def _cancel_search_timeout(self):
        if self._search_timeout is not None:
            GLib.source_remove(self._search_timeout)
            self._search_timeout = None

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        if query == self.search_query:
            return
        self.search_query = query
        self._cancel_search_timeout()
        # Bumped on every keystroke so a search still in flight for an older
        # query cannot land on a newer one, the same guard the library scan uses.
        self.search_generation += 1
        if not query:
            self._clear_search()
            return

        self.show_view("search")
        self._paint_local_results()

        self.search_results = []
        if self.youtube_search_unavailable:
            self.search_loading = False
            self._set_search_note(
                f"{self.youtube_search_unavailable}. Your music folders are "
                "still searched."
            )
            return
        if len(query) < SEARCH_MIN_QUERY:
            self.search_loading = False
            self._set_search_note("Type a little more to search YouTube.")
            return

        # The library filters on every keystroke because it is already in
        # memory; YouTube costs a network round trip, so it waits for a pause.
        self.search_loading = True
        self._set_search_note("")
        self._search_timeout = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS,
            self._start_youtube_search,
            self.search_generation,
            query,
        )

    def _start_youtube_search(self, generation, query):
        self._search_timeout = None
        if generation != self.search_generation:
            return False

        def worker():
            results, reached = search_youtube(query)
            GLib.idle_add(
                self._finish_youtube_search, generation, results, reached
            )

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _finish_youtube_search(self, generation, results, reached):
        if generation != self.search_generation:
            return False
        self.search_loading = False
        self.search_results = results
        if not reached:
            self._set_search_note(
                "Could not reach YouTube. Check the connection, or wait a "
                "moment if several searches have just run in a row."
            )
        elif not results:
            self._set_search_note("No YouTube results for this search.")
        else:
            self._set_search_note("")
        self._start_thumbnail_fetch(generation, results)
        return False

    def _start_thumbnail_fetch(self, generation, results):
        """Fetch the results' artwork behind the rows that are already up.

        After painting rather than before it: the titles are what the search
        was for, and holding a list of results back for a set of images would
        spend the fast half of the search on the slow half. Nothing moves when
        they land either - each cover drops into a frame that was already
        exactly its size, holding the placeholder.
        """
        wanted = [
            result
            for result in results
            if result.thumbnail
            and youtube_art_path(result.video_id, ART_CACHE) is None
        ]
        if not wanted:
            return

        def worker():
            landed = False
            for result in wanted:
                if cache_thumbnail(result.video_id, result.thumbnail, ART_CACHE):
                    landed = True
            # One repaint for the set rather than one per image: rebuilding the
            # rows three times under the pointer would flicker the hover state
            # of a button the user may already be reaching for.
            if landed:
                GLib.idle_add(self._finish_thumbnail_fetch, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_thumbnail_fetch(self, generation):
        """Repaint the results with the artwork that has arrived for them."""
        tracks = {
            id(track): track
            for track in (
                *self.library.all_tracks(),
                *self.player.queue,
                self.player.track,
            )
            if track is not None
        }
        art_changed = False
        for track in tracks.values():
            if track.art is None:
                track.art = cached_thumbnail_for(track.path)
                art_changed = art_changed or track.art is not None
        if art_changed:
            self._refresh_current_view()
            self._update_now_playing()
        if generation == self.search_generation:
            self._paint_youtube_section()
        return False

    def _clear_search(self):
        self._cancel_search_timeout()
        self.search_generation += 1
        self.search_query = ""
        self.search_loading = False
        self.search_results = []
        self.search_note = ""
        # Emptied last, so the search-changed this fires finds the state it
        # would have set already in place and stops rather than recursing.
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        if self.views.get_visible_child_name() == "search":
            self.show_view("library")

    def _download_result(self, result):
        if not self._can_download():
            return
        # Clears whatever an earlier attempt left behind, so a retry that works
        # does not sit under the sentence saying the last one failed.
        self._set_search_note("")
        self._start_youtube_download(
            result.url,
            single=True,
            busy_message=f"Downloading {result.title}",
            on_failure=lambda: self._report_download_failure(result),
        )

    def _report_download_failure(self, result):
        """Say in the section that this particular download did not finish.

        The toast is gone by the time the eye returns to the row that was being
        added, and that row is what the user is looking at.
        """
        if self.views.get_visible_child_name() != "search":
            return
        self._set_search_note(
            f"Could not finish downloading {result.title}. Details has what "
            "yt-dlp reported; ./ipod-fetch.sh --update is the usual fix when "
            "downloads stop working."
        )

    # ------------------------------------------------------------ album view

    def _build_album_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.album_view = scroller

        back = Gtk.Button(label="‹  Your Library", halign=Gtk.Align.START)
        back.add_css_class("flat")
        back.add_css_class("sf-caption")
        back.connect("clicked", lambda _b: self.show_view("library"))
        box.append(back)

        header = Gtk.Box(spacing=20)
        self.album_art_holder = Gtk.Box()
        header.append(self.album_art_holder)
        meta = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.END
        )
        self.album_heading = label("", "sf-album-title", ellipsize=ELLIPSIZE_END)
        meta.append(self.album_heading)
        self.album_subheading = label("", "sf-body")
        meta.append(self.album_subheading)
        self.album_actions = Gtk.Box(spacing=8)
        meta.append(self.album_actions)
        header.append(meta)
        box.append(header)

        self.album_tracks = track_column_view(
            self, columns=("number", "title", "state", "duration", "action")
        )
        box.append(self.album_tracks)
        return scroller

    # -------------------------------------------------------- playlists view

    def _build_playlists_view(self):
        outer = Gtk.Box(spacing=0, vexpand=True)
        self.playlists_view = outer

        rail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rail_box.set_size_request(240, -1)
        rail_box.set_margin_start(22)
        rail_box.set_margin_end(12)
        rail_box.set_margin_top(20)
        rail_box.set_margin_bottom(20)
        head = Gtk.Box(spacing=8)
        head.append(label("Playlists", "sf-section-heading", hexpand=True))
        self.new_playlist_button = Gtk.Button(label="＋ New")
        self.new_playlist_button.add_css_class("sf-button")
        self.new_playlist_button.set_valign(Gtk.Align.CENTER)
        self.new_playlist_button.connect("clicked", self.on_add_playlist)
        head.append(self.new_playlist_button)
        rail_box.append(head)

        self.playlist_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rail_scroll = Gtk.ScrolledWindow(vexpand=True)
        rail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroll.set_child(self.playlist_list)
        rail_box.append(rail_scroll)
        outer.append(rail_box)

        outer.append(Gtk.Separator())

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        detail.set_margin_start(18)
        detail.set_margin_end(22)
        detail.set_margin_top(20)
        detail.set_margin_bottom(20)
        self.playlist_heading = label(
            "", "sf-heading", ellipsize=ELLIPSIZE_END, max_width_chars=24
        )
        detail.append(self.playlist_heading)
        self.playlist_voice_note = Gtk.Box(spacing=7)
        self.playlist_voice_note.set_halign(Gtk.Align.START)
        detail.append(self.playlist_voice_note)
        self.playlist_tracks = track_list_view(self, self._reorder_playlist)
        detail_scroll = Gtk.ScrolledWindow(vexpand=True)
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_child(self.playlist_tracks)
        detail.append(detail_scroll)
        outer.append(detail)
        return outer

    # --------------------------------------------------------- settings view

    def _build_settings_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(18)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.settings_view = scroller

        box.append(self._build_device_summary())
        columns = Gtk.Box(spacing=16, homogeneous=True)
        left = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True
        )
        right = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True
        )
        columns.append(left)
        columns.append(right)
        box.append(columns)

        left.append(self._build_sync_options())
        right.append(self._build_folders_card())
        right.append(self._build_cache_card())
        right.append(self._build_destructive_card())
        return scroller

    def _card(self, title, note=""):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("sf-card")
        head = Gtk.Box(spacing=7)
        head.set_margin_start(14)
        head.set_margin_end(14)
        head.set_margin_top(12)
        head.set_margin_bottom(10)
        head.append(label(title, "sf-row-title"))
        if note:
            head.append(
                label(note, "sf-caption", valign=Gtk.Align.BASELINE, wrap=True)
            )
        card.append(head)
        card.append(Gtk.Separator())
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.append(body)
        return card, body

    def _build_device_summary(self):
        card = Gtk.Box(spacing=20)
        card.add_css_class("sf-card")
        inner = Gtk.Box(spacing=20, hexpand=True)
        inner.set_margin_start(16)
        inner.set_margin_end(16)
        inner.set_margin_top(16)
        inner.set_margin_bottom(16)
        card.append(inner)

        icon = Gtk.Image.new_from_icon_name("multimedia-player-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("sf-dim")
        inner.append(icon)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        name_row = Gtk.Box(spacing=9)
        self.settings_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.settings_dot.add_css_class("sf-dot")
        self.settings_dot.add_css_class("library")
        name_row.append(self.settings_dot)
        self.settings_name = label(
            "", "sf-heading", ellipsize=ELLIPSIZE_END, max_width_chars=20
        )
        name_row.append(self.settings_name)
        self.settings_path = label(
            "",
            "sf-caption",
            "sf-mono",
            valign=Gtk.Align.END,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            max_width_chars=28,
        )
        name_row.append(self.settings_path)
        meta.append(name_row)

        self.settings_meter = StorageMeter(height=9)
        meta.append(self.settings_meter)

        self.settings_figures = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=4,
            min_children_per_line=1,
            column_spacing=18,
            row_spacing=6,
        )
        self.settings_figures.add_css_class("sf-flow")
        meta.append(self.settings_figures)
        inner.append(meta)

        self.sync_button = Gtk.Button(label="Sync")
        self.sync_button.add_css_class("sf-button")
        self.sync_button.add_css_class("accent")
        self.sync_button.set_valign(Gtk.Align.CENTER)
        self.sync_button.connect("clicked", self.on_sync_pending)
        inner.append(self.sync_button)

        self.device_banner = Gtk.Box(spacing=9)
        self.device_banner.add_css_class("sf-warn-card")
        self.device_banner.set_visible(False)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        wrapper.append(card)
        wrapper.append(self.device_banner)
        return wrapper

    def _build_sync_options(self):
        card, body = self._card(
            "Sync options", "· applied on every sync and rebuild"
        )
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        # Playlist names are stored only as spoken audio, never as text, since
        # the device has no screen. Choosing a grouping therefore implies
        # wanting the names read aloud, so that switch follows along.
        self.playlist_mode = Adw.ComboRow(
            title="Playlist grouping",
            subtitle="How tracks are grouped into playlists when syncing",
            model=Gtk.StringList.new(
                ["None", "One per folder", "By artist", "By genre"]
            ),
        )
        self.playlist_mode.connect("notify::selected", self._on_playlist_mode_changed)
        listbox.append(self.playlist_mode)

        self.track_voiceover = Adw.SwitchRow(
            title="Speak track names",
            subtitle="Announced when you press the VoiceOver button",
        )
        listbox.append(self.track_voiceover)

        self.playlist_voiceover = Adw.SwitchRow(
            title="Speak playlist names",
            subtitle="Without this, playlists cannot be told apart on a "
            "screenless device",
        )
        listbox.append(self.playlist_voiceover)

        for row in (self.track_voiceover, self.playlist_voiceover):
            row.connect("notify::active", self._on_voiceover_changed)

        if not self.speech_engine_available:
            for row in (self.track_voiceover, self.playlist_voiceover):
                row.set_sensitive(False)

        listbox.set_margin_start(14)
        listbox.set_margin_end(14)
        listbox.set_margin_top(10)
        listbox.set_margin_bottom(12)
        body.append(listbox)

        if not self.speech_engine_available:
            warn = Gtk.Box(spacing=9)
            warn.add_css_class("sf-warn-card")
            warn.set_margin_start(14)
            warn.set_margin_end(14)
            warn.set_margin_bottom(12)
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.set_margin_start(11)
            icon.set_margin_top(11)
            icon.set_valign(Gtk.Align.START)
            warn.append(icon)
            text = label(
                "No speech engine installed, so spoken names cannot be "
                "generated. Install pico2wave, espeak or say to enable the "
                "two switches above.",
                "sf-caption",
                wrap=True,
                hexpand=True,
            )
            text.set_margin_end(11)
            text.set_margin_top(11)
            text.set_margin_bottom(11)
            warn.append(text)
            body.append(warn)

        # A plain row of three buttons sets a minimum width the whole window
        # then has to honour, so they wrap instead.
        actions = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=3,
            min_children_per_line=1,
            column_spacing=8,
            row_spacing=8,
        )
        actions.add_css_class("sf-flow")
        actions.set_margin_start(14)
        actions.set_margin_end(14)
        actions.set_margin_bottom(14)
        self.add_button = Gtk.Button(label="Add music folder…")
        self.add_button.add_css_class("sf-button")
        self.add_button.connect("clicked", self.on_add_music)
        actions.append(self.add_button)

        self.playlist_button = Gtk.Button(label="Add playlist file…")
        self.playlist_button.add_css_class("sf-button")
        self.playlist_button.connect("clicked", self.on_add_playlist)
        if self.playlist_unavailable:
            self.playlist_button.set_tooltip_text(self.playlist_unavailable)
        actions.append(self.playlist_button)

        self.youtube_button = Gtk.Button(label="Add from YouTube…")
        self.youtube_button.add_css_class("sf-button")
        self.youtube_button.connect("clicked", self.on_add_youtube)
        # Better to say why than to let the user paste a link and watch the
        # download fail several steps later for a reason the log explains only
        # to someone who already knows what to look for.
        if self.youtube_unavailable:
            self.youtube_button.set_tooltip_text(self.youtube_unavailable)
        actions.append(self.youtube_button)
        body.append(actions)
        return card

    def _build_folders_card(self):
        card, body = self._card("Music folders", "· searched for local results")
        self.folder_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.folder_list)
        add = Gtk.Button(label="＋ Add folder…")
        add.add_css_class("flat")
        add.add_css_class("sf-accent")
        add.set_halign(Gtk.Align.START)
        add.set_margin_start(10)
        add.set_margin_bottom(8)
        add.connect("clicked", self.on_add_folder)
        body.append(add)
        return card

    def _build_cache_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("sf-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_margin_start(14)
        inner.set_margin_end(14)
        inner.set_margin_top(14)
        inner.set_margin_bottom(14)
        card.append(inner)

        head = Gtk.Box(spacing=8)
        head.append(label("Preview cache", "sf-row-title"))
        head.append(
            label(
                "auto-downloaded so you could hear a result",
                "sf-caption",
                valign=Gtk.Align.BASELINE,
                wrap=True,
            )
        )
        inner.append(head)

        row = Gtk.Box(spacing=12)
        self.cache_meter = StorageMeter(height=6)
        row.append(self.cache_meter)
        self.cache_figure = label("0 B · 0 files", "sf-caption", "sf-mono")
        row.append(self.cache_figure)
        self.cache_clear = Gtk.Button(label="Clear")
        self.cache_clear.add_css_class("sf-button")
        self.cache_clear.set_sensitive(False)
        self.cache_clear.connect("clicked", self.on_clear_cache)
        row.append(self.cache_clear)
        inner.append(row)

        inner.append(
            label(
                f"Adding a previewed track moves it into "
                f"{home_relative(YOUTUBE_LIBRARY)} and out of this cache. Past "
                f"{human_size(PREVIEW_CACHE_LIMIT)} the oldest previews are "
                "dropped to make room; nothing here is on your iPod.",
                "sf-caption",
                wrap=True,
            )
        )
        return card

    def _populate_cache_card(self):
        previews = self.library.previews
        total = sum(track.size for track in previews)
        self.cache_figure.set_text(
            f"{human_size(total)} · {plural(len(previews), 'file')}"
        )
        self.cache_meter.set_fractions(
            total / PREVIEW_CACHE_LIMIT if PREVIEW_CACHE_LIMIT else 0.0, 0.0
        )
        self.cache_clear.set_sensitive(bool(previews) and not self.busy)

    def on_clear_cache(self, _button):
        """Throw the whole cache away.

        No confirmation, unlike everything in the destructive card: nothing
        here is on the iPod, nothing here is in a music folder, and every one
        of these files can be downloaded again. The tree is removed rather than
        the tracks that are listed, so anything a half-finished download left
        behind goes with them.
        """
        previews = self.library.previews
        freed = sum(track.size for track in previews)
        removed = len(previews)
        # Everything in the cache is about to go, including whatever the bar is
        # playing out of it and anything queued behind that.
        self.player.forget(track.path for track in previews)
        # Generation as well as the process: a download that finished a moment
        # ago has already handed its result to the main loop, and adding that
        # file back would leave a track in the grid that was just deleted.
        self._supersede_preview_fetch()
        try:
            shutil.rmtree(PREVIEW_CACHE)
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._toast(f"Could not clear the preview cache: {exc}")
            return
        self.library.previews = []
        self._populate_cache_card()
        self._refresh_current_view()
        self._toast(
            f"Cleared {plural(removed, 'preview')} · {human_size(freed)} freed"
        )

    def _build_destructive_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        card.add_css_class("sf-danger-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        inner.set_margin_start(14)
        inner.set_margin_end(14)
        inner.set_margin_top(14)
        inner.set_margin_bottom(14)
        card.append(inner)
        inner.append(label("Destructive", "sf-row-title", "sf-alert"))

        rebuild = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label("Rebuild database", "sf-row-title"))
        text.append(
            label(
                "Re-scan the iPod if tracks are not playing", "sf-caption", wrap=True
            )
        )
        rebuild.append(text)
        self.rebuild_button = Gtk.Button(label="Rebuild")
        self.rebuild_button.add_css_class("sf-button")
        self.rebuild_button.set_valign(Gtk.Align.CENTER)
        self.rebuild_button.connect("clicked", self.on_rebuild)
        rebuild.append(self.rebuild_button)
        inner.append(rebuild)

        wipe = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label("Wipe iPod", "sf-row-title"))
        self.wipe_note = label("", "sf-caption", wrap=True)
        text.append(self.wipe_note)
        wipe.append(text)
        self.wipe_button = Gtk.Button(label="Wipe…")
        self.wipe_button.add_css_class("sf-button")
        self.wipe_button.add_css_class("danger")
        self.wipe_button.set_valign(Gtk.Align.CENTER)
        self.wipe_button.connect("clicked", self.on_wipe)
        wipe.append(self.wipe_button)
        inner.append(wipe)

        eject = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label("Eject", "sf-row-title"))
        text.append(
            label("Unmount safely before unplugging", "sf-caption", wrap=True)
        )
        eject.append(text)
        self.eject_button = Gtk.Button(label="⏏ Eject")
        self.eject_button.add_css_class("sf-button")
        self.eject_button.set_valign(Gtk.Align.CENTER)
        self.eject_button.connect("clicked", self.on_eject)
        eject.append(self.eject_button)
        inner.append(eject)
        return card

    # ------------------------------------------------------------ bottom bars

    def _build_sync_bar(self):
        self.sync_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP
        )
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("sf-sync-bar")
        self.sync_revealer.set_child(outer)

        row = Gtk.Box(spacing=14)
        row.set_size_request(-1, 52)
        row.set_margin_start(18)
        row.set_margin_end(18)

        self.sync_spinner = Gtk.Spinner()
        self.sync_spinner.set_valign(Gtk.Align.CENTER)
        row.append(self.sync_spinner)

        self.sync_title = label("Working", "sf-row-title", valign=Gtk.Align.CENTER)
        self.sync_title.set_size_request(150, -1)
        row.append(self.sync_title)

        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        self.progress.set_size_request(-1, 5)
        row.append(self.progress)

        self.sync_count = label("", "sf-caption", "sf-mono", valign=Gtk.Align.CENTER)
        row.append(self.sync_count)

        self.sync_current = label(
            "", "sf-body", hexpand=True, ellipsize=ELLIPSIZE_END, valign=Gtk.Align.CENTER
        )
        row.append(self.sync_current)

        self.details_toggle = Gtk.ToggleButton(label="Details")
        self.details_toggle.add_css_class("sf-button")
        self.details_toggle.set_valign(Gtk.Align.CENTER)
        self.details_toggle.connect(
            "toggled", lambda b: self.details_revealer.set_reveal_child(b.get_active())
        )
        row.append(self.details_toggle)
        outer.append(row)

        self.details_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP
        )
        details = Gtk.Box(spacing=0)
        details.set_size_request(-1, 250)
        details.append(Gtk.Separator())

        file_scroll = Gtk.ScrolledWindow(hexpand=True)
        file_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sync_file_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.sync_file_list.set_margin_start(18)
        self.sync_file_list.set_margin_end(18)
        self.sync_file_list.set_margin_top(10)
        self.sync_file_list.set_margin_bottom(10)
        file_scroll.set_child(self.sync_file_list)
        details.append(file_scroll)

        details.append(Gtk.Separator())
        log_side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        log_side.set_size_request(340, -1)
        log_head = Gtk.Box(spacing=9)
        log_head.set_margin_start(14)
        log_head.set_margin_end(14)
        log_head.set_margin_top(9)
        log_head.set_margin_bottom(9)
        log_head.append(label("SCRIPT OUTPUT", "sf-section-label", hexpand=True))
        copy = Gtk.Button(label="Copy")
        copy.add_css_class("flat")
        copy.add_css_class("sf-caption")
        copy.connect("clicked", self.on_copy_log)
        log_head.append(copy)
        log_side.append(log_head)
        log_side.append(Gtk.Separator())

        self.log_view = Gtk.TextView(
            editable=False,
            monospace=True,
            cursor_visible=False,
            # The lines carry mount points and library paths, which are long
            # enough that without wrapping the interesting half of the message
            # sits off the right edge behind a scrollbar nobody drags.
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=14,
            right_margin=14,
            top_margin=10,
            bottom_margin=10,
        )
        self.log_view.add_css_class("sf-log")
        log_scroll = Gtk.ScrolledWindow(vexpand=True)
        log_scroll.set_child(self.log_view)
        log_side.append(log_scroll)
        details.append(log_side)

        self.details_revealer.set_child(details)
        outer.append(self.details_revealer)
        return self.sync_revealer

    def _build_now_playing_bar(self):
        """The transport, which previews on this computer and never on the iPod.

        All four states share these widgets - nothing playing, a track still
        opening, a file from the library and a previewed download - and differ
        only in what the labels say and what is sensitive. Building them once
        keeps the bar exactly as tall in every state, so the window does not
        resize under the pointer at the moment a track starts.
        """
        bar = Gtk.Box(spacing=16)
        bar.add_css_class("sf-bottom-bar")
        bar.set_size_request(-1, 84)
        bar.set_margin_start(18)
        bar.set_margin_end(18)

        left = Gtk.Box(spacing=11)
        left.set_size_request(150, -1)
        self.playing_art = Gtk.Box(valign=Gtk.Align.CENTER)
        self.playing_art.set_size_request(52, 52)
        left.append(self.playing_art)

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2
        )
        self.playing_title = label(
            "Nothing playing", "sf-row-title", "sf-dim", ellipsize=ELLIPSIZE_END
        )
        self.playing_subtitle = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        self.playing_state_dot = state_dot(STATE_LIBRARY)
        self.playing_subtitle.append(self.playing_state_dot)
        self.playing_artist = label("", "sf-body", ellipsize=ELLIPSIZE_END)
        self.playing_subtitle.append(self.playing_artist)
        self.playing_subtitle.set_visible(False)
        for line in (self.playing_title, self.playing_artist):
            # Capped rather than merely ellipsized: an ellipsizing label still
            # asks for its whole natural width, so one long title would set
            # the width of this column and squeeze the transport out of the
            # middle of the bar.
            line.set_max_width_chars(22)
            line.set_width_chars(0)
        text.append(self.playing_title)
        text.append(self.playing_subtitle)
        left.append(text)
        bar.append(left)

        # A stack rather than a box whose children are hidden in turn: when
        # preview playback is impossible or a file will not decode, the reason
        # belongs in place of the controls it is explaining, and the stack
        # keeps that sentence from being any taller than the transport it
        # replaces.
        self.playing_stack = Gtk.Stack(hexpand=True, valign=Gtk.Align.CENTER)
        transport = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=7, hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        controls = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        self.transport_buttons = {}
        for key, icon in (
            ("previous", "media-skip-backward-symbolic"),
            ("play", "media-playback-start-symbolic"),
            ("next", "media-skip-forward-symbolic"),
        ):
            button = Gtk.Button(icon_name=icon)
            button.add_css_class("flat")
            if key == "play":
                button.add_css_class("circular")
            button.set_sensitive(False)
            button.connect("clicked", getattr(self, f"on_{key}_clicked"))
            controls.append(button)
            self.transport_buttons[key] = button
        transport.append(controls)

        seek = Gtk.Box(spacing=10, halign=Gtk.Align.FILL, hexpand=True)
        self.seek_elapsed = label("0:00", "sf-caption", "sf-mono")
        seek.append(self.seek_elapsed)
        self.seek_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1, 0.01
        )
        self.seek_scale.set_draw_value(False)
        # A request rather than a fixed width, so the bar can still be laid
        # out when the window is dragged down to its minimum.
        self.seek_scale.set_size_request(160, -1)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.set_sensitive(False)
        self.seek_scale.connect("change-value", self._on_seek)
        seek.append(self.seek_scale)
        self.seek_total = label("0:00", "sf-caption", "sf-mono")
        seek.append(self.seek_total)
        transport.append(seek)
        self.playing_stack.add_named(transport, "transport")

        self.playing_message = label(
            "", "sf-body", wrap=True, justify=Gtk.Justification.CENTER, xalign=0.5
        )
        self.playing_stack.add_named(self.playing_message, "message")
        bar.append(self.playing_stack)

        right = Gtk.Box(spacing=12, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        right.set_size_request(150, -1)
        # Ellipsized but uncapped: the minimum width of an ellipsizing label is
        # tiny, so this shrinks away gracefully without widening the window.
        self.playing_status = label(
            "Preview on this computer", "sf-caption", ellipsize=ELLIPSIZE_END
        )
        right.append(self.playing_status)
        bar.append(right)

        self.now_playing_bar = bar
        self._update_now_playing()
        return bar

    # ------------------------------------------------------------- playback

    def play_from(self, view, track):
        """Play one track, queueing the list it was clicked in behind it.

        The queue comes from the view's model rather than from the library, so
        next moves through the album or playlist on screen and in the order it
        is displayed in, which is what the user just sorted it into.
        """
        # Whatever was being fetched is no longer what the bar is for. Without
        # this it would arrive several seconds later and take the bar back off
        # the track the user has just started.
        self._supersede_preview_fetch()
        model = view.get_model()
        tracks = [model.get_item(i).track for i in range(model.get_n_items())]
        for index, candidate in enumerate(tracks):
            if candidate is track:
                self.player.play(tracks, index)
                return
        # Clicked in a view that has already been repainted out from under the
        # row. Playing the one track alone is better than refusing.
        self.player.play([track], 0)

    def preview_result(self, result):
        """Hear a YouTube result, downloading it into the cache if need be."""
        if self.preview_unavailable:
            return
        cached = cached_preview_path(result.video_id, PREVIEW_CACHE)
        if cached is not None:
            self._supersede_preview_fetch()
            self.player.play([self._preview_track(cached)], 0)
            return
        if self.youtube_unavailable:
            return
        self._start_preview_fetch(result)

    def _preview_track(self, path):
        """The Track for a cached file, indexing it if the scan has not.

        A file can be in the cache without being in the list - it was
        downloaded while the scan that would have found it was already
        running - and refusing to play it then would be refusing to play a
        file that is right there.
        """
        path = str(path)
        for track in self.library.previews:
            if track.path == path:
                return track
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        track = Track(path, {"title": Path(path).stem, "size": size}, STATE_PREVIEW)
        self.library.previews.append(track)
        return track

    def _start_preview_fetch(self, result):
        """Download one result into the cache, then play it.

        Deliberately not run through _run: that marks the whole window busy for
        the several seconds a download takes, and previewing needs neither an
        iPod attached nor the rest of the window to stop working. The only
        thing it locks is the bar it is filling.
        """
        self._supersede_preview_fetch()
        generation = self.preview_generation
        # Named from the search result, so the bar says what is arriving rather
        # than staying blank until it has. Replaced by the real thing, tags and
        # all, once the file is on disk.
        pending = Track(
            "",
            {
                "title": result.title,
                "artist": result.uploader,
                "duration": result.duration,
            },
            STATE_PREVIEW,
        )
        self.player.fetch(pending)
        threading.Thread(
            target=self._preview_fetch_worker,
            args=(result, generation, pending),
            daemon=True,
        ).start()

    def _preview_fetch_worker(self, result, generation, pending):
        """Fetch one result into the cache. Runs off the main loop."""
        try:
            PREVIEW_INCOMING.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=PREVIEW_INCOMING))
        except OSError as exc:
            GLib.idle_add(
                self._fail_preview_fetch,
                generation,
                pending,
                f"Could not write to the preview cache: {exc}",
            )
            return
        try:
            self._run_preview_fetch(
                fetch_command(result.url, staging), generation, pending, staging
            )
        finally:
            # Whatever it left behind goes with it, finished or interrupted.
            # A partial download kept under .incoming would be counted by
            # nothing and cleared by nothing short of emptying the cache.
            shutil.rmtree(staging, ignore_errors=True)

    def _run_preview_fetch(self, command, generation, pending, staging):
        """Run the download and hand what it produced back to the main loop."""
        launch_error = None
        with self._preview_lock:
            if generation != self.preview_generation or self._preview_closed:
                return
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                launch_error = exc
            else:
                self._preview_process = proc
        if launch_error is not None:
            GLib.idle_add(self._log, f"failed to run: {launch_error}\n")
            GLib.idle_add(
                self._fail_preview_fetch, generation, pending, PREVIEW_FAILED
            )
            return
        if proc.stdout is not None:
            for line in proc.stdout:
                GLib.idle_add(self._log, line)
        proc.wait()
        with self._preview_lock:
            if self._preview_process is proc:
                self._preview_process = None
            stale = (
                generation != self.preview_generation or self._preview_closed
            )
        if stale:
            return

        # The staging directory is this run's manifest: it was empty, and
        # nothing else writes to it. That is more reliable than parsing what
        # yt-dlp reported, which older versions cannot report at all.
        produced = sorted(
            path
            for path in staging.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        )
        if not produced:
            GLib.idle_add(
                self._fail_preview_fetch, generation, pending, PREVIEW_FAILED
            )
            return

        source = produced[0]
        destination = PREVIEW_CACHE / source.relative_to(staging)
        move_error = None
        with self._preview_lock:
            if generation != self.preview_generation or self._preview_closed:
                return
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            except OSError as exc:
                move_error = exc
        if move_error is not None:
            GLib.idle_add(
                self._fail_preview_fetch,
                generation,
                pending,
                f"Could not move the preview into the cache: {move_error}",
            )
            return

        # Read here rather than on the main loop: the tags come from a second
        # process, and the whole point of downloading off the main loop is not
        # to hand it the slow half afterwards.
        records, _complete, _skipped = scan_tracks(files=[str(destination)])
        GLib.idle_add(
            self._finish_preview_fetch,
            generation,
            str(destination),
            records[0] if records else {},
        )

    def _finish_preview_fetch(self, generation, path, record):
        if generation != self.preview_generation:
            return False
        track = Track(path, record, STATE_PREVIEW)
        self.library.previews = [
            existing for existing in self.library.previews if existing.path != path
        ] + [track]
        # Kept out of the pruning it triggers: it is over the limit only
        # because it just arrived, and dropping the file the user is waiting
        # to hear would be the one deletion they would notice.
        self._prune_preview_cache(keep=[path])
        self._populate_cache_card()
        self._refresh_current_view()
        self.player.play([track], 0)
        return False

    def _fail_preview_fetch(self, generation, track, message):
        if generation != self.preview_generation:
            return False
        self.player.fail(track, message)
        return False

    def _supersede_preview_fetch(self):
        """Disown a download because something else is taking the bar."""
        with self._preview_lock:
            self.preview_generation += 1
            proc = self._preview_process
            self._preview_process = None
        self._terminate_preview_process(proc)

    def _cancel_preview_fetch(self):
        """Stop a download whose result nothing is waiting for any more."""
        with self._preview_lock:
            proc = self._preview_process
            self._preview_process = None
        self._terminate_preview_process(proc)

    @staticmethod
    def _terminate_preview_process(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass

    def _prune_preview_cache(self, keep=()):
        """Drop the oldest previews once the cache has passed its limit."""
        playing = self.player.track
        protected = list(keep) + ([playing.path] if playing else [])
        entries = preview_cache_entries(PREVIEW_CACHE)
        dropped = set()
        for path in prunable_previews(entries, PREVIEW_CACHE_LIMIT, protected):
            try:
                path.unlink()
            except OSError:
                continue
            dropped.add(str(path))
            self._forget_empty_preview_folders(path.parent)
        if dropped:
            self.library.previews = [
                track for track in self.library.previews if track.path not in dropped
            ]
            self.player.forget(dropped)

    @staticmethod
    def _forget_empty_preview_folders(folder):
        """Take the artist folder with the last preview that left it."""
        folder = Path(folder)
        while PREVIEW_CACHE in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                return
            folder = folder.parent

    def _promote_preview(self, track):
        """Keep a previewed track: out of the cache, into the library.

        Add means the same thing here as everywhere else - this is a track I
        want - but a previewed file lives in a cache that gets pruned, so
        keeping it has to move it before anything is queued.
        """
        if track.state != STATE_PREVIEW:
            return
        source = Path(track.path)
        if not source.is_file():
            # Pruned, or cleared from another window, between the row being
            # drawn and the button being pressed. Saying so beats an error
            # about a path the user never saw.
            self.library.previews = [
                existing for existing in self.library.previews if existing is not track
            ]
            self._populate_cache_card()
            self._refresh_current_view()
            self._toast("That preview is no longer in the cache")
            return
        destination = promote_destination(source, PREVIEW_CACHE, YOUTUBE_LIBRARY)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # The same video, downloaded directly on an earlier day. The
                # copy already in the library is the one to keep; overwriting
                # it with the cached duplicate would gain nothing and could
                # lose tags edited since.
                source.unlink()
            else:
                shutil.move(str(source), str(destination))
        except OSError as exc:
            self._toast(f"Could not move the preview into your library: {exc}")
            return
        self._forget_empty_preview_folders(source.parent)

        track.path = str(destination)
        track.relpath = str(destination)
        track.state = STATE_LIBRARY
        track.on_ipod = False
        self.library.previews = [
            existing for existing in self.library.previews if existing is not track
        ]
        # Into the scan's own index as well, so that a library scan finishing
        # afterwards does not drop the track that was just kept.
        self._library_scan_tracks[track.path] = track
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        self._populate_cache_card()
        self._update_now_playing()

        kept = f"Kept in {home_relative(YOUTUBE_LIBRARY)}"
        if self.mount_point and self.device_identity is not None:
            queued = self._queue_sources({track.path: [track]}, show_toast=False)
            self._toast(f"{kept} and queued for sync" if queued else kept)
            return
        self._refresh_current_view()
        self._toast(kept)

    def on_play_clicked(self, _button):
        self.player.toggle()

    def on_previous_clicked(self, _button):
        self.player.previous()

    def on_next_clicked(self, _button):
        self.player.next()

    def _on_seek(self, scale, _scroll, value):
        """A drag or a keypress on the timeline.

        GtkRange emits change-value for user input only and never for
        set_value, so the poll that walks the thumb along cannot be mistaken
        for a seek. Handled here rather than by the default handler because the
        player is the one that decides where the thumb ends up.
        """
        fraction = max(0.0, min(1.0, value))
        scale.set_value(fraction)
        self.player.seek(fraction)
        return True

    def _update_now_playing(self):
        """Repaint the bar from the player. Cheap enough to run per poll."""
        player = self.player
        track = player.track
        loaded = track is not None and player.state != PLAY_IDLE

        painted_art = (
            None if track is None else (track.path, track.state, track.art)
        )
        if self._painted_art != painted_art:
            self._painted_art = painted_art
            child = self.playing_art.get_first_child()
            if child is not None:
                self.playing_art.remove(child)
            if track is None:
                art = label("♪", "sf-idle-art", xalign=0.5, yalign=0.5)
                art.set_size_request(52, 52)
            else:
                art = make_cover(track.art, 52, track.album)
                if track.state == STATE_PREVIEW:
                    # The same dimming a previewed album card gets, so "this is
                    # only here so you could hear it" reads the same everywhere.
                    art.set_opacity(0.6)
            self.playing_art.append(art)

        if track is None:
            self.playing_title.set_text("Nothing playing")
            self.playing_title.add_css_class("sf-dim")
            self.playing_subtitle.set_visible(False)
        else:
            self.playing_title.set_text(track.title)
            self.playing_title.remove_css_class("sf-dim")
            self.playing_artist.set_text(track.artist)
            for name in STATE_LABELS:
                self.playing_state_dot.remove_css_class(name)
            self.playing_state_dot.add_css_class(track.state)
            self.playing_subtitle.set_visible(True)

        message = player.error or self.preview_unavailable
        if message:
            self.playing_message.set_text(message)
            self.playing_stack.set_visible_child_name("message")
        else:
            self.playing_stack.set_visible_child_name("transport")

        playing = player.state == PLAY_PLAYING
        # A download has nothing behind it to drive yet, so the transport reads
        # as the idle one until the file lands. The title and the "Fetching
        # preview…" caption are what say the bar is busy.
        ready = loaded and player.state != PLAY_FETCHING
        self.transport_buttons["play"].set_icon_name(
            "media-playback-pause-symbolic" if playing
            else "media-playback-start-symbolic"
        )
        self.transport_buttons["play"].set_sensitive(
            ready or (bool(player.queue) and player.state != PLAY_FETCHING)
        )
        self.transport_buttons["previous"].set_sensitive(ready)
        self.transport_buttons["next"].set_sensitive(
            ready and player.index + 1 < len(player.queue)
        )
        # Dimmed while there is nothing to drive, which is the design's idle
        # bar: insensitive controls alone still read as controls you could use.
        self.playing_stack.set_opacity(1.0 if ready else 0.32)

        seekable = player.seekable
        self.seek_scale.set_sensitive(seekable)
        self.seek_scale.set_value(
            min(1.0, player.position / player.duration) if seekable else 0.0
        )
        self.seek_elapsed.set_text(human_duration(player.position))
        if player.duration > 0:
            self.seek_total.set_text(human_duration(player.duration))
        else:
            # Unknown only once there is a track whose length has not been
            # worked out yet. An idle bar knows the length perfectly well:
            # there is nothing playing, and "--:--" there reads as a fault.
            self.seek_total.set_text("--:--" if track is not None else "0:00")
        self.playing_status.set_text(self._playing_status())

    def _playing_status(self):
        """The one line on the right, which says what kind of playback this is.

        Preview playback is easy to mistake for the device playing something,
        and a previewed download is easy to mistake for a track already kept,
        so the distinction is stated rather than implied by a dot.
        """
        player = self.player
        if self.preview_unavailable or player.error:
            # The middle of the bar is already carrying the reason; repeating a
            # truncated copy of it here would only compete with it.
            return ""
        if player.state == PLAY_FETCHING:
            return "Fetching preview…"
        if player.state == PLAY_LOADING:
            # Opening, not fetching, even for a preview: by this point the file
            # is on disk and this is the same second a library track takes.
            return "Opening…"
        if player.track is not None and player.track.state == STATE_PREVIEW:
            return "Previewed - add to keep"
        return "Preview on this computer"

    # ---------------------------------------------------------------- state

    def show_view(self, name):
        self.views.set_visible_child_name(name)
        titles = {
            "library": "Your Library",
            "search": "Search",
            "album": "Album",
            "playlists": "Playlists",
            "settings": "Device & Settings",
        }
        self.view_title.set_text(titles.get(name, name.title()))
        for key, button in self.nav_buttons.items():
            if key == name or (name == "album" and key == "library"):
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")
        self.library_controls.set_visible(name == "library")

    def on_refresh_clicked(self, _button):
        self.refresh()
        self._rescan_library()

    def refresh(self):
        """Re-detect the iPod and repaint. Safe to call from the main loop."""
        if self.busy:
            return False

        candidates = find_ipods()
        if len(candidates) != 1:
            self._select_mount(None)
            self.playlists = []
            self.spoken = set()
            self.current_playlist = None
            if len(candidates) > 1:
                self._populate_disconnected_summary(
                    "Multiple iPods connected. Disconnect all but the one you "
                    "want to manage.",
                    False,
                )
            else:
                self._populate_disconnected_summary(
                    "No iPod connected. Plug one in, or mount it if it is "
                    "already attached.",
                    True,
                )
            self._populate_playlist_rail()
            self.stack.set_visible_child_name("device")
            return False

        self._select_mount(candidates[0])
        self.stack.set_visible_child_name("device")
        self._load_sync_options()
        self.playlists = list_playlists(self.mount_point)
        self.spoken = spoken_playlists(self.mount_point)
        self._populate_device_summary()
        self._populate_playlist_rail()
        self._load_device_tracks_async()
        return False

    def _select_mount(self, mount_point):
        identity = volume_identity(mount_point) if mount_point is not None else None
        if mount_point == self.mount_point and identity == self.device_identity:
            return
        if self.discovering_sources and identity != self.device_identity:
            self.source_generation += 1
            self.discovering_sources = False
        self.tag_generation += 1
        self.mount_point = mount_point
        self.device_identity = identity
        self._device_scan_tracks = {}
        self.device_tracks = []
        self.track_names = {}
        self._device_scan_active = False
        self._device_snapshot_ready = False
        if (
            mount_point is not None
            and self.pending_sources
            and self.pending_device_identity != identity
        ):
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_skipped_symlinks.clear()
            self.pending_device_identity = None
            self._toast(
                "Queued changes were discarded because a different iPod was connected"
            )
        self._merge_states()
        self._refresh_current_view()

    def _populate_disconnected_summary(self, message, offer_mount):
        self.device_name.set_text("No iPod")
        self.settings_name.set_text("No iPod connected")
        self.settings_path.set_text(message)
        self.device_dot.remove_css_class("ipod")
        self.device_dot.add_css_class("library")
        self.settings_dot.remove_css_class("ipod")
        self.settings_dot.add_css_class("library")
        self.device_free.set_text("Not connected")
        self.device_count.set_text("")
        self.wipe_note.set_text("Connect an iPod before using device controls.")
        for meter in (self.sidebar_meter, self.settings_meter):
            meter.set_fractions(0, 0, False)
        self._set_settings_figures(None, 0, 0, False)

        child = self.device_banner.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.device_banner.remove(child)
            child = nxt
        warning = label(message, "sf-caption", wrap=True, hexpand=True)
        warning.set_margin_start(11)
        warning.set_margin_top(10)
        warning.set_margin_bottom(10)
        self.device_banner.append(warning)
        if offer_mount:
            mount = Gtk.Button(label="Mount Connected iPod")
            mount.add_css_class("sf-button")
            mount.set_margin_end(10)
            mount.set_valign(Gtk.Align.CENTER)
            mount.connect("clicked", self.on_mount_clicked)
            self.device_banner.append(mount)
        self.device_banner.set_visible(True)

        _tracks, changes, queued_bytes = self._pending_accounting()
        if self.pending_sources:
            self.queued_row.set_visible(True)
            self.queued_label.set_text(
                f"{human_size(queued_bytes)} queued{self._pending_symlink_note()} "
                "— reconnect the same iPod"
            )
            self.sync_button.set_label(
                f"Sync {plural(changes, 'change')}"
            )
        else:
            self.queued_row.set_visible(False)
            self.sync_button.set_label("Nothing queued")
        self._update_device_controls()

    def _populate_device_summary(self):
        name = Path(self.mount_point).name
        self.device_name.set_text(name)
        self.settings_name.set_text(name)
        self.settings_path.set_text(self.mount_point)
        self.device_dot.remove_css_class("library")
        self.device_dot.add_css_class("ipod")
        self.settings_dot.remove_css_class("library")
        self.settings_dot.add_css_class("ipod")
        self.device_banner.set_visible(False)

        total_tracks = count_tracks(self.mount_point)
        self.device_count.set_text(plural(total_tracks, "track"))
        self.wipe_note.set_text(
            f"Removes all {plural(total_tracks, 'track')}. Filenames on the device are "
            "scrambled codes, so back up first."
        )

        _tracks, changes, queued_bytes = self._pending_accounting()
        try:
            usage = shutil.disk_usage(self.mount_point)
            used_fraction = usage.used / usage.total if usage.total else 0
            queued_fraction = queued_bytes / usage.total if usage.total else 0
            over = queued_bytes > usage.free
            self.device_free.set_text(f"{human_size(usage.free)} free")
            for meter in (self.sidebar_meter, self.settings_meter):
                meter.set_fractions(used_fraction, queued_fraction, over)
            self._set_settings_figures(usage, queued_bytes, total_tracks, over)
        except OSError:
            self.device_free.set_text("size unknown")
            self._set_settings_figures(None, queued_bytes, total_tracks, False)

        if self.pending_sources:
            self.queued_row.set_visible(True)
            self.queued_label.set_text(
                f"+{human_size(queued_bytes)} queued to sync"
                f"{self._pending_symlink_note()}"
            )
            self.sync_button.set_label(f"Sync {plural(changes, 'change')}")
            self.sync_button.set_sensitive(not self.busy)
        else:
            self.queued_row.set_visible(False)
            self.sync_button.set_label("Nothing queued")
            self.sync_button.set_sensitive(False)
        self._update_device_controls()

    def _update_device_controls(self):
        connected = bool(self.mount_point)
        enabled = connected and not self.busy
        queue_enabled = (
            enabled
            and self.device_identity is not None
            and not self.discovering_sources
        )
        self.add_button.set_sensitive(queue_enabled)
        self.playlist_button.set_sensitive(
            queue_enabled and self.speech_engine_available
        )
        self.youtube_button.set_sensitive(
            queue_enabled and not self.youtube_unavailable
        )
        # Collected as they are built rather than repainted from here: a
        # repaint would discard the skeleton mid-search, and a device
        # appearing while YouTube is still answering is exactly when that
        # happens.
        downloadable = self._can_download()
        for button in self.search_add_buttons:
            button.set_sensitive(downloadable)
            button.set_tooltip_text(self._youtube_download_tooltip())
        self.new_playlist_button.set_sensitive(
            queue_enabled and self.speech_engine_available
        )
        self.rebuild_button.set_sensitive(enabled)
        self.wipe_button.set_sensitive(enabled)
        self.eject_button.set_sensitive(enabled)
        self.sidebar_eject.set_sensitive(enabled)
        self.playlist_mode.set_sensitive(enabled)
        self.track_voiceover.set_sensitive(enabled and self.speech_engine_available)
        self.playlist_voiceover.set_sensitive(
            enabled and self.speech_engine_available
        )
        accounting_ready = (
            not self._device_scan_active or self._device_snapshot_ready
        )
        self.sync_button.set_sensitive(
            queue_enabled and accounting_ready and bool(self.pending_sources)
        )

    def _set_settings_figures(self, usage, queued_bytes, total_tracks, over):
        child = self.settings_figures.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.settings_figures.remove(child)
            child = nxt

        def figure(value, suffix, *classes):
            box = Gtk.Box(spacing=5)
            box.append(label(value, "sf-row-title", *classes))
            box.append(label(suffix, "sf-body"))
            return box

        if usage is not None:
            self.settings_figures.append(figure(human_size(usage.used), "used"))
            if queued_bytes:
                self.settings_figures.append(
                    figure(
                        human_size(queued_bytes),
                        "queued",
                        "sf-alert" if over else "sf-accent",
                    )
                )
            self.settings_figures.append(
                figure(human_size(usage.free), f"free of {human_size(usage.total)}")
            )
        self.settings_figures.append(
            label(
                plural(total_tracks, "track")
                + " · "
                + plural(len(self.playlists), "playlist"),
                "sf-caption",
                "sf-mono",
                valign=Gtk.Align.CENTER,
            )
        )

    def _populate_playlist_rail(self):
        for container in (self.playlist_rail, self.playlist_list):
            child = container.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                container.remove(child)
                child = nxt

        for name, entries in self.playlists:
            self.playlist_rail.append(self._rail_row(name, entries, compact=True))
            self.playlist_list.append(self._rail_row(name, entries, compact=False))

        playlist_names = {name for name, _entries in self.playlists}
        if not self.playlists:
            self.current_playlist = None
            self._clear_playlist_detail()
        elif self.current_playlist not in playlist_names:
            self.current_playlist = self.playlists[0][0]
        if self.current_playlist is not None:
            self._show_playlist(self.current_playlist)
        self._populate_playlist_shelf()

    def _rail_row(self, name, entries, compact):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-nav-row")
        row = Gtk.Box(spacing=9)
        row.append(make_cover(None, 22 if compact else 34, name, "tiny"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(label(name, ellipsize=ELLIPSIZE_END))
        if not compact:
            text.append(label(plural(len(entries), "track"), "sf-caption"))
        row.append(text)
        spoken = name.lower() in self.spoken
        marker = label("◉" if spoken else "◌", "sf-accent" if spoken else "sf-dim")
        marker.set_tooltip_text(
            "Spoken name available" if spoken else "No spoken name on the device"
        )
        row.append(marker)
        button.set_child(row)
        button.connect("clicked", lambda _b, n=name: self._select_playlist(n))
        return button

    def _reorder_playlist(self, source, target):
        """Move a track within the playlist and write the new order out.

        The order is the playlist, so it has to survive the app closing: the
        list on the device is rewritten and the database rebuilt, rather than
        the change living only in this window.
        """
        if self.busy or self.current_playlist is None:
            return False
        if source == target:
            return True

        entries = list(dict(self.playlists).get(self.current_playlist, []))
        if not 0 <= source < len(entries) or not 0 <= target < len(entries):
            return False
        moved = entries.pop(source)
        entries.insert(target, moved)

        path = playlist_file(self.mount_point, self.current_playlist)
        if path is None or not self._confirmed_device(self.device_identity):
            self._toast("Could not rewrite the playlist on the device")
            return False
        if not write_playlist(
            self.mount_point, self.device_identity, path, entries
        ):
            self._toast("Could not rewrite the playlist on the device")
            return False

        self.playlists = [
            (name, entries if name == self.current_playlist else current)
            for name, current in self.playlists
        ]
        self._show_playlist(self.current_playlist)
        # The device reads the order out of its database, not out of the file,
        # so without this the reorder would show here and nowhere else.
        self._run(
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                "--rebuild-only",
                *self._sync_options(),
            ],
            "Saving playlist order",
            "Playlist reordered",
        )
        return True

    def _select_playlist(self, name):
        self.current_playlist = name
        self._show_playlist(name)
        self.show_view("playlists")

    def _populate_playlist_shelf(self):
        child = self.playlist_shelf.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.playlist_shelf.remove(child)
            child = nxt

        self.shelf_section.set_visible(bool(self.playlists))
        for name, _entries in self.playlists[:5]:
            tile = Gtk.Button()
            tile.add_css_class("flat")
            tile.add_css_class("sf-card")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.append(make_cover(None, 128, name))
            box.append(label(name, "sf-row-title", ellipsize=ELLIPSIZE_END))
            spoken = name.lower() in self.spoken
            note = Gtk.Box(spacing=6)
            note.append(label("◉" if spoken else "◌", "sf-accent" if spoken else "sf-dim"))
            note.append(
                label("spoken name" if spoken else "no spoken name", "sf-caption")
            )
            box.append(note)
            tile.set_child(box)
            tile.connect("clicked", lambda _b, n=name: self._select_playlist(n))
            self.playlist_shelf.append(tile)

        new_tile = Gtk.Button()
        new_tile.add_css_class("sf-new-tile")
        new_tile.set_size_request(150, -1)
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=7,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        inner.append(label("＋", xalign=0.5))
        inner.append(label("New playlist", "sf-row-title", xalign=0.5))
        new_tile.set_child(inner)
        new_tile.connect("clicked", self.on_add_playlist)
        new_tile.set_sensitive(
            bool(self.mount_point)
            and self.device_identity is not None
            and self.speech_engine_available
            and not self.busy
            and not self.discovering_sources
        )
        self.playlist_shelf.append(new_tile)

    def _show_playlist(self, name):
        entries = dict(self.playlists).get(name, [])
        self.playlist_heading.set_text(name)

        child = self.playlist_voice_note.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.playlist_voice_note.remove(child)
            child = nxt
        spoken = name.lower() in self.spoken
        self.playlist_voice_note.append(
            label("◉" if spoken else "◌", "sf-accent" if spoken else "sf-dim")
        )
        self.playlist_voice_note.append(
            label(
                plural(len(entries), "track")
                + " · "
                + (
                    "the device can announce this playlist"
                    if spoken
                    else "no spoken name, so the device cannot announce it"
                ),
                "sf-body",
                wrap=True,
                max_width_chars=34,
            )
        )
        remove = Gtk.Button(label="Remove playlist")
        remove.add_css_class("sf-button")
        remove.set_margin_start(12)
        remove.connect("clicked", self.on_remove_playlist, name)
        remove.set_sensitive(
            bool(self.mount_point) and self.device_identity is not None and not self.busy
        )
        self.playlist_voice_note.append(remove)

        by_relpath = {track.relpath: track for track in self.device_tracks}
        resolved = []
        for relpath in entries:
            track = by_relpath.get(relpath)
            if track is None:
                track = Track(relpath, {"title": Path(relpath).stem}, STATE_IPOD,
                              relpath=relpath)
            resolved.append(track)
        fill_tracks(self.playlist_tracks, resolved)

    def _clear_playlist_detail(self):
        self.playlist_heading.set_text(
            "No playlists" if self.mount_point else "No iPod connected"
        )
        child = self.playlist_voice_note.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.playlist_voice_note.remove(child)
            child = nxt
        self.playlist_voice_note.append(
            label(
                "Add a playlist to this iPod"
                if self.mount_point
                else "Connect an iPod to manage its playlists",
                "sf-body",
                "sf-dim",
            )
        )
        fill_tracks(self.playlist_tracks, [])

    # ---------------------------------------------------------- library scan

    def _rescan_library(self):
        """Index the configured music folders in the background."""
        self.scan_generation += 1
        generation = self.scan_generation
        roots = self.library.roots
        previous_tracks = list(self.library.tracks)
        self._library_scan_tracks = {}
        self.library_status.set_text("Reading your music folders…")

        def worker():
            batch = []

            def publish(root, record):
                batch.append(
                    Track(Path(root, record["path"]), record, STATE_LIBRARY)
                )
                if len(batch) >= 25:
                    ready = batch[:]
                    batch.clear()
                    GLib.idle_add(self._apply_library_batch, generation, ready)

            # The cache first, because it is small and holds the tracks the
            # window is most likely to be asked about again. A cache that does
            # not exist yet fails this scan, which is the same as holding
            # nothing.
            records, complete, _skipped = scan_tracks(
                PREVIEW_CACHE,
                cancelled=lambda: generation != self.scan_generation,
            )
            if generation != self.scan_generation:
                return
            if complete:
                GLib.idle_add(
                    self._apply_preview_scan,
                    generation,
                    [
                        Track(PREVIEW_CACHE / record["path"], record, STATE_PREVIEW)
                        for record in records
                        if not any(
                            part.startswith(".")
                            for part in Path(record["path"]).parts
                        )
                    ],
                )

            for root in roots:
                _records, complete, _skipped_symlinks = scan_tracks(
                    root,
                    on_record=lambda record, r=root: publish(r, record),
                    cancelled=lambda: generation != self.scan_generation,
                )
                if generation != self.scan_generation:
                    return
                if not complete:
                    GLib.idle_add(
                        self._fail_library_scan, generation, previous_tracks
                    )
                    return
            if batch:
                GLib.idle_add(self._apply_library_batch, generation, batch[:])
            GLib.idle_add(self._finish_library_scan, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_preview_scan(self, generation, previews):
        """Take what the cache holds, keeping anything that landed meanwhile.

        Merged rather than replaced: a preview downloaded while this scan was
        running is on disk but was not there when it started, and dropping it
        would take a track out of the grid the moment it arrived in it.
        """
        if generation != self.scan_generation:
            return False
        merged = {
            track.path: track for track in previews if Path(track.path).is_file()
        }
        for track in self.library.previews:
            if track.path not in merged and Path(track.path).exists():
                merged[track.path] = track
        self.library.previews = list(merged.values())
        self._populate_cache_card()
        self._refresh_current_view(scan_complete=False)
        return False

    def _apply_library_batch(self, generation, tracks):
        if generation != self.scan_generation:
            return False
        for track in tracks:
            self._library_scan_tracks[track.path] = track
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        self._refresh_current_view(scan_complete=False)
        return False

    def _finish_library_scan(self, generation):
        if generation != self.scan_generation:
            return False
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        if self.mount_point:
            self._populate_device_summary()
        self._refresh_current_view()
        self._populate_folders()
        return False

    def _fail_library_scan(self, generation, previous_tracks):
        if generation != self.scan_generation:
            return False
        self.library.tracks = previous_tracks
        self._library_scan_tracks = {track.path: track for track in previous_tracks}
        self._merge_states()
        self._refresh_current_view()
        self._populate_folders()
        self.library_status.set_text(
            "Could not finish reading your music folders; the previous library is shown."
        )
        self.library_status.set_visible(True)
        return False

    def _load_device_tracks_async(self):
        """Read the device's tags without blocking the main loop.

        Every one of these files is read over USB, which is far too slow to do
        while the window is trying to draw.
        """
        self.tag_generation += 1
        generation = self.tag_generation
        mount_point = self.mount_point
        previous_tracks = list(self.device_tracks)
        previous_ready = self._device_snapshot_ready
        self._device_scan_tracks = {}
        self._device_scan_active = True
        self._update_device_controls()
        if self.pending_sources and not self._device_snapshot_ready:
            self.queued_label.set_text(
                "Checking which queued tracks are already on this iPod"
            )
            self.sync_button.set_label("Checking iPod…")

        def worker():
            music = Path(mount_point, "iPod_Control", "Music")
            batch = []

            def publish(record):
                batch.append(record)
                if len(batch) >= 25:
                    ready = batch[:]
                    batch.clear()
                    GLib.idle_add(
                        self._apply_device_track_batch,
                        generation,
                        mount_point,
                        ready,
                    )

            _records, complete, _skipped_symlinks = scan_tracks(
                music,
                on_record=publish,
                cancelled=lambda: generation != self.tag_generation,
            )
            if generation != self.tag_generation:
                return
            if not complete:
                GLib.idle_add(
                    self._fail_device_scan,
                    generation,
                    mount_point,
                    previous_tracks,
                    previous_ready,
                )
                return
            if batch:
                GLib.idle_add(
                    self._apply_device_track_batch,
                    generation,
                    mount_point,
                    batch[:],
                )
            GLib.idle_add(self._finish_device_scan, generation, mount_point)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_device_track_batch(
        self, generation, mount_point, records, scan_complete=False
    ):
        if (
            generation != self.tag_generation
            or mount_point is None
            or mount_point != self.mount_point
        ):
            return False
        music = Path(mount_point, "iPod_Control", "Music")
        for record in records:
            relpath = record["path"]
            self._device_scan_tracks[relpath] = Track(
                music / relpath, record, STATE_IPOD, relpath=relpath
            )
        if scan_complete:
            self.device_tracks = sorted(
                self._device_scan_tracks.values(), key=lambda track: track.relpath
            )
            self.track_names = {
                track.relpath: track.title for track in self.device_tracks
            }
            if scan_complete:
                self._device_scan_active = False
                self._device_snapshot_ready = True
            self._merge_states()
            self._refresh_current_view(scan_complete=scan_complete)
        return False

    def _finish_device_scan(self, generation, mount_point):
        result = self._apply_device_track_batch(
            generation, mount_point, [], scan_complete=True
        )
        if generation == self.tag_generation and mount_point == self.mount_point:
            self._populate_device_summary()
            self._update_device_controls()
        return result

    def _fail_device_scan(
        self, generation, mount_point, previous_tracks, previous_ready
    ):
        if generation != self.tag_generation or mount_point != self.mount_point:
            return False
        self._device_scan_active = False
        self._device_snapshot_ready = previous_ready
        self.device_tracks = previous_tracks if previous_ready else []
        self._device_scan_tracks = {
            track.relpath: track for track in self.device_tracks
        }
        self.track_names = {
            track.relpath: track.title for track in self.device_tracks
        }
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()
        self._update_device_controls()
        self._toast("Could not finish reading tracks from this iPod")
        return False

    def _apply_device_tracks(self, generation, records, mount_point=None):
        captured_mount = self.mount_point if mount_point is None else mount_point
        if captured_mount is None:
            return False
        self._device_scan_tracks = {}
        return self._apply_device_track_batch(
            generation, captured_mount, records, scan_complete=True
        )

    def _resolve_current_album(self):
        if self.current_album is None:
            return None
        by_artist = self.group_mode.get_selected() == 1
        for collection in self.library.collections(by_artist):
            if by_artist:
                matches = collection.title.lower() == self.current_album.title.lower()
            else:
                matches = (
                    collection.title.lower(),
                    collection.artist.lower(),
                ) == (
                    self.current_album.title.lower(),
                    self.current_album.artist.lower(),
                )
            if matches:
                return collection
        return None

    def _refresh_current_view(self, scan_complete=True):
        self._populate_albums()
        visible = self.views.get_visible_child_name()
        if visible == "album":
            album = self._resolve_current_album()
            if album is None and scan_complete:
                self.current_album = None
                self.show_view("library")
            elif album is not None:
                self._show_album(album)
        elif visible == "playlists" and self.current_playlist is not None:
            self._show_playlist(self.current_playlist)
        elif visible == "search":
            # A rescan changes which of the results are already on the iPod, so
            # the local half is re-derived rather than left showing the states
            # it had when the query was typed.
            self._paint_local_results()
        return False

    def _merge_states(self):
        """Decide which local tracks are already on the device.

        Matched on tags rather than path: the device stores every track under a
        scrambled four-letter name, so nothing about its location survives.
        """
        on_device = {}
        for track in self.device_tracks:
            on_device.setdefault(track.identity(), []).append(track)
        matched = set()
        self._library_by_path = {
            track.path: track for track in self.library.tracks
        }
        for track in self.library.tracks:
            track.relpath = track.path
            matches = on_device.get(track.identity(), [])
            if matches:
                device_track = matches.pop(0)
                track.state = STATE_IPOD
                track.on_ipod = True
                track.relpath = device_track.relpath
                matched.add(id(device_track))
            elif track.state == STATE_IPOD:
                track.state = STATE_LIBRARY
                track.on_ipod = False

        pending_index = dict(self._library_by_path)
        records = getattr(self, "pending_records", {})
        for path in getattr(self, "pending", set()):
            if path in pending_index or Path(path).suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            record = dict(records.get(path, {}))
            record.setdefault("title", Path(path).stem)
            try:
                record["size"] = Path(path).stat().st_size
            except OSError:
                record["size"] = 0
            track = Track(path, record, STATE_LIBRARY)
            matches = on_device.get(track.identity(), [])
            if matches:
                device_track = matches.pop(0)
                track.state = STATE_IPOD
                track.on_ipod = True
                track.relpath = device_track.relpath
            pending_index[path] = track
        self._pending_track_index = pending_index

        # Device tracks with no local counterpart still belong in the grid, or
        # music copied from another machine would simply not appear. Held
        # separately from the scan results rather than appended to them, which
        # would re-add the same tracks on every refresh.
        self.library.device_only = [
            track for track in self.device_tracks if id(track) not in matched
        ]

    # -------------------------------------------------------------- painting

    def _populate_albums(self):
        if not self._library_ready:
            return
        child = self.album_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.album_flow.remove(child)
            child = nxt

        by_artist = self.group_mode.get_selected() == 1
        collections = self.library.collections(by_artist)
        counts = self.library.counts(by_artist)
        for key, pill in self.album_filters.items():
            text = {
                "all": "All",
                STATE_IPOD: "On iPod",
                STATE_LIBRARY: "In library",
                STATE_PREVIEW: "Previewed",
            }[key]
            total = len(collections) if key == "all" else counts.get(key, 0)
            pill.set_child(label(f"{text} {total}", xalign=0.5))

        shown = [
            collection
            for collection in collections
            if self.album_filter == "all" or collection.state == self.album_filter
        ]
        for collection in shown:
            self.album_flow.append(self._album_card(collection))

        # The table ignores the grouping, since it is every track either way,
        # but it does honour the state filter.
        tracks = [
            track
            for collection in shown
            for track in collection.sorted_tracks()
        ]
        fill_tracks(self.library_table, tracks)

        noun = "artists" if by_artist else "albums"
        self.collection_heading.set_text(noun.capitalize())
        if not collections:
            self.library_status.set_text(
                "No music found. Add a folder under Device & Settings, or "
                "install mutagen with ./install.sh so tags can be read."
            )
            self.library_status.set_visible(True)
        elif not shown:
            self.library_status.set_text(f"No {noun} match this filter.")
            self.library_status.set_visible(True)
        else:
            self.library_status.set_visible(False)

    def _album_card(self, album):
        button = Gtk.Button()
        button.add_css_class("flat")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_size_request(ALBUM_COVER, -1)
        box.set_halign(Gtk.Align.CENTER)

        overlay = Gtk.Overlay()
        # Sized to the cover rather than the cell, so the state badge sits on
        # the artwork instead of floating beside it.
        overlay.set_halign(Gtk.Align.CENTER)
        cover = make_cover(album.art, ALBUM_COVER, f"{album.artist}/{album.title}")
        if album.state == STATE_PREVIEW:
            cover.set_opacity(0.6)
        overlay.set_child(cover)

        badge = Gtk.Box(spacing=5)
        badge.add_css_class("sf-badge")
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.END)
        badge.set_margin_start(7)
        badge.set_margin_bottom(7)
        badge.append(state_dot(album.state))
        badge.append(label(STATE_LABELS[album.state], "sf-caption"))
        overlay.add_overlay(badge)
        box.append(overlay)

        title = label(album.title, "sf-row-title", ellipsize=ELLIPSIZE_END)
        artist = label(album.artist, "sf-body", ellipsize=ELLIPSIZE_END)
        for text in (title, artist):
            # Without a cap, one long album title sets the natural width of
            # every cell in a homogeneous grid and the columns collapse.
            text.set_max_width_chars(16)
            text.set_width_chars(0)
        box.append(title)
        box.append(artist)
        button.set_child(box)
        button.connect("clicked", lambda _b, a=album: self._show_album(a))
        return button

    def _show_album(self, album):
        self.current_album = album
        child = self.album_art_holder.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.album_art_holder.remove(child)
            child = nxt
        self.album_art_holder.append(
            make_cover(album.art, 180, f"{album.artist}/{album.title}")
        )

        self.album_heading.set_text(album.title)
        tracks = album.sorted_tracks()
        total = sum(track.duration for track in tracks)
        size = sum(track.size for track in tracks)
        self.album_subheading.set_text(
            f"{album.artist} · {plural(len(tracks), 'track')} · "
            f"{human_duration(total)} · {human_size(size)} · "
            f"{album.on_ipod_count} of {len(tracks)} on iPod"
        )

        child = self.album_actions.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.album_actions.remove(child)
            child = nxt
        missing = [t for t in tracks if not t.on_ipod]
        if missing and self.mount_point:
            add_all = Gtk.Button(label=f"Queue {plural(len(missing), 'track')}")
            add_all.add_css_class("sf-button")
            add_all.add_css_class("accent")
            add_all.connect("clicked", lambda _b, ts=missing: self._queue_tracks(ts))
            self.album_actions.append(add_all)

        fill_tracks(self.album_tracks, tracks)
        self.show_view("album")

    def _populate_folders(self):
        child = self.folder_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.folder_list.remove(child)
            child = nxt

        counts = {}
        for track in self.library.tracks:
            for root in self.library.roots:
                if str(track.path).startswith(str(root)):
                    counts[root] = counts.get(root, 0) + 1
                    break

        for root in self.library.roots:
            row = Gtk.Box(spacing=10)
            row.set_margin_start(14)
            row.set_margin_end(10)
            row.set_margin_top(9)
            row.set_margin_bottom(9)
            row.append(
                label(home_relative(root), "sf-mono", hexpand=True, ellipsize=Pango.EllipsizeMode.START)
            )
            row.append(label(plural(counts.get(root, 0), "file"), "sf-caption"))
            remove = Gtk.Button(icon_name="window-close-symbolic")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_sensitive(len(self.library.roots) > 1)
            remove.connect("clicked", lambda _b, r=root: self.on_remove_folder(r))
            row.append(remove)
            self.folder_list.append(row)

    # --------------------------------------------------------- staged syncing

    def _pending_track(self, path):
        path = str(path)
        track = self._pending_track_index.get(path)
        if track is not None:
            return track
        record = dict(self.pending_records.get(path, {}))
        try:
            record["size"] = Path(path).stat().st_size
        except OSError:
            record["size"] = 0
        record.setdefault("title", Path(path).stem)
        return Track(path, record, STATE_LIBRARY)

    @staticmethod
    def _record_for_track(track):
        return {
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "genre": track.genre,
            "duration": track.duration,
            "track": track.track_no,
            "art": track.art,
            "size": track.size,
        }

    def _pending_accounting(self):
        tracks = []
        queued_bytes = 0
        for path in self.pending:
            if Path(path).suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            track = self._pending_track_index.get(path)
            if track is None:
                track = self._pending_track(path)
            if not track.on_ipod:
                tracks.append(track)
                queued_bytes += track.size
        playlist_changes = sum(
            Path(source).suffix.lower() in PLAYLIST_EXTENSIONS
            for source in self.pending_sources
        )
        return tracks, len(tracks) + playlist_changes, queued_bytes

    def _pending_symlink_note(self):
        skipped = sum(getattr(self, "pending_skipped_symlinks", {}).values())
        return f" · {plural(skipped, 'symlinked item')} skipped" if skipped else ""

    def _pending_copy_tracks(self):
        return self._pending_accounting()[0]

    def _pending_change_count(self):
        return self._pending_accounting()[1]

    def _queue_sources(
        self,
        sources,
        show_toast=True,
        metadata_complete=False,
        skipped_symlinks=None,
    ):
        sources = {str(source): list(tracks) for source, tracks in sources.items()}
        pending_only = {
            track.path
            for tracks in sources.values()
            for track in tracks
            if Path(track.path).suffix.lower() in AUDIO_EXTENSIONS
            and track.path not in self._library_by_path
        }
        if pending_only and not metadata_complete:
            self.source_generation += 1
            generation = self.source_generation
            device_identity = self.device_identity
            self.discovering_sources = True
            self._update_device_controls()

            def worker():
                enriched, complete = self._scan_pending_tracks(
                    pending_only, generation
                )
                GLib.idle_add(
                    self._finish_pending_enrichment,
                    generation,
                    device_identity,
                    sources,
                    enriched,
                    complete,
                    show_toast,
                    skipped_symlinks,
                )

            threading.Thread(target=worker, daemon=True).start()
            return None
        return self._commit_queue_sources(
            sources,
            show_toast=show_toast,
            skipped_symlinks=skipped_symlinks,
        )

    def _scan_pending_tracks(self, paths, generation):
        paths = set(str(path) for path in paths)
        enriched = {}
        records, complete, _skipped_symlinks = scan_tracks(
            files=paths,
            cancelled=lambda: generation != self.source_generation,
        )
        if not complete:
            return {}, False
        for record in records:
            path = record["path"]
            enriched[path] = Track(path, record, STATE_LIBRARY)
        for path in paths:
            enriched.setdefault(path, self._pending_track(path))
        return enriched, True

    def _finish_pending_enrichment(
        self,
        generation,
        device_identity,
        sources,
        enriched,
        complete,
        show_toast,
        skipped_symlinks,
    ):
        if generation != self.source_generation:
            return False
        self.discovering_sources = False
        self._update_device_controls()
        if device_identity != self.device_identity or not self.mount_point:
            self._toast("The connected iPod changed, so nothing was queued")
            return False
        if not complete:
            self._toast("Could not finish reading those tracks; nothing was queued")
            return False
        resolved = {
            source: [enriched.get(track.path, track) for track in tracks]
            for source, tracks in sources.items()
        }
        self._commit_queue_sources(
            resolved,
            show_toast=show_toast,
            skipped_symlinks=skipped_symlinks,
        )
        return False

    def _commit_queue_sources(
        self,
        sources,
        show_toast=True,
        replace=False,
        skipped_symlinks=None,
    ):
        if not self.mount_point:
            self._toast("Connect an iPod to queue tracks")
            return 0
        if self.device_identity is None:
            self._toast("Could not identify this iPod, so nothing was queued")
            return 0
        before = self._pending_change_count()
        retained_skipped = {
            source: count
            for source, count in self.pending_skipped_symlinks.items()
            if source not in self.pending_sources
        }
        if replace:
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_skipped_symlinks.clear()
            self.pending_skipped_symlinks.update(retained_skipped)
        if not self.pending_sources:
            self.pending_device_identity = self.device_identity
        elif self.pending_device_identity != self.device_identity:
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_skipped_symlinks.clear()
            self.pending_device_identity = self.device_identity

        for source, tracks in sources.items():
            source = str(source)
            members = set()
            for track in tracks:
                members.add(track.path)
                self.pending.add(track.path)
                self.pending_records[track.path] = self._record_for_track(track)
            if members:
                self.pending_sources[source] = members
            else:
                self.pending_sources.pop(source, None)
        for source, count in (skipped_symlinks or {}).items():
            source = str(source)
            if count:
                self.pending_skipped_symlinks[source] = count
            else:
                self.pending_skipped_symlinks.pop(source, None)
        owned = (
            set().union(*self.pending_sources.values())
            if self.pending_sources
            else set()
        )
        self.pending.intersection_update(owned)
        self.pending_records = {
            path: record
            for path, record in self.pending_records.items()
            if path in self.pending
        }
        if not self.pending_sources:
            self.pending_device_identity = None
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()
        added = max(0, self._pending_change_count() - before)
        if show_toast:
            self._toast(
                f"{plural(added, 'change')} queued" if added else "Already queued"
            )
        return added

    def _queue_paths(self, paths, show_toast=True):
        sources = {}
        expanded = []
        for path in dict.fromkeys(str(path) for path in paths):
            expanded.extend(self._audio_files(path) if Path(path).is_dir() else [path])
        for path in dict.fromkeys(expanded):
            if Path(path).is_file():
                sources[path] = [self._pending_track(path)]
        return self._queue_sources(sources, show_toast=show_toast)

    def _queue_tracks(self, tracks):
        return self._queue_sources(
            {track.path: [track] for track in tracks}, show_toast=True
        )

    def _queue_playlist(self, path):
        tracks = [self._pending_track(item) for item in local_playlist_tracks(path)]
        tracks.append(self._pending_track(path))
        return self._queue_sources({str(path): tracks}, show_toast=True)

    def _unqueue_track(self, track):
        key = track.path
        affected = [
            source
            for source, members in self.pending_sources.items()
            if key in members
        ]
        removed_directory = False
        for source in affected:
            members = self.pending_sources.pop(source)
            self.pending_skipped_symlinks.pop(source, None)
            removed_directory = removed_directory or source not in members
        owned = (
            set().union(*self.pending_sources.values())
            if self.pending_sources
            else set()
        )
        self.pending.intersection_update(owned)
        self.pending_records = {
            path: record
            for path, record in self.pending_records.items()
            if path in self.pending
        }
        if not self.pending_sources:
            self.pending_skipped_symlinks.clear()
            self.pending_device_identity = None
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()
        if removed_directory:
            self._toast("The whole folder was removed from the queue")

    def on_sync_pending(self, _button):
        if not self.pending_sources or not self.mount_point:
            return
        if self.pending_device_identity != self.device_identity:
            self._toast("Queued changes belong to a different iPod")
            return
        if not self._confirmed_device(self.pending_device_identity):
            return
        self.source_generation += 1
        generation = self.source_generation
        device_identity = self.device_identity
        paths = sorted(self.pending_sources)
        self.discovering_sources = True
        self._set_busy(True, "Checking queued sources")

        def worker():
            sources, complete, skipped_symlinks = self._scan_queued_sources(
                paths, generation
            )
            GLib.idle_add(
                self._finish_pending_source_scan,
                generation,
                device_identity,
                sources,
                complete,
                skipped_symlinks,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _scan_queued_sources(self, sources, generation):
        refreshed = {}
        skipped_symlinks = {}
        for source in sources:
            path = Path(source)
            if path.is_dir():
                records, complete, skipped = scan_tracks(
                    path,
                    cancelled=lambda: generation != self.source_generation,
                    skip_symlinks=True,
                )
                skipped_symlinks[source] = skipped
                tracks = [
                    Track(path / record["path"], record, STATE_LIBRARY)
                    for record in records
                ]
            elif path.suffix.lower() in PLAYLIST_EXTENSIONS:
                members, complete = read_local_playlist_tracks(path)
                if complete:
                    records, complete, _skipped = scan_tracks(
                        files=members,
                        cancelled=lambda: generation != self.source_generation,
                    )
                tracks = (
                    [
                        Track(record["path"], record, STATE_LIBRARY)
                        for record in records
                    ]
                    if complete
                    else []
                )
                if complete:
                    try:
                        size = path.stat().st_size
                    except OSError:
                        complete = False
                    else:
                        tracks.append(
                            Track(
                                path,
                                {"title": path.stem, "size": size},
                                STATE_LIBRARY,
                            )
                        )
            elif path.suffix.lower() in AUDIO_EXTENSIONS:
                records, complete, _skipped = scan_tracks(
                    files=[path],
                    cancelled=lambda: generation != self.source_generation,
                )
                tracks = [
                    Track(record["path"], record, STATE_LIBRARY)
                    for record in records
                ]
            else:
                tracks, complete = [], False
            if not complete:
                return {}, False, {}
            if tracks:
                refreshed[source] = tracks
        return refreshed, True, skipped_symlinks

    def _finish_pending_source_scan(
        self,
        generation,
        device_identity,
        sources,
        complete,
        skipped_symlinks,
    ):
        if generation != self.source_generation:
            self._set_busy(False)
            return False
        self.discovering_sources = False
        if not complete:
            self._set_busy(False)
            self._toast("Could not re-read queued sources, so sync was cancelled")
            return False
        if not self._confirmed_device(device_identity):
            self._set_busy(False)
            return False
        self._commit_queue_sources(
            sources,
            show_toast=False,
            replace=True,
            skipped_symlinks=skipped_symlinks,
        )
        if not self.pending_sources:
            self._set_busy(False)
            message = "Nothing remains in the queued sources"
            skipped = sum(skipped_symlinks.values())
            if skipped:
                message += f"; {plural(skipped, 'symlinked item')} skipped"
            self._toast(message)
            self.pending_skipped_symlinks.clear()
            return False
        self._set_busy(False)
        skipped = sum(skipped_symlinks.values())
        if skipped:
            self._toast(
                f"{plural(skipped, 'symlinked item')} skipped because links "
                "are not copied"
            )
        self._launch_pending_sync()
        return False

    def _launch_pending_sync(self):
        paths = sorted(self.pending_sources)
        copy_tracks, changes, _queued_bytes = self._pending_accounting()
        self.sync_files = [Path(p).name for p in paths]
        self.sync_total = len(copy_tracks)
        self._run(
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                *self._sync_options(),
                # A track title can start with a dash, and the shell script
                # would read that as a flag rather than a path.
                "--",
                *paths,
            ],
            "Copying to iPod",
            f"{plural(changes, 'change')} synced",
            then=self._clear_pending,
        )

    def _clear_pending(self):
        self.pending.clear()
        self.pending_sources.clear()
        self.pending_records.clear()
        self.pending_skipped_symlinks.clear()
        self._pending_track_index = dict(self._library_by_path)
        self.pending_device_identity = None
        return "Sync complete"

    # ------------------------------------------------------- options plumbing

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

    # --------------------------------------------------------- busy plumbing

    def _set_busy(self, busy, message=""):
        self.busy = busy
        for widget in self._busy_widgets:
            widget.set_sensitive(not busy)
        # Not in _busy_widgets, because whether it is sensitive depends on
        # there being something to clear as well as on nothing running.
        self._populate_cache_card()
        if not busy:
            self._update_device_controls()

        self.sync_revealer.set_reveal_child(busy)
        self.progress.set_visible(busy)
        if busy:
            self.sync_title.set_text(message)
            self.sync_spinner.start()
            self.progress.set_fraction(0)
            self.sync_count.set_text("")
            self.sync_current.set_text("")
        else:
            self.sync_spinner.stop()

    def _log(self, text):
        text = strip_ansi(text)
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), text)
        # Follow the output as it arrives. A pane that stays at the first line
        # of a copy shows the least useful part of a running operation.
        end = buf.create_mark(None, buf.get_end_iter(), False)
        self.log_view.scroll_to_mark(end, 0, False, 0, 0)
        buf.delete_mark(end)
        self._note_progress(text)
        return False

    def _note_progress(self, text):
        match = COPIED_LINE.match(text.rstrip("\n"))
        if match is None:
            return
        name = match.group("name")
        self.sync_current.set_text(name)
        row = Gtk.Box(spacing=11)
        row.append(label("✓", "sf-caption", width_chars=2, xalign=0.5))
        row.append(label(name, "sf-body", hexpand=True, ellipsize=ELLIPSIZE_END))
        row.append(label("Copied", "sf-caption"))
        self.sync_file_list.append(row)

        done = len(list(self._children(self.sync_file_list)))
        if self.sync_total:
            self.progress.set_fraction(min(1.0, done / self.sync_total))
            self.sync_count.set_text(f"{done} of {self.sync_total}")
        else:
            self.sync_count.set_text(f"{done} copied")

    @staticmethod
    def _children(container):
        child = container.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    def _clear_log(self):
        self.log_view.get_buffer().set_text("")
        child = self.sync_file_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.sync_file_list.remove(child)
            child = nxt

    def on_copy_log(self, _button):
        buf = self.log_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set_content(
                Gdk.ContentProvider.new_for_value(GObject.Value(str, text))
            )
            self._toast("Output copied")

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))
        return False

    # ------------------------------------------------------------- commands

    def _device_command_is_current(self, argv):
        if "--ipod" not in argv:
            return True
        index = argv.index("--ipod") + 1
        if index >= len(argv) or argv[index] is None:
            self._toast("Connect an iPod before running this action")
            return False
        mount_point = str(argv[index])
        if (
            mount_point != self.mount_point
            or resolve_device(mount_point, self.device_identity) is None
        ):
            self._toast("The connected iPod changed, so the action was cancelled")
            return False
        return True

    def _run(
        self,
        argv,
        busy_message,
        done_message,
        then=None,
        clear=True,
        on_failure=None,
    ):
        """Run a script in a worker thread, streaming output into the log.

        then, when given, is called on success and returns either the next
        command as (argv, busy_message, done_message) or a string to report as
        the outcome when there is nothing further to do. The YouTube flow uses
        this callback to queue only the tracks that a successful download says
        it produced.

        on_failure is its opposite, for a caller that has somewhere better to
        report the failure than the toast: a search result reports it inline in
        the section where the user pressed Add, which remains on screen.
        """
        if any(part is None for part in argv):
            self._toast("Connect an iPod before running this action")
            return False
        if not self._device_command_is_current(argv):
            return False
        device_command = "--ipod" in argv
        expected_identity = self.device_identity if device_command else None
        if clear:
            self._clear_log()
        self._set_busy(True, busy_message)

        def worker():
            code = -1
            if device_command:
                mount_index = argv.index("--ipod") + 1
                mount_point = str(argv[mount_index])
                if (
                    mount_point != self.mount_point
                    or resolve_device(mount_point, expected_identity) is None
                ):
                    GLib.idle_add(self._cancel_device_command)
                    return
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
            except (OSError, TypeError, ValueError) as exc:
                GLib.idle_add(self._log, f"failed to run: {exc}\n")
            GLib.idle_add(
                self._finish, code, done_message, then, device_command, on_failure
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _cancel_device_command(self):
        self._set_busy(False)
        self._toast("The connected iPod changed, so the action was cancelled")
        return False

    def _invalidate_device_snapshot(self):
        self.tag_generation += 1
        self._device_scan_active = bool(self.mount_point)
        self._device_snapshot_ready = False
        self._device_scan_tracks = {}
        self.device_tracks = []
        self.track_names = {}
        self._merge_states()
        self._populate_device_summary()
        self._update_device_controls()

    def _finish(
        self,
        code,
        done_message,
        then=None,
        device_command=False,
        on_failure=None,
    ):
        if code == 0 and device_command:
            self._invalidate_device_snapshot()
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
            if on_failure is None:
                self._toast(f"Failed (exit {code}) - see Details")
            self.details_toggle.set_active(True)
            self.sync_revealer.set_reveal_child(True)
            if on_failure is not None:
                on_failure()
        self.sync_total = 0
        self.refresh()
        self._rescan_library()
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
            self._discover_music_folder(path)

        dialog.select_folder(self, None, chosen)

    def _discover_music_folder(self, path):
        self.source_generation += 1
        generation = self.source_generation
        device_identity = self.device_identity
        self.discovering_sources = True
        self._update_device_controls()

        def worker():
            tracks, complete, skipped_symlinks = self._scan_source_tracks(
                path, generation
            )
            GLib.idle_add(
                self._finish_music_folder_discovery,
                generation,
                device_identity,
                path,
                tracks,
                complete,
                skipped_symlinks,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _scan_source_tracks(self, path, generation):
        records, complete, skipped_symlinks = scan_tracks(
            path,
            cancelled=lambda: generation != self.source_generation,
            skip_symlinks=True,
        )
        tracks = [
            Track(Path(path, record["path"]), record, STATE_LIBRARY)
            for record in records
        ]
        return tracks, complete, skipped_symlinks

    def _finish_music_folder_discovery(
        self,
        generation,
        device_identity,
        path,
        tracks,
        complete,
        skipped_symlinks,
    ):
        if generation != self.source_generation:
            return False
        self.discovering_sources = False
        self._update_device_controls()
        if device_identity != self.device_identity or not self.mount_point:
            self._toast("The connected iPod changed, so the folder was not queued")
            return False
        if not complete:
            self._toast("Could not finish reading that folder; nothing was queued")
            return False
        if not tracks:
            message = "No supported audio found in that folder"
            if skipped_symlinks:
                message += f"; {plural(skipped_symlinks, 'symlinked item')} skipped"
            self._toast(message)
            return False
        self._queue_sources(
            {str(path): tracks},
            metadata_complete=True,
            skipped_symlinks={str(path): skipped_symlinks},
        )
        return False

    def on_add_folder(self, _button):
        dialog = Gtk.FileDialog(title="Add a folder to search")

        def chosen(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path()
            if not path:
                self._toast("That location is not a local folder")
                return
            root = Path(path)
            if root in self.library.roots:
                return
            self.library.roots.append(root)
            save_music_roots(self.library.roots)
            self._rescan_library()

        dialog.select_folder(self, None, chosen)

    def on_remove_folder(self, root):
        if len(self.library.roots) <= 1:
            return
        self.library.roots = [r for r in self.library.roots if r != root]
        save_music_roots(self.library.roots)
        self._rescan_library()

    def on_add_playlist(self, _button):
        dialog = Gtk.FileDialog(title="Choose a playlist file")
        playlist_filter = Gtk.FileFilter()
        playlist_filter.set_name("Playlists (M3U, PLS)")
        playlist_filter.add_suffix("m3u")
        playlist_filter.add_suffix("pls")
        dialog.set_default_filter(playlist_filter)

        def chosen(dlg, result):
            try:
                chosen_file = dlg.open_finish(result)
            except GLib.Error:
                return
            path = chosen_file.get_path()
            if not path:
                self._toast("That location is not a local file")
                return
            self._add_playlist(path)

        dialog.open(self, None, chosen)

    def _add_playlist(self, path):
        # A named playlist implies wanting the name read aloud, exactly as
        # choosing a grouping under Sync options does: with no screen, the
        # spoken name is the only way to find the playlist again.
        if not self.speech_engine_available:
            self._toast("No speech engine installed")
            return
        self.playlist_voiceover.set_active(True)
        self._queue_playlist(path)

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
                f"title, kept in {home_relative(YOUTUBE_LIBRARY)}, and queued "
                "for the next sync."
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
        self._start_youtube_download(
            url,
            single=not whole_playlist.get_active(),
            busy_message="Downloading from YouTube",
        )

    def _start_youtube_download(self, url, single, busy_message, on_failure=None):
        """Fetch one link and queue whatever that run produced.

        Shared by the dialog and by a search result so both queue exactly the
        tracks the download reported rather than the folder it wrote into.
        """
        # Written by the download and read when it completes, so only what this
        # run actually fetched enters the queue. Without it the whole library
        # would be queued every time.
        handle, new_tracks = tempfile.mkstemp(prefix="ipod-fetch-", suffix=".list")
        os.close(handle)

        fetch = fetch_command(url, YOUTUBE_LIBRARY, new_tracks, single)

        def failed():
            try:
                os.unlink(new_tracks)
            except OSError:
                pass
            if on_failure is not None:
                on_failure()

        if not self._run(
            fetch,
            busy_message,
            "Downloaded",
            then=lambda: self._sync_downloaded(new_tracks),
            on_failure=failed,
        ):
            # Refused before anything ran, so the list file it would have read
            # is left behind unless it is cleaned up here.
            failed()
        return fetch

    def _sync_downloaded(self, new_tracks):
        """Queue what the download produced, or say why there is nothing to."""
        sources = fetched_sources(new_tracks, YOUTUBE_LIBRARY)
        try:
            os.unlink(new_tracks)
        except OSError:
            pass

        if not sources:
            return "Already downloaded - nothing new to add"
        queued = self._queue_paths(sources, show_toast=False)
        if queued is None:
            return "Downloaded; reading track details before queueing"
        return (
            f"{plural(queued, 'track')} queued for sync"
            if queued
            else "Downloaded, but nothing was queued"
        )

    def on_remove_track(self, _button, relpath):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before removing tracks")
            return
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
        dialog.connect(
            "response", self._on_remove_response, relpath, self.device_identity
        )
        dialog.present(self)

    def _on_remove_response(self, _dialog, response, relpath, device_identity):
        if response != "remove":
            return
        if not self._confirmed_device(device_identity):
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
            "Removing track",
            "Track removed",
        )

    def on_remove_playlist(self, _button, name):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before removing playlists")
            return
        dialog = Adw.AlertDialog(
            heading="Remove this playlist?",
            body=(
                f"{name}\n\n"
                "Only the playlist is removed and the database is rebuilt. "
                "The songs it lists stay on the iPod."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            self._on_playlist_remove_response,
            name,
            self.device_identity,
        )
        dialog.present(self)

    def _on_playlist_remove_response(
        self, _dialog, response, name, device_identity
    ):
        if response != "remove":
            return
        if not self._confirmed_device(device_identity):
            return
        self._run(
            [
                str(REMOVE_SCRIPT),
                "--ipod",
                self.mount_point,
                "--yes",
                "--playlist",
                # A playlist named after a song can start with a dash too.
                "--",
                name,
            ],
            "Removing playlist",
            "Playlist removed",
        )

    @staticmethod
    def _audio_files(path):
        found = []
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                candidate = Path(root, name)
                if candidate.suffix.lower() in AUDIO_EXTENSIONS:
                    found.append(str(candidate))
        return found

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
            "Rebuilding database",
            "Database rebuilt",
        )

    def on_wipe(self, _button):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before wiping it")
            return
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
        dialog.connect("response", self._on_wipe_response, self.device_identity)
        dialog.present(self)

    def _confirmed_device(self, device_identity):
        if resolve_device(self.mount_point, device_identity) is None:
            self._toast("The connected iPod changed, so the action was cancelled")
            return False
        return True

    def _on_wipe_response(self, _dialog, response, device_identity):
        if response == "cancel":
            return
        if not self._confirmed_device(device_identity):
            return
        argv = [str(WIPE_SCRIPT), "--ipod", self.mount_point, "--yes"]
        if response == "backup":
            target = Path(os.path.expanduser("~"), "ipod-backup")
            argv += ["--backup", str(target)]
        self._run(argv, "Wiping", "iPod wiped")

    def on_eject(self, _button):
        expected_identity = self.device_identity
        device = resolve_device(
            self.mount_point, expected_identity, require_block=True
        )
        if device is None:
            self._toast("Could not determine the device to unmount")
            return

        self._set_busy(True, "Ejecting")

        def worker():
            current = resolve_device(
                self.mount_point, expected_identity, require_block=True
            )
            if current is None:
                GLib.idle_add(
                    self._finish_dbus,
                    False,
                    "",
                    "the connected iPod changed; eject was cancelled",
                )
                return
            ok, message = udisks_filesystem_call(
                current.block_device, "Unmount"
            )
            GLib.idle_add(
                self._finish_dbus, ok, "Safe to unplug", message, True
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_dbus(
        self, ok, success_message, error_message, invalidate_snapshot=False
    ):
        self._set_busy(False)
        self._toast(success_message if ok else f"Failed: {error_message}")
        if ok and invalidate_snapshot:
            self._invalidate_device_snapshot()
        self.refresh()
        return False

    def on_mount_clicked(self, _button):
        """Mount an iPod that is plugged in but not mounted."""
        candidates = unmounted_vfat_devices()
        if not candidates:
            self._toast("No unmounted iPod found")
            return

        self._set_busy(True, "Mounting")

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
        self._provider = None

    def do_activate(self):
        if self._provider is None:
            self._provider = load_css()
        window = self.props.active_window or IpodWindow(application=self)
        window.present()


if __name__ == "__main__":
    sys.exit(IpodApp().run(sys.argv))
