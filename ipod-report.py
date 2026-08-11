#!/usr/bin/env python3
"""The machine-readable half of what the scripts say.

The scripts report in prose, which is right for a person at a terminal and
useless to anything else: finding out how many tracks are on the device meant
parsing "iPod now holds 42 track(s)". This writes the same facts as JSON, and
it is one program rather than a helper in each script so that every report
names, orders and escapes its fields the same way.

Python rather than shell because JSON is not something a shell can emit
safely. Track and playlist names on this device come from tags and from
YouTube titles, so a quote, a backslash or a newline in one is ordinary, and
printf has no way to say so. Non-ASCII is escaped rather than written through,
which keeps the document valid under any locale and gives a name a FAT volume
handed back as undecodable bytes somewhere to go.

Reading the device is done here rather than being handed in, because the read
and the check that the device is still there have to be the same pass: a
report assembled from a device that went away half way through would be a
definite-looking answer about a device nobody can see. That check is the
whole reason this refuses to print a partial document - the same rule
ipod-fetch.sh --new-tracks follows when it deletes the file it could not fill.

Deliberately independent of ipod_gui, which is a GTK application and imports
the bindings on the way in: the scripts are the product and have to run on a
machine with no bindings at all. So this repeats what ipod_gui/device.py does,
exactly as lib.sh's list_vfat_mounts repeats find_ipods(), and
tests/product-e2e.sh pins the two readings against each other on one device so
they cannot drift apart.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The report's shape, so a caller that stored one can tell whether the fields
# it knows still mean what they did. Bumped only when a field changes meaning
# or leaves; adding one does not need it, because a reader looking for a field
# it knows is unaffected by a field it does not.
SCHEMA = 1

# The device stopped answering part way through. The whole table lives in
# lib.sh, which is where the scripts read it from; this one number is repeated
# because a report run on its own still has to be able to say it, and
# tests/product-e2e.sh fails if the two ever disagree.
EXIT_DEVICE_GONE = 5

MUSIC_PREFIX = "iPod_Control/Music/"


class DeviceGone(Exception):
    """The volume stopped answering between the first read and the last."""


def volume_identity(mount_point):
    """What the volume calls itself, or None if it will not say.

    The filesystem UUID when the kernel knows one, and otherwise a digest of
    the device's own SysInfo, which is what a FAT volume formatted without one
    has instead. Mirrors volume_identity() in ipod_gui/device.py.
    """
    try:
        uuid = subprocess.run(
            ["findmnt", "-rno", "UUID", "--target", str(mount_point)],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        uuid = ""
    if uuid and uuid != "-":
        return f"uuid:{uuid}"

    try:
        sysinfo = Path(mount_point, "iPod_Control", "Device", "SysInfo").read_bytes()
    except OSError:
        return None
    if not sysinfo:
        return None
    return f"sysinfo:{hashlib.sha256(sysinfo).hexdigest()}"


def list_tracks(mount_point):
    """Every track on the device, relative to the music folder and sorted.

    The same paths ipod-remove.sh takes as arguments, so a caller can act on
    what it reads here without transforming it first.

    Walked with os.walk rather than with rglob, which is what the window uses:
    rglob swallows a folder it cannot read and yields nothing for it, so a
    music folder with one unreadable album in it comes back looking like a
    device holding fewer tracks - or none. The window can afford that, because
    a row that fails to appear is a row the user can see is not there. A
    report cannot: a confident "track_count: 0" is what a caller would sync a
    whole library onto, so the walk has to fail instead.
    """
    music = Path(mount_point, "iPod_Control", "Music")
    if not music.is_dir():
        return []

    def unreadable(error):
        raise error

    files = []
    for directory, _subdirectories, names in os.walk(music, onerror=unreadable):
        for name in names:
            path = Path(directory, name)
            if path.is_file():
                files.append(path)
    return [str(path.relative_to(music)) for path in sorted(files)]


def list_playlists(mount_point):
    """The playlists at the volume root, as (name, entries).

    Entries under the music folder come back relative to it, matching
    list_tracks; hand-written lines pointing elsewhere are kept as written.

    A playlist that cannot be read stops the report rather than being left
    out, which is where this parts company with the window's copy: the window
    is painting what it can show and carries on without one row, while a
    report that quietly dropped a playlist would be read as a device that does
    not have it.
    """
    playlists = []
    root = Path(mount_point)
    paths = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".m3u", ".pls"}
    )
    for path in paths:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
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


def speakable_id(name):
    """The name the device files a playlist's spoken recording under.

    The database identifies a playlist by a digest of its name and the
    recording is a WAV named after that, little end first, so asking whether a
    playlist can be announced means deriving the id rather than looking for a
    file named after the playlist. Mirrors speakable_id() in
    ipod_gui/device.py, including the surrogateescape: a volume mounted under
    a non-UTF-8 iocharset hands back names holding bytes no UTF-8 decode
    accepts, and hashing them back the way they were decoded hashes what is
    actually on the volume.
    """
    encoded = name.encode("utf-8", "surrogateescape")
    digest = hashlib.md5(encoded, usedforsecurity=False).digest()[:8]
    return "".join(f"{byte:02x}" for byte in reversed(digest))


def spoken_names(mount_point, names):
    """Which of these playlists the device can say out loud.

    With no screen, a playlist the shuffle cannot announce is one the user
    cannot pick out, so a report of what is on the device has to say which is
    which rather than listing them as though they were equal.
    """
    speakable = Path(mount_point, "iPod_Control", "Speakable", "Playlists")
    if not speakable.is_dir():
        return set()
    stems = {path.stem.lower() for path in speakable.iterdir() if path.is_file()}
    return {name for name in names if speakable_id(name) in stems}


def sync_options(mount_point):
    """The playlist and voiceover flags the last sync saved on the device.

    A file that exists and cannot be read is not the same as no saved options,
    and reporting the second for the first would tell a caller the next
    rebuild is safe when it would silently drop every playlist. So this raises
    rather than guesses, exactly as read_sync_options in lib.sh dies.
    """
    path = Path(mount_point, "iPod_Control", ".sync-options")
    if not path.exists() and not path.is_symlink():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def storage(mount_point):
    """Total, used and free bytes, or None when the volume will not say.

    Asked apart from everything else because a device whose size cannot be
    read is still a device worth reporting, which is the same allowance
    probe_device() makes for the window's storage meter.
    """
    try:
        usage = shutil.disk_usage(str(mount_point))
    except OSError:
        return None
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def still_there(mount_point, identity):
    """Whether the device read a moment ago is still the one at this path.

    Both halves matter: the volume can be unplugged, and it can be unmounted
    and replaced by a different one mounted at the same path, and a report
    assembled across either would describe a device nobody can see.
    """
    return (
        Path(mount_point, "iPod_Control").is_dir()
        and volume_identity(mount_point) == identity
    )


def device_report(mount_point):
    """Everything the device says about itself, in one pass.

    The identity is read first and again last: between them the volume can be
    unplugged, or unmounted and replaced by another one at the same path, and
    either would leave the figures gathered in between describing a device
    that is no longer there.
    """
    mount_point = str(mount_point).rstrip("/") or "/"
    identity = volume_identity(mount_point)
    try:
        playlists = list_playlists(mount_point)
        spoken = spoken_names(mount_point, [name for name, _entries in playlists])
        tracks = list_tracks(mount_point)
        options = sync_options(mount_point)
    except OSError:
        # A read that failed because the volume went away is routine, and it
        # is what a caller polling this will eventually catch it in the middle
        # of. One that failed while the device is still sitting there is a
        # different answer - an unreadable file rather than an absent device -
        # so which it was is decided by looking, not by the exception.
        if still_there(mount_point, identity):
            raise
        raise DeviceGone(f"{mount_point} stopped answering mid-read") from None
    used = storage(mount_point)

    if not still_there(mount_point, identity):
        raise DeviceGone(f"{mount_point} is no longer the device it was")

    return {
        "schema": SCHEMA,
        "mount_point": mount_point,
        "identity": identity,
        "storage": used,
        "track_count": len(tracks),
        "tracks": tracks,
        "playlists": [
            {"name": name, "spoken": name in spoken, "entries": entries}
            for name, entries in playlists
        ],
        "sync_options": options,
    }


RECORD_SEPARATOR = "\x1f"


def capability_report(lines):
    """The capability probes install.sh runs, as a caller can branch on them.

    Handed in rather than probed here, because the probes belong to lib.sh and
    the answers have to be the same ones the installer itself acted on rather
    than a second reading taken moments later. Each line holds the
    capability's id, 1 or 0 for whether it is there, the name the installer
    prints for it, what to say about it, and the packages that would provide
    it - separated by the ASCII unit separator rather than a tab, because bash
    folds runs of tabs together and a capability with nothing to add would
    lose its empty field on the way in.
    """
    capabilities = []
    missing_packages = []
    for line in lines:
        if not line:
            continue
        parts = line.split(RECORD_SEPARATOR)
        if len(parts) != 5:
            raise ValueError(f"malformed capability record: {line!r}")
        name, available, label, detail, packages = parts
        packages = packages.split()
        capabilities.append(
            {
                "id": name,
                "available": available == "1",
                "label": label,
                "detail": detail,
                "packages": packages,
            }
        )
        if available != "1":
            missing_packages += packages
    return {
        "schema": SCHEMA,
        "satisfied": all(entry["available"] for entry in capabilities),
        "capabilities": capabilities,
        # The apt names for everything missing, deduplicated in the order the
        # capabilities were probed, so a caller can install them in one
        # transaction rather than one capability at a time.
        "missing_packages": list(dict.fromkeys(missing_packages)),
    }


def emit(document):
    """Write the document, or nothing at all.

    Serialised in full before the first byte reaches stdout, so a value that
    cannot be encoded fails with an empty stream rather than with half a
    document that a reader would have to guess about.
    """
    text = json.dumps(document, indent=2) + "\n"
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except OSError as error:
        print(f"error: could not write the report: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv):
    if len(argv) < 2:
        print(
            "usage: ipod-report.py device|identity <mount-point>\n"
            "       ipod-report.py capabilities  (records on stdin)",
            file=sys.stderr,
        )
        return 2

    action = argv[1]
    if action in ("device", "identity"):
        if len(argv) != 3:
            print(f"error: {action} takes one mount point", file=sys.stderr)
            return 2
        mount_point = argv[2]
        if action == "identity":
            # No document and no error: an identity the volume will not give
            # is a fact about the volume, and the caller compares whatever
            # comes back against what it got last time either way.
            identity = volume_identity(mount_point)
            if identity is not None:
                print(identity)
            return 0
        try:
            return emit(device_report(mount_point))
        except DeviceGone as error:
            print(f"error: the iPod stopped answering: {error}", file=sys.stderr)
            return EXIT_DEVICE_GONE
        except OSError as error:
            print(f"error: could not read the iPod: {error}", file=sys.stderr)
            return 1

    if action == "capabilities":
        if len(argv) != 2:
            print("error: capabilities takes its records on stdin", file=sys.stderr)
            return 2
        try:
            document = capability_report(sys.stdin.read().splitlines())
        except (OSError, ValueError) as error:
            print(f"error: could not read the capabilities: {error}", file=sys.stderr)
            return 1
        return emit(document)

    print(f"error: unknown report: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
