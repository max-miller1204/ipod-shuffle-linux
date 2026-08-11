"""Turning script output and raw numbers into what a label can show."""

import re
from pathlib import Path


# The scripts colour their output for a terminal. A text view has no idea what
# to do with the escape sequences, so every line arrived as literal noise:
# "[36m==>[0m Removed 1 track(s)".
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# ipod-sync.sh prints one of these per file as it copies. Parsed to drive the
# sync bar, which would otherwise have nothing but an indeterminate pulse to
# show for an operation that can run for minutes.
COPIED_LINE = re.compile(r"^\s*\+ (?P<name>.+?) -> (?P<dest>.+?)\s*$")

# Said by every confirmation whose answer rebuilds the iPod's database, and
# said once here so the same consequence does not reach the user in three
# wordings. The builder empties iPod_Control/Speakable at the start of every
# run and refills only what it can speak, so a machine with no engine takes
# the names down with it - see _sync_options for the whole of it.
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
