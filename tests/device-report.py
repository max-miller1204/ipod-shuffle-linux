#!/usr/bin/env python3
"""Pins the scripts' JSON report against the window's reading of the device.

ipod-report.py repeats what ipod_gui/device.py does rather than importing it,
because the scripts are the product and have to run on a machine with no GTK
bindings, exactly as lib.sh's list_vfat_mounts repeats find_ipods(). What that
costs is the chance of the two drifting: a playlist parsed one way here and
another way there, or a spoken name derived from a digest that stopped
matching. Both are silent, and both would be discovered as a caller acting on
a report of a device that is not the one plugged in.

So the two readings are taken of one device and compared field by field. The
report is the one the script actually printed, not a second call into the
writer, because what is being pinned is what a caller receives.

The report's own promises are checked here too - the count agreeing with the
list it counts, the storage figures adding up - since those are properties of
the document rather than of either reader.
"""

import json
import shutil
import sys
from pathlib import Path

from harness import gui

mount_point = Path(sys.argv[1])
report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

assert report["schema"] == 1, report["schema"]
assert report["mount_point"] == str(mount_point), report["mount_point"]
assert report["identity"] == gui.volume_identity(str(mount_point)), report["identity"]

# The paths ipod-remove.sh takes as arguments, in the order the window lists
# them: a caller reading these has to be able to hand one straight back.
assert report["tracks"] == gui.list_tracks(mount_point), report["tracks"]
assert report["track_count"] == gui.count_tracks(mount_point), report["track_count"]
assert report["track_count"] == len(report["tracks"]), report["track_count"]
assert report["track_count"] > 0, "no tracks on the device to report"

playlists = [(entry["name"], entry["entries"]) for entry in report["playlists"]]
assert playlists == gui.list_playlists(mount_point), playlists
assert playlists, "no playlists at the volume root to report"

# Case-folded on the way in because that is how the window answers: a playlist
# made here as "Gym" and synced onto a FAT volume that already held "gym" is
# announced under the name the volume kept.
announced = {entry["name"].lower() for entry in report["playlists"] if entry["spoken"]}
assert announced == gui.spoken_playlists(
    mount_point, [name for name, _entries in playlists]
), announced

saved = mount_point / "iPod_Control" / ".sync-options"
expected_options = (
    saved.read_text(encoding="utf-8").splitlines() if saved.is_file() else []
)
assert report["sync_options"] == expected_options, report["sync_options"]

# Free space moves under a running system, so only the size of the volume is
# compared; the rest has to be consistent with itself and with that size.
storage = report["storage"]
assert storage is not None, "a readable device reported no storage at all"
assert storage["total_bytes"] == shutil.disk_usage(mount_point).total, storage
assert storage["used_bytes"] + storage["free_bytes"] <= storage["total_bytes"], storage
assert storage["free_bytes"] >= 0, storage

print(
    json.dumps(
        {
            "mount_point": report["mount_point"],
            "identity": report["identity"],
            "tracks": report["track_count"],
            "playlists": [name for name, _entries in playlists],
            "announced": sorted(announced),
            "sync_options": report["sync_options"],
            "free_bytes": storage["free_bytes"],
        },
        indent=2,
    )
)
