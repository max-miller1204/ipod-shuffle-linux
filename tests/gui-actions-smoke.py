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
import tempfile
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


class FakeWindow:
    """Records the commands the window would have run."""

    def __init__(self):
        self.mount_point = "/media/alex/Alex's iPod"
        self.commands = []
        self.toasts = []
        self.track_names = {}

    def _run(self, argv, busy_message, done_message, then=None, clear=True):
        self.commands.append(argv)
        self.then = then

    def _toast(self, message):
        self.toasts.append(message)

    def _sync_options(self):
        return ["--dir-playlists=1", "--playlist-voiceover"]

    # The step that decides what to copy is the one under test, so it is the
    # real implementation rather than another stand-in.
    _sync_downloaded = ipod_gui.IpodWindow._sync_downloaded


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
sync = window.then()
assert sync[0][0].endswith("ipod-sync.sh"), sync
assert sync[0][1:3] == ["--ipod", window.mount_point], sync
# The playlist and voiceover choices made in the window apply to music that
# arrives this way too, rather than only to Add Music.
assert "--dir-playlists=1" in sync[0], sync
assert sync[0][-2:] == ["--", str(downloaded)], sync
assert str(library / "Old Artist") not in sync[0], sync
assert not new_tracks.exists(), "the track list outlived the sync that read it"

# An empty list means the video had been downloaded before, so there is
# nothing to copy and nothing to report as added.
empty = Path(tempfile.mkstemp()[1])
outcome = ipod_gui.IpodWindow._sync_downloaded(window, empty, [])
assert isinstance(outcome, str), outcome
assert "Already downloaded" in outcome, outcome

# A missing list means yt-dlp could not say, and the artist folders are then
# the closest honest answer rather than silently copying nothing.
fallback = ipod_gui.fetched_sources(library / "never-written", library)
assert sorted(fallback) == sorted(
    [str(library / "New Artist"), str(library / "Old Artist")]
), fallback

print(
    json.dumps(
        {
            "remove_command": removal,
            "fetch_command": fetch,
            "sync_after_fetch": sync[0],
            "nothing_new_outcome": outcome,
            "unreported_download_sources": sorted(fallback),
        },
        indent=2,
    )
)
