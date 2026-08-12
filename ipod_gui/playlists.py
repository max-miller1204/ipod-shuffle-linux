"""Playlists you make here, kept as M3U files in a folder of your own.

A playlist used to exist only on the device, which meant making one needed an
iPod attached, a list exported from some other program, and a sync before any
of it could be seen. Building a list is not a device operation, so it happens
here instead: one M3U per playlist under ~/Music/Playlists, holding absolute
paths to your own files, editable with no iPod in sight.

The file is the playlist. Nothing keeps an index beside it, so a list edited in
another program, moved, or deleted by hand cannot disagree with a record of
what it is supposed to hold - the same rule the preview cache follows.

A custom cover is the one thing kept beside it: an image copied into a .covers
folder under the same library and named after the whole playlist filename. It
belongs to this computer and never reaches the device, and the file is still
what says the playlist exists - an image found under a name whose playlist has
gone is cleared rather than worn by the next list to be called that.

The name is the filename, because that is what ipod-sync.sh turns into the
playlist's spoken name on the device. So the characters FAT refuses are refused
here, while the name is being typed, rather than silently mangled two steps
later by the sync and read out as something else.
"""

import os
import shutil
import tempfile
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


class _Gone:
    """A named answer for one particular way of not getting what was asked for.

    False rather than a truthy value of its own, because every other way to
    fail in this module is falsy - `move_entry` returns False for a stale
    source, and add_entries and remove_entry answer None. A caller writing the
    obvious `if move_entry(...)` reads one of these as the failure it is
    instead of toasting a reorder over a playlist it never rewrote.

    One each rather than one shared answer, because a playlist that is not
    there, a row that is not there and an image that cannot be stored are
    different sentences over different repaints. They are told apart by
    identity, the way None is, so a caller that only knows about one of them
    cannot mistake it for the other.
    """

    __slots__ = ("_name",)

    def __init__(self, name):
        self._name = name

    def __bool__(self):
        return False

    def __repr__(self):
        return self._name


# The playlist's own file has gone since the window last listed the folder.
# Not a failure and nothing to write: the file is the playlist, so another
# program deleting one is how it stops existing, and the window is simply
# painted from an older reading of the folder. Answered apart from a file that
# is there and could not be read, which is a folder to go and look at.
PLAYLIST_GONE = _Gone("PLAYLIST_GONE")


class Playlist:
    """One playlist, as its file currently reads.

    `path` is the M3U this app owns, or None for a playlist that exists only on
    the device: those are shown but not edited in place, because their entries
    name scrambled four-letter files on the iPod rather than files in your
    music folders. Such a list is copied here instead, `create_local_playlist`
    writing those entries down again as the files they were made from, after
    which it has a path like any other.
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


def _folder_reachable(path):
    """Whether this folder is still there to be looked in.

    stat rather than lstat, because what is wanted is the folder and not the
    entry naming it: a Music folder symlinked onto a drive that has been
    unplugged is an entry that is perfectly present and names nothing, and
    nothing is what a playlist inside it would be read out of.

    Any error at all is a no, the same way only a definite no counts below.
    Both questions are asked for permission to make the larger claim, so both
    answer no when they cannot be sure.
    """
    try:
        os.stat(path)
    except OSError:
        return False
    return True


def _entry_missing(path):
    """Whether nothing at all sits at this path.

    The directory entry rather than what it names, so lstat rather than a
    read: a playlist symlinked onto a drive that is not plugged in raises
    FileNotFoundError exactly as a deleted one does, and it is still sitting
    in the folder. The error a read failed with cannot tell them apart; the
    entry can.

    Only a definite no counts. A folder that cannot be listed at all leaves
    this unanswered, and unanswered is not gone: taking a playlist off the
    rail claims more than saying it could not be read does, so the doubt goes
    to the smaller claim.

    Which is why the folder is asked as well, and not only the entry. An
    ENOENT names the whole path rather than the last name in it, so a Music
    folder that has gone - unplugged with the drive it was symlinked onto -
    answers "nothing is at that path" about every playlist the user owns,
    while all of them are sitting on the drive. The entry is missing only
    where there is a folder that could have held it.
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        return _folder_reachable(Path(path).parent)
    except OSError:
        return False
    return False


def playlist_contents(path):
    """The paths a playlist lists, and whether the file could be read at all.

    The pair, the way read_local_playlist_tracks answers, because "nothing is
    in it" and "nothing could be read out of it" are the same empty list and
    mean opposite things to an edit. Every edit here is a read and a rewrite -
    a playlist symlinked onto a drive that is not plugged in, or a file that
    has gone since the folder was last listed, reads as empty while the folder
    it sits in is still perfectly writable, and a rewrite from that read would
    replace the whole list with whatever the edit added.

    Those last two are then told apart from each other, because they send the
    reader to two different places: a playlist that is not there any more is a
    repaint, and one that is there and could not be read is a folder to go and
    look at. So the second answer is True, PLAYLIST_GONE or False, and the
    three edits below hand that difference on to the window rather than each
    deciding it again from an error they no longer have.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], PLAYLIST_GONE if _entry_missing(path) else False
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


def playlist_names_here(root):
    """Every name the folder has already spent, whatever it spent it on.

    local_playlists answers with the playlists that can be shown, which is a
    narrower question: it drops an entry that is not a regular file, because
    there is nothing to read out of a directory or a socket. A name is gone
    either way - `local_playlist_file` would land on that entry and the write
    would fail - so choosing a name asks the folder for its names rather than
    for its playlists, and picking one and refusing one agree about what is
    there. Reading no playlist's contents to collect names is the point as
    much as the answer is: this runs on the main thread.
    """
    try:
        entries = list(Path(root).iterdir())
    except OSError:
        return []
    return [
        entry.stem for entry in entries if entry.suffix.lower() == PLAYLIST_SUFFIX
    ]


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


def create_local_playlist(root, name, entries=()):
    """Make a playlist and return its file, or None if it cannot.

    Empty is what naming one gives, and holding something already is what
    copying a list off the device gives. One writer for both, because the two
    differ in nothing else: a name that is taken is refused either way, rather
    than overwriting a playlist that is sitting there.

    It starts with no custom cover even when the store holds one under this
    name. That image belonged to a playlist that has gone - deleted by hand
    while the app was not running is all it takes - and reusing a name does
    not make this the same list.
    """
    path = local_playlist_file(root, name)
    if path.exists():
        return None
    if not write_playlist_entries(path, entries):
        return None
    remove_playlist_cover(path)
    return path


PLAYLIST_COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
PLAYLIST_COVERS_FOLDER = ".covers"


# The image a cover was to be copied from has gone since it was chosen, and an
# image in a format the store cannot hold. Told apart because only the second
# is answered by choosing a different kind of file, and neither is the folder
# refusing the write, which is a folder to go and look at.
IMAGE_GONE = _Gone("IMAGE_GONE")
IMAGE_UNSUPPORTED = _Gone("IMAGE_UNSUPPORTED")


def _cover_slots(playlist):
    """Every filename a cover for this playlist file could be stored under.

    A cover is named after the complete playlist filename, so ``Mix.m3u`` owns
    ``.covers/Mix.m3u.png`` and the three other suffixes and nothing else. The
    whole name rather than a prefix of it, because a prefix is not exclusive:
    "Mix.m3u.old" is a name a user may type, and the cover it owns is
    ``Mix.m3u.old.m3u.png``, which begins with everything "Mix" would match on.
    """
    folder = playlist.parent / PLAYLIST_COVERS_FOLDER
    return [folder / f"{playlist.name}{suffix}" for suffix in PLAYLIST_COVER_SUFFIXES]


def playlist_custom_cover(path):
    """The image chosen for this local playlist, or None.

    Covers live beside the playlist library, not in the M3U and not among the
    tracks handed to the sync script. Naming them after the complete playlist
    filename keeps a cover for ``Mix.m3u`` apart from an unrelated ``Mix.jpg``.

    Four names asked about rather than the folder listed, because the rail
    asks this for every playlist on it every time it repaints.
    """
    for cover in _cover_slots(Path(path)):
        try:
            if cover.is_file():
                return cover
        except OSError:
            return None
    return None


def remove_playlist_cover(path):
    """Remove a playlist's custom image, leaving song artwork as the fallback.

    Every name a cover of this playlist could have rather than only the one
    being shown, so a store holding two formats of it is left holding neither.
    Also what keeps a name safe to reuse: a cover outliving the playlist it was
    named after would be worn by the next list to be called the same thing.
    """
    playlist = Path(path)
    removed = True
    for cover in _cover_slots(playlist):
        try:
            cover.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            removed = False
    try:
        (playlist.parent / PLAYLIST_COVERS_FOLDER).rmdir()
    except OSError:
        pass
    return removed


def set_playlist_cover(path, image):
    """Copy an image into the local cover store and return its new path.

    The four ways this fails are four different sentences, and only one of
    them is about the file that was chosen: PLAYLIST_GONE for a playlist that
    is no longer there, IMAGE_UNSUPPORTED for a file the store cannot hold,
    IMAGE_GONE for an image that went between being chosen and being copied,
    and None for a folder that refused the write. Told apart here rather than
    collapsed into one, because a read-only playlist folder answered as "pick
    another image" leaves the user choosing files against a refusal that
    cannot change.

    Copied through a temporary file in the destination folder and renamed over
    what was there, so a copy that fails halfway leaves the playlist wearing
    the cover it had rather than half an image.
    """
    playlist = Path(path)
    source = Path(image)
    suffix = source.suffix.lower()
    if not playlist.is_file():
        return PLAYLIST_GONE
    if suffix not in PLAYLIST_COVER_SUFFIXES:
        return IMAGE_UNSUPPORTED
    if not source.is_file():
        return IMAGE_GONE

    folder = playlist.parent / PLAYLIST_COVERS_FOLDER
    destination = folder / f"{playlist.name}{suffix}"
    if source == destination:
        return destination
    staging = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        descriptor, staging = tempfile.mkstemp(dir=folder, suffix=".part")
        os.close(descriptor)
        shutil.copyfile(source, staging)
        os.replace(staging, destination)
    except OSError:
        if staging is not None:
            try:
                Path(staging).unlink()
            except OSError:
                pass
        return None

    # A playlist has one cover, so a cover it had in another format is not a
    # second one to fall back to: it is the image this one replaces.
    for other in _cover_slots(playlist):
        if other != destination:
            try:
                other.unlink()
            except OSError:
                pass
    return destination


def delete_local_playlist(path):
    """Delete a playlist and its computer-only custom cover.

    True once the playlist is gone, which includes finding it already gone: a
    list deleted in another program is deleted, and this window is painted
    from an older reading of the folder. The cover is cleared on that path
    too, or the deletion would leave behind an image with nothing to belong
    to for the next playlist of that name to inherit.
    """
    playlist = Path(path)
    try:
        playlist.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    remove_playlist_cover(playlist)
    return True


def rename_local_playlist(path, new_name):
    """Move a playlist and its computer-only custom cover to a new name.

    The cover moves first and is moved back if the playlist itself will not
    move, so a rename that fails leaves both files where they were. Whatever
    the new name's cover slots held is cleared either way: the name was free,
    so anything stored under it belonged to a playlist that has gone, and a
    renamed list must not arrive wearing it.

    Landing on the name it already has moves nothing and clears nothing - the
    slot the cover is in is its own. It is still answered against the folder
    rather than waved through, because a playlist that has gone has not been
    renamed to anything, its own name included.
    """
    source = Path(path)
    destination = local_playlist_file(source.parent, new_name)
    if destination == source:
        return destination if source.is_file() else None
    if destination.exists():
        return None
    cover = playlist_custom_cover(source)
    renamed_cover = None
    remove_playlist_cover(destination)
    try:
        if cover is not None:
            renamed_cover = cover.with_name(f"{destination.name}{cover.suffix}")
            os.replace(cover, renamed_cover)
        os.replace(source, destination)
    except OSError:
        if cover is not None and renamed_cover is not None:
            try:
                os.replace(renamed_cover, cover)
            except OSError:
                pass
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

    The name it lands on is resolved against the folder and not only against
    the names the caller knew about. The caller's list is stale by
    construction: a file dialog stands open for as long as the user browses,
    and a playlist another program wrote in the meantime would be swallowed by
    this write. Unlike create_local_playlist and rename_local_playlist, which
    refuse, the name here is one this function chose rather than one the user
    typed: refusing it would leave them pressing Import against an answer that
    cannot change, because nothing in the asking picks the name. So a taken
    name moves to the next free number instead. The write is still refused
    outright if the file appears between that listing and the write, because a
    playlist that is already there is never overwritten.

    It arrives with no cover for the reason create_local_playlist does: the
    name it lands on is free, so anything the store holds under it belonged to
    a playlist that has gone.
    """
    source = Path(source)
    tracks, complete = read_local_playlist_tracks(source)
    if not complete:
        return None, 0, "That playlist file could not be read"
    if not tracks:
        return None, 0, "Nothing in that playlist could be found on this computer"
    here = playlist_names_here(root)
    path = local_playlist_file(root, unique_name(source.stem, [*taken, *here]))
    if path.exists():
        return None, 0, f"There is already a playlist called {path.stem}"
    if not write_playlist_entries(path, tracks):
        return None, 0, f"Could not write into {path.parent}"
    remove_playlist_cover(path)
    return path, len(tracks), None


def _edit_refusal(complete):
    """What an edit answers when the read it starts with produced no list.

    PLAYLIST_GONE passes straight through, because a playlist that is not
    there is a repaint and a sentence of its own. Anything else is None, the
    same answer a failed write gives, because both send the reader to the
    folder. Written once rather than at each of the three edits below, which
    all begin by reading the list they are about to rewrite and all owe their
    caller the same difference.
    """
    return PLAYLIST_GONE if complete is PLAYLIST_GONE else None


def add_entries(path, entries):
    """Append tracks to a playlist, skipping ones it already lists.

    Returns how many were added, PLAYLIST_GONE if the playlist itself is no
    longer there, or None if it could not be read or written. A playlist is an
    ordered list rather than a set, but the same track landing in one twice is
    always a mis-click here: the only way to ask for it is to press Add twice,
    which is what a double click on one button is.
    """
    current, complete = playlist_contents(path)
    if not complete:
        return _edit_refusal(complete)
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

    Returns how many lines went, PLAYLIST_GONE if the playlist itself is no
    longer there, or None if it could not be read or written, the way
    add_entries counts what it added. The ways nothing changes are not one
    thing: a playlist that no longer lists the track has already been edited
    somewhere else, and one that is no longer there at all has been deleted
    somewhere else, while a playlist that could not be rewritten is a folder
    to go and look at - and a caller told only "False" reports whichever of
    those it guessed at.
    """
    current, complete = playlist_contents(path)
    if not complete:
        return _edit_refusal(complete)
    remaining = [line for line in current if line != str(entry)]
    removed = len(current) - len(remaining)
    if not removed:
        return 0
    return removed if write_playlist_entries(path, remaining) else None


# What move_entry answers when the row dropped on is the stale one. A drag
# names two rows, and only one of them is the track: when the other one is the
# row the file has lost, the dragged track is still listed, so "that track is
# no longer here" would be a sentence about a row that never went anywhere. It
# needs an answer of its own.
TARGET_GONE = _Gone("TARGET_GONE")


def move_entry(path, source_index, target_index):
    """Reorder one track within a playlist, by position.

    By position rather than by path, because the same file may legitimately be
    listed twice and dragging one of them must not move the other.

    Returns True when the new order was written, False when the playlist no
    longer has the row being dragged, TARGET_GONE when it no longer has the
    row that was dropped on, PLAYLIST_GONE when the playlist itself is no
    longer there, or None if it could not be read or written - remove_entry's
    answers, with the vanished row answered as two because a drag reads two of
    them off the same painted list. A row the file has lost since the window
    painted it is a repaint, while a list that could not be rewritten is a
    folder to go and look at, and a caller told only "False" reports whichever
    of those it guessed at.
    """
    entries, complete = playlist_contents(path)
    if not complete:
        return _edit_refusal(complete)
    if not 0 <= source_index < len(entries):
        return False
    if not 0 <= target_index < len(entries):
        return TARGET_GONE
    moved = entries.pop(source_index)
    entries.insert(target_index, moved)
    return True if write_playlist_entries(path, entries) else None
