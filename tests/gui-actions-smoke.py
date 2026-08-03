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

import importlib.util
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ipod_gui", repo / "ipod-gui.py")
ipod_gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipod_gui)


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

    def set_sensitive(self, value):
        self.sensitive = value

    def set_visible(self, value):
        self.visible = value

    def set_text(self, value):
        self.text = value

    def set_label(self, value):
        self.text = value

    def set_reveal_child(self, value):
        self.revealed = value

    def set_fraction(self, value):
        self.fraction = value

    def start(self):
        self.spinning = True

    def stop(self):
        self.spinning = False


class FakeWindow:
    """Records the commands the window would have run."""

    def __init__(self):
        self.mount_point = "/media/alex/Alex's iPod"
        self.device_identity = "uuid:test-ipod"
        self.busy = False
        self.discovering_sources = False
        self.source_generation = 0
        self._device_scan_active = False
        self._device_snapshot_ready = True
        self.pending_device_identity = None
        self.pending = set()
        self.pending_sources = {}
        self.pending_records = {}
        self.pending_skipped_symlinks = {}
        self._pending_track_index = {}
        self._library_by_path = {}
        self.commands = []
        self.toasts = []
        self.track_names = {}
        self.library = type("Library", (), {"tracks": [], "device_only": []})()
        self.device_tracks = []
        self.speech_engine_available = True
        self.playlist_voiceover = FakeSwitch()

    def _run(self, argv, busy_message, done_message, then=None, clear=True):
        self.commands.append(argv)
        self.then = then

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
    _sync_downloaded = ipod_gui.IpodWindow._sync_downloaded
    _clear_pending = ipod_gui.IpodWindow._clear_pending
    _audio_files = ipod_gui.IpodWindow._audio_files
    _pending_track = ipod_gui.IpodWindow._pending_track
    _pending_accounting = ipod_gui.IpodWindow._pending_accounting
    _pending_copy_tracks = ipod_gui.IpodWindow._pending_copy_tracks
    _pending_change_count = ipod_gui.IpodWindow._pending_change_count
    _record_for_track = staticmethod(ipod_gui.IpodWindow._record_for_track)
    _merge_states = ipod_gui.IpodWindow._merge_states
    _scan_pending_tracks = ipod_gui.IpodWindow._scan_pending_tracks
    _finish_pending_enrichment = ipod_gui.IpodWindow._finish_pending_enrichment
    _commit_queue_sources = ipod_gui.IpodWindow._commit_queue_sources
    _queue_sources = ipod_gui.IpodWindow._queue_sources
    _queue_paths = ipod_gui.IpodWindow._queue_paths
    _queue_playlist = ipod_gui.IpodWindow._queue_playlist
    _unqueue_track = ipod_gui.IpodWindow._unqueue_track
    _scan_queued_sources = ipod_gui.IpodWindow._scan_queued_sources
    _finish_pending_source_scan = ipod_gui.IpodWindow._finish_pending_source_scan
    _launch_pending_sync = ipod_gui.IpodWindow._launch_pending_sync
    _update_device_controls = ipod_gui.IpodWindow._update_device_controls
    _confirmed_device = ipod_gui.IpodWindow._confirmed_device


# ------------------------------------------------------------------ removal

window = FakeWindow()
relpath = "Road Trip/Disc 1/01 - Highway.mp3"
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: window.device_identity
try:
    ipod_gui.IpodWindow._on_remove_response(
        window, None, "remove", relpath, window.device_identity
    )
finally:
    ipod_gui.volume_identity = original_volume_identity

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
    ipod_gui.IpodWindow._on_remove_response(
        quiet, None, answer, relpath, quiet.device_identity
    )
    assert quiet.commands == [], (answer, quiet.commands)

disconnected_removal = FakeWindow()
removed_device = disconnected_removal.device_identity
disconnected_removal.mount_point = None
disconnected_removal.device_identity = None
ipod_gui.IpodWindow._on_remove_response(
    disconnected_removal, None, "remove", relpath, removed_device
)
assert disconnected_removal.commands == [], disconnected_removal.commands
assert "changed" in disconnected_removal.toasts[-1], disconnected_removal.toasts

replaced_removal = FakeWindow()
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: "uuid:replacement-ipod"
try:
    ipod_gui.IpodWindow._on_remove_response(
        replaced_removal,
        None,
        "remove",
        relpath,
        replaced_removal.device_identity,
    )
finally:
    ipod_gui.volume_identity = original_volume_identity
assert replaced_removal.commands == [], replaced_removal.commands
assert "changed" in replaced_removal.toasts[-1], replaced_removal.toasts

# ---------------------------------------------------------------- log output

# The scripts colour their output for a terminal, and a text view shows the
# escape sequences literally, which is how the Output pane came to read
# "[36m==>[0m Removed 1 track(s)".
coloured = "\x1b[36m==>\x1b[0m Removed 1 track(s)\n"
assert ipod_gui.strip_ansi(coloured) == "==> Removed 1 track(s)\n", coloured
assert ipod_gui.strip_ansi("plain\n") == "plain\n"

# ------------------------------------------------------- playable formats
#
# The GUI cannot source lib.sh, so compare its necessary copy of the format
# list with the canonical shell declaration.
lib_sh = (repo / "lib.sh").read_text(encoding="utf-8")
declared = re.search(r'^readonly SUPPORTED_EXT="([^"]+)"', lib_sh, re.MULTILINE)
assert declared, "lib.sh no longer declares SUPPORTED_EXT"
assert {f".{e}" for e in declared.group(1).split("|")} == ipod_gui.AUDIO_EXTENSIONS, (
    declared.group(1),
    ipod_gui.AUDIO_EXTENSIONS,
)

scan_root = Path(tempfile.mkdtemp())
(scan_root / "Artist").mkdir()
(scan_root / "Artist" / "Fallback.mp3").write_bytes(b"not really an mp3")
(scan_root / "Artist" / "ignored.txt").touch()
original_interpreter = ipod_gui._tag_interpreter
original_tag_python = ipod_gui.TAG_PYTHON
original_reader = ipod_gui._TAG_READER
try:
    ipod_gui.TAG_PYTHON = None
    ipod_gui._tag_interpreter = lambda: None
    fallback, complete, skipped_symlinks = ipod_gui.scan_tracks(scan_root)
    assert complete
    assert skipped_symlinks == 0
    assert fallback == [
        {
            "path": "Artist/Fallback.mp3",
            "title": "Fallback",
            "size": len(b"not really an mp3"),
        }
    ], fallback

    ipod_gui.TAG_PYTHON = sys.executable
    ipod_gui._TAG_READER = """
import json, sys, time
print(json.dumps({"path": "Artist/Fallback.mp3", "title": "Fallback"}), flush=True)
print(json.dumps({"path": "Artist/Fallback.mp3", "title": "Tagged"}), flush=True)
time.sleep(10)
"""
    streamed = []
    started = time.monotonic()
    records, complete, skipped_symlinks = ipod_gui.scan_tracks(
        scan_root, streamed.append, timeout=0.4
    )
    elapsed = time.monotonic() - started
    assert elapsed < 2, elapsed
    assert not complete, "a timed-out scan was reported as complete"
    assert skipped_symlinks == 0
    assert records[0]["title"] == "Tagged", records
    assert [record["title"] for record in streamed] == ["Fallback", "Tagged"], streamed

    started = time.monotonic()
    _records, complete, _skipped_symlinks = ipod_gui.scan_tracks(
        scan_root,
        timeout=10,
        cancelled=lambda: time.monotonic() - started >= 0.1,
    )
    assert not complete, "a cancelled scan was reported as complete"
    assert time.monotonic() - started < 2, "cancelled scan left its reader running"
finally:
    ipod_gui._tag_interpreter = original_interpreter
    ipod_gui.TAG_PYTHON = original_tag_python
    ipod_gui._TAG_READER = original_reader

_records, complete, _skipped_symlinks = ipod_gui.scan_tracks(scan_root / "missing")
assert not complete, "a missing scan root was reported as complete"

symlink_root = Path(tempfile.mkdtemp())
regular_track = symlink_root / "regular.mp3"
regular_track.write_bytes(b"regular")
(symlink_root / "linked.mp3").symlink_to(regular_track)
linked_directory = Path(tempfile.mkdtemp())
(linked_directory / "nested.mp3").write_bytes(b"nested")
(symlink_root / "linked-directory").symlink_to(linked_directory, target_is_directory=True)
library_records, complete, skipped_symlinks = ipod_gui.scan_tracks(symlink_root)
assert complete
assert {record["path"] for record in library_records} == {
    "linked.mp3",
    "regular.mp3",
}
assert skipped_symlinks == 0
symlink_records, complete, skipped_symlinks = ipod_gui.scan_tracks(
    symlink_root, skip_symlinks=True
)
assert complete
assert [record["path"] for record in symlink_records] == ["regular.mp3"]
assert skipped_symlinks == 2

root_records, complete, skipped_symlinks = ipod_gui.scan_tracks(
    symlink_root / "linked-directory", skip_symlinks=True
)
assert complete
assert root_records == []
assert skipped_symlinks == 1

exact_track = scan_root / "Exact.mp3"
exact_track.write_bytes(b"exact")
unrelated = scan_root / "Artist" / "Unrelated.mp3"
unrelated.write_bytes(b"unrelated")
exact_records, complete, skipped_symlinks = ipod_gui.scan_tracks(files=[exact_track])
assert complete
assert skipped_symlinks == 0
assert [record["path"] for record in exact_records] == [str(exact_track)]

exact_link = scan_root / "Exact Link.mp3"
exact_link.symlink_to(exact_track)
exact_records, complete, skipped_symlinks = ipod_gui.scan_tracks(files=[exact_link])
assert complete
assert skipped_symlinks == 0
assert [record["path"] for record in exact_records] == [str(exact_link)]


class FolderDiscoveryWindow:
    _finish_music_folder_discovery = (
        ipod_gui.IpodWindow._finish_music_folder_discovery
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
            ipod_gui.Track(
                track_path,
                {"title": "Song", "artist": "Artist", "album": "Album"},
                ipod_gui.STATE_LIBRARY,
            )
        ], True, 0

    def _queue_sources(
        self, sources, metadata_complete=False, skipped_symlinks=None
    ):
        assert metadata_complete
        assert skipped_symlinks == {"/music": 0}
        self.queued = {
            source: [track.path for track in tracks]
            for source, tracks in sources.items()
        }

    def _toast(self, message):
        raise AssertionError(message)


discovery_window = FolderDiscoveryWindow()
scheduled = []
scheduled_event = threading.Event()
original_glib = ipod_gui.GLib


def record_idle(callback, *args):
    scheduled.append((callback, args))
    scheduled_event.set()
    return 1


ipod_gui.GLib = type("ImmediateGLib", (), {"idle_add": staticmethod(record_idle)})
try:
    main_thread = threading.get_ident()
    ipod_gui.IpodWindow._discover_music_folder(discovery_window, "/music")
    assert scheduled_event.wait(2), "folder discovery did not reach GLib"
finally:
    ipod_gui.GLib = original_glib
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
partial_track = ipod_gui.Track(
    "/music/partial.mp3", {"title": "Partial"}, ipod_gui.STATE_LIBRARY
)
ipod_gui.IpodWindow._finish_music_folder_discovery(
    failed_discovery,
    failed_discovery.source_generation,
    failed_discovery.device_identity,
    "/music",
    [partial_track],
    False,
    0,
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
        str(pending_only): ipod_gui.Track(
            pending_only,
            {
                "title": "Outside",
                "artist": "Artist",
                "album": "Album",
                "duration": 120,
            },
            ipod_gui.STATE_LIBRARY,
        )
    }, True


enrichment_window._scan_pending_tracks = enrich_pending
scheduled = []
scheduled_event = threading.Event()
ipod_gui.GLib = type("ImmediateGLib", (), {"idle_add": staticmethod(record_idle)})
try:
    result = enrichment_window._queue_sources(
        {str(pending_only): [enrichment_window._pending_track(pending_only)]}
    )
    assert result is None, "pending-only tags were not enriched asynchronously"
    assert scheduled_event.wait(2), "pending enrichment did not reach GLib"
finally:
    ipod_gui.GLib = original_glib
callback, callback_args = scheduled[0]
callback(*callback_args)
assert enrichment_window.pending_records[str(pending_only)]["artist"] == "Artist"


class Selected:
    def __init__(self, value):
        self.value = value

    def get_selected(self):
        return self.value


class VisibleView:
    def __init__(self, name):
        self.name = name

    def get_visible_child_name(self):
        return self.name


class RefreshWindow:
    _resolve_current_album = ipod_gui.IpodWindow._resolve_current_album

    def __init__(self, visible):
        old_track = ipod_gui.Track("old.mp3", {"album": "Album"}, ipod_gui.STATE_LIBRARY)
        new_track = ipod_gui.Track("new.mp3", {"album": "Album"}, ipod_gui.STATE_LIBRARY)
        self.current_album = ipod_gui.Album("Album", "Unknown artist")
        self.current_album.add(old_track)
        replacement = ipod_gui.Album("Album", "Unknown artist")
        replacement.add(new_track)
        self.library = type("Library", (), {"collections": lambda _self, _mode: [replacement]})()
        self.group_mode = Selected(0)
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
ipod_gui.IpodWindow._refresh_current_view(library_refresh)
assert not library_refresh.shown, "a library repaint reopened stale album detail"

album_refresh = RefreshWindow("album")
stale_album = album_refresh.current_album
ipod_gui.IpodWindow._refresh_current_view(album_refresh)
assert album_refresh.current_album is not stale_album, "album detail kept stale tracks"
assert album_refresh.shown == [album_refresh.current_album], album_refresh.shown

partial_refresh = RefreshWindow("album")
partial_album = partial_refresh.current_album
partial_refresh.library = type(
    "EmptyLibrary", (), {"collections": lambda _self, _mode: []}
)()
ipod_gui.IpodWindow._refresh_current_view(partial_refresh, scan_complete=False)
assert partial_refresh.views.name == "album", "partial scan closed album detail"
assert partial_refresh.current_album is partial_album, "partial scan dropped album state"
ipod_gui.IpodWindow._refresh_current_view(partial_refresh, scan_complete=True)
assert partial_refresh.views.name == "library", "completed scan kept a missing album open"

disconnected_scan = type(
    "DisconnectedScan",
    (),
    {"tag_generation": 3, "mount_point": None},
)()
assert not ipod_gui.IpodWindow._apply_device_track_batch(
    disconnected_scan, 3, "/media/iPod", []
), "a disconnected device scan was applied"

common = {
    "title": "Same Song",
    "artist": "Same Artist",
    "album": "Same Album",
    "duration": 200.2,
}
local_one = ipod_gui.Track("/music/one.mp3", common, ipod_gui.STATE_LIBRARY)
local_two = ipod_gui.Track("/music/two.mp3", common, ipod_gui.STATE_LIBRARY)
device_one = ipod_gui.Track(
    "/media/iPod/iPod_Control/Music/F00/AAAA.mp3",
    {**common, "duration": 200.4},
    ipod_gui.STATE_IPOD,
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
ipod_gui.IpodWindow._merge_states(merge_window)
assert sum(track.on_ipod for track in merge_library.tracks) == 1
assert local_one.relpath == "F00/AAAA.mp3", local_one.relpath
assert local_two.relpath == local_two.path, local_two.relpath

other_album = ipod_gui.Track(
    "/media/iPod/iPod_Control/Music/F00/BBBB.mp3",
    {**common, "album": "Other Album"},
    ipod_gui.STATE_IPOD,
    relpath="F00/BBBB.mp3",
)
merge_window.device_tracks = [other_album]
ipod_gui.IpodWindow._merge_states(merge_window)
assert not any(track.on_ipod for track in merge_library.tracks)
assert merge_library.device_only == [other_album]


class SelectionWindow:
    def __init__(self):
        queued = ipod_gui.Track("/music/queued.mp3", common, ipod_gui.STATE_LIBRARY)
        self.mount_point = "/media/A"
        self.device_identity = "uuid:A"
        self.discovering_sources = False
        self.source_generation = 0
        self.pending_device_identity = "uuid:A"
        self.pending = {queued.path}
        self.pending_sources = {queued.path: {queued.path}}
        self.pending_records = {}
        self.pending_skipped_symlinks = {}
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


identity_window = SelectionWindow()
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda mount: {
    "/media/A-again": "uuid:A",
    "/media/B": "uuid:B",
}.get(mount)
try:
    ipod_gui.IpodWindow._select_mount(identity_window, None)
    assert identity_window.pending, "disconnect discarded a device-bound queue"
    ipod_gui.IpodWindow._select_mount(identity_window, "/media/A-again")
    assert identity_window.pending, "same device reconnect discarded its queue"
    ipod_gui.IpodWindow._select_mount(identity_window, "/media/B")
finally:
    ipod_gui.volume_identity = original_volume_identity
assert identity_window.pending == set(), identity_window.pending
assert identity_window.pending_sources == {}, identity_window.pending_sources
assert identity_window.toasts and "different iPod" in identity_window.toasts[-1]

# ----------------------------------------------------------------- playlist

playlist_window = FakeWindow()
playlist_root = Path(tempfile.mkdtemp())
playlist_track = playlist_root / "Party Song.mp3"
playlist_track.touch()
playlist_path = playlist_root / "Party Mix.m3u"
playlist_path.write_text(f"{playlist_track.name}\n", encoding="utf-8")
playlist_window.library.tracks = [
    ipod_gui.Track(
        playlist_track,
        {"title": "Party Song", "artist": "Artist"},
        ipod_gui.STATE_LIBRARY,
    )
]
playlist_window._merge_states()
ipod_gui.IpodWindow._add_playlist(playlist_window, playlist_path)

assert playlist_window.commands == [], playlist_window.commands
assert playlist_window.pending_sources == {
    str(playlist_path): {str(playlist_path), str(playlist_track)}
}, playlist_window.pending_sources
assert playlist_window.pending == {
    str(playlist_path),
    str(playlist_track),
}, playlist_window.pending
# A playlist that cannot speak its name cannot be found again on a screenless
# device, so adding one switches the spoken names on rather than warning later.
assert playlist_window.playlist_voiceover.active, "adding a playlist left voiceover off"

silent_window = FakeWindow()
silent_window.speech_engine_available = False
ipod_gui.IpodWindow._add_playlist(silent_window, playlist_path)
assert not silent_window.playlist_voiceover.active, "voiceover flipped without an engine"
assert not silent_window.commands, "a playlist was synced without spoken names"
assert silent_window.toasts == ["No speech engine installed"], silent_window.toasts

folder_window = FakeWindow()
folder_members = [
    ipod_gui.Track(
        f"/music/Album/{name}.mp3",
        {"title": name},
        ipod_gui.STATE_LIBRARY,
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

ipod_gui.IpodWindow._set_busy(busy_window, False)
assert not busy_window.playlist_button.sensitive, "busy reset enabled Add Playlist"
assert busy_window.youtube_button.sensitive, "busy reset left YouTube disabled"
# Nothing is queued, so there is nothing for Sync to do.
assert not busy_window.sync_button.sensitive, "Sync offered with an empty queue"
assert all(w.sensitive for w in busy_window._busy_widgets), "widgets left disabled"
assert not busy_window.sync_revealer.revealed, "sync bar left showing when idle"
assert not busy_window.sync_spinner.spinning, "spinner left running when idle"

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
queued_track = ipod_gui.Track(
    "/home/alex/Music/one.mp3",
    {"title": "one"},
    ipod_gui.STATE_LIBRARY,
)
queued_window.pending = {queued_track.path}
queued_window.pending_sources = {queued_track.path: {queued_track.path}}
ipod_gui.IpodWindow._set_busy(queued_window, False)
assert queued_window.sync_button.sensitive, "queued changes could not be synced"
queued_window._device_scan_active = True
queued_window._device_snapshot_ready = False
queued_window._update_device_controls()
assert not queued_window.sync_button.sensitive
queued_window._device_snapshot_ready = True
queued_window._update_device_controls()
assert queued_window.sync_button.sensitive

# -------------------------------------------------------- playlist removal

playlist_removal = FakeWindow()
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: playlist_removal.device_identity
try:
    ipod_gui.IpodWindow._on_playlist_remove_response(
        playlist_removal,
        None,
        "remove",
        "twizzy",
        playlist_removal.device_identity,
    )
finally:
    ipod_gui.volume_identity = original_volume_identity

playlist_rm = playlist_removal.commands[0]
assert playlist_rm[0].endswith("ipod-remove.sh"), playlist_rm
assert playlist_rm[1:3] == ["--ipod", playlist_removal.mount_point], playlist_rm
assert "--yes" in playlist_rm, playlist_rm
assert "--playlist" in playlist_rm, playlist_rm
assert playlist_rm[-2:] == ["--", "twizzy"], playlist_rm

for answer in ("cancel", "close"):
    quiet = FakeWindow()
    ipod_gui.IpodWindow._on_playlist_remove_response(
        quiet, None, answer, "twizzy", quiet.device_identity
    )
    assert quiet.commands == [], (answer, quiet.commands)

disconnected_playlist_removal = FakeWindow()
removed_device = disconnected_playlist_removal.device_identity
disconnected_playlist_removal.mount_point = None
disconnected_playlist_removal.device_identity = None
ipod_gui.IpodWindow._on_playlist_remove_response(
    disconnected_playlist_removal,
    None,
    "remove",
    "twizzy",
    removed_device,
)
assert disconnected_playlist_removal.commands == []
assert "changed" in disconnected_playlist_removal.toasts[-1]

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
parsed = ipod_gui.list_playlists(fake_volume)
assert parsed == [
    ("Party", ["Yeat/Song [x1].mp3", "/kept/as/written.mp3"]),
    ("Radio", ["Yeat/Song [x1].mp3", "/second.mp3"]),
    ("mix..v2", ["Yeat/Song [x1].mp3"]),
], parsed

# ------------------------------------------------------------------ youtube

assert ipod_gui.is_downloadable_url("https://www.youtube.com/watch?v=abc")
assert ipod_gui.is_downloadable_url("  http://youtu.be/abc  ")
for rejected in ("", "youtube.com/watch?v=abc", "not a link", "file:///etc/passwd"):
    assert not ipod_gui.is_downloadable_url(rejected), rejected

window = FakeWindow()
url = "https://www.youtube.com/watch?v=abc&list=PL123"
ipod_gui.IpodWindow._on_youtube_response(
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
ipod_gui.IpodWindow._on_youtube_response(
    rejected_window, None, "download", Value("not a link"), Value(False)
)
assert rejected_window.commands == [], rejected_window.commands
assert rejected_window.toasts, "a rejected link said nothing"

# What the download reported is exactly what gets copied. Anything else in the
# library, downloaded on an earlier day, stays where it is.
library = Path(tempfile.mkdtemp())
downloaded = library / "New Artist" / "New Song [abc].mp3"
downloaded.parent.mkdir(parents=True)
downloaded.touch()
(library / "Old Artist").mkdir()
new_tracks.write_text(f"{downloaded}\n\n")

ipod_gui.YOUTUBE_LIBRARY = library
window.library.tracks = [
    ipod_gui.Track(
        downloaded,
        {"title": "New Song", "artist": "New Artist"},
        ipod_gui.STATE_LIBRARY,
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
# nothing to copy and nothing to report as added.
empty = Path(tempfile.mkstemp()[1])
outcome = ipod_gui.IpodWindow._sync_downloaded(window, empty)
assert isinstance(outcome, str), outcome
assert "Already downloaded" in outcome, outcome

# A missing list means yt-dlp could not say, and the artist folders are then
# the closest honest answer rather than silently copying nothing.
fallback = ipod_gui.fetched_sources(library / "never-written", library)
assert sorted(fallback) == sorted(
    [str(library / "New Artist"), str(library / "Old Artist")]
), fallback

# --------------------------------------------------------------- staged sync
#
# Adding queues a track rather than copying it, so the command that finally
# runs has to name every queued path and nothing else. Copying the whole
# library instead would fill a 2GB device from a single click.
queue_window = FakeWindow()
queue_window.sync_files = []
queue_window.sync_total = 0
sync_source = Path(tempfile.mkdtemp()) / "Music"
sync_source.mkdir()
queued_paths = {
    str(path): ipod_gui.Track(
        path,
        {"title": Path(path).stem, "size": size},
        ipod_gui.STATE_LIBRARY,
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
already_copied = ipod_gui.Track(
    copied_path,
    {"title": "Dawn", "size": 4096},
    ipod_gui.STATE_LIBRARY,
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
replacement = ipod_gui.Track(
    already_copied.path,
    {"title": "Dawn", "size": 4096},
    ipod_gui.STATE_LIBRARY,
)
queue_window.library.tracks = [*queued_paths.values(), replacement]
queue_window._merge_states()
assert queue_window._pending_change_count() == len(queued_paths) + 1
assert sum(track.size for track in queue_window._pending_copy_tracks()) == 7168
added_after_queue = sync_source / "03 Added Later.mp3"
added_after_queue.write_bytes(b"x" * 512)
skipped_during_sync = sync_source / "04 Linked.mp3"
skipped_during_sync.symlink_to(exact_track)
skipped_source = Path(tempfile.mkdtemp()) / "Only Links"
skipped_source.mkdir()
removed_before_sync = skipped_source / "Removed.mp3"
removed_before_sync.write_bytes(b"removed")
removed_track = ipod_gui.Track(
    removed_before_sync,
    {"title": "Removed", "size": len(b"removed")},
    ipod_gui.STATE_LIBRARY,
)
queue_window.library.tracks.append(removed_track)
queue_window.pending.add(removed_track.path)
queue_window.pending_sources[str(skipped_source)] = {removed_track.path}
queue_window._merge_states()
removed_before_sync.unlink()
skipped_only_link = skipped_source / "Only Link.mp3"
skipped_only_link.symlink_to(exact_track)
command_ready = threading.Event()
record_command = queue_window._run


def record_preflight_command(argv, busy_message, done_message, then=None, clear=True):
    record_command(argv, busy_message, done_message, then, clear)
    command_ready.set()


queue_window._run = record_preflight_command
original_volume_identity = ipod_gui.volume_identity
original_glib = ipod_gui.GLib
ipod_gui.volume_identity = lambda _mount: queue_window.device_identity
ipod_gui.GLib = type(
    "ImmediateGLib",
    (),
    {"idle_add": staticmethod(lambda callback, *args: callback(*args))},
)
try:
    ipod_gui.IpodWindow.on_sync_pending(queue_window, None)
    assert command_ready.wait(5), "queued sources were not re-read before sync"
finally:
    ipod_gui.volume_identity = original_volume_identity
    ipod_gui.GLib = original_glib

staged = queue_window.commands[0]
assert staged[0].endswith("ipod-sync.sh"), staged
assert staged[1:3] == ["--ipod", queue_window.mount_point], staged
# Everything after -- is a path, because a track title can begin with a dash.
separator = staged.index("--")
assert staged[separator + 1:] == [str(sync_source)], staged
assert queue_window.sync_total == len(queued_paths) + 2, queue_window.sync_total
assert str(added_after_queue) in queue_window.pending
assert str(skipped_during_sync) not in queue_window.pending
assert str(skipped_source) not in queue_window.pending_sources
assert queue_window.pending_skipped_symlinks == {
    str(skipped_source): 1,
    str(sync_source): 1,
}
assert ipod_gui.IpodWindow._pending_symlink_note(queue_window) == (
    " · 2 symlinked items skipped"
)
assert queue_window.toasts[-1] == (
    "2 symlinked items skipped because links are not copied"
)

# The queue is only cleared once the copy has actually succeeded, which is
# what the then callback is for.
assert queue_window.pending == {
    *queued_paths,
    already_copied.path,
    str(added_after_queue),
}, "queue emptied before the sync ran"
cleared = queue_window.then()
assert queue_window.pending == set(), "queue survived a successful sync"
assert queue_window.pending_sources == {}, "sync sources survived a successful sync"
assert queue_window.pending_skipped_symlinks == {}
assert queue_window.pending_device_identity is None, "queue stayed device-bound"
assert isinstance(cleared, str), cleared

failed_sync = FakeWindow()
failed_member = "/missing/source/song.mp3"
failed_sync.pending = {failed_member}
failed_sync.pending_sources = {"/missing/source": {failed_member}}
failed_sync.pending_device_identity = failed_sync.device_identity
failure_ready = threading.Event()
original_toast = failed_sync._toast


def record_failure(message):
    original_toast(message)
    failure_ready.set()


failed_sync._toast = record_failure
original_volume_identity = ipod_gui.volume_identity
original_glib = ipod_gui.GLib
ipod_gui.volume_identity = lambda _mount: failed_sync.device_identity
ipod_gui.GLib = type(
    "ImmediateGLib",
    (),
    {"idle_add": staticmethod(lambda callback, *args: callback(*args))},
)
try:
    ipod_gui.IpodWindow.on_sync_pending(failed_sync, None)
    assert failure_ready.wait(5), "failed source scan did not report its refusal"
finally:
    ipod_gui.volume_identity = original_volume_identity
    ipod_gui.GLib = original_glib
assert failed_sync.commands == [], failed_sync.commands
assert not failed_sync.busy
assert "cancelled" in failed_sync.toasts[-1]

outside_window = FakeWindow()
outside_track = ipod_gui.Track(
    "/outside/Album/Song.mp3",
    {
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "duration": 120,
        "size": 8192,
    },
    ipod_gui.STATE_LIBRARY,
)
outside_window.device_tracks = [
    ipod_gui.Track(
        "/media/iPod/iPod_Control/Music/F00/ABCD.mp3",
        {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "duration": 120,
        },
        ipod_gui.STATE_IPOD,
        relpath="F00/ABCD.mp3",
    )
]
outside_window._queue_sources(
    {"/outside/Album": [outside_track]}, metadata_complete=True
)
assert outside_window._pending_accounting()[1:] == (0, 0)


class RunGuardWindow:
    _device_command_is_current = ipod_gui.IpodWindow._device_command_is_current

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
started = ipod_gui.IpodWindow._run(
    run_guard,
    ["ipod-sync.sh", "--ipod", None],
    "Running",
    "Done",
)
assert started is False
assert run_guard.toasts == ["Connect an iPod before running this action"]

run_guard = RunGuardWindow()
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: "uuid:replacement"
try:
    started = ipod_gui.IpodWindow._run(
        run_guard,
        ["ipod-sync.sh", "--ipod", run_guard.mount_point, "--rebuild-only"],
        "Running",
        "Done",
    )
finally:
    ipod_gui.volume_identity = original_volume_identity
assert started is False
assert "changed" in run_guard.toasts[-1]


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
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: "uuid:replacement"
try:
    ipod_gui.IpodWindow.on_eject(eject_guard, None)
finally:
    ipod_gui.volume_identity = original_volume_identity
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
ipod_gui.IpodWindow._finish(
    finish_window, 0, "Done", device_command=True
)
assert finish_window.events[0] == "invalidate", finish_window.events
assert finish_window.sync_total == 0

# An empty queue must not launch a script at all.
idle_window = FakeWindow()
idle_window.pending = set()
idle_window.pending_sources = {}
idle_window.sync_files = []
idle_window.sync_total = 0
ipod_gui.IpodWindow.on_sync_pending(idle_window, None)
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

assert ipod_gui.playlist_file(reorder_root, "Morning Ride") == m3u
assert ipod_gui.playlist_file(reorder_root, "Nonexistent") is None

parsed_order = dict(ipod_gui.list_playlists(reorder_root))["Morning Ride"]
reordered = [parsed_order[1], parsed_order[0], parsed_order[2]]
reorder_identity = "uuid:reorder"
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: reorder_identity
try:
    assert ipod_gui.write_playlist(
        reorder_root, reorder_identity, m3u, reordered
    )
finally:
    ipod_gui.volume_identity = original_volume_identity

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
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: "uuid:replacement"
try:
    assert not ipod_gui.write_playlist(
        reorder_root, reorder_identity, m3u, parsed_order
    )
finally:
    ipod_gui.volume_identity = original_volume_identity
assert m3u.read_text(encoding="utf-8") == original
# The rewrite is atomic, so no half-written list can be left behind for the
# firmware to choke on if the device is pulled mid-write.
assert not list(reorder_root.glob(".*tmp")), list(reorder_root.glob(".*tmp"))

pls = reorder_root / "Gym.pls"
pls.write_text("[playlist]\nFile1=iPod_Control/Music/F00/LDPX.mp3\n", encoding="utf-8")
original_volume_identity = ipod_gui.volume_identity
ipod_gui.volume_identity = lambda _mount: reorder_identity
try:
    assert ipod_gui.write_playlist(
        reorder_root,
        reorder_identity,
        pls,
        ["F00/QMRT.mp3", "F00/LDPX.mp3"],
    )
finally:
    ipod_gui.volume_identity = original_volume_identity
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
    return ipod_gui.TrackItem(
        ipod_gui.Track("/tmp/x.mp3", {"title": title}, ipod_gui.STATE_LIBRARY), 1
    )


Gtk = ipod_gui.Gtk
by_title = ipod_gui.track_sorter(lambda track: track.title.lower())
assert by_title.compare(sortable("a"), sortable("b")) == Gtk.Ordering.SMALLER
assert by_title.compare(sortable("b"), sortable("a")) == Gtk.Ordering.LARGER
assert by_title.compare(sortable("a"), sortable("A")) == Gtk.Ordering.EQUAL

by_duration = ipod_gui.track_sorter(lambda track: track.duration)
short, long_ = sortable("short"), sortable("long")
short.track.duration, long_.track.duration = 10.0, 400.0
assert by_duration.compare(short, long_) == Gtk.Ordering.SMALLER

# Every sortable column must produce a usable sorter, not just the one above.
for _key, _title, _expand, sort_key in ipod_gui.TRACK_COLUMNS:
    if sort_key is None:
        continue
    built = ipod_gui.track_sorter(sort_key)
    assert built.compare(sortable("a"), sortable("b")) in (
        Gtk.Ordering.SMALLER,
        Gtk.Ordering.EQUAL,
        Gtk.Ordering.LARGER,
    ), _key

# ------------------------------------------------------ per-file sync output
#
# ipod-sync.sh prints one of these per file; the sync bar counts them. The
# destination can contain spaces, so the pattern must not stop at one.
progress = ipod_gui.COPIED_LINE.match("  + Harbour Light.mp3 -> F00/LDPX.mp3\n".rstrip())
assert progress, "per-file sync line no longer parses"
assert progress.group("name") == "Harbour Light.mp3", progress.group("name")
assert progress.group("dest") == "F00/LDPX.mp3", progress.group("dest")

spaced = ipod_gui.COPIED_LINE.match("  + A Song.mp3 -> Some Folder/B QRST.mp3")
assert spaced, "a destination containing a space did not parse"
assert spaced.group("dest") == "Some Folder/B QRST.mp3", spaced.group("dest")

# Aggregate lines are not per-file lines and must not be counted as copies.
for line in ("==> Copied 4 file(s)", "warning: Skipped 1 unsupported file(s)"):
    assert ipod_gui.COPIED_LINE.match(line) is None, line

# The shell script must still emit the format the bar parses.
sync_sh = (repo / "ipod-sync.sh").read_text(encoding="utf-8")
assert "'  + %s -> %s\\n'" in sync_sh, "ipod-sync.sh stopped reporting each file"

print(
    json.dumps(
        {
            "staged_sync_command": staged,
            "remove_command": removal,
            "playlist_queue_sources": sorted(playlist_window.pending_sources),
            "playlist_remove_command": playlist_rm,
            "parsed_playlists": parsed,
            "fetch_command": fetch,
            "queued_after_fetch": sorted(window.pending_sources),
            "nothing_new_outcome": outcome,
            "unreported_download_sources": sorted(fallback),
        },
        indent=2,
    )
)
