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
        self.pending_device_identity = None
        self.pending = {}
        self.pending_sources = {}
        self.commands = []
        self.toasts = []
        self.track_names = {}
        self.library = type("Library", (), {"tracks": []})()
        self.speech_engine_available = True
        self.playlist_voiceover = FakeSwitch()

    def _run(self, argv, busy_message, done_message, then=None, clear=True):
        self.commands.append(argv)
        self.then = then

    def _toast(self, message):
        self.toasts.append(message)

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
    _queue_sources = ipod_gui.IpodWindow._queue_sources
    _queue_paths = ipod_gui.IpodWindow._queue_paths
    _queue_playlist = ipod_gui.IpodWindow._queue_playlist
    _update_device_controls = ipod_gui.IpodWindow._update_device_controls


# ------------------------------------------------------------------ removal

window = FakeWindow()
relpath = "Road Trip/Disc 1/01 - Highway.mp3"
ipod_gui.IpodWindow._on_remove_response(window, None, "remove", relpath)

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
    ipod_gui.IpodWindow._on_remove_response(quiet, None, answer, relpath)
    assert quiet.commands == [], (answer, quiet.commands)

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
    fallback = ipod_gui.scan_tracks(scan_root)
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
    records = ipod_gui.scan_tracks(scan_root, streamed.append, timeout=0.4)
    elapsed = time.monotonic() - started
    assert elapsed < 2, elapsed
    assert records[0]["title"] == "Tagged", records
    assert [record["title"] for record in streamed] == ["Fallback", "Tagged"], streamed

    started = time.monotonic()
    ipod_gui.scan_tracks(
        scan_root,
        timeout=10,
        cancelled=lambda: time.monotonic() - started >= 0.1,
    )
    assert time.monotonic() - started < 2, "cancelled scan left its reader running"
finally:
    ipod_gui._tag_interpreter = original_interpreter
    ipod_gui.TAG_PYTHON = original_tag_python
    ipod_gui._TAG_READER = original_reader


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
        self.pending_device_identity = "uuid:A"
        self.pending = {queued.path: queued}
        self.pending_sources = {queued.path: {queued.path}}
        self.tag_generation = 0
        self._device_scan_tracks = {}
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
assert identity_window.pending == {}, identity_window.pending
assert identity_window.pending_sources == {}, identity_window.pending_sources
assert identity_window.toasts and "different iPod" in identity_window.toasts[-1]

# ----------------------------------------------------------------- playlist

playlist_window = FakeWindow()
playlist_root = Path(tempfile.mkdtemp())
playlist_track = playlist_root / "Party Song.mp3"
playlist_track.touch()
playlist_path = playlist_root / "Party Mix.m3u"
playlist_path.write_text(f"{playlist_track.name}\n", encoding="utf-8")
ipod_gui.IpodWindow._add_playlist(playlist_window, playlist_path)

assert playlist_window.commands == [], playlist_window.commands
assert playlist_window.pending_sources == {
    str(playlist_path): {str(playlist_path), str(playlist_track)}
}, playlist_window.pending_sources
assert set(playlist_window.pending) == {
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
busy_window.pending = {}

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
queued_window.pending = {"/home/alex/Music/one.mp3": object()}
ipod_gui.IpodWindow._set_busy(queued_window, False)
assert queued_window.sync_button.sensitive, "queued changes could not be synced"

# -------------------------------------------------------- playlist removal

playlist_removal = FakeWindow()
ipod_gui.IpodWindow._on_playlist_remove_response(playlist_removal, None, "remove", "twizzy")

playlist_rm = playlist_removal.commands[0]
assert playlist_rm[0].endswith("ipod-remove.sh"), playlist_rm
assert playlist_rm[1:3] == ["--ipod", playlist_removal.mount_point], playlist_rm
assert "--yes" in playlist_rm, playlist_rm
assert "--playlist" in playlist_rm, playlist_rm
assert playlist_rm[-2:] == ["--", "twizzy"], playlist_rm

for answer in ("cancel", "close"):
    quiet = FakeWindow()
    ipod_gui.IpodWindow._on_playlist_remove_response(quiet, None, answer, "twizzy")
    assert quiet.commands == [], (answer, quiet.commands)

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
queued_paths = {
    "/home/alex/Music/Kova/Nightbus/01 Nightbus.mp3": object(),
    "/home/alex/Music/-Dashed Title.mp3": object(),
}
queue_window.pending = dict(queued_paths)
queue_window.pending_sources = {
    path: {path} for path in queued_paths
}
queue_window.pending_device_identity = queue_window.device_identity
ipod_gui.IpodWindow.on_sync_pending(queue_window, None)

staged = queue_window.commands[0]
assert staged[0].endswith("ipod-sync.sh"), staged
assert staged[1:3] == ["--ipod", queue_window.mount_point], staged
# Everything after -- is a path, because a track title can begin with a dash.
separator = staged.index("--")
assert sorted(staged[separator + 1:]) == sorted(queued_paths), staged
assert queue_window.sync_total == len(queued_paths), queue_window.sync_total

# The queue is only cleared once the copy has actually succeeded, which is
# what the then callback is for.
assert queue_window.pending == queued_paths, "queue emptied before the sync ran"
cleared = queue_window.then()
assert queue_window.pending == {}, "queue survived a successful sync"
assert queue_window.pending_sources == {}, "sync sources survived a successful sync"
assert queue_window.pending_device_identity is None, "queue stayed device-bound"
assert isinstance(cleared, str), cleared

# An empty queue must not launch a script at all.
idle_window = FakeWindow()
idle_window.pending = {}
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
assert ipod_gui.write_playlist(reorder_root, m3u, reordered)

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
# The rewrite is atomic, so no half-written list can be left behind for the
# firmware to choke on if the device is pulled mid-write.
assert not list(reorder_root.glob(".*tmp")), list(reorder_root.glob(".*tmp"))

pls = reorder_root / "Gym.pls"
pls.write_text("[playlist]\nFile1=iPod_Control/Music/F00/LDPX.mp3\n", encoding="utf-8")
assert ipod_gui.write_playlist(reorder_root, pls, ["F00/QMRT.mp3", "F00/LDPX.mp3"])
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
