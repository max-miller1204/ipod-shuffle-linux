#!/usr/bin/env python3
"""Checks making, editing and syncing a playlist made in the app.

A playlist is now a file this app writes rather than one the user had to bring
with them, so every edit is a rewrite of that file - and the file is what
ipod-sync.sh is handed. Two things therefore have to hold, and neither is
visible from a screenshot: the file says exactly what the window shows, and
what an edit stages for the next sync is that same file and the tracks it
lists. Both are asserted here.

No window is created, because that would need a display. The methods are
called unbound against a stand-in that records what would have been run, which
is the approach the other GUI checks use.
"""

import sys
import tempfile
import threading
from pathlib import Path

from harness import gui


PLAYLISTS = Path(tempfile.mkdtemp(prefix="playlists-"))
MUSIC = Path(tempfile.mkdtemp(prefix="music-"))
gui.PLAYLIST_LIBRARY = PLAYLISTS


def song(name, artist="Artist"):
    path = MUSIC / artist / f"{name}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(name.encode("utf-8"))
    return path


def track_for(path, state=None):
    return gui.Track(
        path,
        {"title": Path(path).stem, "artist": Path(path).parent.name, "size": 8},
        state or gui.STATE_LIBRARY,
    )


# ------------------------------------------------------------- the store
#
# Everything below the window: what a name may be, what a file holds after an
# edit, and what happens to a list another program wrote.

assert gui.default_name([]) == "Playlist 1"
assert gui.default_name(["Playlist 1", "Playlist 3"]) == "Playlist 2"
# Case-insensitively, because the name becomes a filename on a FAT volume and
# offering "playlist 1" beside "Playlist 1" offers a name the sync refuses.
assert gui.default_name(["playlist 1"]) == "Playlist 2"

assert gui.name_problem("Gym") is None
assert "Enter a name" in gui.name_problem("   ")
assert "cannot contain" in gui.name_problem("Road/Trip")
assert "cannot contain" in gui.name_problem('Say "hi"')
assert "cannot end with a dot" in gui.name_problem("Mix.")
assert "already a playlist" in gui.name_problem("gym", ["Gym"])
# A name FAT accepts but that reads oddly is still the user's to choose.
assert gui.name_problem("Gym & Co. 2026 - part 1") is None

assert gui.unique_name("Gym", []) == "Gym"
assert gui.unique_name("Gym", ["Gym"]) == "Gym 2"
assert gui.unique_name("Gym", ["Gym", "gym 2"]) == "Gym 3"
assert gui.unique_name("Road/Trip", []) == "Road_Trip"
assert gui.unique_name("   ", []) == "Playlist"

assert gui.sanitise_name("Mix: 2026?") == "Mix_ 2026_"
assert gui.sanitise_name("trailing.  ") == "trailing"

created = gui.create_local_playlist(PLAYLISTS, "Gym")
assert created == PLAYLISTS / "Gym.m3u", created
assert created.is_file()
# Created empty rather than left as an idea: the file is the playlist, so a
# playlist that has been named exists on disk before anything is added to it.
assert gui.read_playlist_entries(created) == []
assert gui.create_local_playlist(PLAYLISTS, "Gym") is None, "a name was reused"

first, second = song("Lithium"), song("Debaser", "Pixies")
assert gui.add_entries(created, [first, second]) == 2
assert gui.read_playlist_entries(created) == [str(first), str(second)]
# The header every player expects, written by us rather than assumed.
assert created.read_text(encoding="utf-8").startswith("#EXTM3U\n")
assert gui.add_entries(created, [first]) == 0, "a track was listed twice"

assert gui.move_entry(created, 1, 0)
assert gui.read_playlist_entries(created) == [str(second), str(first)]
assert not gui.move_entry(created, 5, 0), "a move past the end was accepted"

# The two ways a removal changes nothing are two different things to go and
# look at, so a count answers them apart the way add_entries does: a playlist
# that no longer lists the track has been edited somewhere else, while one
# that could not be rewritten is a folder to check the permissions on.
# write_playlist_entries writes a scratch file beside the playlist and renames
# it into place, so a directory sitting where that scratch file goes refuses
# the write at exactly the point a full or read-only disk does.
assert gui.remove_entry(created, second) == 1
assert gui.read_playlist_entries(created) == [str(first)]
assert gui.remove_entry(created, second) == 0, "removing nothing reported a change"
blocked_scratch = created.with_name(f".{created.name}.tmp")
blocked_scratch.mkdir()
try:
    assert gui.remove_entry(created, first) is None, (
        "a playlist that could not be rewritten reported nothing to remove"
    )
    assert gui.add_entries(created, [second]) is None
finally:
    blocked_scratch.rmdir()
assert gui.read_playlist_entries(created) == [str(first)], (
    "a refused write left the playlist half rewritten"
)

# A playlist that cannot be read is not a playlist with nothing in it. Every
# edit is a read and a rewrite, and the folder stays writable when the file
# itself is not readable - a list symlinked onto a drive that is not plugged
# in reads as empty, and os.replace would happily put the symlink's place
# under a one-line file. So an edit that could not read refuses to write.
unplugged_list = PLAYLISTS / "On The Drive.m3u"
unplugged_list.symlink_to("/nowhere/mounted/On The Drive.m3u")
assert gui.read_playlist_entries(unplugged_list) == []
assert gui.add_entries(unplugged_list, [str(first)]) is None, (
    "an unreadable playlist was rewritten from a read that did not happen"
)
assert gui.remove_entry(unplugged_list, first) is None
assert not gui.move_entry(unplugged_list, 1, 0)
assert unplugged_list.is_symlink(), "the edit replaced the playlist"
assert not unplugged_list.exists(), "the edit wrote where the drive should be"
unplugged_list.unlink()
# The same for one that has gone since the folder was listed: adding a track
# to a playlist somebody deleted is not a reason to write it back.
assert gui.add_entries(PLAYLISTS / "Never Made.m3u", [str(first)]) is None
assert not (PLAYLISTS / "Never Made.m3u").exists(), "an edit revived a playlist"
# A playlist whose entries are readable but whose files have gone is a
# different thing again, and still perfectly editable.
assert gui.playlist_contents(created) == ([str(first)], True)

renamed = gui.rename_local_playlist(PLAYLISTS / "Gym.m3u", "Gym Two")
assert renamed == PLAYLISTS / "Gym Two.m3u", renamed
assert not (PLAYLISTS / "Gym.m3u").exists()
gui.create_local_playlist(PLAYLISTS, "Blocker")
assert gui.rename_local_playlist(PLAYLISTS / "Gym Two.m3u", "Blocker") is None, (
    "a rename swallowed an existing playlist"
)
assert gui.delete_local_playlist(PLAYLISTS / "Blocker.m3u")
assert gui.delete_local_playlist(PLAYLISTS / "Never existed.m3u"), (
    "deleting a playlist that is already gone was reported as a failure"
)

# A file another program dropped in the folder is adopted whatever case it
# spelled its suffix in, so both edits act on the file that is there rather
# than on one rebuilt from the name it is listed under: rebuilding it would
# report deleting a playlist still sitting in the folder, and refuse to rename
# one that is plainly there.
shouted = PLAYLISTS / "Shouted.M3U"
gui.write_playlist_entries(shouted, [str(first)])
shout = {p.name: p for p in gui.local_playlists(PLAYLISTS)}["Shouted"]
quietened = gui.rename_local_playlist(shout.path, "Quiet")
assert quietened == PLAYLISTS / "Quiet.m3u", quietened
assert not shouted.exists(), "the rename left the old file behind"
assert gui.delete_local_playlist(quietened)
assert not quietened.exists(), "a delete reported success and removed nothing"

# An entry whose file has gone is kept rather than quietly dropped by the next
# edit: an unplugged drive must not empty a playlist that mentions it.
survivor = PLAYLISTS / "Survivor.m3u"
gui.write_playlist_entries(survivor, [str(first), "/elsewhere/unplugged.mp3"])
gui.add_entries(survivor, [second])
assert gui.read_playlist_entries(survivor) == [
    str(first),
    "/elsewhere/unplugged.mp3",
    str(second),
], gui.read_playlist_entries(survivor)
assert gui.delete_local_playlist(survivor)

# Importing resolves what another program wrote against wherever it wrote it,
# because those paths stop meaning anything once the file has been copied.
foreign_dir = Path(tempfile.mkdtemp(prefix="exported-"))
foreign_track = foreign_dir / "Exported.mp3"
foreign_track.write_bytes(b"exported")
foreign = foreign_dir / "Road Trip.m3u"
foreign.write_text(
    "#EXTM3U\n"
    "#EXTINF:123,Exported\n"
    "Exported.mp3\n"
    f"file://{first}\n"
    "/gone/missing.mp3\n"
    "https://example.invalid/stream.mp3\n",
    encoding="utf-8",
)
imported, kept, trouble = gui.import_playlist_file(PLAYLISTS, foreign)
assert imported == PLAYLISTS / "Road Trip.m3u", imported
assert kept == 2, kept
assert trouble is None, trouble
assert gui.read_playlist_entries(imported) == [str(foreign_track), str(first)], (
    gui.read_playlist_entries(imported)
)
# A second import of the same file is a second playlist rather than a silent
# overwrite of the one already there.
again, _kept, _trouble = gui.import_playlist_file(
    PLAYLISTS, foreign, [p.name for p in gui.local_playlists(PLAYLISTS)]
)
assert again == PLAYLISTS / "Road Trip 2.m3u", again
empty_export = foreign_dir / "Nothing Here.m3u"
empty_export.write_text("/gone/one.mp3\n/gone/two.mp3\n", encoding="utf-8")
assert gui.import_playlist_file(PLAYLISTS, empty_export) == (
    None,
    0,
    "Nothing in that playlist could be found on this computer",
), gui.import_playlist_file(PLAYLISTS, empty_export)
assert not (PLAYLISTS / "Nothing Here.m3u").exists(), (
    "an import that found nothing still left a playlist behind"
)
# The three ways an import fails are three different things to go and fix, so
# each says which one happened rather than all of them blaming the library.
_none, _zero, unreadable = gui.import_playlist_file(
    PLAYLISTS, foreign_dir / "Not There.m3u"
)
assert "could not be read" in unreadable, unreadable
blocked = foreign_dir / "not-a-folder"
blocked.write_bytes(b"in the way")
_none, _zero, unwritable = gui.import_playlist_file(blocked, foreign)
assert "Could not write into" in unwritable, unwritable
for playlist in gui.local_playlists(PLAYLISTS):
    gui.delete_local_playlist(playlist.path)

# The two halves of the Playlists view are matched by name, the way FAT
# matches them: a local "Gym" and a device "gym" are one playlist.
gui.create_local_playlist(PLAYLISTS, "Gym")
gui.create_local_playlist(PLAYLISTS, "aardvark")
listed = gui.local_playlists(PLAYLISTS)
assert [p.name for p in listed] == ["aardvark", "Gym"], [p.name for p in listed]
merged = gui.merge_with_device(listed, [("gym", ["F00/AAAA.mp3"]), ("Genres", [])])
assert [p.name for p in merged] == ["aardvark", "Gym", "Genres"], merged
assert merged[1].editable, "a local playlist was not editable"
assert not merged[2].editable, "a device playlist was offered for editing"
for playlist in listed:
    gui.delete_local_playlist(playlist.path)


# -------------------------------------------------------------- the window


class FakeSwitch:
    def __init__(self):
        self.active = False

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active


class Entry:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text


class FakeLibrary:
    def __init__(self):
        self.tracks = []
        self.device_only = []
        self.previews = []

    all_tracks = gui.LibraryIndex.all_tracks


class FakeWindow:
    """The parts of the window an edit to a playlist touches."""

    def __init__(self, mount_point="/media/alex/iPod", speech=True):
        self.mount_point = mount_point
        self.device_identity = "uuid:test-ipod" if mount_point else None
        self.busy = False
        self.discovering_sources = False
        self.source_generation = 0
        self.speech_engine_available = speech
        self.playlist_unavailable = None if speech else "No speech engine installed"
        self.playlist_voiceover = FakeSwitch()
        self.local_playlists = []
        self.playlists = []
        self.spoken = set()
        self.current_playlist = None
        self.library = FakeLibrary()
        self.device_tracks = []
        self.pending = set()
        self.pending_sources = {}
        self.pending_records = {}
        self._pending_track_index = {}
        self._library_by_path = {}
        self.commands = []
        self.toasts = []
        self.downloads = []
        self.repaints = 0
        self._load_local_playlists()

    # Recorded rather than run: what a playlist edit asks the device to do is
    # the argument vector, and running it would need an iPod.
    def _run(self, argv, busy_message, done_message, **_kwargs):
        self.commands.append(argv)
        return True

    def _toast(self, message):
        self.toasts.append(message)

    # What a YouTube result asks for is a download that lands in a playlist,
    # so what is recorded is the request: running it would need the network.
    def _start_youtube_download(self, url, **kwargs):
        self.downloads.append((url, kwargs))

    def _populate_playlist_rail(self):
        self.repaints += 1

    def _populate_device_summary(self):
        pass

    def _refresh_current_view(self):
        pass

    def _update_device_controls(self):
        pass

    def _select_playlist(self, name):
        self.current_playlist = name

    def _show_playlist(self, _name):
        pass

    def _keep_preview(self, track):
        # The real one moves the file; here it only has to answer the question
        # the playlist add asks, which is whether the file is safe to list.
        track.state = gui.STATE_LIBRARY
        return True

    def library_tracks(self, paths):
        self.library.tracks = [track_for(path) for path in paths]
        self._merge_states()

    # The real implementations, which are the subject.
    _load_local_playlists = gui.IpodWindow._load_local_playlists
    _shown_playlists = gui.IpodWindow._shown_playlists
    _local_playlist = gui.IpodWindow._local_playlist
    _playlist_on_device = gui.IpodWindow._playlist_on_device
    _playlist_state = gui.IpodWindow._playlist_state
    _playlist_tracks = gui.IpodWindow._playlist_tracks
    _device_only_track = gui.IpodWindow._device_only_track
    _add_tracks_to_playlist = gui.IpodWindow._add_tracks_to_playlist
    _remove_track_from_playlist = gui.IpodWindow._remove_track_from_playlist
    _move_track_between = gui.IpodWindow._move_track_between
    _add_result_to_playlist = gui.IpodWindow._add_result_to_playlist
    _after_playlist_change = gui.IpodWindow._after_playlist_change
    _stage_playlist = gui.IpodWindow._stage_playlist
    _stage_playlists = gui.IpodWindow._stage_playlists
    _send_playlist_to_ipod = gui.IpodWindow._send_playlist_to_ipod
    _add_download_to_playlist = gui.IpodWindow._add_download_to_playlist
    _on_new_playlist_response = gui.IpodWindow._on_new_playlist_response
    _on_rename_response = gui.IpodWindow._on_rename_response
    _on_playlist_remove_response = gui.IpodWindow._on_playlist_remove_response
    _remove_device_playlist = gui.IpodWindow._remove_device_playlist
    _import_playlist = gui.IpodWindow._import_playlist
    _reorder_playlist = gui.IpodWindow._reorder_playlist
    _confirmed_device = gui.IpodWindow._confirmed_device
    is_queued = gui.IpodWindow.is_queued
    unqueue_source = gui.IpodWindow.unqueue_source
    _prune_pending = gui.IpodWindow._prune_pending
    _queue_playlists = gui.IpodWindow._queue_playlists
    _scan_queued_sources = gui.IpodWindow._scan_queued_sources
    _queue_sources = gui.IpodWindow._queue_sources
    _commit_queue_sources = gui.IpodWindow._commit_queue_sources
    _scan_pending_tracks = gui.IpodWindow._scan_pending_tracks
    _finish_pending_enrichment = gui.IpodWindow._finish_pending_enrichment
    _merge_states = gui.IpodWindow._merge_states
    _pending_track = gui.IpodWindow._pending_track
    _pending_accounting = gui.IpodWindow._pending_accounting
    _pending_change_count = gui.IpodWindow._pending_change_count
    _record_for_track = staticmethod(gui.IpodWindow._record_for_track)


original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: "uuid:test-ipod"


def new_playlist(window, name, then=None):
    window._on_new_playlist_response(None, "create", Entry(name), then)


# Making one is a name and nothing else: no file to choose, no iPod to wait
# for, and the playlist exists the moment the dialog closes.
window = FakeWindow()
window.library_tracks([first, second])
new_playlist(window, "Gym")
assert (PLAYLISTS / "Gym.m3u").is_file(), "creating a playlist wrote no file"
assert [p.name for p in window.local_playlists] == ["Gym"]
assert window.current_playlist == "Gym", window.current_playlist
assert window.toasts == ["Gym created"], window.toasts
# Nothing is staged for an empty playlist: there is nothing to copy and no
# copy on the device to rewrite.
assert window.pending_sources == {}, window.pending_sources

# Anything but Create writes nothing at all.
quiet = FakeWindow()
quiet._on_new_playlist_response(None, "cancel", Entry("Ignored"), None)
assert not (PLAYLISTS / "Ignored.m3u").exists(), "cancelling created a playlist"
# So does a name the dialog would have refused, in case it is reached anyway.
quiet._on_new_playlist_response(None, "create", Entry("bad/name"), None)
assert not list(PLAYLISTS.glob("bad*")), "a refused name was created"

# Adding a track appends it to the file and stages the playlist with the
# tracks it lists, which is exactly what the sync is later handed.
window._add_tracks_to_playlist("Gym", [track_for(first)])
assert gui.read_playlist_entries(PLAYLISTS / "Gym.m3u") == [str(first)]
assert window.pending_sources == {
    str(PLAYLISTS / "Gym.m3u"): {str(PLAYLISTS / "Gym.m3u"), str(first)}
}, window.pending_sources
assert window.toasts[-1] == "1 track added to Gym · queued for sync", window.toasts
# A playlist implies wanting its name read aloud, since a screenless device
# has no other way to tell one from another.
assert window.playlist_voiceover.active, "adding to a playlist left voiceover off"

window._add_tracks_to_playlist("Gym", [track_for(first)])
assert window.toasts[-1] == "Already in Gym", window.toasts
assert gui.read_playlist_entries(PLAYLISTS / "Gym.m3u") == [str(first)]

# A previewed file lives in a cache that gets pruned, so it is kept first and
# the entry names where it landed rather than where it was heard from.
previewed = track_for(second, gui.STATE_PREVIEW)
window._add_tracks_to_playlist("Gym", [previewed])
assert previewed.state == gui.STATE_LIBRARY, "a preview was listed from the cache"
assert gui.read_playlist_entries(PLAYLISTS / "Gym.m3u") == [str(first), str(second)]

# Creating from a track's own ⋯ menu makes the playlist and puts the track in
# it, which is the shortest path from a song to a new playlist.
new_playlist(
    window,
    "From Menu",
    then=lambda name: window._add_tracks_to_playlist(name, [track_for(second)]),
)
assert gui.read_playlist_entries(PLAYLISTS / "From Menu.m3u") == [str(second)]
assert window.current_playlist == "From Menu", window.current_playlist

# Moving lands the track in the target before it leaves the source, so a
# failed write cannot lose it from both.
window._move_track_between("Gym", "From Menu", track_for(first))
assert gui.read_playlist_entries(PLAYLISTS / "From Menu.m3u") == [
    str(second),
    str(first),
]
assert gui.read_playlist_entries(PLAYLISTS / "Gym.m3u") == [str(second)]
assert window.toasts[-1].startswith("Moved to From Menu"), window.toasts
# Both ends are re-staged, or the source would sync with the track still in
# it: what is queued is a set of members per source, so the source having been
# queued before the move is not the same as it having been queued after.
assert set(window.pending_sources) == {
    str(PLAYLISTS / "Gym.m3u"),
    str(PLAYLISTS / "From Menu.m3u"),
}, window.pending_sources
assert str(first) not in window.pending_sources[str(PLAYLISTS / "Gym.m3u")], (
    window.pending_sources
)
assert str(first) in window.pending_sources[str(PLAYLISTS / "From Menu.m3u")]

# Removing takes the entry out and leaves the track exactly where it was.
window._remove_track_from_playlist("From Menu", track_for(first))
assert gui.read_playlist_entries(PLAYLISTS / "From Menu.m3u") == [str(second)]
assert first.is_file(), "removing from a playlist deleted the track"
assert window.toasts[-1].startswith("Removed from From Menu"), window.toasts

# A move that lands the track but cannot rewrite the source is a copy, and is
# reported as one: the track is in both lists now, and a toast reading "Moved"
# would leave the next sync to put it on the device twice with nothing said.
# The scratch file the rewrite renames into place is a directory here, which
# is how a read-only or full disk refuses at that same point.
half_moved = FakeWindow()
half_moved.library_tracks([first, second])
new_playlist(half_moved, "Stuck")
new_playlist(half_moved, "Landed")
gui.write_playlist_entries(PLAYLISTS / "Stuck.m3u", [str(first)])
half_moved._load_local_playlists()
scratch = PLAYLISTS / ".Stuck.m3u.tmp"
scratch.mkdir()
try:
    half_moved._move_track_between("Stuck", "Landed", track_for(first))
finally:
    scratch.rmdir()
assert gui.read_playlist_entries(PLAYLISTS / "Landed.m3u") == [str(first)]
assert gui.read_playlist_entries(PLAYLISTS / "Stuck.m3u") == [str(first)], (
    "the source was rewritten after all"
)
assert half_moved.toasts[-1].startswith(
    "Copied to Landed, but could not remove it from Stuck"
), half_moved.toasts
# Both ends are still staged: the source keeps the track, and a queue that
# skipped it would sync a playlist that disagrees with its own file.
assert set(half_moved.pending_sources) == {
    str(PLAYLISTS / "Stuck.m3u"),
    str(PLAYLISTS / "Landed.m3u"),
}, half_moved.pending_sources
for leftover in ("Stuck", "Landed"):
    gui.delete_local_playlist(PLAYLISTS / f"{leftover}.m3u")

# Removing a track the file no longer lists is not a failed write: the row was
# painted before something else edited the playlist, so the window re-reads
# and says so rather than sending the user to check the permissions on a file
# that was never the trouble.
stale = FakeWindow()
stale.library_tracks([first, second])
new_playlist(stale, "Stale")
gui.write_playlist_entries(PLAYLISTS / "Stale.m3u", [str(first)])
stale._load_local_playlists()
painted = stale.repaints
stale._remove_track_from_playlist("Stale", track_for(second))
assert stale.toasts[-1] == "That track is no longer in Stale", stale.toasts
assert gui.read_playlist_entries(PLAYLISTS / "Stale.m3u") == [str(first)]
assert stale.repaints > painted, "the window kept showing a row the file had lost"

# One that genuinely could not be rewritten still says exactly that.
scratch = PLAYLISTS / ".Stale.m3u.tmp"
scratch.mkdir()
try:
    stale._remove_track_from_playlist("Stale", track_for(first))
finally:
    scratch.rmdir()
assert stale.toasts[-1] == "Could not write Stale", stale.toasts
assert gui.read_playlist_entries(PLAYLISTS / "Stale.m3u") == [str(first)]
gui.delete_local_playlist(PLAYLISTS / "Stale.m3u")

# Reordering rewrites the file, because the order is the playlist and it has
# to survive the window closing.
window.current_playlist = "From Menu"
window._add_tracks_to_playlist("From Menu", [track_for(first)])
assert window._reorder_playlist(1, 0)
assert gui.read_playlist_entries(PLAYLISTS / "From Menu.m3u") == [
    str(first),
    str(second),
]
# A device-only playlist has no local file to hold the order, so that one
# still goes straight to the device and rebuilds; nothing here should have.
assert window.commands == [], window.commands

# An emptied playlist that is on the device stays staged: the sync is what
# removes the device's copy, so a queue holding nothing would leave it there.
window.playlists = [("From Menu", ["F00/AAAA.mp3"])]
window._remove_track_from_playlist("From Menu", track_for(first))
window._remove_track_from_playlist("From Menu", track_for(second))
assert gui.read_playlist_entries(PLAYLISTS / "From Menu.m3u") == []
assert str(PLAYLISTS / "From Menu.m3u") in window.pending_sources
# One that was never on the device has nothing to say to it.
window.playlists = []
window._remove_track_from_playlist("Gym", track_for(second))
assert str(PLAYLISTS / "Gym.m3u") not in window.pending_sources, (
    window.pending_sources
)

# With no iPod attached an edit is still an edit: the list is kept here either
# way, and syncing it is a separate act.
detached = FakeWindow(mount_point=None)
new_playlist(detached, "Offline")
detached.library_tracks([first])
detached._add_tracks_to_playlist("Offline", [track_for(first)])
assert gui.read_playlist_entries(PLAYLISTS / "Offline.m3u") == [str(first)]
assert detached.pending_sources == {}, detached.pending_sources
assert detached.toasts[-1] == "1 track added to Offline", detached.toasts

# Without a speech engine the device could not announce the playlist, so it is
# not staged - and the edit says so rather than failing at sync time.
silent = FakeWindow(speech=False)
silent.library_tracks([first])
new_playlist(silent, "Silent")
silent._add_tracks_to_playlist("Silent", [track_for(first)])
assert silent.pending_sources == {}, silent.pending_sources
assert "no speech engine installed" in silent.toasts[-1], silent.toasts
assert not silent.playlist_voiceover.active, "voiceover flipped without an engine"

# A track that exists only on the iPod has no local file to list, and writing
# its device path into a playlist would ask the next sync to copy the device's
# own files back onto itself.
device_track = gui.Track(
    "/media/alex/iPod/iPod_Control/Music/F00/AAAA.mp3",
    {"title": "Only There"},
    gui.STATE_IPOD,
    relpath="F00/AAAA.mp3",
)
assert window._device_only_track(device_track), "a device file was offered"
assert not window._device_only_track(track_for(first))
assert not FakeWindow(mount_point=None)._device_only_track(device_track)

# A device playlist stores its entries relative to the iPod's music folder, so
# a row for one the device scan has not resolved carries that bare name and
# nothing else. It is no more addable than a resolved device track: written
# into a playlist here it would be resolved against the playlist folder, find
# nothing, and be dropped by the next sync without a word.
unresolved = gui.Track(
    "F00/CCCC.mp3", {"title": "CCCC"}, gui.STATE_IPOD, relpath="F00/CCCC.mp3"
)
assert window._device_only_track(unresolved), "a relative device entry was offered"
assert FakeWindow(mount_point=None)._device_only_track(unresolved), (
    "unplugging the iPod made a relative entry addable"
)

# The album page adds a whole record at once and a search result arrives from
# somewhere else again, so the guard is at the one door they all come through
# rather than beside the menu that happened to have it first.
album_window = FakeWindow()
album_window.library_tracks([first])
new_playlist(album_window, "Record")
album_window._add_tracks_to_playlist("Record", [track_for(first), device_track])
assert gui.read_playlist_entries(PLAYLISTS / "Record.m3u") == [str(first)], (
    gui.read_playlist_entries(PLAYLISTS / "Record.m3u")
)
album_window._add_tracks_to_playlist("Record", [device_track])
assert gui.read_playlist_entries(PLAYLISTS / "Record.m3u") == [str(first)]
assert "only on the iPod" in album_window.toasts[-1], album_window.toasts
gui.delete_local_playlist(PLAYLISTS / "Record.m3u")

# The rows a device playlist produces are what the ⋯ on one offers, so they go
# through the same door - both the entry the device scan resolved and the one
# it has not.
device_rows_window = FakeWindow()
device_rows_window.library_tracks([first])
new_playlist(device_rows_window, "Mine")
device_rows_window.playlists = [("Genres", ["F00/AAAA.mp3", "F00/CCCC.mp3"])]
device_rows_window.device_tracks = [device_track]
offered = device_rows_window._playlist_tracks(
    device_rows_window._shown_playlists()[-1]
)
assert [row.path for row in offered] == [device_track.path, "F00/CCCC.mp3"], offered
device_rows_window._add_tracks_to_playlist("Mine", offered)
assert gui.read_playlist_entries(PLAYLISTS / "Mine.m3u") == [], (
    gui.read_playlist_entries(PLAYLISTS / "Mine.m3u")
)
assert "only on the iPod" in device_rows_window.toasts[-1], (
    device_rows_window.toasts
)
gui.delete_local_playlist(PLAYLISTS / "Mine.m3u")

# An album holds what is only on the device beside what is here, so a refusal
# of part of it has to be said either way: a toast counting only what landed
# reports adding a record that arrived short.
partial = FakeWindow()
partial.library_tracks([first, second])
new_playlist(partial, "Mixed")
partial._add_tracks_to_playlist(
    "Mixed", [track_for(first), device_track, track_for(second)]
)
assert gui.read_playlist_entries(PLAYLISTS / "Mixed.m3u") == [
    str(first),
    str(second),
], gui.read_playlist_entries(PLAYLISTS / "Mixed.m3u")
assert partial.toasts[-1].startswith(
    "2 tracks added to Mixed · 1 track only on the iPod"
), partial.toasts
gui.delete_local_playlist(PLAYLISTS / "Mixed.m3u")

# Making a playlist from a track's ⋯ paints it whatever the menu then does
# with it: an album that is entirely on the iPod adds nothing at all, and a
# playlist the window has switched to but left out of its own rail is a
# playlist the user has been told twice does and does not exist.
refused = FakeWindow()
refused.library_tracks([first])
painted = refused.repaints
new_playlist(
    refused,
    "All On The iPod",
    then=lambda name: refused._add_tracks_to_playlist(name, [device_track]),
)
assert (PLAYLISTS / "All On The iPod.m3u").is_file(), "the playlist was not made"
assert "only on the iPod" in refused.toasts[-1], refused.toasts
assert refused.repaints > painted, "the rail never heard of the new playlist"
assert refused.current_playlist == "All On The iPod", refused.current_playlist
gui.delete_local_playlist(PLAYLISTS / "All On The iPod.m3u")

# Renaming moves the file and, because the name is what the device says out
# loud, removes the copy the iPod knows under the old one.
window.playlists = [("Gym", [])]
window.current_playlist = "Gym"
window._on_rename_response(None, "rename", "Gym", Entry("Gym Mix"))
assert (PLAYLISTS / "Gym Mix.m3u").is_file(), "the rename wrote no file"
assert not (PLAYLISTS / "Gym.m3u").exists(), "the old name was left behind"
assert window.current_playlist == "Gym Mix", window.current_playlist
rename_command = window.commands[-1]
assert rename_command[0].endswith("ipod-remove.sh"), rename_command
assert "--playlist" in rename_command, rename_command
assert rename_command[-2:] == ["--", "Gym"], rename_command
# What was staged named the old file, which no longer exists.
assert str(PLAYLISTS / "Gym.m3u") not in window.pending_sources

# Deleting a playlist that is only here needs no device at all.
window.playlists = []
window.commands = []
window._on_playlist_remove_response(None, "remove", "Gym Mix", "uuid:test-ipod")
assert not (PLAYLISTS / "Gym Mix.m3u").exists(), "the playlist file survived"
assert window.commands == [], "a local-only delete ran a device command"
assert window.toasts[-1] == "Gym Mix deleted", window.toasts

# One that is also on the device is deleted here and removed there.
new_playlist(window, "Both")
window.playlists = [("Both", ["F00/AAAA.mp3"])]
window._on_playlist_remove_response(None, "remove", "Both", "uuid:test-ipod")
assert not (PLAYLISTS / "Both.m3u").exists()
removal = window.commands[-1]
assert removal[0].endswith("ipod-remove.sh"), removal
assert removal[1:3] == ["--ipod", window.mount_point], removal
assert "--yes" in removal and "--playlist" in removal, removal
assert removal[-2:] == ["--", "Both"], removal

# Anything but the destructive response deletes nothing.
new_playlist(window, "Kept")
for answer in ("cancel", "close"):
    window._on_playlist_remove_response(None, answer, "Kept", "uuid:test-ipod")
assert (PLAYLISTS / "Kept.m3u").is_file(), "a cancelled delete removed the file"

# A device playlist with no local copy is still removed by the script alone.
window.commands = []
window.playlists = [("Genres", ["F00/BBBB.mp3"])]
window._on_playlist_remove_response(None, "remove", "Genres", "uuid:test-ipod")
assert window.commands and window.commands[-1][-2:] == ["--", "Genres"]

# And if the iPod it belongs to has gone in the meantime, nothing runs: the
# playlist named is on a device that is no longer the one under the mount.
swapped = FakeWindow()
swapped.playlists = [("Genres", ["F00/BBBB.mp3"])]
gui.volume_identity = lambda _mount: "uuid:a-different-ipod"
try:
    swapped._on_playlist_remove_response(
        None, "remove", "Genres", "uuid:test-ipod"
    )
finally:
    gui.volume_identity = lambda _mount: "uuid:test-ipod"
assert swapped.commands == [], swapped.commands
assert "changed" in swapped.toasts[-1], swapped.toasts

# A queue survives the iPod being unplugged, and a playlist can be deleted with
# nothing attached, so unstaging cannot be a thing that needs a device: a sync
# still naming a file that has been deleted is cancelled outright, every time
# it is pressed, until something else happens to take that file out.
unplugged = FakeWindow()
unplugged.library_tracks([first])
new_playlist(unplugged, "Gone")
gui.write_playlist_entries(PLAYLISTS / "Gone.m3u", [str(first)])
unplugged._load_local_playlists()
unplugged._send_playlist_to_ipod("Gone")
assert unplugged.is_queued(PLAYLISTS / "Gone.m3u"), unplugged.pending_sources
unplugged.mount_point = None
unplugged.device_identity = None
unplugged._on_playlist_remove_response(None, "remove", "Gone", "uuid:test-ipod")
assert not (PLAYLISTS / "Gone.m3u").exists(), "the playlist file survived"
assert unplugged.pending_sources == {}, unplugged.pending_sources
assert unplugged.pending == set(), unplugged.pending
assert unplugged.toasts[-1] == "Gone deleted", unplugged.toasts

# The same for a rename, which leaves the queue naming a file that is about to
# be called something else.
renamer = FakeWindow()
renamer.library_tracks([first])
new_playlist(renamer, "Before")
gui.write_playlist_entries(PLAYLISTS / "Before.m3u", [str(first)])
renamer._load_local_playlists()
renamer._send_playlist_to_ipod("Before")
staged_before = str(PLAYLISTS / "Before.m3u")
assert staged_before in renamer.pending_sources, renamer.pending_sources
renamer.mount_point = None
renamer.device_identity = None
renamer._on_rename_response(None, "rename", "Before", Entry("After"))
assert (PLAYLISTS / "After.m3u").is_file(), "the rename wrote no file"
assert staged_before not in renamer.pending_sources, renamer.pending_sources
gui.delete_local_playlist(PLAYLISTS / "After.m3u")

# And neither unstages anything when the file did not budge. Being told a
# rename or a delete failed, while the sync it was queued for has silently
# stopped carrying it, leaves a playlist that is still there and a Sync that
# will not mention it - with the page still showing an insensitive "Queued for
# sync" there is nothing left to press to put it back.
original_rename = gui.rename_local_playlist
original_delete = gui.delete_local_playlist
for operation, run, said in (
    (
        "rename_local_playlist",
        lambda window: window._on_rename_response(
            None, "rename", "Stays", Entry("Moved")
        ),
        "Could not rename Stays",
    ),
    (
        "delete_local_playlist",
        lambda window: window._on_playlist_remove_response(
            None, "remove", "Stays", "uuid:test-ipod"
        ),
        "Could not delete Stays",
    ),
):
    stubborn = FakeWindow()
    stubborn.library_tracks([first])
    new_playlist(stubborn, "Stays")
    gui.write_playlist_entries(PLAYLISTS / "Stays.m3u", [str(first)])
    stubborn._load_local_playlists()
    stubborn._send_playlist_to_ipod("Stays")
    assert stubborn.is_queued(PLAYLISTS / "Stays.m3u"), stubborn.pending_sources
    # The failure the store reports when the file cannot be moved or unlinked:
    # a read-only folder, a permission, a disk with nothing left.
    setattr(gui, operation, lambda *_args: None)
    try:
        run(stubborn)
    finally:
        gui.rename_local_playlist = original_rename
        gui.delete_local_playlist = original_delete
    assert stubborn.toasts[-1] == said, (operation, stubborn.toasts)
    assert (PLAYLISTS / "Stays.m3u").is_file(), operation
    assert stubborn.is_queued(PLAYLISTS / "Stays.m3u"), (
        f"a failed {operation} took the playlist out of the queue"
    )
    assert str(first) in stubborn.pending, (
        f"a failed {operation} took the playlist's tracks with it"
    )
    gui.delete_local_playlist(PLAYLISTS / "Stays.m3u")

# A track the library scan has not indexed yet - a download that has just
# landed - makes staging read its tags in the background first. Moving one
# stages two playlists, and each reading supersedes the one before it, so both
# ends have to go into a single reading: otherwise the target is reported as
# queued while the queue never hears of it.
fresh_one, fresh_two = song("Just Landed"), song("Also New")
moving = FakeWindow()
moving.library_tracks([first])
new_playlist(moving, "From")
new_playlist(moving, "To")
gui.write_playlist_entries(PLAYLISTS / "From.m3u", [str(fresh_one), str(fresh_two)])
gui.write_playlist_entries(PLAYLISTS / "To.m3u", [str(first)])
moving._load_local_playlists()


def enrich(paths, _generation):
    return {
        path: gui.Track(
            path, {"title": Path(path).stem, "size": 8}, gui.STATE_LIBRARY
        )
        for path in paths
    }, True


moving._scan_pending_tracks = enrich
scheduled = []
arrived = threading.Event()
original_glib = gui.GLib


def record_idle(callback, *args):
    scheduled.append((callback, args))
    arrived.set()
    return 1


gui.GLib = type("ImmediateGLib", (), {"idle_add": staticmethod(record_idle)})
try:
    moving._move_track_between("From", "To", track_for(fresh_one))
    assert arrived.wait(2), "staging an unindexed track never reached GLib"
finally:
    gui.GLib = original_glib
assert len(scheduled) == 1, (
    f"one edit started {len(scheduled)} readings, and the last cancels the rest"
)
enrichment, enrichment_args = scheduled[0]
enrichment(*enrichment_args)
assert set(moving.pending_sources) == {
    str(PLAYLISTS / "From.m3u"),
    str(PLAYLISTS / "To.m3u"),
}, moving.pending_sources
assert str(fresh_one) in moving.pending_sources[str(PLAYLISTS / "To.m3u")]
assert str(fresh_one) not in moving.pending_sources[str(PLAYLISTS / "From.m3u")]
assert str(fresh_two) in moving.pending_sources[str(PLAYLISTS / "From.m3u")]

# Deleting a playlist while that reading is still running takes it out of the
# sync it never finished joining. There is nothing in the queue to drop yet,
# so the reading has to land on a playlist that is not there any more and let
# it go: staged, it would name a file the next sync cannot re-read, and every
# press of Sync from then on would be cancelled with nothing left to press.
vanishing = FakeWindow()
vanishing.library_tracks([first])
new_playlist(vanishing, "Vanishing")
gui.write_playlist_entries(PLAYLISTS / "Vanishing.m3u", [str(fresh_one)])
vanishing._load_local_playlists()
vanishing._scan_pending_tracks = enrich
scheduled.clear()
arrived.clear()
gui.GLib = type("ImmediateGLib", (), {"idle_add": staticmethod(record_idle)})
try:
    vanishing._send_playlist_to_ipod("Vanishing")
    assert arrived.wait(2), "staging an unindexed track never reached GLib"
finally:
    gui.GLib = original_glib
assert vanishing.pending_sources == {}, (
    "the playlist was queued before its tracks had been read"
)
vanishing._on_playlist_remove_response(
    None, "remove", "Vanishing", "uuid:test-ipod"
)
assert not (PLAYLISTS / "Vanishing.m3u").exists(), "the playlist file survived"
landed, landed_args = scheduled[-1]
landed(*landed_args)
assert vanishing.pending_sources == {}, (
    "a deleted playlist came back when its reading landed"
)
assert vanishing.pending == set(), vanishing.pending
# The window is not left waiting on the reading it dropped.
assert not vanishing.discovering_sources, "the window stayed in its reading state"

# The folder is one other programs read and write, so a queued playlist can be
# deleted or moved from outside the app between staging it and pressing Sync.
# That one is dropped from the queue rather than failing the re-read of every
# source: a failed scan leaves it staged, and every press of Sync after it is
# cancelled with nothing named the user could go and put right.
scanning = FakeWindow()
scanning.library_tracks([first])
new_playlist(scanning, "Still Here")
new_playlist(scanning, "Taken Away")
for name in ("Still Here", "Taken Away"):
    gui.write_playlist_entries(PLAYLISTS / f"{name}.m3u", [str(first)])
scanning._load_local_playlists()
original_scan_tracks = gui.scan_tracks


def scan_stub(root=None, files=(), **_kwargs):
    del root
    return [
        {"path": str(path), "title": Path(path).stem, "size": 8}
        for path in files
    ], True


queued_paths = [
    str(PLAYLISTS / "Still Here.m3u"),
    str(PLAYLISTS / "Taken Away.m3u"),
]
gui.scan_tracks = scan_stub
try:
    gui.delete_local_playlist(PLAYLISTS / "Taken Away.m3u")
    refreshed, complete = scanning._scan_queued_sources(
        queued_paths, scanning.source_generation
    )
finally:
    gui.scan_tracks = original_scan_tracks
assert complete, "one playlist that had gone failed the re-read of them all"
assert set(refreshed) == {str(PLAYLISTS / "Still Here.m3u")}, refreshed
assert [track.path for track in refreshed[queued_paths[0]]] == [
    str(first),
    queued_paths[0],
], refreshed[queued_paths[0]]

# A playlist that is there but cannot be read is the other thing entirely, and
# still stops the sync: dropping it would sync a list without the tracks it
# lists, which on a device with no screen is a playlist that plays nothing.
gui.scan_tracks = lambda root=None, **_kwargs: ([], False)
try:
    unreadable_scan = scanning._scan_queued_sources(
        [queued_paths[0]], scanning.source_generation
    )
finally:
    gui.scan_tracks = original_scan_tracks
assert unreadable_scan == ({}, False), unreadable_scan
gui.delete_local_playlist(PLAYLISTS / "Still Here.m3u")

# Importing adopts the file rather than pointing at where it sat, so from then
# on it is an ordinary playlist that can be edited here.
import_window = FakeWindow()
import_window.library_tracks([first, foreign_track])
import_window._import_playlist(foreign)
adopted = PLAYLISTS / "Road Trip.m3u"
assert adopted.is_file(), "the import wrote no playlist"
assert import_window.current_playlist == "Road Trip"
assert "Imported Road Trip · 2 tracks" in import_window.toasts[-1]
assert str(adopted) in import_window.pending_sources
# An import that could not read the file says so, rather than sending the user
# looking through a library that was never the trouble.
import_window._import_playlist(foreign_dir / "Not There.m3u")
assert "could not be read" in import_window.toasts[-1], import_window.toasts
gui.delete_local_playlist(adopted)

# What a YouTube result adds is found by video id: the same press has to work
# for a video the music folder already holds, whose download reports nothing
# new because there was nothing new to fetch.
youtube_library = Path(tempfile.mkdtemp(prefix="youtube-"))
gui.YOUTUBE_LIBRARY = youtube_library
downloaded = youtube_library / "Queen" / "Bohemian Rhapsody [fJ9rUzIMcZQ].mp3"
downloaded.parent.mkdir(parents=True)
downloaded.write_bytes(b"downloaded")
assert gui.downloaded_file("fJ9rUzIMcZQ", youtube_library) == downloaded
assert gui.downloaded_file("notthere", youtube_library) is None
assert gui.downloaded_file("", youtube_library) is None

download_window = FakeWindow()
new_playlist(download_window, "Fresh")
download_window.library_tracks([downloaded, first])
outcome = download_window._add_download_to_playlist("Fresh", "fJ9rUzIMcZQ", [])
assert outcome == "Added to Fresh · queued for sync", outcome
assert gui.read_playlist_entries(PLAYLISTS / "Fresh.m3u") == [str(downloaded)]
# Downloaded again from a different search: the file is already listed.
assert download_window._add_download_to_playlist(
    "Fresh", "fJ9rUzIMcZQ", []
).startswith("Already in Fresh")
# Nothing to go on, and the run reported several files, so it says so rather
# than guessing which one was meant.
vague = download_window._add_download_to_playlist(
    "Fresh", "", [str(downloaded), str(first)]
)
assert "could not tell which track" in vague, vague
# One reported file and no id is unambiguous.
single = download_window._add_download_to_playlist("Fresh", "", [str(first)])
assert single.startswith("Added to Fresh"), single
gone = download_window._add_download_to_playlist("Deleted", "abc", [])
assert "no longer there" in gone, gone

# Picking a playlist from a result's ⋯ starts that download and carries both
# the playlist and the id it will be found by afterwards.
result = gui.SearchResult(
    title="Bohemian Rhapsody",
    uploader="Queen",
    duration=355,
    url="https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
    video_id="fJ9rUzIMcZQ",
)
download_window._add_result_to_playlist("Fresh", result)
assert len(download_window.downloads) == 1, download_window.downloads
url, asked = download_window.downloads[-1]
assert url == result.url, url
assert asked["playlist"] == "Fresh", asked
assert asked["video_id"] == "fJ9rUzIMcZQ", asked
# A playlist deleted since the menu was painted says so, rather than closing
# the menu on nothing: a press that starts no download and shows no message is
# indistinguishable from one the app never received.
download_window._add_result_to_playlist("Deleted", result)
assert download_window.toasts[-1] == "There is no playlist called Deleted", (
    download_window.toasts
)
assert len(download_window.downloads) == 1, (
    "a download started with nowhere to put it"
)

# The same from a result's ⋯ → New playlist…: the download runs for as long as
# it runs, and the playlist it will land in is in the rail for all of it.
result_new = FakeWindow()
painted = result_new.repaints
new_playlist(
    result_new,
    "For A Video",
    then=lambda name: result_new._add_result_to_playlist(name, result),
)
assert (PLAYLISTS / "For A Video.m3u").is_file(), "the playlist was not made"
assert result_new.downloads, "the download never started"
assert result_new.repaints > painted, "the rail never heard of the new playlist"
gui.delete_local_playlist(PLAYLISTS / "For A Video.m3u")

# The Playlists view resolves each entry against what it knows, and an entry
# nothing knows about still becomes a row rather than disappearing.
resolve_window = FakeWindow()
new_playlist(resolve_window, "Resolve")
resolve_window.library_tracks([first])
gui.write_playlist_entries(
    PLAYLISTS / "Resolve.m3u", [str(first), "/gone/Missing Song.mp3"]
)
resolve_window._load_local_playlists()
rows = resolve_window._playlist_tracks(resolve_window._local_playlist("Resolve"))
assert [row.title for row in rows] == ["Lithium", "Missing Song"], rows
assert rows[0].artist == "Artist", rows[0].artist

# A device playlist's rows come from what was read off the device instead.
resolve_window.playlists = [("Genres", ["F00/AAAA.mp3", "F00/CCCC.mp3"])]
resolve_window.device_tracks = [
    gui.Track(
        "/media/alex/iPod/iPod_Control/Music/F00/AAAA.mp3",
        {"title": "On The Device"},
        gui.STATE_IPOD,
        relpath="F00/AAAA.mp3",
    )
]
device_rows = resolve_window._playlist_tracks(
    resolve_window._shown_playlists()[-1]
)
assert [row.title for row in device_rows] == ["On The Device", "CCCC"], device_rows
assert all(row.state == gui.STATE_IPOD for row in device_rows)

# The dot beside a playlist means what it means beside a track: on the iPod,
# or here and waiting to be.
assert resolve_window._playlist_state(
    resolve_window._shown_playlists()[-1]
) == gui.STATE_IPOD
assert resolve_window._playlist_state(
    resolve_window._local_playlist("Resolve")
) == gui.STATE_LIBRARY

# Send to iPod stages a playlist that was built with nothing plugged in.
send_window = FakeWindow()
send_window.library_tracks([first])
new_playlist(send_window, "Later")
gui.write_playlist_entries(PLAYLISTS / "Later.m3u", [str(first)])
send_window._load_local_playlists()
assert not send_window.is_queued(PLAYLISTS / "Later.m3u")
send_window._send_playlist_to_ipod("Later")
assert send_window.is_queued(PLAYLISTS / "Later.m3u"), send_window.pending_sources
assert send_window.toasts[-1] == "Later · queued for sync", send_window.toasts

gui.volume_identity = original_volume_identity

print(
    f"playlists ok: {len(list(PLAYLISTS.glob('*.m3u')))} left in the store",
    file=sys.stderr,
)
