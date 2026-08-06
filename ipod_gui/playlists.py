"""Playlists you make here, kept as M3U files in a folder of your own.

A playlist used to exist only on the device, which meant making one needed an
iPod attached, a list exported from some other program, and a sync before any
of it could be seen. Building a list is not a device operation, so it happens
here instead: one M3U per playlist under ~/Music/Playlists, holding absolute
paths to your own files, editable with no iPod in sight.

The file is the playlist. Nothing keeps an index beside it, so a list edited in
another program, moved, or deleted by hand cannot disagree with a record of
what it is supposed to hold - the same rule the preview cache follows.

The name is the filename, because that is what ipod-sync.sh turns into the
playlist's spoken name on the device. So the characters FAT refuses are refused
here, while the name is being typed, rather than silently mangled two steps
later by the sync and read out as something else.
"""

import os
from pathlib import Path

from .model import read_local_playlist_tracks


# One format, always written, so a playlist's name is enough to find its file.
# A .pls dropped into the folder by something else is left alone rather than
# half-adopted; importing one converts it.
PLAYLIST_SUFFIX = ".m3u"

# What FAT cannot store in a filename, which is what ipod-sync.sh replaces with
# underscores when it writes the playlist onto the device. Refused at the point
# the name is typed instead, so the name shown here is the name the device will
# announce.
FAT_FORBIDDEN = set('\\/:*?"<>|') | {chr(code) for code in range(32)} | {"\x7f"}


class Playlist:
    """One playlist, as its file currently reads.

    `path` is the M3U this app owns, or None for a playlist that exists only on
    the device: those are shown but not edited here, because their entries name
    scrambled four-letter files on the iPod and there is nothing local to write
    down in their place.
    """

    __slots__ = ("name", "path", "entries")

    def __init__(self, name, path, entries):
        self.name = name
        self.path = None if path is None else Path(path)
        # Kept as written, including entries whose file is missing right now:
        # an unplugged external drive must not quietly empty a playlist the
        # next time one of its tracks is added or removed.
        self.entries = list(entries)

    @property
    def editable(self):
        return self.path is not None


def merge_with_device(local, device_playlists):
    """Every playlist to show: the local ones, then those only on the device.

    Matched by name, case-insensitively, because that is how FAT matches them:
    a local "Gym" and a device "gym" are one playlist that has been synced, not
    two that happen to look alike.
    """
    merged = list(local)
    known = {playlist.name.casefold() for playlist in merged}
    for name, entries in device_playlists:
        if name.casefold() not in known:
            merged.append(Playlist(name, None, entries))
    return merged


def local_playlist_file(root, name):
    return Path(root) / f"{name}{PLAYLIST_SUFFIX}"


def playlist_contents(path):
    """The paths a playlist lists, and whether the file could be read at all.

    The pair, the way read_local_playlist_tracks answers, because "nothing is
    in it" and "nothing could be read out of it" are the same empty list and
    mean opposite things to an edit. Every edit here is a read and a rewrite -
    a playlist symlinked onto a drive that is not plugged in, or a file that
    has gone since the folder was last listed, reads as empty while the folder
    it sits in is still perfectly writable, and a rewrite from that read would
    replace the whole list with whatever the edit added.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], False
    entries = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries, True


def read_playlist_entries(path):
    """The paths a playlist lists, in order, as written.

    Empty for a file that could not be read, because a rail has a row to paint
    either way. Editing is what needs the difference, and asks for it.
    """
    return playlist_contents(path)[0]


def write_playlist_entries(path, entries):
    """Rewrite a playlist beside itself, then rename it into place.

    Atomically, because a half-written list is one ipod-sync.sh would copy
    faithfully: the failure would show up not here but on a device with no
    screen to show it on.
    """
    path = Path(path)
    body = ["#EXTM3U", *(str(entry) for entry in entries)]
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text("\n".join(body) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False
    return True


def local_playlists(root):
    """Every playlist in the folder, by name."""
    try:
        paths = list(Path(root).iterdir())
    except OSError:
        return []
    playlists = [
        Playlist(path.stem, path, read_playlist_entries(path))
        for path in paths
        if path.is_file() and path.suffix.lower() == PLAYLIST_SUFFIX
    ]
    playlists.sort(key=lambda playlist: playlist.name.lower())
    return playlists


def name_problem(name, taken=()):
    """Why this name cannot be used, as a sentence, or None.

    Answered before anything is written, so a dialog can refuse a name while it
    is being typed rather than reporting the failure once it is too late.
    """
    name = (name or "").strip()
    if not name:
        return "Enter a name for the playlist"
    if any(character in FAT_FORBIDDEN for character in name):
        return 'A playlist name cannot contain \\ / : * ? " < > |'
    if name.endswith("."):
        return "A playlist name cannot end with a dot"
    # Case-insensitively, because FAT is: "Gym" and "gym" are one file on the
    # device, and the sync refuses the second of them rather than merging.
    if name.casefold() in {str(other).casefold() for other in taken}:
        return f"There is already a playlist called {name}"
    return None


def default_name(taken):
    """The first free "Playlist N".

    Numbered rather than dated or random, because this name is read aloud on a
    device with no screen. For the same reason it carries no punctuation: a
    speech engine either spells "#" out or drops it, and neither is a name.
    """
    used = {str(other).casefold() for other in taken}
    number = 1
    while f"playlist {number}" in used:
        number += 1
    return f"Playlist {number}"


def sanitise_name(name):
    """A name reduced to one a FAT volume can hold.

    Only used where the name was not typed here - importing a file another
    program named - because a name being entered is refused rather than
    silently rewritten under the cursor.
    """
    cleaned = "".join(
        "_" if character in FAT_FORBIDDEN else character
        for character in (name or "").strip()
    )
    return cleaned.rstrip(". ")


def unique_name(wanted, taken):
    """`wanted`, or the first "wanted 2", "wanted 3" that is free."""
    wanted = sanitise_name(wanted) or "Playlist"
    used = {str(other).casefold() for other in taken}
    if wanted.casefold() not in used:
        return wanted
    number = 2
    while f"{wanted} {number}".casefold() in used:
        number += 1
    return f"{wanted} {number}"


def create_local_playlist(root, name):
    """Make an empty playlist and return its file, or None if it cannot."""
    path = local_playlist_file(root, name)
    if path.exists():
        return None
    return path if write_playlist_entries(path, []) else None


def delete_local_playlist(path):
    """Delete a playlist's file. True when it is gone afterwards.

    Given the file rather than the name it is listed under, because a name does
    not name a file: a Gym.M3U dropped into the folder by another program is
    read as "Gym", and rebuilding "Gym.m3u" from that would report deleting a
    playlist that is still sitting there.
    """
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def rename_local_playlist(path, new_name):
    """Move a playlist to a new name, returning its file or None.

    Refuses to land on an existing playlist, so a rename can never silently
    swallow the list that was already called that. The new file is written with
    the suffix this app owns, whatever the old one happened to be spelled.
    """
    source = Path(path)
    destination = local_playlist_file(source.parent, new_name)
    if destination.exists() and destination != source:
        return None
    try:
        os.replace(source, destination)
    except OSError:
        return None
    return destination


def import_playlist_file(root, source, taken=()):
    """Copy an M3U or PLS from elsewhere into the folder.

    The entries are resolved to absolute paths on the way in: a file:// URI, a
    Windows separator, or a path relative to wherever the list came from all
    stop meaning anything once the file has been copied somewhere else. An
    entry naming nothing on this computer is dropped rather than carried,
    because the sync would skip it anyway and every later edit would rewrite
    it.

    Returns (path, tracks, None), or (None, 0, problem) naming which of the
    four ways this fails happened. An unreadable file and a folder that cannot
    be written are not "nothing was found": reported as one, they send the user
    looking through a playlist that was never the trouble.

    The name it lands on is checked against the folder and not only against the
    names the caller knew about, which is the same rule create_local_playlist
    and rename_local_playlist hold. The caller's list is stale by construction:
    a file dialog stands open for as long as the user browses, and a playlist
    another program wrote in the meantime would be swallowed by this write.
    """
    source = Path(source)
    tracks, complete = read_local_playlist_tracks(source)
    if not complete:
        return None, 0, "That playlist file could not be read"
    if not tracks:
        return None, 0, "Nothing in that playlist could be found on this computer"
    path = local_playlist_file(root, unique_name(source.stem, taken))
    if path.exists():
        return None, 0, f"There is already a playlist called {path.stem}"
    if not write_playlist_entries(path, tracks):
        return None, 0, f"Could not write into {path.parent}"
    return path, len(tracks), None


def add_entries(path, entries):
    """Append tracks to a playlist, skipping ones it already lists.

    Returns how many were added, or None if the playlist could not be read or
    written. A playlist is an ordered list rather than a set, but the same
    track landing in one twice is always a mis-click here: the only way to ask
    for it is to press Add twice, which is what a double click on one button
    is.
    """
    current, complete = playlist_contents(path)
    if not complete:
        return None
    known = set(current)
    added = []
    for entry in entries:
        entry = str(entry)
        if entry not in known:
            known.add(entry)
            added.append(entry)
    if not added:
        return 0
    return len(added) if write_playlist_entries(path, current + added) else None


def remove_entry(path, entry):
    """Drop every mention of one track.

    Returns how many lines went, or None if the playlist could not be read or
    written, the way add_entries counts what it added. The two ways nothing
    changes are not one thing: a playlist that no longer lists the track has
    already been edited somewhere else, while a playlist that could not be
    rewritten is a folder to go and look at - and a caller told only "False"
    reports whichever of those it guessed at.
    """
    current, complete = playlist_contents(path)
    if not complete:
        return None
    remaining = [line for line in current if line != str(entry)]
    removed = len(current) - len(remaining)
    if not removed:
        return 0
    return removed if write_playlist_entries(path, remaining) else None


def move_entry(path, source_index, target_index):
    """Reorder one track within a playlist, by position.

    By position rather than by path, because the same file may legitimately be
    listed twice and dragging one of them must not move the other.
    """
    entries, complete = playlist_contents(path)
    if not complete:
        return False
    if not 0 <= source_index < len(entries) or not 0 <= target_index < len(entries):
        return False
    moved = entries.pop(source_index)
    entries.insert(target_index, moved)
    return write_playlist_entries(path, entries)
