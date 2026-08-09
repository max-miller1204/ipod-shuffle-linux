#!/usr/bin/env python3
"""Whether the window can see the spoken names the real builder just wrote.

The shuffle has no screen, so a playlist it cannot announce is one the user
has no way to identify, and the window says which are which. It has to say so
from the recordings the database builder leaves behind - and those are named
after an id derived from the playlist's name, never after the name itself.
Reading the directory and matching stems against playlist names therefore
finds nothing on a device where every playlist is in fact announced.

Run against a device the real builder has written spoken names onto, because
that is the only writer whose naming counts. A double agreeing with this code
would agree with it wrong just as readily.
"""

import json
import sys
from pathlib import Path

from harness import gui

mount_point = Path(sys.argv[1])
names = [name for name, _entries in gui.list_playlists(mount_point)]
assert names, "no playlists at the volume root to ask about"

recordings = sorted(
    path.name
    for path in (
        mount_point / "iPod_Control" / "Speakable" / "Playlists"
    ).iterdir()
    if path.is_file()
)
# One per playlist plus the built-in All Songs list, which the device speaks
# for itself and the window never asks about.
assert len(recordings) == len(names) + 1, recordings

spoken = gui.spoken_playlists(mount_point, names)
assert spoken == {name.lower() for name in names}, (spoken, names)

# The same reading done the way it looks like it should work, which is how
# this was wrong: every playlist above is announced, and a directory listing
# matched against the names says none of them are.
stems = {Path(recording).stem.lower() for recording in recordings}
assert not stems & {name.lower() for name in names}, stems

# A playlist the device holds no recording for is the other half of the
# answer, or the window would call every playlist announced.
never_synced = gui.spoken_playlists(mount_point, ["Never Synced"])
assert not never_synced, f"announced with no recording on the device: {never_synced}"

print(
    json.dumps(
        {
            "playlists": names,
            "recordings": recordings,
            "announced": sorted(spoken),
        },
        indent=2,
    )
)
