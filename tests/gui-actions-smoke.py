#!/usr/bin/env python3
"""Checks the commands the GUI builds for removing tracks and for YouTube.

Both are destructive or expensive in ways the log only reveals afterwards: a
removal is an rm on the device, and a download that then copies the wrong
sources pushes an entire library onto 2GB of flash. The argument vectors are
asserted here rather than inferred from a screenshot.

No window is created, because that would need a display. The methods are
called unbound against a stand-in that records what would have been run,
which is the approach the other GUI checks use.
"""

import http.server
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from harness import REPO, gui


class Value:
    def __init__(self, value):
        self.value = value

    def get_text(self):
        return self.value

    def get_active(self):
        return self.value


class FakeSwitch:
    def __init__(self):
        self.active = False
        self.sensitive = True

    def set_active(self, value):
        self.active = value

    def set_sensitive(self, value):
        self.sensitive = value


class FakeWidget:
    """Stands in for any widget _set_busy touches.

    One class rather than several, because the point of the check is which
    controls end up sensitive, not which GTK type each one happens to be.
    """

    def __init__(self):
        self.sensitive = True
        self.visible = False
        self.text = None
        self.revealed = False
        self.spinning = False
        self.fraction = 0.0
        self.active = False
        self.tooltip = None

    def set_sensitive(self, value):
        self.sensitive = value

    def set_active(self, value):
        self.active = value

    def set_visible(self, value):
        self.visible = value

    def set_text(self, value):
        self.text = value

    def get_text(self):
        return self.text

    def set_label(self, value):
        self.text = value

    def set_reveal_child(self, value):
        self.revealed = value

    def set_fraction(self, value):
        self.fraction = value

    def set_fractions(self, used, queued, over=False):
        self.fractions = (used, queued, over)

    def set_tooltip_text(self, value):
        self.tooltip = value

    def start(self):
        self.spinning = True

    def stop(self):
        self.spinning = False


class FakeLibrary:
    """The three lists the window reads tracks out of.

    Instances rather than one shared class attribute each, because the preview
    list is appended to as well as replaced, and a shared list would carry one
    check's previews into the next one.
    """

    def __init__(self):
        self.tracks = []
        self.device_only = []
        self.previews = []

    all_tracks = gui.LibraryIndex.all_tracks


class FakeWindow:
    """Records the commands the window would have run."""

    def _update_refresh_spinner(self):
        # Chrome the real window spins while a scan runs; nothing to show here.
        pass

    def __init__(self):
        self.mount_point = "/media/alex/Alex's iPod"
        self.device_identity = "uuid:test-ipod"
        self.busy = False
        self.probe_generation = 0
        self.tag_generation = 0
        self.discovering_sources = False
        self.source_generation = 0
        self._device_scan_active = False
        self._device_snapshot_ready = True
        self.pending_device_identity = None
        self.pending = set()
        self.pending_sources = {}
        self.pending_records = {}
        self._pending_track_index = {}
        self._library_by_path = {}
        self.commands = []
        self.busy_messages = []
        self.done_messages = []
        self.on_failure = None
        self.toasts = []
        self.track_names = {}
        self.youtube_unavailable = None
        # Collected as the YouTube rows are built, exactly like _busy_widgets,
        # so a new Add button cannot be forgotten by the capability gating.
        self.search_add_buttons = []
        self.library = FakeLibrary()
        self.device_tracks = []
        self.speech_engine_available = True
        self.playlist_voiceover = FakeSwitch()
        self.cache_figure = FakeWidget()
        self.cache_meter = FakeWidget()
        self.cache_clear = FakeWidget()

    def _run(
        self,
        argv,
        busy_message,
        done_message,
        then=None,
        clear=True,
        on_failure=None,
    ):
        self.commands.append(argv)
        self.then = then
        self.on_failure = on_failure
        self.busy_messages.append(busy_message)
        self.done_messages.append(done_message)
        return True

    def _toast(self, message):
        self.toasts.append(message)

    def _set_busy(self, busy, message=""):
        self.busy = busy

    def _sync_options(self):
        return ["--dir-playlists=1", "--playlist-voiceover"]

    def _populate_device_summary(self):
        pass

    def _refresh_current_view(self):
        pass

    # The step that decides what to copy is the one under test, so it is the
    # real implementation rather than another stand-in. Same for the one that
    # empties the queue once a staged sync has succeeded.
    _sync_downloaded = gui.IpodWindow._sync_downloaded
    _clear_pending = gui.IpodWindow._clear_pending
    _audio_files = gui.IpodWindow._audio_files
    _pending_track = gui.IpodWindow._pending_track
    _pending_accounting = gui.IpodWindow._pending_accounting
    _pending_copy_tracks = gui.IpodWindow._pending_copy_tracks
    _pending_change_count = gui.IpodWindow._pending_change_count
    _record_for_track = staticmethod(gui.IpodWindow._record_for_track)
    _merge_states = gui.IpodWindow._merge_states
    _scan_pending_tracks = gui.IpodWindow._scan_pending_tracks
    _finish_pending_enrichment = gui.IpodWindow._finish_pending_enrichment
    _source_gone = staticmethod(gui.IpodWindow._source_gone)
    is_playlist_queued = gui.IpodWindow.is_playlist_queued
    _commit_queue_sources = gui.IpodWindow._commit_queue_sources
    _queue_sources = gui.IpodWindow._queue_sources
    _queue_paths = gui.IpodWindow._queue_paths
    _queue_playlists = gui.IpodWindow._queue_playlists
    _unqueue_track = gui.IpodWindow._unqueue_track
    _prune_pending = gui.IpodWindow._prune_pending
    _scan_queued_sources = gui.IpodWindow._scan_queued_sources
    _finish_pending_source_scan = gui.IpodWindow._finish_pending_source_scan
    _launch_pending_sync = gui.IpodWindow._launch_pending_sync
    _update_device_controls = gui.IpodWindow._update_device_controls
    _confirmed_device = gui.IpodWindow._confirmed_device
    _youtube_download_tooltip = gui.IpodWindow._youtube_download_tooltip
    _can_download = gui.IpodWindow._can_download
    _start_youtube_download = gui.IpodWindow._start_youtube_download
    _populate_cache_card = gui.IpodWindow._populate_cache_card


# ------------------------------------------------------------------ removal

window = FakeWindow()
relpath = "Road Trip/Disc 1/01 - Highway.mp3"
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: window.device_identity
try:
    gui.IpodWindow._on_remove_response(
        window, None, "remove", relpath, window.device_identity
    )
finally:
    gui.volume_identity = original_volume_identity

removal = window.commands[0]
assert removal[0].endswith("ipod-remove.sh"), removal
# The mount point has to be passed explicitly. Autodetection refuses to choose
# between two connected iPods, and the window already knows which one this is.
assert removal[1:3] == ["--ipod", window.mount_point], removal
# No terminal is attached to answer a confirmation prompt.
assert "--yes" in removal, removal
# The path stays relative to the music folder, which is what the script
# resolves against and what keeps a track name from escaping it. It comes
# after --, because a song whose title starts with a dash is not a flag.
assert removal[-2:] == ["--", relpath], removal

# Anything other than the destructive response must run nothing at all.
for answer in ("cancel", "close"):
    quiet = FakeWindow()
    gui.IpodWindow._on_remove_response(
        quiet, None, answer, relpath, quiet.device_identity
    )
    assert quiet.commands == [], (answer, quiet.commands)

disconnected_removal = FakeWindow()
removed_device = disconnected_removal.device_identity
disconnected_removal.mount_point = None
disconnected_removal.device_identity = None
gui.IpodWindow._on_remove_response(
    disconnected_removal, None, "remove", relpath, removed_device
)
assert disconnected_removal.commands == [], disconnected_removal.commands
assert "changed" in disconnected_removal.toasts[-1], disconnected_removal.toasts

replaced_removal = FakeWindow()
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: "uuid:replacement-ipod"
try:
    gui.IpodWindow._on_remove_response(
        replaced_removal,
        None,
        "remove",
        relpath,
        replaced_removal.device_identity,
    )
finally:
    gui.volume_identity = original_volume_identity
assert replaced_removal.commands == [], replaced_removal.commands
assert "changed" in replaced_removal.toasts[-1], replaced_removal.toasts

# ---------------------------------------------------------------- log output

# The scripts colour their output for a terminal, and the GUI's log view would
# show the escape sequences literally as "[36m==>[0m Removed 1 track(s)".
coloured = "\x1b[36m==>\x1b[0m Removed 1 track(s)\n"
assert gui.strip_ansi(coloured) == "==> Removed 1 track(s)\n", coloured
assert gui.strip_ansi("plain\n") == "plain\n"

# ------------------------------------------------------- playable formats
#
# The GUI cannot source lib.sh, so compare its necessary copy of the format
# list with the canonical shell declaration.
lib_sh = (REPO / "lib.sh").read_text(encoding="utf-8")
declared = re.search(r'^readonly SUPPORTED_EXT="([^"]+)"', lib_sh, re.MULTILINE)
assert declared, "lib.sh no longer declares SUPPORTED_EXT"
assert {f".{e}" for e in declared.group(1).split("|")} == gui.AUDIO_EXTENSIONS, (
    declared.group(1),
    gui.AUDIO_EXTENSIONS,
)

scan_root = Path(tempfile.mkdtemp())
(scan_root / "Artist").mkdir()
(scan_root / "Artist" / "Fallback.mp3").write_bytes(b"not really an mp3")
(scan_root / "Artist" / "ignored.txt").touch()
original_interpreter = gui._tag_interpreter
original_tag_python = gui.TAG_PYTHON
original_reader = gui._TAG_READER
try:
    gui.TAG_PYTHON = None
    gui._tag_interpreter = lambda: None
    fallback, complete = gui.scan_tracks(scan_root)
    assert complete
    assert fallback == [
        {
            "path": "Artist/Fallback.mp3",
            "title": "Fallback",
            "size": len(b"not really an mp3"),
        }
    ], fallback

    gui.TAG_PYTHON = sys.executable
    gui._TAG_READER = """
import json, sys, time
print(json.dumps({"path": "Artist/Fallback.mp3", "title": "Fallback"}), flush=True)
print(json.dumps({"path": "Artist/Fallback.mp3", "title": "Tagged"}), flush=True)
time.sleep(10)
"""
    streamed = []
    started = time.monotonic()
    records, complete = gui.scan_tracks(
        scan_root, streamed.append, timeout=0.4
    )
    elapsed = time.monotonic() - started
    assert elapsed < 2, elapsed
    assert not complete, "a timed-out scan was reported as complete"
    assert records[0]["title"] == "Tagged", records
    assert [record["title"] for record in streamed] == ["Fallback", "Tagged"], streamed

    started = time.monotonic()
    _records, complete = gui.scan_tracks(
        scan_root,
        timeout=10,
        cancelled=lambda: time.monotonic() - started >= 0.1,
    )
    assert not complete, "a cancelled scan was reported as complete"
    assert time.monotonic() - started < 2, "cancelled scan left its reader running"
finally:
    gui._tag_interpreter = original_interpreter
    gui.TAG_PYTHON = original_tag_python
    gui._TAG_READER = original_reader

_records, complete = gui.scan_tracks(scan_root / "missing")
assert not complete, "a missing scan root was reported as complete"

# The scan has to see exactly what ipod-sync.sh copies, because the count it
# produces drives the sync progress bar: a track counted here and skipped
# there leaves a finished sync reading short of the end, and a track skipped
# here and copied there overruns it. The script enumerates with find -L, so
# every case below is asserted against what find -L reaches.
symlink_root = Path(tempfile.mkdtemp())
regular_track = symlink_root / "regular.mp3"
regular_track.write_bytes(b"regular")
(symlink_root / "linked.mp3").symlink_to(regular_track)
outside_directory = Path(tempfile.mkdtemp())
(outside_directory / "nested.mp3").write_bytes(b"nested")
(outside_directory / "outside.mp3").write_bytes(b"outside")
# A link leaving the scanned tree entirely, which is the layout the whole
# arrangement exists for: a library of links into an archive elsewhere.
(symlink_root / "linked-away.mp3").symlink_to(outside_directory / "outside.mp3")
(symlink_root / "linked-directory").symlink_to(
    outside_directory, target_is_directory=True
)
# A dangling link named like a track used to fail the entire scan, which
# showed an empty library because of one broken link.
(symlink_root / "dangling.mp3").symlink_to(symlink_root / "not-there.mp3")
(symlink_root / "dangling-directory").symlink_to(
    symlink_root / "not-there", target_is_directory=True
)
# A folder that links back to its own parent. os.walk has no loop detection,
# so without the ancestry guard this scan never returns.
(symlink_root / "loop").symlink_to(symlink_root, target_is_directory=True)
library_records, complete = gui.scan_tracks(symlink_root)
assert complete, "a symlinked library was reported as incomplete"
assert {record["path"] for record in library_records} == {
    "regular.mp3",
    "linked.mp3",
    "linked-away.mp3",
    "linked-directory/nested.mp3",
    "linked-directory/outside.mp3",
}, library_records

# Scanning through the link reaches the same files as scanning the directory
# it points at, which is what makes a linked source folder syncable at all.
through_link, complete = gui.scan_tracks(symlink_root / "linked-directory")
assert complete
assert {record["path"] for record in through_link} == {
    "nested.mp3",
    "outside.mp3",
}, through_link

# Two routes to one folder are two folders to find, which copies both, so the
# guard must prune its own ancestry and nothing wider.
twin_root = Path(tempfile.mkdtemp())
(twin_root / "album").mkdir()
(twin_root / "album" / "song.mp3").write_bytes(b"song")
(twin_root / "twin").symlink_to(twin_root / "album", target_is_directory=True)
twin_records, complete = gui.scan_tracks(twin_root)
assert complete
assert {record["path"] for record in twin_records} == {
    "album/song.mp3",
    "twin/song.mp3",
}, twin_records

exact_track = scan_root / "Exact.mp3"
exact_track.write_bytes(b"exact")
unrelated = scan_root / "Artist" / "Unrelated.mp3"
unrelated.write_bytes(b"unrelated")
exact_records, complete = gui.scan_tracks(files=[exact_track])
assert complete
assert [record["path"] for record in exact_records] == [str(exact_track)]

exact_link = scan_root / "Exact Link.mp3"
exact_link.symlink_to(exact_track)
exact_records, complete = gui.scan_tracks(files=[exact_link])
assert complete
assert [record["path"] for record in exact_records] == [str(exact_link)]


class FolderDiscoveryWindow:
    _finish_music_folder_discovery = (
        gui.IpodWindow._finish_music_folder_discovery
    )

    def __init__(self):
        self.source_generation = 0
        self.device_identity = "uuid:test-ipod"
        self.mount_point = "/media/iPod"
        self.discovering_sources = False
        self.worker_thread = None
        self.queued = None

    def _update_device_controls(self):
        pass

    def _scan_source_tracks(self, path, _generation):
        self.worker_thread = threading.get_ident()
        track_path = str(Path(path) / "song.mp3")
        return [
            gui.Track(
                track_path,
                {"title": "Song", "artist": "Artist", "album": "Album"},
                gui.STATE_LIBRARY,
            )
        ], True

    def _queue_sources(self, sources, metadata_complete=False):
        assert metadata_complete
        self.queued = {
            source: [track.path for track in tracks]
            for source, tracks in sources.items()
        }

    def _toast(self, message):
        raise AssertionError(message)


discovery_window = FolderDiscoveryWindow()
scheduled = []
scheduled_event = threading.Event()
original_glib = gui.GLib


def record_idle(callback, *args):
    scheduled.append((callback, args))
    scheduled_event.set()
    return 1


gui.GLib = type("ImmediateGLib", (), {"idle_add": staticmethod(record_idle)})
try:
    main_thread = threading.get_ident()
    gui.IpodWindow._discover_music_folder(discovery_window, "/music")
    assert scheduled_event.wait(2), "folder discovery did not reach GLib"
finally:
    gui.GLib = original_glib
assert discovery_window.worker_thread != main_thread, "folder walked on GTK thread"
assert discovery_window.queued is None, "folder queued before the GLib boundary"
callback, callback_args = scheduled[0]
callback(*callback_args)
assert discovery_window.queued == {
    "/music": ["/music/song.mp3"]
}, discovery_window.queued
assert not discovery_window.discovering_sources, "folder discovery stayed active"

failed_discovery = FolderDiscoveryWindow()
failed_discovery.toasts = []
failed_discovery._toast = failed_discovery.toasts.append
partial_track = gui.Track(
    "/music/partial.mp3", {"title": "Partial"}, gui.STATE_LIBRARY
)
gui.IpodWindow._finish_music_folder_discovery(
    failed_discovery,
    failed_discovery.source_generation,
    failed_discovery.device_identity,
    "/music",
    [partial_track],
    False,
)
assert failed_discovery.queued is None, "partial folder scan entered the queue"
assert "nothing was queued" in failed_discovery.toasts[-1]

enrichment_window = FakeWindow()
pending_only = Path(tempfile.mkdtemp()) / "Outside.mp3"
pending_only.touch()
enrichment_window._update_device_controls = lambda: None


def enrich_pending(paths, _generation):
    assert paths == {str(pending_only)}
    return {
        str(pending_only): gui.Track(
            pending_only,
            {
                "title": "Outside",
                "artist": "Artist",
                "album": "Album",
                "duration": 120,
            },
            gui.STATE_LIBRARY,
        )
    }, True


enrichment_window._scan_pending_tracks = enrich_pending
scheduled = []
scheduled_event = threading.Event()
gui.GLib = type("ImmediateGLib", (), {"idle_add": staticmethod(record_idle)})
try:
    result = enrichment_window._queue_sources(
        {str(pending_only): [enrichment_window._pending_track(pending_only)]}
    )
    assert result is None, "pending-only tags were not enriched asynchronously"
    assert scheduled_event.wait(2), "pending enrichment did not reach GLib"
finally:
    gui.GLib = original_glib
callback, callback_args = scheduled[0]
callback(*callback_args)
assert enrichment_window.pending_records[str(pending_only)]["artist"] == "Artist"


class Toggle:
    """One of the two grouping buttons, as much of it as the code reads."""

    def __init__(self, active):
        self.active = active

    def get_active(self):
        return self.active


class VisibleView:
    def __init__(self, name):
        self.name = name

    def get_visible_child_name(self):
        return self.name


class RefreshWindow:
    _resolve_current_album = gui.IpodWindow._resolve_current_album
    # The real accessor, not a stand-in for it: which view is on screen is what
    # decides whether a repaint reopens album detail, so a fake that answered
    # differently would be checking itself.
    current_view = gui.IpodWindow.current_view
    _grouped_by_artist = gui.IpodWindow._grouped_by_artist
    # A repaint clears the coalesced queue behind it, so the real one is here
    # too rather than a stand-in that would not.
    _cancel_refresh = gui.IpodWindow._cancel_refresh

    def __init__(self, visible):
        self._refresh_timer = None
        old_track = gui.Track("old.mp3", {"album": "Album"}, gui.STATE_LIBRARY)
        new_track = gui.Track("new.mp3", {"album": "Album"}, gui.STATE_LIBRARY)
        self.current_album = gui.Album("Album", "Unknown artist")
        self.current_album.add(old_track)
        replacement = gui.Album("Album", "Unknown artist")
        replacement.add(new_track)
        self.library = type("Library", (), {"collections": lambda _self, _mode: [replacement]})()
        self.group_buttons = {"album": Toggle(True), "artist": Toggle(False)}
        self.views = VisibleView(visible)
        self.current_playlist = None
        self.repaints = 0
        self.shown = []

    def _populate_albums(self):
        self.repaints += 1

    def _show_album(self, album):
        self.current_album = album
        self.shown.append(album)

    def _show_playlist(self, name):
        self.shown.append(name)

    def show_view(self, name):
        self.views.name = name


library_refresh = RefreshWindow("library")
gui.IpodWindow._refresh_current_view(library_refresh)
assert not library_refresh.shown, "a library repaint reopened stale album detail"

album_refresh = RefreshWindow("album")
stale_album = album_refresh.current_album
gui.IpodWindow._refresh_current_view(album_refresh)
assert album_refresh.current_album is not stale_album, "album detail kept stale tracks"
assert album_refresh.shown == [album_refresh.current_album], album_refresh.shown

partial_refresh = RefreshWindow("album")
partial_album = partial_refresh.current_album
partial_refresh.library = type(
    "EmptyLibrary", (), {"collections": lambda _self, _mode: []}
)()
gui.IpodWindow._refresh_current_view(partial_refresh, scan_complete=False)
assert partial_refresh.views.name == "album", "partial scan closed album detail"
assert partial_refresh.current_album is partial_album, "partial scan dropped album state"
gui.IpodWindow._refresh_current_view(partial_refresh, scan_complete=True)
assert partial_refresh.views.name == "library", "completed scan kept a missing album open"

disconnected_scan = type(
    "DisconnectedScan",
    (),
    {"tag_generation": 3, "mount_point": None},
)()
assert not gui.IpodWindow._apply_device_track_batch(
    disconnected_scan, 3, "/media/iPod", []
), "a disconnected device scan was applied"

common = {
    "title": "Same Song",
    "artist": "Same Artist",
    "album": "Same Album",
    "duration": 200.2,
}
local_one = gui.Track("/music/one.mp3", common, gui.STATE_LIBRARY)
local_two = gui.Track("/music/two.mp3", common, gui.STATE_LIBRARY)
device_one = gui.Track(
    "/media/iPod/iPod_Control/Music/F00/AAAA.mp3",
    {**common, "duration": 200.4},
    gui.STATE_IPOD,
    relpath="F00/AAAA.mp3",
)
merge_library = type(
    "MergeLibrary",
    (),
    {"tracks": [local_one, local_two], "device_only": []},
)()
merge_window = type(
    "MergeWindow",
    (),
    {"library": merge_library, "device_tracks": [device_one]},
)()
gui.IpodWindow._merge_states(merge_window)
assert sum(track.on_ipod for track in merge_library.tracks) == 1
assert local_one.relpath == "F00/AAAA.mp3", local_one.relpath
assert local_two.relpath == local_two.path, local_two.relpath

other_album = gui.Track(
    "/media/iPod/iPod_Control/Music/F00/BBBB.mp3",
    {**common, "album": "Other Album"},
    gui.STATE_IPOD,
    relpath="F00/BBBB.mp3",
)
merge_window.device_tracks = [other_album]
gui.IpodWindow._merge_states(merge_window)
assert not any(track.on_ipod for track in merge_library.tracks)
assert merge_library.device_only == [other_album]


class SelectionWindow:
    def _update_refresh_spinner(self):
        # Chrome the real window spins while a scan runs; nothing to show here.
        pass

    def __init__(self):
        queued = gui.Track("/music/queued.mp3", common, gui.STATE_LIBRARY)
        self.mount_point = "/media/A"
        self.device_identity = "uuid:A"
        self.discovering_sources = False
        self.source_generation = 0
        self.pending_device_identity = "uuid:A"
        self.pending = {queued.path}
        self.pending_sources = {queued.path: {queued.path}}
        self.pending_records = {}
        self.tag_generation = 0
        self._device_scan_tracks = {}
        self._device_scan_active = False
        self._device_snapshot_ready = True
        self.device_tracks = []
        self.track_names = {}
        self.toasts = []

    def _merge_states(self):
        pass

    def _refresh_current_view(self):
        pass

    def _toast(self, message):
        self.toasts.append(message)


# The identity arrives beside the mount point rather than being read here:
# recognising the device is a decision the probe already paid for over USB, so
# selecting one costs nothing and cannot block a repaint.
identity_window = SelectionWindow()
gui.IpodWindow._select_mount(identity_window, None, None)
assert identity_window.pending, "disconnect discarded a device-bound queue"
gui.IpodWindow._select_mount(identity_window, "/media/A-again", "uuid:A")
assert identity_window.pending, "same device reconnect discarded its queue"
gui.IpodWindow._select_mount(identity_window, "/media/B", "uuid:B")
assert identity_window.pending == set(), identity_window.pending
assert identity_window.pending_sources == {}, identity_window.pending_sources
assert identity_window.toasts and "different iPod" in identity_window.toasts[-1]

# ----------------------------------------------------------------- playlist
#
# What a playlist costs the queue: one source holding the list itself and every
# track it names. The file is a member too, so an emptied playlist is still a
# queued change - the sync is what removes the device's copy of it.
# tests/gui-playlists.py covers making and editing the list; this is the seam
# between a playlist and the sync.

playlist_window = FakeWindow()
playlist_root = Path(tempfile.mkdtemp())
playlist_track = playlist_root / "Party Song.mp3"
playlist_track.touch()
playlist_path = playlist_root / "Party Mix.m3u"
playlist_path.write_text(f"{playlist_track.name}\n", encoding="utf-8")
playlist_window.library.tracks = [
    gui.Track(
        playlist_track,
        {"title": "Party Song", "artist": "Artist"},
        gui.STATE_LIBRARY,
    )
]
playlist_window._merge_states()
playlist_window._queue_playlists([playlist_path], show_toast=False)

assert playlist_window.commands == [], playlist_window.commands
assert playlist_window.pending_sources == {
    str(playlist_path): {str(playlist_path), str(playlist_track)}
}, playlist_window.pending_sources
assert playlist_window.pending == {
    str(playlist_path),
    str(playlist_track),
}, playlist_window.pending
# One change to copy and one list to write, rather than two tracks: the m3u is
# not audio, so it counts as the playlist change and not as a file to play.
assert playlist_window._pending_change_count() == 2, (
    playlist_window._pending_change_count()
)
assert [t.path for t in playlist_window._pending_copy_tracks()] == [
    str(playlist_track)
]

emptied = playlist_root / "Emptied.m3u"
emptied.write_text("#EXTM3U\n", encoding="utf-8")
playlist_window._queue_playlists([emptied], show_toast=False)
assert playlist_window.pending_sources[str(emptied)] == {str(emptied)}, (
    "an emptied playlist left nothing for the sync to rewrite"
)

folder_window = FakeWindow()
folder_members = [
    gui.Track(
        f"/music/Album/{name}.mp3",
        {"title": name},
        gui.STATE_LIBRARY,
    )
    for name in ("One", "Two")
]
folder_window.library.tracks = folder_members
folder_window._merge_states()
folder_window._queue_sources({"/music/Album": folder_members})
folder_window._unqueue_track(folder_members[0])
assert folder_window.pending_sources == {}, folder_window.pending_sources
assert folder_window.pending == set(), folder_window.pending
assert "whole folder" in folder_window.toasts[-1], folder_window.toasts

# Coming out of an operation must not enable a control this machine cannot
# support. _set_busy re-applies the capability gating on the way out, and the
# widgets it blanket-disables are collected in _busy_widgets rather than named
# one at a time, so a new control cannot be forgotten here.
busy_window = FakeWindow()
busy_window._busy_widgets = [FakeWidget() for _ in range(3)]
for attr in (
    "add_button",
    "playlist_button",
    "youtube_button",
    "sync_button",
    "new_playlist_button",
    "rebuild_button",
    "wipe_button",
    "eject_button",
    "sidebar_eject",
    "playlist_mode",
    "track_voiceover",
    "progress",
    "sync_revealer",
    "sync_spinner",
    "sync_title",
    "sync_count",
    "sync_current",
):
    setattr(busy_window, attr, FakeWidget())
busy_window.youtube_unavailable = None
busy_window.speech_engine_available = False
busy_window.pending = set()

scan_entered = threading.Event()
allow_scan_exit = threading.Event()
scan_cancelled = threading.Event()
concurrent_reader_acquired = threading.Event()
writer_acquired = threading.Event()
release_writer = threading.Event()
blocked_reader_acquired = threading.Event()
original_scan_tracks = gui.scan_tracks


def blocking_device_scan(_music, on_record=None, cancelled=None):
    scan_entered.set()
    while not cancelled() and not allow_scan_exit.wait(0.01):
        pass
    if cancelled():
        scan_cancelled.set()
    return [], False


def read_device_io(acquired):
    with gui.DEVICE_IO_LOCK.read():
        acquired.set()


def write_device_io():
    with gui.DEVICE_IO_LOCK.write():
        writer_acquired.set()
        release_writer.wait(5)


gui.scan_tracks = blocking_device_scan
try:
    gui.IpodWindow._load_device_tracks_async(busy_window)
    assert scan_entered.wait(5), "device tag scan did not start"
    concurrent_reader = threading.Thread(
        target=read_device_io, args=(concurrent_reader_acquired,), daemon=True
    )
    concurrent_reader.start()
    assert concurrent_reader_acquired.wait(5), "device readers blocked each other"

    writer = threading.Thread(target=write_device_io, daemon=True)
    writer.start()
    assert not writer_acquired.wait(0.1), "writer overlapped a device reader"

    tag_generation = busy_window.tag_generation

    def tag_cancelled():
        return tag_generation != busy_window.tag_generation

    probe_generation = busy_window.probe_generation

    def probe_cancelled():
        return probe_generation != busy_window.probe_generation

    gui.IpodWindow._set_busy(busy_window, True, "Changing the device")
    assert tag_cancelled(), "starting a command left the tag scan current"
    assert probe_cancelled(), "starting a command left the device probe running"
    assert scan_cancelled.wait(5), "device tag scan did not stop"
    assert writer_acquired.wait(5), "writer did not start after readers drained"

    blocked_reader = threading.Thread(
        target=read_device_io, args=(blocked_reader_acquired,), daemon=True
    )
    blocked_reader.start()
    assert not blocked_reader_acquired.wait(0.1), "reader overlapped a writer"
    release_writer.set()
    assert blocked_reader_acquired.wait(5), "reader did not follow the writer"
finally:
    allow_scan_exit.set()
    release_writer.set()
    gui.scan_tracks = original_scan_tracks

gui.IpodWindow._set_busy(busy_window, False)
# Making and importing a playlist writes a file in a folder of the user's own,
# so neither waits for a speech engine - this window has none. What an engine
# is needed for is putting a playlist on the device, which is refused when the
# playlist is staged rather than when it is made.
assert busy_window.playlist_button.sensitive, "importing needed a speech engine"
assert busy_window.new_playlist_button.sensitive, "New needed a speech engine"
assert busy_window.youtube_button.sensitive, "busy reset left YouTube disabled"
# Nothing is queued, so there is nothing for Sync to do.
assert not busy_window.sync_button.sensitive, "Sync offered with an empty queue"
assert all(w.sensitive for w in busy_window._busy_widgets), "widgets left disabled"
assert not busy_window.sync_revealer.revealed, "sync bar left showing when idle"
assert not busy_window.sync_spinner.spinning, "spinner left running when idle"

search_add = FakeWidget()
busy_window.search_add_buttons = [search_add]
busy_window.mount_point = None
busy_window._update_device_controls()
assert not search_add.sensitive, "a disconnected result Add remained enabled"
assert search_add.tooltip == "Connect an iPod to download and queue a track"
busy_window.mount_point = "/media/alex/Alex's iPod"
busy_window._update_device_controls()
assert search_add.sensitive, "a reconnected result Add remained disabled"
assert search_add.tooltip is None, "a reconnected result kept its stale tooltip"
busy_window.youtube_unavailable = "ffmpeg is not installed"
busy_window._update_device_controls()
assert not search_add.sensitive, "an unavailable download remained enabled"
assert search_add.tooltip == busy_window.youtube_unavailable
busy_window.mount_point = None
busy_window._update_device_controls()
assert search_add.tooltip == busy_window.youtube_unavailable

# With something queued, the same reset has to offer the sync.
queued_window = FakeWindow()
queued_window._busy_widgets = []
for attr in (
    "add_button",
    "playlist_button",
    "youtube_button",
    "sync_button",
    "new_playlist_button",
    "rebuild_button",
    "wipe_button",
    "eject_button",
    "sidebar_eject",
    "playlist_mode",
    "track_voiceover",
    "progress",
    "sync_revealer",
    "sync_spinner",
    "sync_title",
    "sync_count",
    "sync_current",
):
    setattr(queued_window, attr, FakeWidget())
queued_window.youtube_unavailable = None
queued_window.speech_engine_available = True
queued_track = gui.Track(
    "/home/alex/Music/one.mp3",
    {"title": "one"},
    gui.STATE_LIBRARY,
)
queued_window.pending = {queued_track.path}
queued_window.pending_sources = {queued_track.path: {queued_track.path}}
gui.IpodWindow._set_busy(queued_window, False)
assert queued_window.sync_button.sensitive, "queued changes could not be synced"
queued_window._device_scan_active = True
queued_window._device_snapshot_ready = False
queued_window._update_device_controls()
assert not queued_window.sync_button.sensitive
queued_window._device_snapshot_ready = True
queued_window._update_device_controls()
assert queued_window.sync_button.sensitive

# The rows the window shows come from the m3u files at the volume root, with
# entries under the music folder rewritten to match the track list's keys so
# both share tag-derived titles.
fake_volume = Path(tempfile.mkdtemp())
(fake_volume / "Party.M3U").write_text(
    "#EXTM3U\r\niPod_Control/Music/Yeat/Song [x1].mp3\r\n\r\n/kept/as/written.mp3\n",
    encoding="utf-8",
)
(fake_volume / "mix..v2.m3u").write_text(
    "iPod_Control/Music/Yeat/Song [x1].mp3\n",
    encoding="utf-8",
)
(fake_volume / "Radio.PLS").write_text(
    "[playlist]\nFile2=/second.mp3\nTitle2=Second\nfile1=iPod_Control/Music/Yeat/Song [x1].mp3\n",
    encoding="utf-8",
)
(fake_volume / "iPod_Control").mkdir()
parsed = gui.list_playlists(fake_volume)
assert parsed == [
    ("Party", ["Yeat/Song [x1].mp3", "/kept/as/written.mp3"]),
    ("Radio", ["Yeat/Song [x1].mp3", "/second.mp3"]),
    ("mix..v2", ["Yeat/Song [x1].mp3"]),
], parsed

# One probe brings all of that back at once, off the main loop. The window
# paints from what it returns and never asks the device again, so anything the
# probe drops is a row, a count or a meter that silently goes empty.
(fake_volume / "iPod_Control" / "Music" / "F00").mkdir(parents=True)
(fake_volume / "iPod_Control" / "Music" / "F00" / "AAAA.mp3").write_text("song")
(fake_volume / "iPod_Control" / "Speakable" / "Playlists").mkdir(parents=True)
(fake_volume / "iPod_Control" / "Speakable" / "Playlists" / "Party.wav").write_text(
    "spoken"
)
original_find_ipods = gui.find_ipods
gui.find_ipods = lambda: [str(fake_volume)]
try:
    volume_probe = gui.probe_device()
finally:
    gui.find_ipods = original_find_ipods
assert volume_probe.mount_point == str(fake_volume), volume_probe.mount_point
assert volume_probe.readable is True, volume_probe.readable
assert volume_probe.playlists == parsed, volume_probe.playlists
assert volume_probe.spoken == {"party"}, volume_probe.spoken
assert volume_probe.track_count == 1, volume_probe.track_count
assert volume_probe.usage is not None, volume_probe.usage

# ------------------------------------------------------------------ youtube

assert gui.is_downloadable_url("https://www.youtube.com/watch?v=abc")
assert gui.is_downloadable_url("  http://youtu.be/abc  ")
for rejected in ("", "youtube.com/watch?v=abc", "not a link", "file:///etc/passwd"):
    assert not gui.is_downloadable_url(rejected), rejected

window = FakeWindow()
url = "https://www.youtube.com/watch?v=abc&list=PL123"
gui.IpodWindow._on_youtube_response(
    window, None, "download", Value(url), Value(False)
)

fetch = window.commands[0]
assert fetch[0].endswith("ipod-fetch.sh"), fetch
assert fetch[-1] == url, fetch
# Off means the linked video only. Without it a link that carries a list=
# parameter quietly downloads someone's 200-track playlist.
assert "--single" in fetch, fetch
new_tracks = Path(fetch[fetch.index("--new-tracks") + 1])

# A refused link must not start a download at all.
rejected_window = FakeWindow()
gui.IpodWindow._on_youtube_response(
    rejected_window, None, "download", Value("not a link"), Value(False)
)
assert rejected_window.commands == [], rejected_window.commands
assert rejected_window.toasts, "a rejected link said nothing"

# What the download reported is exactly what gets queued. Anything else in the
# library, downloaded on an earlier day, stays where it is.
library = Path(tempfile.mkdtemp())
downloaded = library / "New Artist" / "New Song [abc].mp3"
downloaded.parent.mkdir(parents=True)
downloaded.touch()
(library / "Old Artist").mkdir()
new_tracks.write_text(f"{downloaded}\n\n")

gui.YOUTUBE_LIBRARY = library
window.library.tracks = [
    gui.Track(
        downloaded,
        {"title": "New Song", "artist": "New Artist"},
        gui.STATE_LIBRARY,
    )
]
window._merge_states()
queued_outcome = window.then()
assert isinstance(queued_outcome, str), queued_outcome
assert "queued" in queued_outcome, queued_outcome
assert window.pending_sources == {
    str(downloaded): {str(downloaded)}
}, window.pending_sources
assert len(window.commands) == 1, window.commands
assert not new_tracks.exists(), "the track list outlived the sync that read it"

# An empty list means the video had been downloaded before, so there is
# nothing to queue and nothing to report as added.
empty = Path(tempfile.mkstemp()[1])
outcome = gui.IpodWindow._sync_downloaded(window, empty)
assert isinstance(outcome, str), outcome
assert "Already downloaded" in outcome, outcome

# A missing list means yt-dlp could not say, and the artist folders are then
# the closest honest answer rather than silently queueing nothing.
fallback = gui.fetched_sources(library / "never-written", library)
assert sorted(fallback) == sorted(
    [str(library / "New Artist"), str(library / "Old Artist")]
), fallback

# ------------------------------------------------------------------- search
#
# The search field queries two sources at once, and the two halves fail
# independently: metadata needs only yt-dlp, while the download needs ffmpeg
# and a JavaScript runtime as well. Getting that gating wrong either blanks a
# working search or offers an Add that dies with HTTP 403 several steps later.

phrase_search = gui.youtube_search_command("/venv/yt-dlp", "bohemian rhapsody")
assert phrase_search[0] == "/venv/yt-dlp", phrase_search
assert phrase_search[-1] == "ytsearch3:bohemian rhapsody", phrase_search
# Without this yt-dlp resolves every hit's media URLs, turning a one-second
# list of titles into half a minute of work nobody asked for.
assert "--flat-playlist" in phrase_search, phrase_search
assert "--dump-json" in phrase_search, phrase_search
# The separator has to come last, or a query beginning with a dash is read as
# an option.
assert phrase_search[-2] == "--", phrase_search

# A pasted link is looked up as itself. Searching for it would return whatever
# YouTube makes of the URL as a phrase, which is never the linked video.
link = "https://www.youtube.com/watch?v=abc"
assert gui.youtube_search_command("/venv/yt-dlp", f"  {link}  ")[-1] == link
assert gui.youtube_search_target("queen", limit=5) == "ytsearch5:queen"
# A linked playlist is capped to the same shortlist a search returns, so
# pasting an album link cannot flood the section.
capped = gui.youtube_search_command("/venv/yt-dlp", link, limit=2)
assert capped[capped.index("--playlist-items") + 1] == "1-2", capped

parsed_results = gui.parse_search_results(
    [
        json.dumps(
            {
                "id": "fJ9rUzIMcZQ",
                "title": "Bohemian Rhapsody",
                "url": "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
                "duration": 360,
                "channel": "Queen Official",
            }
        ),
        "WARNING: something yt-dlp wanted to mention",
        json.dumps({"id": "onlyanid", "title": "No URL", "duration": "bad"}),
        json.dumps(["not", "an", "object"]),
        json.dumps({"title": "Unreachable", "url": "not-a-link"}),
        "",
    ]
)
assert [r.title for r in parsed_results] == ["Bohemian Rhapsody", "No URL"], [
    r.title for r in parsed_results
]
assert parsed_results[0].uploader == "Queen Official", parsed_results[0].uploader
assert parsed_results[0].duration == 360.0, parsed_results[0].duration
# An entry that carries only an id still becomes the watch URL ipod-fetch.sh
# would have been given anyway, rather than being dropped.
assert parsed_results[1].url == "https://www.youtube.com/watch?v=onlyanid"
assert parsed_results[1].duration == 0.0, parsed_results[1].duration
assert parsed_results[1].uploader == "YouTube", parsed_results[1].uploader

# The skeleton reserves exactly as many rows as the search can return, so the
# layout cannot jump at the moment the results land.
assert gui.YOUTUBE_SEARCH_RESULTS == 3, gui.YOUTUBE_SEARCH_RESULTS
flood = gui.parse_search_results(
    [json.dumps({"id": f"id{n}", "title": str(n)}) for n in range(9)]
)
assert len(flood) == gui.YOUTUBE_SEARCH_RESULTS, len(flood)


# ---------------------------------------------------------------- artwork
#
# A search result has no file to read a cover out of, so its artwork is the
# video's thumbnail. Redirected at the real cache so that nothing here reads
# or writes the one the user's own library fills.
art_cache = Path(tempfile.mkdtemp()) / "art"
gui.ART_CACHE = art_cache

# The smallest size that still covers the largest square artwork is drawn at.
# Below it the album page is visibly soft; above it a quarter of a megabyte is
# downloaded to be shown at 36 pixels in a list of three.
sizes = [
    {"url": "https://i.ytimg.com/vi/x/default.jpg", "width": 120, "height": 90},
    {"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 480, "height": 360},
    {"url": "https://i.ytimg.com/vi/x/maxres.jpg", "width": 1280, "height": 720},
]
assert gui.YOUTUBE_ART_SIZE == 360, gui.YOUTUBE_ART_SIZE
assert gui.thumbnail_from_entry({"thumbnails": sizes}).endswith("hq.jpg")
assert gui.thumbnail_from_entry({"thumbnails": sizes}, want=90).endswith(
    "default.jpg"
)
# A thumbnail is cropped to its short edge, so that is what has to clear the
# target: judging by width would take the wide one here and then have 200
# pixels to stretch across a 400 pixel square.
assert gui.thumbnail_from_entry(
    {
        "thumbnails": [
            {"url": "https://x.invalid/wide.jpg", "width": 450, "height": 200},
            {"url": "https://x.invalid/square.jpg", "width": 900, "height": 900},
        ]
    },
    want=400,
).endswith("square.jpg")
# Nothing big enough means the largest there is, rather than nothing at all.
assert gui.thumbnail_from_entry({"thumbnails": sizes[:1]}).endswith(
    "default.jpg"
)
# Sizes are not always reported, and yt-dlp lists its best last.
assert gui.thumbnail_from_entry(
    {"thumbnails": [{"url": "https://x.invalid/a.jpg"}, {"url": "https://x.invalid/b.jpg"}]}
) == "https://x.invalid/b.jpg"
assert (
    gui.thumbnail_from_entry({"thumbnail": "https://x.invalid/only.jpg"})
    == "https://x.invalid/only.jpg"
)
# Nothing usable is not a reason to break the result: it shows the placeholder.
assert gui.thumbnail_from_entry({}) == ""
assert gui.thumbnail_from_entry({"thumbnails": "not a list"}) == ""
assert gui.thumbnail_from_entry({"thumbnails": [{"url": "/relative.jpg"}]}) == ""
assert gui.thumbnail_from_entry({"thumbnail": "file:///etc/passwd"}) == ""

thumbed = gui.parse_search_results(
    [json.dumps({"id": "abc", "title": "Art", "thumbnails": sizes})]
)
assert thumbed[0].thumbnail.endswith("hq.jpg"), thumbed[0].thumbnail

# The id arrives over the network and this is the only thing between it and a
# filename, so it is a whitelist rather than an attempt at escaping.
assert gui.youtube_art_file("dQw4w9WgXcQ", art_cache) == (
    art_cache / "yt-dQw4w9WgXcQ.img"
)
for hostile in ("../../etc/passwd", "a/b", "", "  ", "a" * 65, "a;rm -rf ~"):
    assert gui.youtube_art_file(hostile, art_cache) is None, hostile

# The id ipod-fetch.sh writes into every filename is what lets a downloaded
# file find the artwork the search already fetched. Read as the last bracketed
# group, because a title can carry brackets of its own.
assert gui.video_id_from_name("Song [Live] [dQw4w9WgXcQ].mp3") == "dQw4w9WgXcQ"
assert gui.video_id_from_name("Song [Live].mp3") == "Live"
assert gui.video_id_from_name("Ordinary Track.mp3") == ""
assert gui.video_id_from_name("") == ""

# Artwork wider than the square is what fills it: the short edge lands on the
# square exactly and the frame clips the rest, which is the centre crop every
# music player shows a video thumbnail as.
assert gui.cover_pixel_size(1280, 720, 36) == 64, "16:9 was not cropped to fill"
assert gui.cover_pixel_size(600, 600, 140) == 140, "a square cover was scaled up"
assert gui.cover_pixel_size(300, 600, 36) == 72, "a tall cover was not filled"
# Rounded up: a pixel short would show a hairline of the frame down one edge.
assert gui.cover_pixel_size(101, 100, 100) == 101
# A texture that reports nothing is drawn at the size asked for rather than
# dividing by zero.
assert gui.cover_pixel_size(0, 0, 36) == 36


class ThumbnailServer(http.server.BaseHTTPRequestHandler):
    """Serves the handful of answers a thumbnail fetch has to survive."""

    bodies = {
        "/thumb.jpg": b"\xff\xd8\xff" + b"jpeg" * 64,
        "/huge.jpg": b"x" * 4096,
        "/empty.jpg": b"",
    }

    def do_GET(self):
        body = self.bodies.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


images = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ThumbnailServer)
threading.Thread(target=images.serve_forever, daemon=True).start()
image_host = f"http://127.0.0.1:{images.server_address[1]}"

landed = art_cache / "yt-thumbtest.img"
assert gui.fetch_thumbnail(f"{image_host}/thumb.jpg", landed) is True
assert landed.read_bytes() == ThumbnailServer.bodies["/thumb.jpg"]

# Every failure leaves the row showing its placeholder rather than an error the
# user cannot act on, and leaves nothing half-written behind for the next paint
# to try to load.
missing_art = art_cache / "yt-missing.img"
assert gui.fetch_thumbnail(f"{image_host}/gone.jpg", missing_art) is False
assert gui.fetch_thumbnail(f"{image_host}/empty.jpg", missing_art) is False
# A URL off the network is checked rather than trusted: urlopen is as happy to
# read file:// as https://.
assert gui.fetch_thumbnail("file:///etc/passwd", missing_art) is False
assert gui.fetch_thumbnail("", missing_art) is False

real_max = gui.THUMBNAIL_MAX_BYTES
gui.THUMBNAIL_MAX_BYTES = 64
try:
    assert gui.fetch_thumbnail(f"{image_host}/huge.jpg", missing_art) is False
finally:
    gui.THUMBNAIL_MAX_BYTES = real_max
assert not missing_art.exists(), "a failed fetch left a file behind"
assert not list(art_cache.glob("*.part")), sorted(p.name for p in art_cache.iterdir())

# An image is fetched once and then belongs to every later search, preview and
# scan naming the same video, so a cached one is never asked for again.
assert gui.youtube_art_path("thumbtest", art_cache) == str(landed)
assert gui.youtube_art_path("notfetched", art_cache) is None
assert gui.youtube_art_path("../escape", art_cache) is None
assert (
    gui.cache_thumbnail("thumbtest", "http://127.0.0.1:1/unreachable", art_cache)
    == str(landed)
), "a cached thumbnail was downloaded a second time"
assert gui.cache_thumbnail("../escape", f"{image_host}/thumb.jpg", art_cache) is None
assert gui.cache_thumbnail("second", f"{image_host}/thumb.jpg", art_cache) == str(
    art_cache / "yt-second.img"
)

# What connects a result's artwork to the track it becomes. Embedded art still
# wins: a file that carries its own cover is showing the album's, not the
# video's.
from_youtube = gui.Track(
    "/music/youtube/Queen/Bohemian [thumbtest].mp3", {}, gui.STATE_LIBRARY
)
assert from_youtube.art == str(landed), from_youtube.art
tagged_art = gui.Track(
    "/music/youtube/Queen/Bohemian [thumbtest].mp3",
    {"art": "/cache/art/abc.img"},
    gui.STATE_LIBRARY,
)
assert tagged_art.art == "/cache/art/abc.img", tagged_art.art
assert gui.Track("/music/ripped/track.mp3", {}, gui.STATE_LIBRARY).art is None
# The bar names a preview before it has a file at all, and that must not send
# an empty path looking for artwork.
assert gui.Track("", {"title": "Fetching"}, gui.STATE_PREVIEW).art is None


class ArtWindow:
    """The parts of the window a search's thumbnail pass touches."""

    _start_thumbnail_fetch = gui.IpodWindow._start_thumbnail_fetch
    _finish_thumbnail_fetch = gui.IpodWindow._finish_thumbnail_fetch

    def __init__(self):
        self.search_generation = 7
        self.painted = 0
        self.refreshed = 0
        self.now_playing_updates = 0
        self.library = gui.LibraryIndex()
        self.player = gui.PreviewPlayer(lambda: None)

    def _paint_youtube_section(self):
        self.painted += 1

    def _refresh_current_view(self):
        self.refreshed += 1

    def _update_now_playing(self):
        self.now_playing_updates += 1


class ImmediateThread:
    """Runs the worker where it was started.

    The window hands its thumbnail pass to a thread it keeps no handle on, so
    there is nothing to join; running it inline is what makes the check
    deterministic rather than timed.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def result_with_art(video_id, path="/thumb.jpg"):
    return gui.SearchResult(
        video_id, "Uploader", 0, f"https://youtu.be/{video_id}", video_id,
        f"{image_host}{path}",
    )


real_thread, real_idle = gui.threading.Thread, gui.GLib.idle_add
gui.threading.Thread = ImmediateThread
gui.GLib.idle_add = lambda callback, *args: callback(*args)
try:
    fetching = ArtWindow()
    fetching._start_thumbnail_fetch(
        fetching.search_generation,
        [result_with_art("arrived"), result_with_art("alsohere")],
    )
    assert (art_cache / "yt-arrived.img").is_file()
    assert (art_cache / "yt-alsohere.img").is_file()
    # One repaint for the set rather than one per image: rebuilding the rows
    # under the pointer would flicker the hover state of a button the user may
    # already be reaching for.
    assert fetching.painted == 1, fetching.painted

    # Artwork that arrives for a query the user has already typed past must not
    # repaint the results of the one they are looking at now.
    stale_art = ArtWindow()
    stale_preview = gui.Track(
        "/cache/Queen/Preview [stale].opus", {}, gui.STATE_PREVIEW
    )
    playing_preview = gui.Track(
        "/cache/Queen/Preview [stale].opus", {}, gui.STATE_PREVIEW
    )
    assert stale_preview.art is None
    assert playing_preview.art is None
    stale_art.library.previews = [stale_preview]
    stale_art.player.track = playing_preview
    stale_art.player.queue = [playing_preview]
    stale_art._start_thumbnail_fetch(
        stale_art.search_generation - 1, [result_with_art("stale")]
    )
    assert (art_cache / "yt-stale.img").is_file()
    assert stale_art.painted == 0, stale_art.painted
    assert stale_preview.art == str(art_cache / "yt-stale.img"), stale_preview.art
    assert playing_preview.art == str(
        art_cache / "yt-stale.img"
    ), playing_preview.art
    assert stale_art.refreshed == 1, stale_art.refreshed
    assert stale_art.now_playing_updates == 1, stale_art.now_playing_updates

    # Nothing to fetch is not a repaint either: the rows already painted are
    # showing the cached artwork.
    cached_only = ArtWindow()
    cached_only._start_thumbnail_fetch(cached_only.search_generation, [result_with_art("arrived")])
    assert cached_only.painted == 0, cached_only.painted

    # A result whose artwork cannot be fetched keeps its placeholder, and the
    # rows are left alone rather than being rebuilt to show the same thing.
    unreachable = ArtWindow()
    unreachable._start_thumbnail_fetch(
        unreachable.search_generation, [result_with_art("nothing", "/gone.jpg")]
    )
    assert unreachable.painted == 0, unreachable.painted
    assert not (art_cache / "yt-nothing.img").exists()
finally:
    gui.threading.Thread = real_thread
    gui.GLib.idle_add = real_idle
    images.shutdown()


def stub_yt_dlp(script):
    """A yt-dlp stand-in, so no test here depends on the network."""
    path = Path(tempfile.mkdtemp()) / "yt-dlp"
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


original_lib_output = gui.lib_function_output
found_line = json.dumps({"id": "abc", "title": "Found", "duration": 12})
for script, expected_results, expected_reached, why in (
    (f"echo '{found_line}'", ["Found"], True, "a working search"),
    # Exit 0 having printed nothing is how yt-dlp reports no matches, and
    # exit 1 is how it reports not reaching YouTube at all. Collapsing the two
    # makes the user retype a query that was perfectly fine.
    ("exit 0", [], True, "a search with no matches"),
    ("echo 'ERROR: unable to download' >&2; exit 1", [], False, "an offline search"),
):
    gui.lib_function_output = lambda _name, s=script: stub_yt_dlp(s)
    try:
        found, reached = gui.search_youtube("anything", timeout=20)
    finally:
        gui.lib_function_output = original_lib_output
    assert [r.title for r in found] == expected_results, why
    assert reached is expected_reached, why

# With no yt-dlp at all there is nothing to run, and that is a failure to
# reach YouTube rather than an empty result set.
gui.lib_function_output = lambda _name: None
try:
    missing_found, missing_reached = gui.search_youtube("anything")
finally:
    gui.lib_function_output = original_lib_output
assert missing_found == [] and missing_reached is False

# Searching survives what downloading cannot. yt-dlp reads metadata without
# ffmpeg and without a JavaScript runtime; only the media URL is signed.
original_succeeds = gui.lib_function_succeeds
original_which = gui.shutil.which
gui.lib_function_succeeds = lambda name: name == "yt_dlp_bin"
gui.shutil.which = lambda _name: None
try:
    assert gui.youtube_search_unavailable_reason() is None, "search over-gated"
    download_reason = gui.youtube_unavailable_reason()
finally:
    gui.lib_function_succeeds = original_succeeds
    gui.shutil.which = original_which
assert download_reason and "ffmpeg" in download_reason, download_reason

gui.lib_function_succeeds = lambda _name: False
try:
    assert "yt-dlp" in (gui.youtube_search_unavailable_reason() or "")
    assert gui.preview_unavailable_reason() == (
        "GStreamer is not installed - see Preview playback in the README"
    )
finally:
    gui.lib_function_succeeds = original_succeeds

# The local half. Every word has to match, in any order, across title, artist
# and album, because a phrase match would need whatever order the tagger used.
search_library = [
    gui.Track(
        "/music/queen/bohemian.mp3",
        {"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night At The Opera"},
        gui.STATE_LIBRARY,
    ),
    gui.Track(
        "/music/queen/love.mp3",
        {"title": "Somebody To Love", "artist": "Queen", "album": "A Day At The Races"},
        gui.STATE_IPOD,
    ),
    gui.Track(
        "/music/other/rain.mp3",
        {"title": "Rain", "artist": "Someone Else", "album": "Weather"},
        gui.STATE_LIBRARY,
    ),
]
assert [t.title for t in gui.local_search_matches(search_library, "queen rhapsody")] == [
    "Bohemian Rhapsody"
]
assert [t.title for t in gui.local_search_matches(search_library, "QUEEN")] == [
    "Somebody To Love",
    "Bohemian Rhapsody",
], "artist matches were not ordered by album"
# A track that lives only on the device is still findable, or music copied
# from another machine would be invisible to the one field that searches.
assert gui.local_search_matches(search_library, "races")[0].state == gui.STATE_IPOD
assert gui.local_search_matches(search_library, "   ") == []
assert gui.local_search_matches(search_library, "queen rain") == []

# Adding a result runs the same download the dialog does, and refuses in every
# case where it could not finish.
result_window = FakeWindow()
found_result = gui.SearchResult(
    title="Bohemian Rhapsody",
    uploader="Queen Official",
    duration=360.0,
    url=link,
    video_id="abc",
)
result_window._set_search_note = lambda text: result_window.notes.append(text)
result_window.notes = []
gui.IpodWindow._download_result(result_window, found_result)
result_fetch = result_window.commands[-1]
assert result_fetch[0].endswith("ipod-fetch.sh"), result_fetch
assert result_fetch[-1] == link, result_fetch
# A search result is one video. Without this a hit that carries a list=
# parameter would download the whole playlist behind it.
assert "--single" in result_fetch, result_fetch
assert "Bohemian Rhapsody" in result_window.busy_messages[-1], result_window.busy_messages
result_list = Path(result_fetch[result_fetch.index("--new-tracks") + 1])
assert result_list.exists(), "the download had no list file to report into"

for attribute, value, why in (
    ("mount_point", None, "no iPod connected"),
    ("device_identity", None, "an iPod that has not been identified"),
    ("busy", True, "a script already running"),
    ("discovering_sources", True, "a queue still being scanned"),
    ("youtube_unavailable", "ffmpeg is not installed", "a missing dependency"),
):
    refusing = FakeWindow()
    refusing._set_search_note = lambda _text: None
    setattr(refusing, attribute, value)
    assert not gui.IpodWindow._can_download(refusing), why
    gui.IpodWindow._download_result(refusing, found_result)
    assert refusing.commands == [], why

# A download refused before it started must not leave its list file behind.
refused_run = FakeWindow()
refused_run._run = lambda *_a, **_k: False
refused_fetch = gui.IpodWindow._start_youtube_download(
    refused_run, link, single=True, busy_message="Downloading"
)
refused_list = Path(refused_fetch[refused_fetch.index("--new-tracks") + 1])
assert not refused_list.exists(), "a refused download left its list file behind"


class FailureWindow:
    """Enough of the window for _finish to report a failure against."""

    def __init__(self):
        self.sync_total = 1
        self.toasts = []
        self.failures = 0
        self.details_toggle = FakeWidget()
        self.sync_revealer = FakeWidget()

    def _set_busy(self, _busy):
        pass

    def _toast(self, message):
        self.toasts.append(message)

    def refresh(self):
        pass

    def _rescan_library(self):
        pass


# A download that dies part-way has to say so where the user is looking, which
# is the row they pressed Add on; the toast has gone by the time they look back.
failure_window = FailureWindow()
gui.IpodWindow._finish(
    failure_window,
    1,
    "Downloaded",
    on_failure=lambda: setattr(
        failure_window, "failures", failure_window.failures + 1
    ),
)
assert failure_window.failures == 1, "a failed download reported nothing inline"
assert failure_window.details_toggle.active, "a failure left Details closed"
assert failure_window.sync_revealer.revealed, "a failure hid the script output"
assert failure_window.toasts == [], "an inline failure also raised a toast"

generic_failure_window = FailureWindow()
gui.IpodWindow._finish(generic_failure_window, 2, "Finished")
assert generic_failure_window.toasts == ["Failed (exit 2) - see Details"]

# Success must not fire it, or every finished download would claim to have
# failed as well.
success_window = FailureWindow()
gui.IpodWindow._finish(
    success_window,
    0,
    "Downloaded",
    on_failure=lambda: setattr(
        success_window, "failures", success_window.failures + 1
    ),
)
assert success_window.failures == 0, "a successful download reported a failure"


class SearchEntry:
    def __init__(self, text=""):
        self.text = text

    def get_text(self):
        return self.text

    def set_text(self, value):
        self.text = value


class SearchWindow:
    """Enough of the window to drive the search without a display."""

    current_view = gui.IpodWindow.current_view

    def __init__(self, unavailable=None):
        self.search_entry = SearchEntry()
        self.search_query = ""
        self.search_generation = 0
        self.search_results = []
        self.search_loading = False
        self.search_note = ""
        self.search_add_buttons = []
        self._search_timeout = None
        self.youtube_search_unavailable = unavailable
        self.views = VisibleView("library")
        self.shown = []
        self.painted = 0
        self.artwork_wanted = []

    def show_view(self, name):
        self.shown.append(name)
        self.views.name = name

    def _paint_local_results(self):
        self.painted += 1

    def _paint_youtube_section(self):
        pass

    def _start_youtube_search(self, _generation, _query):
        # Never reached without a main loop; named so scheduling it does not
        # depend on the network being there.
        return False

    def _start_thumbnail_fetch(self, _generation, results):
        # The pass itself is driven separately, against a local server. Here it
        # only has to exist, so that landing results never reaches the network.
        self.artwork_wanted.append([result.video_id for result in results])

    _on_search_changed = gui.IpodWindow._on_search_changed
    _clear_search = gui.IpodWindow._clear_search
    _cancel_search_timeout = gui.IpodWindow._cancel_search_timeout
    _set_search_note = gui.IpodWindow._set_search_note
    _finish_youtube_search = gui.IpodWindow._finish_youtube_search
    _navigate = gui.IpodWindow._navigate


typing = SearchWindow()
typing.search_entry.set_text("bohemian")
typing._on_search_changed(typing.search_entry)
assert typing.views.name == "search", typing.shown
# The library is already in memory, so it filters on the keystroke rather than
# waiting for the network half.
assert typing.painted == 1, typing.painted
# YouTube costs a round trip, so it is scheduled rather than run.
assert typing._search_timeout is not None, "the YouTube search was not deferred"
scheduled = typing.search_generation

typing.search_entry.set_text("bohemian r")
typing._on_search_changed(typing.search_entry)
assert typing.search_generation != scheduled, "a keystroke reused a stale generation"

# A result for a query the user has moved on from must not land.
typing._finish_youtube_search(scheduled, [found_result], True)
assert typing.search_results == [], "a stale search overwrote the current one"
typing._finish_youtube_search(typing.search_generation, [found_result], True)
assert typing.search_results == [found_result]
assert typing.search_note == "", typing.search_note
assert not typing.search_loading
# Artwork is asked for once the rows are up, so the titles are not held back
# behind a set of images. A stale result asks for nothing at all.
assert typing.artwork_wanted == [[found_result.video_id]], typing.artwork_wanted

# Reaching YouTube and finding nothing is not the same as not reaching it, and
# each says so in the section rather than in a toast.
typing._finish_youtube_search(typing.search_generation, [], True)
assert "No YouTube results" in typing.search_note, typing.search_note
typing._finish_youtube_search(typing.search_generation, [], False)
assert "Could not reach YouTube" in typing.search_note, typing.search_note

# Emptying the field puts the library back rather than leaving stale results
# behind a field that no longer explains them.
typing.search_entry.set_text("")
typing._on_search_changed(typing.search_entry)
assert typing.views.name == "library", typing.shown
assert typing.search_results == [] and typing.search_query == ""
assert typing._search_timeout is None, "a search stayed scheduled after clearing"

# One letter matches most of a library and would spend a round trip per
# keystroke, so it says so instead of searching.
brief = SearchWindow()
brief.search_entry.set_text("q")
brief._on_search_changed(brief.search_entry)
assert brief._search_timeout is None, "a one-letter query searched YouTube"
assert "Type a little more" in brief.search_note, brief.search_note
assert brief.painted == 1, "the library was not searched for a short query"

# With no yt-dlp the YouTube half says so in place of its rows, and the local
# half carries on: gating the whole field on it would blank a working search.
ungated = SearchWindow(unavailable="yt-dlp is not installed - run ./install.sh")
ungated.search_entry.set_text("bohemian")
ungated._on_search_changed(ungated.search_entry)
assert ungated._search_timeout is None, "a search ran without yt-dlp"
assert "yt-dlp is not installed" in ungated.search_note, ungated.search_note
assert "still searched" in ungated.search_note, ungated.search_note
assert ungated.painted == 1, "the local half was gated on the remote half"

# Following a sidebar row ends the search, or the next keystroke would reopen a
# view the user had just navigated away from.
navigating = SearchWindow()
navigating.search_entry.set_text("bohemian")
navigating._on_search_changed(navigating.search_entry)
navigating._navigate("playlists")
assert navigating.views.name == "playlists", navigating.shown
assert navigating.search_entry.get_text() == "", "the field kept a spent query"
assert navigating.search_query == "" and navigating.search_results == []

# --------------------------------------------------------------- staged sync
#
# Adding queues a track rather than copying it, so the command that finally
# runs has to name every queued path and nothing else. Copying the whole
# library instead would fill a 2GB device from a single click.
queue_window = FakeWindow()
queue_window.sync_total = 0
sync_source = Path(tempfile.mkdtemp()) / "Music"
sync_source.mkdir()
queued_paths = {
    str(path): gui.Track(
        path,
        {"title": Path(path).stem, "size": size},
        gui.STATE_LIBRARY,
    )
    for path, size in (
        (sync_source / "01 Nightbus.mp3", 1024),
        (sync_source / "-Dashed Title.mp3", 2048),
    )
}
for path, track in queued_paths.items():
    Path(path).write_bytes(b"x" * track.size)
copied_path = sync_source / "02 Dawn.mp3"
copied_path.write_bytes(b"x" * 4096)
already_copied = gui.Track(
    copied_path,
    {"title": "Dawn", "size": 4096},
    gui.STATE_LIBRARY,
)
already_copied.on_ipod = True
queue_window.library.tracks = [*queued_paths.values(), already_copied]
queue_window.pending = {*queued_paths, already_copied.path}
queue_window.pending_sources = {
    str(sync_source): set(queue_window.pending)
}
queue_window.pending_device_identity = queue_window.device_identity
queue_window._merge_states()
assert queue_window._pending_change_count() == len(queued_paths)
assert sum(track.size for track in queue_window._pending_copy_tracks()) == 3072
replacement = gui.Track(
    already_copied.path,
    {"title": "Dawn", "size": 4096},
    gui.STATE_LIBRARY,
)
queue_window.library.tracks = [*queued_paths.values(), replacement]
queue_window._merge_states()
assert queue_window._pending_change_count() == len(queued_paths) + 1
assert sum(track.size for track in queue_window._pending_copy_tracks()) == 7168
added_after_queue = sync_source / "03 Added Later.mp3"
added_after_queue.write_bytes(b"x" * 512)
linked_during_sync = sync_source / "04 Linked.mp3"
linked_during_sync.symlink_to(exact_track)
# A source folder whose only remaining track is a link. It used to drop
# out of the sync entirely; now it is a source like any other.
linked_source = Path(tempfile.mkdtemp()) / "Only Links"
linked_source.mkdir()
removed_before_sync = linked_source / "Removed.mp3"
removed_before_sync.write_bytes(b"removed")
removed_track = gui.Track(
    removed_before_sync,
    {"title": "Removed", "size": len(b"removed")},
    gui.STATE_LIBRARY,
)
queue_window.library.tracks.append(removed_track)
queue_window.pending.add(removed_track.path)
queue_window.pending_sources[str(linked_source)] = {removed_track.path}
queue_window._merge_states()
removed_before_sync.unlink()
linked_only_link = linked_source / "Only Link.mp3"
linked_only_link.symlink_to(exact_track)
command_ready = threading.Event()
record_command = queue_window._run


def record_preflight_command(argv, busy_message, done_message, then=None, clear=True):
    record_command(argv, busy_message, done_message, then, clear)
    command_ready.set()


queue_window._run = record_preflight_command
original_volume_identity = gui.volume_identity
original_glib = gui.GLib
gui.volume_identity = lambda _mount: queue_window.device_identity
gui.GLib = type(
    "ImmediateGLib",
    (),
    {"idle_add": staticmethod(lambda callback, *args: callback(*args))},
)
try:
    gui.IpodWindow.on_sync_pending(queue_window, None)
    assert command_ready.wait(5), "queued sources were not re-read before sync"
finally:
    gui.volume_identity = original_volume_identity
    gui.GLib = original_glib

staged = queue_window.commands[0]
assert staged[0].endswith("ipod-sync.sh"), staged
assert staged[1:3] == ["--ipod", queue_window.mount_point], staged
# Everything after -- is a path, because a track title can begin with a dash.
separator = staged.index("--")
assert staged[separator + 1:] == sorted(
    [str(sync_source), str(linked_source)]
), staged
# The re-read before a sync sees the links, because the copy will. The
# folder that holds nothing but a link stays a source, and the link that
# appeared inside an already-queued folder joins the queue.
assert queue_window.sync_total == len(queued_paths) + 4, queue_window.sync_total
assert str(added_after_queue) in queue_window.pending
assert str(linked_during_sync) in queue_window.pending
assert str(linked_only_link) in queue_window.pending
assert str(linked_source) in queue_window.pending_sources

# The queue is only cleared once the copy has actually succeeded, which is
# what the then callback is for.
assert queue_window.pending == {
    *queued_paths,
    already_copied.path,
    str(added_after_queue),
    str(linked_during_sync),
    str(linked_only_link),
}, "queue emptied before the sync ran"
cleared = queue_window.then()
assert queue_window.pending == set(), "queue survived a successful sync"
assert queue_window.pending_sources == {}, "sync sources survived a successful sync"
assert queue_window.pending_device_identity is None, "queue stayed device-bound"
assert isinstance(cleared, str), cleared

def sync_pending_with(window):
    """Press Sync, then run the scan's answer on this thread.

    The scan runs on a worker that posts its result back through GLib. Letting
    that through inline would run the answer - the toast, the queue it rebuilds
    and the command it launches - on the worker, while this thread is already
    reading them. Recorded and called here instead, so nothing is asserted
    while another thread is still writing it.
    """
    landed = []
    arrived = threading.Event()

    def record_idle(callback, *args):
        landed.append((callback, args))
        arrived.set()
        return 1

    original_volume_identity = gui.volume_identity
    original_glib = gui.GLib
    gui.volume_identity = lambda _mount: window.device_identity
    gui.GLib = type(
        "RecordingGLib", (), {"idle_add": staticmethod(record_idle)}
    )
    try:
        gui.IpodWindow.on_sync_pending(window, None)
        assert arrived.wait(5), "the source scan before a sync never reported"
        for callback, args in landed:
            callback(*args)
    finally:
        gui.volume_identity = original_volume_identity
        gui.GLib = original_glib


# A queued source that has gone - a folder on a stick that was unplugged, a
# playlist another program deleted - is dropped rather than failing the re-read
# of every source. Failing it would leave the source staged and cancel every
# press of Sync after it for the rest of the session, naming nothing the user
# could go and put right.
failed_sync = FakeWindow()
failed_member = "/missing/source/song.mp3"
failed_sync.pending = {failed_member}
failed_sync.pending_sources = {"/missing/source": {failed_member}}
failed_sync.pending_device_identity = failed_sync.device_identity
sync_pending_with(failed_sync)
assert failed_sync.commands == [], failed_sync.commands
assert not failed_sync.busy
assert failed_sync.pending_sources == {}, failed_sync.pending_sources
# Dropped is said, not merely done: what went is the part of what the user
# staged that will not happen.
assert (
    failed_sync.toasts[-1] == "Dropped 1 queued source with nothing left to sync"
), failed_sync.toasts

# A sync that loses one source and keeps another still runs, and still says
# what it lost - said here rather than folded into the sync's own message,
# which _clear_pending replaces on success and a non-zero exit never shows.
partial_sync = FakeWindow()
kept_source = Path(tempfile.mkdtemp(prefix="kept-source-")) / "Kept.mp3"
kept_source.write_bytes(b"kept")
partial_sync.pending = {str(kept_source), failed_member}
partial_sync.pending_sources = {
    str(kept_source): {str(kept_source)},
    "/missing/source": {failed_member},
}
partial_sync.pending_device_identity = partial_sync.device_identity
sync_pending_with(partial_sync)
assert partial_sync.commands, "a sync with one source left never ran"
assert str(kept_source) in partial_sync.commands[-1], partial_sync.commands[-1]
assert "/missing/source" not in partial_sync.commands[-1], partial_sync.commands[-1]
assert (
    partial_sync.toasts[-1] == "Dropped 1 queued source with nothing left to sync"
), partial_sync.toasts
# The sync's own message says what it did, and nothing more: it is replaced by
# what _clear_pending returns the moment the copy succeeds.
assert partial_sync.done_messages[-1].endswith("synced"), partial_sync.done_messages

# A folder that is still there but has been emptied by hand is the same news:
# the queue is rebuilt without it either way, so the user hears about it.
emptied_sync = FakeWindow()
emptied_folder = Path(tempfile.mkdtemp(prefix="emptied-source-"))
emptied_sync.pending = {str(kept_source)}
emptied_sync.pending_sources = {
    str(kept_source): {str(kept_source)},
    str(emptied_folder): set(),
}
emptied_sync.pending_device_identity = emptied_sync.device_identity
sync_pending_with(emptied_sync)
assert emptied_sync.commands, "a sync with one source left never ran"
assert str(emptied_folder) not in emptied_sync.commands[-1], (
    emptied_sync.commands[-1]
)
assert (
    emptied_sync.toasts[-1] == "Dropped 1 queued source with nothing left to sync"
), emptied_sync.toasts

# One that is there but cannot be read is the other thing entirely, and still
# cancels: syncing around it would copy a queue the user never approved.
unreadable_sync = FakeWindow()
unreadable_source = Path(tempfile.mkdtemp(prefix="unreadable-")) / "notes.txt"
unreadable_source.write_text("not a source this can read", encoding="utf-8")
unreadable_sync.pending = {str(unreadable_source)}
unreadable_sync.pending_sources = {str(unreadable_source): {str(unreadable_source)}}
unreadable_sync.pending_device_identity = unreadable_sync.device_identity
sync_pending_with(unreadable_sync)
assert unreadable_sync.commands == [], unreadable_sync.commands
assert not unreadable_sync.busy
assert "cancelled" in unreadable_sync.toasts[-1], unreadable_sync.toasts
assert str(unreadable_source) in unreadable_sync.pending_sources, (
    "a source that is there but unreadable was dropped from the queue"
)

outside_window = FakeWindow()
outside_track = gui.Track(
    "/outside/Album/Song.mp3",
    {
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "duration": 120,
        "size": 8192,
    },
    gui.STATE_LIBRARY,
)
outside_window.device_tracks = [
    gui.Track(
        "/media/iPod/iPod_Control/Music/F00/ABCD.mp3",
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "duration": 120,
        },
        gui.STATE_IPOD,
        relpath="F00/ABCD.mp3",
    )
]
outside_window._queue_sources(
    {"/outside/Album": [outside_track]}, metadata_complete=True
)
assert outside_window._pending_accounting()[1:] == (0, 0)


class RunGuardWindow:
    _device_command_is_current = gui.IpodWindow._device_command_is_current

    def __init__(self):
        self.toasts = []
        self.mount_point = "/media/iPod"
        self.device_identity = "uuid:expected"

    def _toast(self, message):
        self.toasts.append(message)

    def _clear_log(self):
        raise AssertionError("invalid command cleared the log")

    def _set_busy(self, *_args):
        raise AssertionError("invalid command made the window busy")


run_guard = RunGuardWindow()
started = gui.IpodWindow._run(
    run_guard,
    ["ipod-sync.sh", "--ipod", None],
    "Running",
    "Done",
)
assert started is False
assert run_guard.toasts == ["Connect an iPod before running this action"]

run_guard = RunGuardWindow()
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: "uuid:replacement"
try:
    started = gui.IpodWindow._run(
        run_guard,
        ["ipod-sync.sh", "--ipod", run_guard.mount_point, "--rebuild-only"],
        "Running",
        "Done",
    )
finally:
    gui.volume_identity = original_volume_identity
assert started is False
assert "changed" in run_guard.toasts[-1]


class SerializedRunWindow:
    _device_command_is_current = gui.IpodWindow._device_command_is_current

    def __init__(self):
        self.mount_point = "/media/iPod"
        self.device_identity = "uuid:expected"
        self.probe_generation = 0
        self.busy = False
        self.toasts = []
        self.finished = threading.Event()

    def _toast(self, message):
        self.toasts.append(message)

    def _clear_log(self):
        pass

    def _set_busy(self, busy, _message=""):
        if busy:
            self.probe_generation += 1
        self.busy = busy

    def _cancel_device_command(self):
        raise AssertionError("current command was cancelled")

    def _log(self, _line):
        pass

    def _finish(self, *_args):
        self.finished.set()


class CompletedProcess:
    stdout = ()

    def wait(self):
        return 0


serialized_run = SerializedRunWindow()
probe_entered = threading.Event()
release_probe = threading.Event()
process_started = threading.Event()
probe_generation = serialized_run.probe_generation


def blocking_count(_mount_point, cancelled=None):
    probe_entered.set()
    release_probe.wait(5)
    return 0


original_find_ipods = gui.find_ipods
original_volume_identity = gui.volume_identity
original_saved_sync_options = gui.saved_sync_options
original_list_playlists = gui.list_playlists
original_spoken_playlists = gui.spoken_playlists
original_count_tracks = gui.count_tracks
original_resolve_device = gui.resolve_device
original_popen = gui.subprocess.Popen
original_idle_add = gui.GLib.idle_add
gui.find_ipods = lambda: [serialized_run.mount_point]
gui.volume_identity = lambda _mount: serialized_run.device_identity
gui.saved_sync_options = lambda _mount: (0, [], False, False)
gui.list_playlists = lambda _mount: []
gui.spoken_playlists = lambda _mount: set()
gui.count_tracks = blocking_count
gui.resolve_device = lambda mount, identity, require_block=False: gui.DeviceHandle(
    mount, identity, "/dev/sdz"
)


def locked_popen(*_args, **_kwargs):
    process_started.set()
    return CompletedProcess()


gui.subprocess.Popen = locked_popen
gui.GLib.idle_add = lambda callback, *args: callback(*args)
probe_thread = threading.Thread(
    target=lambda: gui.probe_device(
        cancelled=lambda: probe_generation != serialized_run.probe_generation
    )
)
try:
    probe_thread.start()
    assert probe_entered.wait(5), "probe did not reach the device walk"
    failsafe = threading.Timer(2, release_probe.set)
    failsafe.start()
    began = time.monotonic()
    started = gui.IpodWindow._run(
        serialized_run,
        [
            "ipod-sync.sh",
            "--ipod",
            serialized_run.mount_point,
            "--rebuild-only",
        ],
        "Running",
        "Done",
    )
    returned_after = time.monotonic() - began
    assert started is True
    assert returned_after < 1, returned_after
    assert not process_started.is_set(), "command overlapped the device probe"
    failsafe.cancel()
    release_probe.set()
    assert process_started.wait(5), "command did not start after the probe"
    assert serialized_run.finished.wait(5), "command worker did not finish"
finally:
    release_probe.set()
    probe_thread.join(5)
    gui.find_ipods = original_find_ipods
    gui.volume_identity = original_volume_identity
    gui.saved_sync_options = original_saved_sync_options
    gui.list_playlists = original_list_playlists
    gui.spoken_playlists = original_spoken_playlists
    gui.count_tracks = original_count_tracks
    gui.resolve_device = original_resolve_device
    gui.subprocess.Popen = original_popen
    gui.GLib.idle_add = original_idle_add


class EjectGuardWindow:
    def __init__(self):
        self.mount_point = "/media/iPod"
        self.device_identity = "uuid:expected"
        self.toasts = []

    def _toast(self, message):
        self.toasts.append(message)

    def _set_busy(self, *_args):
        raise AssertionError("stale device began ejecting")


eject_guard = EjectGuardWindow()
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: "uuid:replacement"
try:
    gui.IpodWindow.on_eject(eject_guard, None)
finally:
    gui.volume_identity = original_volume_identity
assert eject_guard.toasts, "stale device eject failed silently"


class FinishWindow:
    def __init__(self):
        self.events = []
        self.sync_total = 1

    def _invalidate_device_snapshot(self):
        self.events.append("invalidate")

    def _set_busy(self, _busy):
        self.events.append("idle")

    def _toast(self, _message):
        self.events.append("toast")

    def refresh(self):
        self.events.append("refresh")

    def _rescan_library(self):
        self.events.append("library")


finish_window = FinishWindow()
gui.IpodWindow._finish(
    finish_window, 0, "Done", device_command=True
)
assert finish_window.events[0] == "invalidate", finish_window.events
assert finish_window.sync_total == 0

# An empty queue must not launch a script at all.
idle_window = FakeWindow()
idle_window.pending = set()
idle_window.pending_sources = {}
idle_window.sync_total = 0
gui.IpodWindow.on_sync_pending(idle_window, None)
assert idle_window.commands == [], idle_window.commands

# -------------------------------------------------------- playlist reordering
#
# A playlist's order is the only thing the user arranged by hand, so a reorder
# has to reach the device rather than living in the window. The entries under
# the music folder get their prefix back; anything hand-written is left as it
# was, because restoring the prefix blindly would break an absolute path.
reorder_root = Path(tempfile.mkdtemp())
(reorder_root / "iPod_Control" / "Music" / "F00").mkdir(parents=True)
for code in ("LDPX", "QMRT"):
    (reorder_root / "iPod_Control" / "Music" / "F00" / f"{code}.mp3").write_bytes(b"x")
absolute_entry = reorder_root / "absolute-and-existing.mp3"
absolute_entry.write_bytes(b"x")

m3u = reorder_root / "Morning Ride.m3u"
m3u.write_text(
    "iPod_Control/Music/F00/LDPX.mp3\n"
    "iPod_Control/Music/F00/QMRT.mp3\n"
    f"{absolute_entry}\n",
    encoding="utf-8",
)

assert gui.playlist_file(reorder_root, "Morning Ride") == m3u
assert gui.playlist_file(reorder_root, "Nonexistent") is None

parsed_order = dict(gui.list_playlists(reorder_root))["Morning Ride"]
reordered = [parsed_order[1], parsed_order[0], parsed_order[2]]
reorder_identity = "uuid:reorder"
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: reorder_identity
try:
    assert gui.write_playlist(
        reorder_root, reorder_identity, m3u, reordered
    )
finally:
    gui.volume_identity = original_volume_identity

written = [
    line
    for line in m3u.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
]
assert written == [
    "iPod_Control/Music/F00/QMRT.mp3",
    "iPod_Control/Music/F00/LDPX.mp3",
    str(absolute_entry),
], written
original = m3u.read_text(encoding="utf-8")
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: "uuid:replacement"
try:
    assert not gui.write_playlist(
        reorder_root, reorder_identity, m3u, parsed_order
    )
finally:
    gui.volume_identity = original_volume_identity
assert m3u.read_text(encoding="utf-8") == original
# The rewrite is atomic, so no half-written list can be left behind for the
# firmware to choke on if the device is pulled mid-write.
assert not list(reorder_root.glob(".*tmp")), list(reorder_root.glob(".*tmp"))

pls = reorder_root / "Gym.pls"
pls.write_text("[playlist]\nFile1=iPod_Control/Music/F00/LDPX.mp3\n", encoding="utf-8")
original_volume_identity = gui.volume_identity
gui.volume_identity = lambda _mount: reorder_identity
try:
    assert gui.write_playlist(
        reorder_root,
        reorder_identity,
        pls,
        ["F00/QMRT.mp3", "F00/LDPX.mp3"],
    )
finally:
    gui.volume_identity = original_volume_identity
pls_text = pls.read_text(encoding="utf-8")
assert "NumberOfEntries=2" in pls_text, pls_text
assert "File1=iPod_Control/Music/F00/QMRT.mp3" in pls_text, pls_text
assert "File2=iPod_Control/Music/F00/LDPX.mp3" in pls_text, pls_text

# ---------------------------------------------------------- column sorting
#
# Driven through Gtk.Sorter.compare so the comparison is invoked exactly as
# the column view invokes it, trailing user_data included. That argument is
# the whole point of the test: it used to land on a lambda default and
# replace the key function with None, so every sortable column raised
# TypeError and quietly did nothing.
def sortable(title):
    return gui.TrackItem(
        gui.Track("/tmp/x.mp3", {"title": title}, gui.STATE_LIBRARY), 1
    )


Gtk = gui.Gtk
by_title = gui.track_sorter(lambda track: track.title.lower())
assert by_title.compare(sortable("a"), sortable("b")) == Gtk.Ordering.SMALLER
assert by_title.compare(sortable("b"), sortable("a")) == Gtk.Ordering.LARGER
assert by_title.compare(sortable("a"), sortable("A")) == Gtk.Ordering.EQUAL

by_duration = gui.track_sorter(lambda track: track.duration)
short, long_ = sortable("short"), sortable("long")
short.track.duration, long_.track.duration = 10.0, 400.0
assert by_duration.compare(short, long_) == Gtk.Ordering.SMALLER

# Every sortable column must produce a usable sorter, not just the one above.
for _key, _title, _expand, sort_key in gui.TRACK_COLUMNS:
    if sort_key is None:
        continue
    built = gui.track_sorter(sort_key)
    assert built.compare(sortable("a"), sortable("b")) in (
        Gtk.Ordering.SMALLER,
        Gtk.Ordering.EQUAL,
        Gtk.Ordering.LARGER,
    ), _key

# ------------------------------------------------------ per-file sync output
#
# ipod-sync.sh prints one of these per file; the sync bar counts them. The
# destination can contain spaces, so the pattern must not stop at one.
progress = gui.COPIED_LINE.match("  + Harbour Light.mp3 -> F00/LDPX.mp3\n".rstrip())
assert progress, "per-file sync line no longer parses"
assert progress.group("name") == "Harbour Light.mp3", progress.group("name")
assert progress.group("dest") == "F00/LDPX.mp3", progress.group("dest")

spaced = gui.COPIED_LINE.match("  + A Song.mp3 -> Some Folder/B QRST.mp3")
assert spaced, "a destination containing a space did not parse"
assert spaced.group("dest") == "Some Folder/B QRST.mp3", spaced.group("dest")

# Aggregate lines are not per-file lines and must not be counted as copies.
for line in ("==> Copied 4 file(s)", "warning: Skipped 1 unsupported file(s)"):
    assert gui.COPIED_LINE.match(line) is None, line

# The shell script must still emit the format the bar parses.
sync_sh = (REPO / "ipod-sync.sh").read_text(encoding="utf-8")
assert "'  + %s -> %s\\n'" in sync_sh, "ipod-sync.sh stopped reporting each file"

# ------------------------------------------------------------ preview player
#
# Playback is driven from GStreamer messages that only arrive on a running main
# loop, and CI has no GStreamer at all, so the pipeline is a stand-in whose
# messages this file delivers by hand. What is being checked is the state
# machine the bar reads - which track, which state, whose queue - rather than
# whether GStreamer decodes, which is its own problem.


class FakeBus:
    def __init__(self):
        self.handlers = {}
        self.watched = False

    def add_signal_watch(self):
        self.watched = True

    def connect(self, name, handler):
        self.handlers[name] = handler

    def deliver(self, name, message):
        self.handlers[name](self, message)


class FakeError:
    def __init__(self, message):
        self.message = message


class FakeMessage:
    def __init__(self, src=None, state=None, error=None):
        self.src = src
        self.state = state
        self.error = error

    def parse_state_changed(self):
        return (None, self.state, None)

    def parse_error(self):
        return (self.error, "debug detail")


class FakePipeline:
    def __init__(self):
        self.bus = FakeBus()
        self.properties = {}
        self.states = []
        self.seeks = []
        self.position = 0
        self.duration = 0

    def get_bus(self):
        return self.bus

    def set_property(self, name, value):
        self.properties[name] = value

    def set_state(self, state):
        self.states.append(state)

    def query_position(self, _format):
        return (True, self.position)

    def query_duration(self, _format):
        return (True, self.duration)

    def seek_simple(self, _format, _flags, position):
        self.seeks.append(position)
        self.position = position


class FakeFactory:
    @staticmethod
    def make(name, _instance_name=None):
        FakeGst.made.append(name)
        if name != "playbin3":
            return object()
        FakeGst.pipeline = FakePipeline()
        return FakeGst.pipeline


class FakeGst:
    SECOND = 1_000_000_000
    ElementFactory = FakeFactory
    made = []
    pipeline = None

    class Format:
        TIME = "time"

    class SeekFlags:
        FLUSH = 1
        KEY_UNIT = 2

    class State:
        NULL = "null"
        READY = "ready"
        PAUSED = "paused"
        PLAYING = "playing"

    @staticmethod
    def filename_to_uri(path):
        return f"file://{path}"


gui.gst = lambda: FakeGst


def preview_track(name, duration=180.0, state=None):
    return gui.Track(
        Path("/music") / f"{name}.mp3",
        {"title": name, "artist": "Someone", "album": "Somewhere",
         "duration": duration},
        state or gui.STATE_LIBRARY,
    )


repaints = []
player = gui.PreviewPlayer(lambda: repaints.append(True))
first, second = preview_track("First"), preview_track("Second", 90.0)
player.play([first, second], 0)

pipeline = FakeGst.pipeline
assert player.state == gui.PLAY_LOADING, player.state
assert player.track is first
assert player.queue == [first, second] and player.index == 0
assert pipeline.properties["uri"] == "file:///music/First.mp3", pipeline.properties
# NULL before the new URI, or playbin3 reuses the previous stream's decoders.
assert pipeline.states == ["null", "playing"], pipeline.states
# Video would open a second window for a preview the user asked to hear.
assert "video-sink" in pipeline.properties
assert pipeline.bus.watched, "nothing is listening for end of stream or errors"
assert repaints, "starting a track did not repaint the bar"
player.seek(0.5)
assert not pipeline.seeks, "a seek was sent before playback finished opening"

# Only the pipeline's own transition means audio is coming out; every element
# in it reports its own, and one of those arriving first would clear the
# loading state while the file was still being opened.
pipeline.bus.deliver(
    "message::state-changed", FakeMessage(src=object(), state=FakeGst.State.PLAYING)
)
assert player.state == gui.PLAY_LOADING, player.state
pipeline.bus.deliver(
    "message::state-changed", FakeMessage(src=pipeline, state=FakeGst.State.PLAYING)
)
assert player.state == gui.PLAY_PLAYING, player.state

# The poll is what moves the timeline, and a decoded duration replaces the one
# the tag claimed.
pipeline.position = 30 * FakeGst.SECOND
pipeline.duration = 200 * FakeGst.SECOND
assert player._tick() is True
assert player.position == 30.0 and player.duration == 200.0, (
    player.position, player.duration
)

# A seek takes effect on the bar immediately and is then left alone for a poll
# or two, because a pipeline that has to pre-roll answers with the old position
# first and the thumb springing back reads as the seek being refused.
player.seek(0.5)
assert pipeline.seeks == [100 * FakeGst.SECOND], pipeline.seeks
assert player.position == 100.0, player.position
pipeline.position = 30 * FakeGst.SECOND
for _ in range(gui.SEEK_SETTLE_POLLS):
    player._tick()
    assert player.position == 100.0, player.position
player._tick()
assert player.position == 30.0, player.position

# Seeking past either end is clamped rather than refused, since a drag to the
# very edge of the trough is a normal thing to do.
player.seek(1.4)
assert pipeline.seeks[-1] == 200 * FakeGst.SECOND, pipeline.seeks
player.seek(-0.2)
assert pipeline.seeks[-1] == 0, pipeline.seeks

# The end of a track moves to the next one in the list it was started from.
pipeline.bus.deliver("message::eos", FakeMessage())
assert player.track is second and player.index == 1, player.index
assert player.state == gui.PLAY_LOADING, player.state
assert pipeline.properties["uri"] == "file:///music/Second.mp3"

# The end of the queue stops rather than wrapping: a queue started from one
# album would otherwise play forever with nothing in the bar saying it looped.
pipeline.bus.deliver("message::eos", FakeMessage())
assert player.state == gui.PLAY_IDLE, player.state
assert player.track is None, player.track
assert pipeline.states[-1] == "null", pipeline.states

# Previous steps back only near the start of a track; later in it the gesture
# means "play this again", which is what every other player does.
player.play([first, second], 1)
player.position = 1.0
player.previous()
assert player.index == 0 and player.track is first, player.index
player.play([first, second], 1)
pipeline.bus.deliver(
    "message::state-changed", FakeMessage(src=pipeline, state=FakeGst.State.PLAYING)
)
pipeline.duration = 90 * FakeGst.SECOND
pipeline.position = int((gui.RESTART_WINDOW + 1) * FakeGst.SECOND)
player._tick()
assert player.position > gui.RESTART_WINDOW, player.position
seek_count = len(pipeline.seeks)
player.previous()
assert player.index == 1, "previous stepped back from the middle of a track"
assert len(pipeline.seeks) == seek_count + 1, pipeline.seeks
assert pipeline.seeks[-1] == 0, pipeline.seeks

# A file GStreamer cannot decode says so and stops, keeping the track so the
# bar can name what failed.
player.play([first], 0)
pipeline.bus.deliver(
    "message::error", FakeMessage(error=FakeError("Missing decoder for audio/x-flac"))
)
assert player.state == gui.PLAY_IDLE, player.state
assert player.error == "Missing decoder for audio/x-flac", player.error
assert player.track is first, "the bar lost the name of the track that failed"

# Starting the next track clears the previous failure.
player.play([second], 0)
assert player.error is None, player.error

# With no GStreamer at all the player says so rather than raising into a click
# handler, and nothing is left running behind the message.
gui.gst = lambda: None
silent = gui.PreviewPlayer(None)
silent.play([first], 0)
assert silent.state == gui.PLAY_IDLE, silent.state
assert silent.error == gui.GSTREAMER_UNAVAILABLE, silent.error
assert silent._pipeline is None, "a player with no GStreamer built a pipeline"
gui.gst = lambda: FakeGst

# ------------------------------------------------------------ now-playing bar


class BarWidget(FakeWidget):
    """One stand-in for every widget the bar repaints."""

    def __init__(self, text=None, *classes, **_kwargs):
        super().__init__()
        self.text = text
        self.classes = set(classes)
        self.children = []
        self.icon = None
        self.value = 0.0
        self.opacity = 1.0
        self.child_name = None

    def add_css_class(self, name):
        self.classes.add(name)

    def remove_css_class(self, name):
        self.classes.discard(name)

    def get_text(self):
        return self.text

    def get_first_child(self):
        return self.children[0] if self.children else None

    def append(self, child):
        self.children.append(child)

    def remove(self, child):
        self.children.remove(child)

    def set_size_request(self, _width, _height):
        pass

    def set_icon_name(self, name):
        self.icon = name

    def set_value(self, value):
        self.value = value

    def set_opacity(self, value):
        self.opacity = value

    def set_visible_child_name(self, name):
        self.child_name = name


class BarWindow:
    """Enough of the window for the bar to repaint without a display."""

    def __init__(self, unavailable=None):
        self.preview_unavailable = unavailable
        self._painted_art = gui.UNPAINTED
        self.playing_art = BarWidget()
        self.playing_title = BarWidget()
        self.playing_artist = BarWidget()
        self.playing_subtitle = BarWidget()
        self.playing_state_dot = BarWidget()
        self.playing_message = BarWidget()
        self.playing_stack = BarWidget()
        self.transport_buttons = {
            name: BarWidget() for name in ("previous", "play", "next")
        }
        self.seek_scale = BarWidget()
        self.seek_elapsed = BarWidget()
        self.seek_total = BarWidget()
        self.playing_status = BarWidget()
        self.preview_generation = 0
        self._preview_process = None
        self._preview_lock = threading.Lock()
        self._preview_closed = False
        self.player = gui.PreviewPlayer(self._update_now_playing)

    _update_now_playing = gui.IpodWindow._update_now_playing
    _playing_status = gui.IpodWindow._playing_status
    play_from = gui.IpodWindow.play_from
    _supersede_preview_fetch = gui.IpodWindow._supersede_preview_fetch
    _cancel_preview_fetch = gui.IpodWindow._cancel_preview_fetch
    _terminate_preview_process = staticmethod(
        gui.IpodWindow._terminate_preview_process
    )


# The bar builds artwork and labels as it repaints, which needs a display it
# does not have here. Patched for this section only; every other check in the
# file runs against the real helpers.
real_label, real_cover = gui.label, gui.make_cover
gui.label = BarWidget
gui.make_cover = lambda *_args, **_kwargs: BarWidget()

bar = BarWindow()
bar._update_now_playing()
assert bar.playing_title.get_text() == "Nothing playing"
assert "sf-dim" in bar.playing_title.classes
# The placeholder has to be painted on the very first repaint, which is a bar
# with nothing playing - the state a "has this changed?" guard reads as
# unchanged unless it starts from something no track can equal.
assert bar.playing_art.children, "the idle bar painted no placeholder artwork"
assert not bar.playing_subtitle.visible
assert bar.playing_stack.child_name == "transport"
assert bar.playing_stack.opacity < 1.0, "the idle transport was not dimmed"
assert not bar.transport_buttons["play"].sensitive
assert not bar.seek_scale.sensitive
# Nothing is playing, so the length of nothing is known exactly; "--:--" here
# reads as a fault rather than as an empty bar.
assert bar.seek_total.get_text() == "0:00", bar.seek_total.get_text()
assert bar.playing_status.get_text() == "Preview on this computer"

bar.player.play([first, second], 0)
assert bar.playing_title.get_text() == "First"
assert "sf-dim" not in bar.playing_title.classes
assert bar.playing_artist.get_text() == "Someone"
assert bar.playing_subtitle.visible
assert gui.STATE_LIBRARY in bar.playing_state_dot.classes
assert bar.playing_stack.opacity == 1.0
assert bar.transport_buttons["next"].sensitive, "a queued next track was not offered"
assert bar.playing_status.get_text() == "Opening…", bar.playing_status.get_text()
assert not bar.seek_scale.sensitive
placeholder_art = bar.playing_art.get_first_child()
first.art = "/cache/art/late-thumbnail.img"
bar._update_now_playing()
assert bar.playing_art.get_first_child() is not placeholder_art
# The tag's duration until the pipeline can be asked, rather than an empty
# timeline for the second it takes to find out.
assert bar.seek_total.get_text() == "3:00", bar.seek_total.get_text()

FakeGst.pipeline.bus.deliver(
    "message::state-changed",
    FakeMessage(src=FakeGst.pipeline, state=FakeGst.State.PLAYING),
)
assert bar.transport_buttons["play"].icon == "media-playback-pause-symbolic"
assert bar.playing_status.get_text() == "Preview on this computer"
bar.player.toggle()
assert bar.player.state == gui.PLAY_PAUSED
assert bar.player._poll is None
assert bar.transport_buttons["play"].icon == "media-playback-start-symbolic"

# Pressing play again while a track is still opening stops it, rather than
# doing nothing until the pipeline gets where it was already going. The
# transition that completes afterwards must not undo that.
bar.player.play([first], 0)
assert bar.player.state == gui.PLAY_LOADING
bar.player.toggle()
assert bar.player.state == gui.PLAY_PAUSED, bar.player.state
assert not bar.seek_scale.sensitive
seek_count = len(FakeGst.pipeline.seeks)
bar.player.seek(0.5)
assert len(FakeGst.pipeline.seeks) == seek_count
FakeGst.pipeline.bus.deliver(
    "message::state-changed",
    FakeMessage(src=FakeGst.pipeline, state=FakeGst.State.PAUSED),
)
assert bar.seek_scale.sensitive
FakeGst.pipeline.bus.deliver(
    "message::state-changed",
    FakeMessage(src=FakeGst.pipeline, state=FakeGst.State.PLAYING),
)
assert bar.player.state == gui.PLAY_PAUSED, "a paused track resumed itself"

# The last track of a queue offers no next, or the button would promise
# something pressing it cannot deliver.
bar.player.play([first, second], 1)
assert not bar.transport_buttons["next"].sensitive

# A previewed track is a download kept only so it could be heard, and the bar
# says so: mistaking one for a track already in the library is how a preview
# gets lost when the cache is pruned.
previewed = preview_track("Fetched", 120.0, gui.STATE_PREVIEW)

# Before any of that, the download itself. It takes seconds rather than the
# instant a local file takes, so the bar carries the wait - named, because a
# bar that stays idle for four seconds reads as a play button that did nothing.
bar.player.fetch(previewed)
assert bar.player.state == gui.PLAY_FETCHING, bar.player.state
assert bar.playing_title.get_text() == "Fetched"
assert bar.playing_status.get_text() == "Fetching preview…", bar.playing_status.text
# There is no pipeline yet, so there is nothing for the transport to do. Live
# buttons over a file that does not exist would be the one thing worse than
# waiting.
assert not bar.transport_buttons["play"].sensitive, "transport live while fetching"
assert not bar.transport_buttons["next"].sensitive
assert bar.playing_stack.opacity < 1.0, "the fetching transport was not dimmed"
assert not bar.seek_scale.sensitive
bar.player.toggle()
assert bar.player.state == gui.PLAY_FETCHING, "toggle disturbed a fetch"

# A download that never arrives says so where the track was named, and takes
# the queue with it: a play button offering a file that was never downloaded
# fails again on every press.
bar.player.fail(previewed, gui.PREVIEW_FAILED)
assert bar.playing_stack.child_name == "message"
assert "ipod-fetch.sh --update" in bar.playing_message.get_text()
assert bar.player.queue == [], bar.player.queue
assert not bar.transport_buttons["play"].sensitive

bar.player.play([previewed], 0)
# Opening, not fetching: the file is on disk by now, and this is the same
# second any library track takes to start.
assert bar.playing_status.get_text() == "Opening…", bar.playing_status.text
assert gui.STATE_PREVIEW in bar.playing_state_dot.classes
FakeGst.pipeline.bus.deliver(
    "message::state-changed",
    FakeMessage(src=FakeGst.pipeline, state=FakeGst.State.PLAYING),
)
assert bar.playing_status.get_text() == "Previewed - add to keep", (
    bar.playing_status.text
)

# A decoding failure is stated where the controls were, not in a toast that has
# gone by the time the eye returns to the bar that stopped.
FakeGst.pipeline.bus.deliver(
    "message::error", FakeMessage(error=FakeError("Missing decoder for audio/x-flac"))
)
assert bar.playing_stack.child_name == "message"
assert bar.playing_message.get_text() == "Missing decoder for audio/x-flac"
# The right-hand caption keeps quiet rather than competing with it in a column
# too narrow to hold the same sentence.
assert bar.playing_status.get_text() == ""

# No GStreamer means the reason replaces the transport permanently, rather than
# dead buttons that give no hint why pressing them does nothing.
missing = BarWindow(unavailable=gui.GSTREAMER_UNAVAILABLE)
missing._update_now_playing()
assert missing.playing_stack.child_name == "message"
assert missing.playing_message.get_text() == (
    "GStreamer is not installed - see Preview playback in the README"
)


class FakeModel:
    def __init__(self, tracks):
        self.items = [gui.TrackItem(track, n) for n, track in enumerate(tracks, 1)]

    def get_n_items(self):
        return len(self.items)

    def get_item(self, index):
        return self.items[index]


class FakeView:
    def __init__(self, tracks):
        self.model = FakeModel(tracks)

    def get_model(self):
        return self.model


# Playing a row queues the list it was clicked in, in the order it is displayed
# in: a next button that jumped to somewhere else in the library would be
# unusable in an album the user had just sorted.
third = preview_track("Third", 60.0)
view = FakeView([second, first, third])
queued = BarWindow()
queued.play_from(view, first)
assert queued.player.queue == [second, first, third]
assert queued.player.index == 1, queued.player.index

# A row clicked in a view that has already been repainted out from under it
# still plays, rather than refusing because its position no longer exists.
queued.play_from(view, preview_track("Vanished"))
assert len(queued.player.queue) == 1 and queued.player.index == 0

gui.label, gui.make_cover = real_label, real_cover


# ----------------------------------------------------------- preview cache
#
# Hearing a YouTube result downloads it, so previewing twenty songs would add
# twenty files to the music folder if the download went straight there. It
# goes to a prunable cache instead, and adding a previewed track is what moves
# it out. Both halves of that are checked against real files: a promotion that
# silently leaves the file in the cache loses it at the next prune.

preview_cache = Path(tempfile.mkdtemp())
preview_library = Path(tempfile.mkdtemp())
gui.PREVIEW_CACHE = preview_cache
gui.PREVIEW_INCOMING = preview_cache / ".incoming"
gui.YOUTUBE_LIBRARY = preview_library


def cache_file(relative, contents="audio", when=None):
    path = preview_cache / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    if when is not None:
        os.utime(path, (when, when))
    return path


# The preview form takes no --new-tracks: it downloads into a directory of its
# own, so what that directory holds afterwards is the report, and it works with
# a yt-dlp too old to say what it fetched.
preview_fetch = gui.fetch_command("https://youtu.be/abc", preview_cache)
assert preview_fetch[0].endswith("ipod-fetch.sh"), preview_fetch
assert preview_fetch[preview_fetch.index("--output") + 1] == str(preview_cache)
assert "--new-tracks" not in preview_fetch, preview_fetch
# Without this a link carrying a list= parameter previews a whole playlist.
assert "--single" in preview_fetch, preview_fetch
assert preview_fetch[-1] == "https://youtu.be/abc", preview_fetch

old = cache_file("Queen/Bohemian Rhapsody [fJ9rUzIMcZQ].mp3", "x" * 40, when=1_000)
new = cache_file("Nirvana/Lithium [abc123].mp3", "y" * 60, when=2_000)
cache_file("Sleeves/cover.jpg", "not audio")
cache_file(".incoming/tmp1/Half Downloaded [zzz].mp3", "partial")

entries = gui.preview_cache_entries(preview_cache)
assert [path for path, _size, _mtime in entries] == [old, new], entries
# A download still running lives under .incoming and is not a preview yet;
# counting it would put a half-written file in the grid and in the figures.
assert all(".incoming" not in str(path) for path, _s, _m in entries), entries

assert gui.cached_preview_path("abc123", preview_cache) == new
assert gui.cached_preview_path("fJ9rUzIMcZQ", preview_cache) == old
assert gui.cached_preview_path("", preview_cache) is None
assert gui.cached_preview_path("nosuchid", preview_cache) is None
# The id is matched as the text ipod-fetch.sh wrote into the filename, not as
# a glob: "[abc123]" read as a pattern is a character class matching a file
# named "1.mp3", and the cache would then hand back the wrong song.
assert gui.cached_preview_path("a", preview_cache) is None
assert gui.cached_preview_path("abc", preview_cache) is None

# Oldest first, and only as far as it takes to get back under the limit.
assert gui.prunable_previews(entries, 1000) == []
assert gui.prunable_previews(entries, 60) == [old]
assert gui.prunable_previews(entries, 0) == [old, new]
# Never what is playing: the bar would be left naming a file that is gone.
assert gui.prunable_previews(entries, 0, keep=[old]) == [new]

assert gui.promote_destination(new, preview_cache, preview_library) == (
    preview_library / "Nirvana" / "Lithium [abc123].mp3"
)
# A file that somehow sits outside the cache still lands somewhere sensible
# rather than raising on the way out of it.
assert gui.promote_destination(
    "/elsewhere/Stray.mp3", preview_cache, preview_library
) == preview_library / "Stray.mp3"


class PreviewWindow:
    """Enough of the window for the cache, its promotions and its card."""

    def __init__(self, mount_point="/media/alex/Alex's iPod"):
        self.mount_point = mount_point
        self.device_identity = "uuid:test-ipod" if mount_point else None
        self.busy = False
        self.discovering_sources = False
        self.source_generation = 0
        self.scan_generation = 0
        self.preview_generation = 0
        self._preview_process = None
        self._preview_lock = threading.Lock()
        self._preview_closed = False
        self._device_scan_active = False
        self._device_snapshot_ready = True
        self.pending = set()
        self.pending_sources = {}
        self.pending_records = {}
        self.pending_device_identity = None
        self._pending_track_index = {}
        self._library_by_path = {}
        self._library_scan_tracks = {}
        self.library = FakeLibrary()
        self.device_tracks = []
        self.toasts = []
        self.repaints = 0
        self.cache_figure = FakeWidget()
        self.cache_meter = FakeWidget()
        self.cache_clear = FakeWidget()
        self.preview_unavailable = None
        self.youtube_unavailable = None
        self.player = gui.PreviewPlayer(lambda: None)

    def _toast(self, message):
        self.toasts.append(message)

    def _refresh_current_view(self, scan_complete=True):
        self.repaints += 1

    def _request_refresh(self, scan_complete=False):
        # The real one waits out a coalescing interval on the main loop, which
        # these checks do not run. Painting straight away keeps them counting
        # repaints rather than counting timers.
        self._refresh_current_view(scan_complete=scan_complete)

    def _populate_device_summary(self):
        pass

    def _update_now_playing(self):
        pass

    def _update_device_controls(self):
        pass

    _populate_cache_card = gui.IpodWindow._populate_cache_card
    on_clear_cache = gui.IpodWindow.on_clear_cache
    _promote_preview = gui.IpodWindow._promote_preview
    _keep_preview = gui.IpodWindow._keep_preview
    _prune_preview_cache = gui.IpodWindow._prune_preview_cache
    _forget_empty_preview_folders = staticmethod(
        gui.IpodWindow._forget_empty_preview_folders
    )
    _finish_preview_fetch = gui.IpodWindow._finish_preview_fetch
    _fail_preview_fetch = gui.IpodWindow._fail_preview_fetch
    _apply_preview_scan = gui.IpodWindow._apply_preview_scan
    _supersede_preview_fetch = gui.IpodWindow._supersede_preview_fetch
    _cancel_preview_fetch = gui.IpodWindow._cancel_preview_fetch
    _terminate_preview_process = staticmethod(
        gui.IpodWindow._terminate_preview_process
    )
    _preview_track = gui.IpodWindow._preview_track
    preview_result = gui.IpodWindow.preview_result
    _preview_unavailable_reason = gui.IpodWindow._preview_unavailable_reason
    _merge_states = gui.IpodWindow._merge_states
    _queue_sources = gui.IpodWindow._queue_sources
    _commit_queue_sources = gui.IpodWindow._commit_queue_sources
    _prune_pending = gui.IpodWindow._prune_pending
    _pending_accounting = gui.IpodWindow._pending_accounting
    _pending_change_count = gui.IpodWindow._pending_change_count
    _pending_track = gui.IpodWindow._pending_track
    _record_for_track = staticmethod(gui.IpodWindow._record_for_track)


def previewed(path):
    return gui.Track(
        path,
        {
            "title": Path(path).stem,
            "artist": Path(path).parent.name,
            "size": Path(path).stat().st_size,
        },
        gui.STATE_PREVIEW,
    )


# A previewed track is in the grid like any other, which is what finally
# produces the third state the whole window already knows how to draw.
cache_window = PreviewWindow()
cache_window.library.previews = [previewed(old), previewed(new)]
assert len(cache_window.library.all_tracks()) == 2
assert {t.state for t in cache_window.library.all_tracks()} == {
    gui.STATE_PREVIEW
}

cache_window._populate_cache_card()
assert cache_window.cache_figure.get_text() == "100 B · 2 files", (
    cache_window.cache_figure.get_text()
)
assert cache_window.cache_clear.sensitive, "Clear offered nothing to clear"
# A run in progress must not be able to delete files out from under itself.
cache_window.busy = True
cache_window._populate_cache_card()
assert not cache_window.cache_clear.sensitive
cache_window.busy = False

# Adding a previewed track moves the file into the library and queues it, the
# same one press does for a track that was already there.
promote_window = PreviewWindow()
kept = previewed(new)
promote_window.library.previews = [kept]
promote_window._merge_states()
promote_window._promote_preview(kept)
destination = preview_library / "Nirvana" / "Lithium [abc123].mp3"
assert destination.is_file(), "the preview never reached the library"
assert not new.exists(), "the preview was copied rather than moved"
# The artist folder goes with the last file to leave it, or the cache fills up
# with empty folders nothing will ever clear.
assert not (preview_cache / "Nirvana").exists(), "an empty artist folder was left"
assert kept.state == gui.STATE_LIBRARY, kept.state
assert kept.path == str(destination), kept.path
assert promote_window.library.previews == [], promote_window.library.previews
assert [t.path for t in promote_window.library.tracks] == [str(destination)]
assert promote_window.pending == {str(destination)}, promote_window.pending
assert "queued for sync" in promote_window.toasts[-1], promote_window.toasts
assert str(preview_library) in promote_window.toasts[-1], promote_window.toasts

# Keeping a download is something you do to your own music folder, so it works
# with nothing plugged in - and then says only what it did.
detached = PreviewWindow(mount_point=None)
alone = previewed(cache_file("Pixies/Debaser [pix1].mp3", "z" * 20))
detached.library.previews = [alone]
detached._merge_states()
detached._promote_preview(alone)
assert (preview_library / "Pixies" / "Debaser [pix1].mp3").is_file()
assert detached.pending == set(), detached.pending
assert detached.toasts[-1] == f"Kept in {gui.home_relative(preview_library)}"

# The same video downloaded directly on an earlier day. The library copy is
# the one to keep; the cached duplicate is dropped rather than written over it.
existing = preview_library / "Queen" / "Bohemian Rhapsody [fJ9rUzIMcZQ].mp3"
existing.parent.mkdir(parents=True, exist_ok=True)
existing.write_text("the copy already kept", encoding="utf-8")
duplicate_window = PreviewWindow()
duplicate = previewed(old)
duplicate_window.library.previews = [duplicate]
duplicate_window._merge_states()
duplicate_window._promote_preview(duplicate)
assert existing.read_text(encoding="utf-8") == "the copy already kept"
assert not old.exists(), "the cached duplicate was left behind"
assert duplicate.path == str(existing), duplicate.path

# Pruned, or cleared from another window, between the row being drawn and the
# button being pressed.
missing_window = PreviewWindow()
gone = previewed(cache_file("Ghost/Vanished [g1].mp3"))
Path(gone.path).unlink()
missing_window.library.previews = [gone]
missing_window._promote_preview(gone)
assert missing_window.library.previews == []
assert "no longer in the cache" in missing_window.toasts[-1], missing_window.toasts

stale_scan = PreviewWindow()
stale_scan.scan_generation = 1
removed_during_scan = previewed(cache_file("Gone/Stale [old1].mp3"))
Path(removed_during_scan.path).unlink()
stale_scan._apply_preview_scan(1, [removed_during_scan])
assert stale_scan.library.previews == [], stale_scan.library.previews

# What a finished download does: index it, prune back under the limit, play it.
landing = PreviewWindow()
landed = cache_file("Blur/Song 2 [blur2].mp3", "b" * 30, when=3_000)
stale = cache_file("Old/Filler [fill1].mp3", "c" * 90, when=500)
landing.library.previews = [previewed(stale)]
real_limit = gui.PREVIEW_CACHE_LIMIT
gui.PREVIEW_CACHE_LIMIT = 50
landing._finish_preview_fetch(
    landing.preview_generation,
    str(landed),
    {"title": "Song 2", "artist": "Blur", "size": 30},
)
# The track that just arrived is over the limit only because it arrived, and
# dropping the file the user is waiting to hear is the one deletion they would
# notice.
assert landed.is_file(), "the preview being played was pruned"
assert not stale.exists(), "the oldest preview survived a full cache"
assert [t.path for t in landing.library.previews] == [str(landed)]
assert landing.player.track is landing.library.previews[0]
assert landing.player.state == gui.PLAY_LOADING, landing.player.state
gui.PREVIEW_CACHE_LIMIT = real_limit

# A download the user has already moved on from must not take the bar back off
# whatever they started instead.
superseded = PreviewWindow()
superseded._supersede_preview_fetch()
assert (
    superseded._finish_preview_fetch(0, str(landed), {"title": "Song 2"}) is False
)
assert superseded.player.track is None, superseded.player.track
assert superseded.library.previews == [], superseded.library.previews
assert superseded._fail_preview_fetch(0, kept, "boom") is False
assert superseded.player.error is None

# Playing a result already in the cache costs nothing: no download is started,
# and the capability check says so by not asking for one.
cached_window = PreviewWindow()
result = gui.SearchResult("Song 2", "Blur", 120.0, "https://youtu.be/blur2", "blur2")
cached_window.youtube_unavailable = "yt-dlp is not installed"
assert cached_window._preview_unavailable_reason(result) is None
cached_window.preview_result(result)
assert cached_window.player.track is not None
assert cached_window.player.track.path == str(landed), cached_window.player.track.path
assert cached_window.library.previews[0].path == str(landed)

# One that is not cached has to be downloaded first, so it needs everything a
# download needs, and a disabled button that says why beats an Add that fails
# several steps later.
uncached = gui.SearchResult("Nothing", "Nobody", 0, "https://youtu.be/none", "none")
assert cached_window._preview_unavailable_reason(uncached) == "yt-dlp is not installed"
# No GStreamer means nothing can be previewed at all, cached or not.
cached_window.preview_unavailable = gui.GSTREAMER_UNAVAILABLE
assert cached_window._preview_unavailable_reason(result) == gui.GSTREAMER_UNAVAILABLE

# Clearing throws the tree away rather than the files that are listed, so a
# half-finished download goes with them, and stops a preview that is playing
# rather than leaving the bar naming a file that no longer exists.
clearing = PreviewWindow()
clearing.library.previews = [previewed(landed)]
clearing.player.play(clearing.library.previews, 0)
assert clearing.player.track is not None
clearing.on_clear_cache(None)
assert not preview_cache.exists(), "the cache survived being cleared"
assert clearing.library.previews == []
assert clearing.player.track is None, "a cleared preview was left in the bar"
# And out of the queue behind it, or the play button offers a file that has
# just been deleted and fails on every press.
assert clearing.player.queue == [], clearing.player.queue
assert clearing.cache_figure.get_text() == "0 B · 0 files"
assert not clearing.cache_clear.sensitive
assert "freed" in clearing.toasts[-1], clearing.toasts

# A cache that does not exist is a cache holding nothing, not an error: this is
# every machine that has never previewed anything.
assert gui.preview_cache_entries(preview_cache) == []
assert gui.cached_preview_path("blur2", preview_cache) is None

print(
    json.dumps(
        {
            "staged_sync_command": staged,
            "remove_command": removal,
            "playlist_queue_sources": sorted(playlist_window.pending_sources),
            "parsed_playlists": parsed,
            "fetch_command": fetch,
            "queued_after_fetch": sorted(window.pending_sources),
            "nothing_new_outcome": outcome,
            "unreported_download_sources": sorted(fallback),
            "chosen_thumbnail": gui.thumbnail_from_entry({"thumbnails": sizes}),
            "cached_artwork": sorted(path.name for path in art_cache.iterdir()),
            "artwork_for_downloaded_file": from_youtube.art,
        },
        indent=2,
    )
)
