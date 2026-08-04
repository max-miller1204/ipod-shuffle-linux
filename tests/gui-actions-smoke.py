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
import os
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

    all_tracks = ipod_gui.LibraryIndex.all_tracks


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
        self.busy_messages = []
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
    _youtube_download_tooltip = ipod_gui.IpodWindow._youtube_download_tooltip
    _can_download = ipod_gui.IpodWindow._can_download
    _start_youtube_download = ipod_gui.IpodWindow._start_youtube_download
    _populate_cache_card = ipod_gui.IpodWindow._populate_cache_card


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

# The scripts colour their output for a terminal, and the GUI's log view would
# show the escape sequences literally as "[36m==>[0m Removed 1 track(s)".
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

# What the download reported is exactly what gets queued. Anything else in the
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
# nothing to queue and nothing to report as added.
empty = Path(tempfile.mkstemp()[1])
outcome = ipod_gui.IpodWindow._sync_downloaded(window, empty)
assert isinstance(outcome, str), outcome
assert "Already downloaded" in outcome, outcome

# A missing list means yt-dlp could not say, and the artist folders are then
# the closest honest answer rather than silently queueing nothing.
fallback = ipod_gui.fetched_sources(library / "never-written", library)
assert sorted(fallback) == sorted(
    [str(library / "New Artist"), str(library / "Old Artist")]
), fallback

# ------------------------------------------------------------------- search
#
# The search field queries two sources at once, and the two halves fail
# independently: metadata needs only yt-dlp, while the download needs ffmpeg
# and a JavaScript runtime as well. Getting that gating wrong either blanks a
# working search or offers an Add that dies with HTTP 403 several steps later.

phrase_search = ipod_gui.youtube_search_command("/venv/yt-dlp", "bohemian rhapsody")
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
assert ipod_gui.youtube_search_command("/venv/yt-dlp", f"  {link}  ")[-1] == link
assert ipod_gui.youtube_search_target("queen", limit=5) == "ytsearch5:queen"
# A linked playlist is capped to the same shortlist a search returns, so
# pasting an album link cannot flood the section.
capped = ipod_gui.youtube_search_command("/venv/yt-dlp", link, limit=2)
assert capped[capped.index("--playlist-items") + 1] == "1-2", capped

parsed_results = ipod_gui.parse_search_results(
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
assert ipod_gui.YOUTUBE_SEARCH_RESULTS == 3, ipod_gui.YOUTUBE_SEARCH_RESULTS
flood = ipod_gui.parse_search_results(
    [json.dumps({"id": f"id{n}", "title": str(n)}) for n in range(9)]
)
assert len(flood) == ipod_gui.YOUTUBE_SEARCH_RESULTS, len(flood)


def stub_yt_dlp(script):
    """A yt-dlp stand-in, so no test here depends on the network."""
    path = Path(tempfile.mkdtemp()) / "yt-dlp"
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


original_lib_output = ipod_gui.lib_function_output
found_line = json.dumps({"id": "abc", "title": "Found", "duration": 12})
for script, expected_results, expected_reached, why in (
    (f"echo '{found_line}'", ["Found"], True, "a working search"),
    # Exit 0 having printed nothing is how yt-dlp reports no matches, and
    # exit 1 is how it reports not reaching YouTube at all. Collapsing the two
    # makes the user retype a query that was perfectly fine.
    ("exit 0", [], True, "a search with no matches"),
    ("echo 'ERROR: unable to download' >&2; exit 1", [], False, "an offline search"),
):
    ipod_gui.lib_function_output = lambda _name, s=script: stub_yt_dlp(s)
    try:
        found, reached = ipod_gui.search_youtube("anything", timeout=20)
    finally:
        ipod_gui.lib_function_output = original_lib_output
    assert [r.title for r in found] == expected_results, why
    assert reached is expected_reached, why

# With no yt-dlp at all there is nothing to run, and that is a failure to
# reach YouTube rather than an empty result set.
ipod_gui.lib_function_output = lambda _name: None
try:
    missing_found, missing_reached = ipod_gui.search_youtube("anything")
finally:
    ipod_gui.lib_function_output = original_lib_output
assert missing_found == [] and missing_reached is False

# Searching survives what downloading cannot. yt-dlp reads metadata without
# ffmpeg and without a JavaScript runtime; only the media URL is signed.
original_succeeds = ipod_gui.lib_function_succeeds
original_which = ipod_gui.shutil.which
ipod_gui.lib_function_succeeds = lambda name: name == "yt_dlp_bin"
ipod_gui.shutil.which = lambda _name: None
try:
    assert ipod_gui.youtube_search_unavailable_reason() is None, "search over-gated"
    download_reason = ipod_gui.youtube_unavailable_reason()
finally:
    ipod_gui.lib_function_succeeds = original_succeeds
    ipod_gui.shutil.which = original_which
assert download_reason and "ffmpeg" in download_reason, download_reason

ipod_gui.lib_function_succeeds = lambda _name: False
try:
    assert "yt-dlp" in (ipod_gui.youtube_search_unavailable_reason() or "")
    assert ipod_gui.preview_unavailable_reason() == (
        "GStreamer is not installed - see Preview playback in the README"
    )
finally:
    ipod_gui.lib_function_succeeds = original_succeeds

# The local half. Every word has to match, in any order, across title, artist
# and album, because a phrase match would need whatever order the tagger used.
search_library = [
    ipod_gui.Track(
        "/music/queen/bohemian.mp3",
        {"title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night At The Opera"},
        ipod_gui.STATE_LIBRARY,
    ),
    ipod_gui.Track(
        "/music/queen/love.mp3",
        {"title": "Somebody To Love", "artist": "Queen", "album": "A Day At The Races"},
        ipod_gui.STATE_IPOD,
    ),
    ipod_gui.Track(
        "/music/other/rain.mp3",
        {"title": "Rain", "artist": "Someone Else", "album": "Weather"},
        ipod_gui.STATE_LIBRARY,
    ),
]
assert [t.title for t in ipod_gui.local_search_matches(search_library, "queen rhapsody")] == [
    "Bohemian Rhapsody"
]
assert [t.title for t in ipod_gui.local_search_matches(search_library, "QUEEN")] == [
    "Somebody To Love",
    "Bohemian Rhapsody",
], "artist matches were not ordered by album"
# A track that lives only on the device is still findable, or music copied
# from another machine would be invisible to the one field that searches.
assert ipod_gui.local_search_matches(search_library, "races")[0].state == ipod_gui.STATE_IPOD
assert ipod_gui.local_search_matches(search_library, "   ") == []
assert ipod_gui.local_search_matches(search_library, "queen rain") == []

# Adding a result runs the same download the dialog does, and refuses in every
# case where it could not finish.
result_window = FakeWindow()
found_result = ipod_gui.SearchResult(
    title="Bohemian Rhapsody",
    uploader="Queen Official",
    duration=360.0,
    url=link,
    video_id="abc",
)
result_window._set_search_note = lambda text: result_window.notes.append(text)
result_window.notes = []
ipod_gui.IpodWindow._download_result(result_window, found_result)
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
    assert not ipod_gui.IpodWindow._can_download(refusing), why
    ipod_gui.IpodWindow._download_result(refusing, found_result)
    assert refusing.commands == [], why

# A download refused before it started must not leave its list file behind.
refused_run = FakeWindow()
refused_run._run = lambda *_a, **_k: False
refused_fetch = ipod_gui.IpodWindow._start_youtube_download(
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
ipod_gui.IpodWindow._finish(
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
ipod_gui.IpodWindow._finish(generic_failure_window, 2, "Finished")
assert generic_failure_window.toasts == ["Failed (exit 2) - see Details"]

# Success must not fire it, or every finished download would claim to have
# failed as well.
success_window = FailureWindow()
ipod_gui.IpodWindow._finish(
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

    _on_search_changed = ipod_gui.IpodWindow._on_search_changed
    _clear_search = ipod_gui.IpodWindow._clear_search
    _cancel_search_timeout = ipod_gui.IpodWindow._cancel_search_timeout
    _set_search_note = ipod_gui.IpodWindow._set_search_note
    _finish_youtube_search = ipod_gui.IpodWindow._finish_youtube_search
    _navigate = ipod_gui.IpodWindow._navigate


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


ipod_gui.gst = lambda: FakeGst


def preview_track(name, duration=180.0, state=None):
    return ipod_gui.Track(
        Path("/music") / f"{name}.mp3",
        {"title": name, "artist": "Someone", "album": "Somewhere",
         "duration": duration},
        state or ipod_gui.STATE_LIBRARY,
    )


repaints = []
player = ipod_gui.PreviewPlayer(lambda: repaints.append(True))
first, second = preview_track("First"), preview_track("Second", 90.0)
player.play([first, second], 0)

pipeline = FakeGst.pipeline
assert player.state == ipod_gui.PLAY_LOADING, player.state
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
assert player.state == ipod_gui.PLAY_LOADING, player.state
pipeline.bus.deliver(
    "message::state-changed", FakeMessage(src=pipeline, state=FakeGst.State.PLAYING)
)
assert player.state == ipod_gui.PLAY_PLAYING, player.state

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
for _ in range(ipod_gui.SEEK_SETTLE_POLLS):
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
assert player.state == ipod_gui.PLAY_LOADING, player.state
assert pipeline.properties["uri"] == "file:///music/Second.mp3"

# The end of the queue stops rather than wrapping: a queue started from one
# album would otherwise play forever with nothing in the bar saying it looped.
pipeline.bus.deliver("message::eos", FakeMessage())
assert player.state == ipod_gui.PLAY_IDLE, player.state
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
pipeline.position = int((ipod_gui.RESTART_WINDOW + 1) * FakeGst.SECOND)
player._tick()
assert player.position > ipod_gui.RESTART_WINDOW, player.position
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
assert player.state == ipod_gui.PLAY_IDLE, player.state
assert player.error == "Missing decoder for audio/x-flac", player.error
assert player.track is first, "the bar lost the name of the track that failed"

# Starting the next track clears the previous failure.
player.play([second], 0)
assert player.error is None, player.error

# With no GStreamer at all the player says so rather than raising into a click
# handler, and nothing is left running behind the message.
ipod_gui.gst = lambda: None
silent = ipod_gui.PreviewPlayer(None)
silent.play([first], 0)
assert silent.state == ipod_gui.PLAY_IDLE, silent.state
assert silent.error == ipod_gui.GSTREAMER_UNAVAILABLE, silent.error
assert silent._pipeline is None, "a player with no GStreamer built a pipeline"
ipod_gui.gst = lambda: FakeGst

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
        self._painted_art = ipod_gui.UNPAINTED
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
        self.player = ipod_gui.PreviewPlayer(self._update_now_playing)

    _update_now_playing = ipod_gui.IpodWindow._update_now_playing
    _playing_status = ipod_gui.IpodWindow._playing_status
    play_from = ipod_gui.IpodWindow.play_from
    _supersede_preview_fetch = ipod_gui.IpodWindow._supersede_preview_fetch
    _cancel_preview_fetch = ipod_gui.IpodWindow._cancel_preview_fetch
    _terminate_preview_process = staticmethod(
        ipod_gui.IpodWindow._terminate_preview_process
    )


# The bar builds artwork and labels as it repaints, which needs a display it
# does not have here. Patched for this section only; every other check in the
# file runs against the real helpers.
real_label, real_cover = ipod_gui.label, ipod_gui.make_cover
ipod_gui.label = BarWidget
ipod_gui.make_cover = lambda *_args, **_kwargs: BarWidget()

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
assert ipod_gui.STATE_LIBRARY in bar.playing_state_dot.classes
assert bar.playing_stack.opacity == 1.0
assert bar.transport_buttons["next"].sensitive, "a queued next track was not offered"
assert bar.playing_status.get_text() == "Opening…", bar.playing_status.get_text()
assert not bar.seek_scale.sensitive
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
assert bar.player.state == ipod_gui.PLAY_PAUSED
assert bar.player._poll is None
assert bar.transport_buttons["play"].icon == "media-playback-start-symbolic"

# Pressing play again while a track is still opening stops it, rather than
# doing nothing until the pipeline gets where it was already going. The
# transition that completes afterwards must not undo that.
bar.player.play([first], 0)
assert bar.player.state == ipod_gui.PLAY_LOADING
bar.player.toggle()
assert bar.player.state == ipod_gui.PLAY_PAUSED, bar.player.state
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
assert bar.player.state == ipod_gui.PLAY_PAUSED, "a paused track resumed itself"

# The last track of a queue offers no next, or the button would promise
# something pressing it cannot deliver.
bar.player.play([first, second], 1)
assert not bar.transport_buttons["next"].sensitive

# A previewed track is a download kept only so it could be heard, and the bar
# says so: mistaking one for a track already in the library is how a preview
# gets lost when the cache is pruned.
previewed = preview_track("Fetched", 120.0, ipod_gui.STATE_PREVIEW)

# Before any of that, the download itself. It takes seconds rather than the
# instant a local file takes, so the bar carries the wait - named, because a
# bar that stays idle for four seconds reads as a play button that did nothing.
bar.player.fetch(previewed)
assert bar.player.state == ipod_gui.PLAY_FETCHING, bar.player.state
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
assert bar.player.state == ipod_gui.PLAY_FETCHING, "toggle disturbed a fetch"

# A download that never arrives says so where the track was named, and takes
# the queue with it: a play button offering a file that was never downloaded
# fails again on every press.
bar.player.fail(previewed, ipod_gui.PREVIEW_FAILED)
assert bar.playing_stack.child_name == "message"
assert "ipod-fetch.sh --update" in bar.playing_message.get_text()
assert bar.player.queue == [], bar.player.queue
assert not bar.transport_buttons["play"].sensitive

bar.player.play([previewed], 0)
# Opening, not fetching: the file is on disk by now, and this is the same
# second any library track takes to start.
assert bar.playing_status.get_text() == "Opening…", bar.playing_status.text
assert ipod_gui.STATE_PREVIEW in bar.playing_state_dot.classes
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
missing = BarWindow(unavailable=ipod_gui.GSTREAMER_UNAVAILABLE)
missing._update_now_playing()
assert missing.playing_stack.child_name == "message"
assert missing.playing_message.get_text() == (
    "GStreamer is not installed - see Preview playback in the README"
)


class FakeModel:
    def __init__(self, tracks):
        self.items = [ipod_gui.TrackItem(track, n) for n, track in enumerate(tracks, 1)]

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

ipod_gui.label, ipod_gui.make_cover = real_label, real_cover


# ----------------------------------------------------------- preview cache
#
# Hearing a YouTube result downloads it, so previewing twenty songs would add
# twenty files to the music folder if the download went straight there. It
# goes to a prunable cache instead, and adding a previewed track is what moves
# it out. Both halves of that are checked against real files: a promotion that
# silently leaves the file in the cache loses it at the next prune.

preview_cache = Path(tempfile.mkdtemp())
preview_library = Path(tempfile.mkdtemp())
ipod_gui.PREVIEW_CACHE = preview_cache
ipod_gui.PREVIEW_INCOMING = preview_cache / ".incoming"
ipod_gui.YOUTUBE_LIBRARY = preview_library


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
preview_fetch = ipod_gui.fetch_command("https://youtu.be/abc", preview_cache)
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

entries = ipod_gui.preview_cache_entries(preview_cache)
assert [path for path, _size, _mtime in entries] == [old, new], entries
# A download still running lives under .incoming and is not a preview yet;
# counting it would put a half-written file in the grid and in the figures.
assert all(".incoming" not in str(path) for path, _s, _m in entries), entries

assert ipod_gui.cached_preview_path("abc123", preview_cache) == new
assert ipod_gui.cached_preview_path("fJ9rUzIMcZQ", preview_cache) == old
assert ipod_gui.cached_preview_path("", preview_cache) is None
assert ipod_gui.cached_preview_path("nosuchid", preview_cache) is None
# The id is matched as the text ipod-fetch.sh wrote into the filename, not as
# a glob: "[abc123]" read as a pattern is a character class matching a file
# named "1.mp3", and the cache would then hand back the wrong song.
assert ipod_gui.cached_preview_path("a", preview_cache) is None
assert ipod_gui.cached_preview_path("abc", preview_cache) is None

# Oldest first, and only as far as it takes to get back under the limit.
assert ipod_gui.prunable_previews(entries, 1000) == []
assert ipod_gui.prunable_previews(entries, 60) == [old]
assert ipod_gui.prunable_previews(entries, 0) == [old, new]
# Never what is playing: the bar would be left naming a file that is gone.
assert ipod_gui.prunable_previews(entries, 0, keep=[old]) == [new]

assert ipod_gui.promote_destination(new, preview_cache, preview_library) == (
    preview_library / "Nirvana" / "Lithium [abc123].mp3"
)
# A file that somehow sits outside the cache still lands somewhere sensible
# rather than raising on the way out of it.
assert ipod_gui.promote_destination(
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
        self.pending_skipped_symlinks = {}
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
        self.player = ipod_gui.PreviewPlayer(lambda: None)

    def _toast(self, message):
        self.toasts.append(message)

    def _refresh_current_view(self, scan_complete=True):
        self.repaints += 1

    def _populate_device_summary(self):
        pass

    def _update_now_playing(self):
        pass

    def _update_device_controls(self):
        pass

    _populate_cache_card = ipod_gui.IpodWindow._populate_cache_card
    on_clear_cache = ipod_gui.IpodWindow.on_clear_cache
    _promote_preview = ipod_gui.IpodWindow._promote_preview
    _prune_preview_cache = ipod_gui.IpodWindow._prune_preview_cache
    _forget_empty_preview_folders = staticmethod(
        ipod_gui.IpodWindow._forget_empty_preview_folders
    )
    _finish_preview_fetch = ipod_gui.IpodWindow._finish_preview_fetch
    _fail_preview_fetch = ipod_gui.IpodWindow._fail_preview_fetch
    _apply_preview_scan = ipod_gui.IpodWindow._apply_preview_scan
    _supersede_preview_fetch = ipod_gui.IpodWindow._supersede_preview_fetch
    _cancel_preview_fetch = ipod_gui.IpodWindow._cancel_preview_fetch
    _terminate_preview_process = staticmethod(
        ipod_gui.IpodWindow._terminate_preview_process
    )
    _preview_track = ipod_gui.IpodWindow._preview_track
    preview_result = ipod_gui.IpodWindow.preview_result
    _preview_unavailable_reason = ipod_gui.IpodWindow._preview_unavailable_reason
    _merge_states = ipod_gui.IpodWindow._merge_states
    _queue_sources = ipod_gui.IpodWindow._queue_sources
    _commit_queue_sources = ipod_gui.IpodWindow._commit_queue_sources
    _pending_accounting = ipod_gui.IpodWindow._pending_accounting
    _pending_change_count = ipod_gui.IpodWindow._pending_change_count
    _pending_track = ipod_gui.IpodWindow._pending_track
    _record_for_track = staticmethod(ipod_gui.IpodWindow._record_for_track)


def previewed(path):
    return ipod_gui.Track(
        path,
        {
            "title": Path(path).stem,
            "artist": Path(path).parent.name,
            "size": Path(path).stat().st_size,
        },
        ipod_gui.STATE_PREVIEW,
    )


# A previewed track is in the grid like any other, which is what finally
# produces the third state the whole window already knows how to draw.
cache_window = PreviewWindow()
cache_window.library.previews = [previewed(old), previewed(new)]
assert len(cache_window.library.all_tracks()) == 2
assert {t.state for t in cache_window.library.all_tracks()} == {
    ipod_gui.STATE_PREVIEW
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
assert kept.state == ipod_gui.STATE_LIBRARY, kept.state
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
assert detached.toasts[-1] == f"Kept in {ipod_gui.home_relative(preview_library)}"

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
real_limit = ipod_gui.PREVIEW_CACHE_LIMIT
ipod_gui.PREVIEW_CACHE_LIMIT = 50
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
assert landing.player.state == ipod_gui.PLAY_LOADING, landing.player.state
ipod_gui.PREVIEW_CACHE_LIMIT = real_limit

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
result = ipod_gui.SearchResult("Song 2", "Blur", 120.0, "https://youtu.be/blur2", "blur2")
cached_window.youtube_unavailable = "yt-dlp is not installed"
assert cached_window._preview_unavailable_reason(result) is None
cached_window.preview_result(result)
assert cached_window.player.track is not None
assert cached_window.player.track.path == str(landed), cached_window.player.track.path
assert cached_window.library.previews[0].path == str(landed)

# One that is not cached has to be downloaded first, so it needs everything a
# download needs, and a disabled button that says why beats an Add that fails
# several steps later.
uncached = ipod_gui.SearchResult("Nothing", "Nobody", 0, "https://youtu.be/none", "none")
assert cached_window._preview_unavailable_reason(uncached) == "yt-dlp is not installed"
# No GStreamer means nothing can be previewed at all, cached or not.
cached_window.preview_unavailable = ipod_gui.GSTREAMER_UNAVAILABLE
assert cached_window._preview_unavailable_reason(result) == ipod_gui.GSTREAMER_UNAVAILABLE

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
assert ipod_gui.preview_cache_entries(preview_cache) == []
assert ipod_gui.cached_preview_path("blur2", preview_cache) is None

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
