"""Finding the iPod and reading what is on it, over USB.

Device-facing operations here talk to a mounted device or to udisks, so they
can be slow enough to matter on the main loop.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from gi.repository import Gio, GLib


class _DeviceIOLock:
    """Let device readers overlap while excluding mutations.

    Probes and tag scans only read, so blocking one behind the other can hide
    an unplugged device for the full timeout of a stuck read. Writers wait for
    all readers and prevent new readers until the mutation is complete.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        self._owners = set()

    @contextmanager
    def read(self):
        thread_id = threading.get_ident()
        with self._condition:
            if thread_id in self._owners:
                raise RuntimeError("device I/O lock is not reentrant")
            while self._writer or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
            self._owners.add(thread_id)
        try:
            yield
        finally:
            with self._condition:
                self._owners.remove(thread_id)
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def write(self):
        thread_id = threading.get_ident()
        with self._condition:
            if thread_id in self._owners:
                raise RuntimeError("device I/O lock is not reentrant")
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
                self._owners.add(thread_id)
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._owners.remove(thread_id)
                self._writer = False
                self._condition.notify_all()


DEVICE_IO_LOCK = _DeviceIOLock()


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


def count_tracks(mount_point, cancelled=None):
    """How many files the device holds, walking it over USB.

    Takes the same cancellation callback as the tag scan, because this is the
    one call in here whose cost grows with the size of the library: a walk
    superseded by a newer one should stop competing for the bus rather than
    finish a count nothing will read.
    """
    music = Path(mount_point, "iPod_Control", "Music")
    if not music.is_dir():
        return 0
    total = 0
    for path in music.rglob("*"):
        if cancelled is not None and cancelled():
            break
        if path.is_file():
            total += 1
    return total


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


def speakable_id(name):
    """The name the device files a playlist's spoken name under.

    Nothing on the shuffle is stored as text, the spoken names least of all:
    the database identifies a playlist by an id derived from its name, and the
    recording is a WAV named after that id, little end first. Asking whether a
    playlist can be announced therefore means deriving the id and looking for
    that file - a WAV named after the playlist would be a file the firmware
    never reads.

    Names arrive decoded from a FAT volume, and a volume mounted under a
    non-UTF-8 iocharset hands back bytes no UTF-8 decode accepts, escaped into
    lone surrogates. Encoding those back the way they were decoded hashes the
    bytes actually on the volume; encoding strictly would raise instead, on the
    thread the whole device reading runs on.
    """
    encoded = name.encode("utf-8", "surrogateescape")
    digest = hashlib.md5(encoded, usedforsecurity=False).digest()[:8]
    return "".join(f"{byte:02x}" for byte in reversed(digest))


def spoken_playlists(mount_point, names):
    """Which of these playlists the device can say out loud, case-folded.

    With no screen, a playlist the shuffle cannot announce is one the user has
    no way to identify, so the interface says which are which rather than
    listing them all as though they were equal.

    Names are taken in rather than read back out of the recordings, because the
    id is a hash: it says whether a given name is spoken, and cannot be turned
    back into the name. They arrive as the device spells them, which is what
    was hashed - a playlist made here as "Gym" and synced onto a FAT volume
    that already held "gym" is announced under the name the volume kept.
    """
    speakable = Path(mount_point, "iPod_Control", "Speakable", "Playlists")
    if not speakable.is_dir():
        return set()
    stems = {p.stem.lower() for p in speakable.iterdir() if p.is_file()}
    return {name.lower() for name in names if speakable_id(name) in stems}


class DeviceProbe:
    """One reading of the connected device, as the window paints it.

    The window used to make each of these calls itself, one at a time, from
    the main loop. Gathering them into a value means the crossing to the
    device happens once, on a worker thread, and painting afterwards touches
    nothing but memory.
    """

    __slots__ = (
        "candidates",
        "mount_point",
        "identity",
        "sync_options",
        "playlists",
        "spoken",
        "track_count",
        "usage",
        "readable",
    )

    def __init__(
        self,
        candidates,
        mount_point=None,
        identity=None,
        sync_options=None,
        playlists=None,
        spoken=None,
        track_count=0,
        usage=None,
        readable=True,
    ):
        self.candidates = candidates
        # A probe is built complete or not at all: every field a connected
        # device has is passed at once, so a device read half way cannot be
        # mistaken for one holding nothing.
        self.mount_point = mount_point
        self.identity = identity
        self.sync_options = sync_options or (0, [], False, False)
        self.playlists = playlists or []
        self.spoken = spoken or set()
        self.track_count = track_count
        self.usage = usage
        # False only when detection answered and the volume then stopped
        # answering, which is what unplugging mid-read looks like.
        self.readable = readable


def probe_device(cancelled=None):
    """Read everything the window needs from the device, off the main loop.

    Detection, identity, saved options, playlists, the track count and free
    space in one pass. Every one of them is a subprocess or a walk over USB,
    and a 2GB device full of small files makes the count alone slow enough to
    stall a redraw, so none of this may run where the window draws.
    """
    if cancelled is not None and cancelled():
        return DeviceProbe([])
    with DEVICE_IO_LOCK.read():
        if cancelled is not None and cancelled():
            return DeviceProbe([])
        return _probe_device(cancelled)


def _probe_device(cancelled=None):
    candidates = find_ipods()
    if len(candidates) != 1:
        return DeviceProbe(candidates)
    if cancelled is not None and cancelled():
        return DeviceProbe(candidates)

    mount_point = candidates[0]
    try:
        identity = volume_identity(mount_point)
        if cancelled is not None and cancelled():
            return DeviceProbe(candidates)
        sync_options = saved_sync_options(mount_point)
        if cancelled is not None and cancelled():
            return DeviceProbe(candidates)
        playlists = list_playlists(mount_point)
        if cancelled is not None and cancelled():
            return DeviceProbe(candidates)
        spoken = spoken_playlists(mount_point, [name for name, _ in playlists])
        if cancelled is not None and cancelled():
            return DeviceProbe(candidates)
        track_count = count_tracks(mount_point, cancelled=cancelled)
        if cancelled is not None and cancelled():
            return DeviceProbe(candidates)
    except OSError:
        # The volume went away between findmnt answering and this walk, which
        # is routine: unplugging is what fires the refresh in the first place.
        # Reported as unreadable rather than as an iPod holding nothing.
        return DeviceProbe(candidates, readable=False)

    try:
        usage = shutil.disk_usage(mount_point)
    except OSError:
        # Asked apart from the reads above, because a device whose size cannot
        # be read is still a device worth showing, and always has been.
        usage = None

    if cancelled is not None and cancelled():
        return DeviceProbe(candidates)
    if (
        not Path(mount_point, "iPod_Control").is_dir()
        or volume_identity(mount_point) != identity
    ):
        return DeviceProbe(candidates, readable=False)

    return DeviceProbe(
        candidates,
        mount_point=mount_point,
        identity=identity,
        sync_options=sync_options,
        playlists=playlists,
        spoken=spoken,
        track_count=track_count,
        usage=usage,
    )
