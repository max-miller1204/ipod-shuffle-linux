#!/usr/bin/env python3
"""Focused checks for GUI playlist state and command-line mapping.

Also checks the single pass the window reads a connected device with. Every
call it folds together used to run on the main loop, one at a time, so the
value it returns has to agree with each of them exactly: a probe that quietly
disagreed would paint a device that is not the one plugged in.
"""

import json
import sys
from pathlib import Path

from harness import gui


class Value:
    def __init__(self, value):
        self.value = value

    def get_selected(self):
        return self.value

    def get_active(self):
        return self.value


class FakeWindow:
    loaded_playlist_mode = 0
    loaded_playlist_args = []
    playlist_mode = Value(3)
    track_voiceover = Value(True)
    playlist_voiceover = Value(True)


class ProbeWindow:
    """Records what a probe of a connected device leaves on the window."""

    def __init__(self):
        self.busy = False
        self.probe_generation = 1
        self.probe_answered = False
        self.mount_point = None
        self.device_identity = None
        self.device_track_count = 0
        self.device_usage = None
        self.playlists = []
        self.spoken = set()
        self.loaded_options = None
        self.painted = []
        self.stack = self

    def set_visible_child_name(self, name):
        self.painted.append(name)

    def _select_mount(self, mount_point, identity):
        self.mount_point = mount_point
        self.device_identity = identity

    def _load_sync_options(self, options):
        self.loaded_options = options

    def _populate_device_summary(self):
        self.painted.append("summary")

    def _populate_playlist_rail(self):
        self.painted.append("rail")

    def _load_device_tracks_async(self):
        self.painted.append("tags")


mount_point = Path(sys.argv[1])
state = gui.saved_sync_options(mount_point)
assert state == (
    3,
    ["--id3-playlists={genre}"],
    True,
    True,
), state

# One crossing to the device has to bring back exactly what the six separate
# calls did, so each is still run here and compared against the probe's copy.
original_find_ipods = gui.find_ipods
gui.find_ipods = lambda: [str(mount_point)]
try:
    probe = gui.probe_device()
finally:
    gui.find_ipods = original_find_ipods

assert probe.readable is True, probe.readable
assert probe.mount_point == str(mount_point), probe.mount_point
assert probe.identity == gui.volume_identity(str(mount_point)), probe.identity
assert probe.sync_options == state, probe.sync_options
assert probe.playlists == gui.list_playlists(mount_point), probe.playlists
assert probe.spoken == gui.spoken_playlists(mount_point), probe.spoken
assert probe.track_count == gui.count_tracks(mount_point), probe.track_count
# The synthetic device is a real directory, so this is a real reading; a probe
# that returned None here would be the storage meter silently going blank.
assert probe.usage is not None and probe.usage.total > 0, probe.usage

# A superseded walk stops counting rather than finishing a number nothing will
# read, which is what keeps two probes from competing for the same bus. The
# real count has to be non-zero first, or a walk that never started would look
# exactly like one that stopped.
assert probe.track_count > 0, probe.track_count
abandoned = gui.count_tracks(mount_point, cancelled=lambda: True)
assert abandoned == 0, abandoned

# Painting afterwards reads the probe, never the device: every figure the
# window shows has to arrive from the value rather than from a second walk.
probe_window = ProbeWindow()
gui.IpodWindow._apply_probe(probe_window, probe_window.probe_generation, probe)
assert probe_window.mount_point == str(mount_point), probe_window.mount_point
assert probe_window.device_identity == probe.identity, probe_window.device_identity
assert probe_window.loaded_options == state, probe_window.loaded_options
assert probe_window.playlists == probe.playlists, probe_window.playlists
assert probe_window.spoken == probe.spoken, probe_window.spoken
assert probe_window.device_track_count == probe.track_count, (
    probe_window.device_track_count
)
assert probe_window.device_usage is probe.usage, probe_window.device_usage
assert probe_window.probe_answered is True, probe_window.probe_answered
assert probe_window.painted == ["device", "summary", "rail", "tags"], (
    probe_window.painted
)

command_options = gui.IpodWindow._sync_options(FakeWindow())
assert command_options == [
    "--id3-playlists={genre}",
    "--voiceover",
    "--playlist-voiceover",
], command_options

print(
    json.dumps(
        {
            "restored_gui_state": {
                "playlist_mode": "By genre",
                "speak_tracks": state[2],
                "speak_playlists": state[3],
            },
            "sync_script_arguments": command_options,
            "device_probe": {
                "mount_point": probe.mount_point,
                "readable": probe.readable,
                "tracks": probe.track_count,
                "playlists": [name for name, _entries in probe.playlists],
                "spoken": sorted(probe.spoken),
                "free_bytes": probe.usage.free,
            },
        },
        indent=2,
    )
)
