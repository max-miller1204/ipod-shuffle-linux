"""Turning script output and raw numbers into what a label can show."""

import json
import re
from pathlib import Path


# The scripts colour their output for a terminal. A text view has no idea what
# to do with the escape sequences, so every line arrived as literal noise:
# "[36m==>[0m Removed 1 track(s)".
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# What the sync bar says about each thing a script has finished with.
#
# The whole vocabulary each side of the stream can use is declared in
# ipod-report.py, which refuses to encode a status that is not in it, so a word
# missing from here is a status this window would show as a bare identifier
# rather than one it could quietly drop.
FILE_STATUS_LABELS = {
    "copied": "Copied",
    "duplicate": "Already there",
    "missing": "Not found",
    "broken": "Broken link",
    "removed": "Removed",
}

PLAYLIST_STATUS_LABELS = {
    "written": "Playlist written",
    "removed": "Playlist removed",
    "skipped": "Playlist skipped",
}

# The stretches of a run that are one long wait rather than a file at a time.
# Shown in place of the file name, because a bar still showing the last track
# it copied while the database is being rebuilt looks like a run that stalled.
STAGE_LABELS = {
    "backup": "Backing up",
    "clear": "Clearing the iPod",
    "copy": "Copying",
    "rebuild": "Rebuilding the database",
}


def progress_event(line):
    """One event of a script's progress stream, or None if it is not one.

    The scripts report what they are doing as JSON on a stream of its own,
    which is what drives the sync bar. This used to be a regex over the copy
    lines in their human output, so rewording one of those broke the bar and
    nothing failed when it did.

    A line that will not parse is dropped rather than raised on: the stream is
    how the window watches a copy that is already under way on the device, and
    the copy is not made any less real by a line nobody can read.
    """
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
        return None
    return event

# Said by every confirmation whose answer rebuilds the database over names the
# device goes on holding, and said once here so the same consequence does not
# reach the user in three wordings. The builder empties iPod_Control/Speakable
# at the start of every run and refills only what it can speak, so a machine
# with no engine takes the names down with it - see _sync_options for the whole
# of it. A wipe rebuilds too and says none of this: it takes the tracks and
# playlists themselves, so it leaves nothing behind that had a name to lose.
SPOKEN_NAMES_LOST = (
    "With no speech engine installed, the rebuild that removal runs leaves "
    "the iPod's spoken names gone."
)


def strip_ansi(text):
    """Text as a terminal would show it, without the colour escapes."""
    return ANSI_ESCAPE.sub("", text)


def home_relative(path):
    """~/Music/youtube rather than the full path, which reads as noise."""
    try:
        return str(Path("~", Path(path).relative_to(Path.home())))
    except ValueError:
        return str(path)


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}".replace(".0 ", " ")
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def plural(count, singular, suffix="s"):
    return f"{count} {singular}" + ("" if count == 1 else suffix)


def human_duration(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"
