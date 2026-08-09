#!/usr/bin/env python3
"""Prints what an Add to playlist gets back in each way a playlist stops reading.

The window's sentence comes from this answer, so this is the same run of
situations as the screenshots, one layer down and including the two the GUI
cannot easily be put into: a folder that cannot be listed, and a folder that
has gone with the drive under it.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "tests"))
from harness import gui  # noqa: E402

MUSIC = Path(tempfile.mkdtemp(prefix="music-"))
LISTED = MUSIC / "Highway.mp3"
LISTED.write_bytes(b"song")
SONG = MUSIC / "Sunrise Drive.mp3"
SONG.write_bytes(b"song")

cases = []


def case(what, path):
    _entries, complete = gui.playlist_contents(path)
    added = gui.add_entries(path, [str(SONG)])
    cases.append((what, repr(complete), repr(added)))


folder = Path(tempfile.mkdtemp(prefix="playlists-"))

readable = folder / "Gym.m3u"
gui.write_playlist_entries(readable, [str(LISTED)])
case("a playlist that is there and readable", readable)

deleted = folder / "Road Trip.m3u"
gui.write_playlist_entries(deleted, [str(LISTED)])
deleted.unlink()
case("deleted by another program", deleted)

unplugged = folder / "On A Drive.m3u"
unplugged.symlink_to("/nowhere/mounted/On A Drive.m3u")
case("in the folder, pointing at an unplugged drive", unplugged)

blocked = Path(tempfile.mkdtemp(prefix="blocked-")) / "Playlists"
blocked.write_bytes(b"a file where the folder should be")
case("in a folder that cannot be listed", blocked / "Locked Away.m3u")

if os.geteuid() != 0:
    shut = Path(tempfile.mkdtemp(prefix="shut-"))
    gui.write_playlist_entries(shut / "Locked Away.m3u", [str(LISTED)])
    shut.chmod(0o000)
    try:
        case("in a folder with no read permission", shut / "Locked Away.m3u")
    finally:
        shut.chmod(0o700)

gone_folder = Path(tempfile.mkdtemp(prefix="unlisted-")) / "Music"
case("in a folder that is not there at all", gone_folder / "On The Drive.m3u")

unplugged_folder = Path(tempfile.mkdtemp(prefix="unplugged-")) / "Music"
unplugged_folder.symlink_to("/nowhere/mounted/Music")
case("in a Music folder on an unplugged drive", unplugged_folder / "Road.m3u")

width = max(len(row[0]) for row in cases)
print(f"{'the playlist a user pressed Add on':<{width}}  {'read':<14}  add_entries")
print("-" * (width + 32))
for what, complete, added in cases:
    print(f"{what:<{width}}  {complete:<14}  {added}")

# All three edits, on the one playlist that has gone: the difference is
# answered once where the read happens rather than three times over.
print()
print("every edit on the playlist another program deleted:")
print(f"  add_entries   -> {gui.add_entries(deleted, [str(SONG)])!r}")
print(f"  remove_entry  -> {gui.remove_entry(deleted, str(LISTED))!r}")
print(f"  move_entry    -> {gui.move_entry(deleted, 0, 1)!r}")
print(f"  and it stayed deleted: {not deleted.exists()}")
