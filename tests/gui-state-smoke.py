#!/usr/bin/env python3
"""Focused checks for the device probe and the flags a sync is run with.

The probe is the single pass the window reads a connected device with. Every
call it folds together used to run on the main loop, one at a time, so the
value it returns has to agree with each of them exactly: a probe that quietly
disagreed would paint a device that is not the one plugged in.

The flags are the other half, and they are no longer a reading of anything.
The device this runs against was last synced with a grouping the window has
since stopped offering, so what it is handed now is what retires that choice.
"""

import json
import sys
from pathlib import Path

from harness import gui


class FakeWindow:
    """A window on a machine that can speak names, and one that cannot."""

    def __init__(self, speech=True):
        self.speech_engine_available = speech


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
        self.painted = []
        self.stack = self

    def set_visible_child_name(self, name):
        self.painted.append(name)

    def _select_mount(self, mount_point, identity):
        self.mount_point = mount_point
        self.device_identity = identity

    def _populate_device_summary(self):
        self.painted.append("summary")

    def _populate_playlist_rail(self):
        self.painted.append("rail")

    def _load_device_tracks_async(self):
        self.painted.append("tags")


mount_point = Path(sys.argv[1])
saved_options = (
    (mount_point / "iPod_Control" / ".sync-options").read_text().splitlines()
)

# One crossing to the device has to bring back exactly what the five separate
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
assert probe.playlists == gui.list_playlists(mount_point), probe.playlists
assert probe.spoken == gui.spoken_playlists(
    mount_point, [name for name, _entries in probe.playlists]
), probe.spoken
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

# Fixed flags, not a reading of the device. This iPod was last synced with a
# genre grouping, and the sync the GUI launches has to overwrite that rather
# than inherit it: nothing in the window offers a grouping any more, so a
# device still asking for one would keep generating playlists nobody chose.
assert "--auto-id3-playlists" in saved_options, saved_options
command_options = gui.IpodWindow._sync_options(FakeWindow())
assert command_options == ["--voiceover", "--playlist-voiceover"], command_options

# Without an engine to generate the recordings, asking for spoken names would
# produce none. --forget-options clears the saved file just as passing flags
# overwrites it, so the stale grouping goes either way.
speechless_options = gui.IpodWindow._sync_options(FakeWindow(speech=False))
assert speechless_options == ["--forget-options"], speechless_options

print(
    json.dumps(
        {
            "saved_on_device": saved_options,
            "sync_script_arguments": command_options,
            "sync_script_arguments_without_speech": speechless_options,
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
