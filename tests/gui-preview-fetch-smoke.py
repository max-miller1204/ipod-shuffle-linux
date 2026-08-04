#!/usr/bin/env python3
"""Checks that previewing a result really lands a file in the preview cache.

The checks in gui-actions-smoke.py drive the window's own bookkeeping against
files made by hand. This one runs the download: the real ipod-fetch.sh against
the suite's yt-dlp double. Everything between pressing play and having a file
to play is a script, a staging directory and a move, and none of those can be
verified from in-memory state.

Run from tests/product-e2e.sh, which supplies the yt-dlp, ffmpeg and deno
doubles on PATH. Takes the cache directory and the music folder a promotion
would move into, so neither check touches the real ones.
"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from harness import gui

cache = Path(sys.argv[1])
library = Path(sys.argv[2])
gui.PREVIEW_CACHE = cache
gui.PREVIEW_INCOMING = cache / ".incoming"
gui.YOUTUBE_LIBRARY = library

# There is no main loop here, so what the download hands back for the main loop
# to run is run as it arrives instead.
gui.GLib.idle_add = lambda callback, *args: callback(*args)

# GStreamer is optional, and what it does with the file is checked against a
# stand-in pipeline elsewhere. Here the player is asked one thing: which track
# the finished download handed it.
gui.gst = lambda: None


class Window:
    """The parts of the window that a preview fetch touches."""

    def __init__(self):
        self.preview_generation = 1
        self._preview_process = None
        self._preview_lock = threading.Lock()
        self._preview_closed = False
        self.library = gui.LibraryIndex()
        self.logged = []
        self.repaints = 0
        self.player = gui.PreviewPlayer(lambda: None)

    def _log(self, text):
        self.logged.append(text)

    def _refresh_current_view(self, scan_complete=True):
        self.repaints += 1

    def _populate_cache_card(self):
        pass

    _preview_fetch_worker = gui.IpodWindow._preview_fetch_worker
    _run_preview_fetch = gui.IpodWindow._run_preview_fetch
    _finish_preview_fetch = gui.IpodWindow._finish_preview_fetch
    _fail_preview_fetch = gui.IpodWindow._fail_preview_fetch
    _prune_preview_cache = gui.IpodWindow._prune_preview_cache
    _forget_empty_preview_folders = staticmethod(
        gui.IpodWindow._forget_empty_preview_folders
    )
    _terminate_preview_process = staticmethod(
        gui.IpodWindow._terminate_preview_process
    )


result = gui.SearchResult(
    "Test Track",
    "Test Artist",
    180.0,
    "https://example.invalid/watch?v=test",
    "testvideo",
)
pending = gui.Track(
    "", {"title": result.title, "artist": result.uploader}, gui.STATE_PREVIEW
)

window = Window()
real_popen = gui.subprocess.Popen
owned_launches = []


def owned_popen(*args, **kwargs):
    if kwargs.get("start_new_session"):
        acquired = window._preview_lock.acquire(blocking=False)
        if acquired:
            window._preview_lock.release()
        assert not acquired, "preview process launched without ownership"
        owned_launches.append(args[0])
    return real_popen(*args, **kwargs)


gui.subprocess.Popen = owned_popen
try:
    window._preview_fetch_worker(result, window.preview_generation, pending)
finally:
    gui.subprocess.Popen = real_popen
assert len(owned_launches) == 1, owned_launches

expected = cache / "Test Artist" / "Test Track [testvideo].mp3"
assert expected.is_file(), sorted(str(p) for p in cache.rglob("*"))

# The artist folders ipod-fetch.sh writes are kept, so keeping the track later
# is a move that lands it exactly where downloading it directly would have.
assert expected.parent.name == "Test Artist", expected

# The staging directory goes with the download that used it. A partial file
# left under .incoming is counted by nothing and cleared by nothing short of
# emptying the whole cache.
assert not list((cache / ".incoming").glob("*")), "staging outlived the download"

# Downloading into the music folder is the thing the cache exists to prevent:
# previewing twenty songs would otherwise add twenty of them to the library.
assert not library.exists(), "a preview reached the music folder"

previews = window.library.previews
assert len(previews) == 1, previews
assert previews[0].path == str(expected), previews[0].path
assert previews[0].state == gui.STATE_PREVIEW, previews[0].state
assert previews[0].size > 0, previews[0].size
# The whole point of the download: the player is handed the finished file, not
# the placeholder the bar was showing while it ran.
assert window.player.track is previews[0], window.player.track
assert window.repaints, "the grid was not repainted for a track it now holds"
assert any("ExtractAudio" in line for line in window.logged), window.logged

assert gui.cached_preview_path("testvideo", cache) == expected
assert gui.cached_preview_path("testvideo", library) is None

# Previewing again once the file has left the cache - kept, pruned or cleared -
# has to download it again. Each download gets a staging directory of its own
# precisely so that yt-dlp's --download-archive cannot record the video as
# fetched in a folder it can then be removed from, which would leave every
# later preview of it downloading nothing at all.
expected.unlink()
second = Window()
second._preview_fetch_worker(result, second.preview_generation, pending)
assert expected.is_file(), "a second preview of the same video downloaded nothing"
assert second.player.track is not None, second.player.error
assert second.player.track.path == str(expected), second.player.track.path

closed = Window()
closed._preview_closed = True
closed_result = gui.SearchResult(
    "Closed", "Nobody", 0, "https://example.invalid/v=closed", "closed"
)
closed._preview_fetch_worker(closed_result, closed.preview_generation, pending)
assert gui.cached_preview_path("closed", cache) is None
assert closed.library.previews == []

# A download that produces nothing says so in the bar, where the track it was
# going to play is already named, rather than leaving it fetching forever.
# YouTube changing something under yt-dlp is the ordinary way this happens, so
# the double is replaced by one that fails the way that does.
broken = Path(tempfile.mkdtemp()) / "yt-dlp"
broken.write_text(
    "#!/bin/sh\necho 'ERROR: unable to download' >&2\nexit 1\n", encoding="utf-8"
)
broken.chmod(0o755)
os.environ["IPOD_VENV_YT_DLP"] = str(broken)

missing = Window()
missing._preview_fetch_worker(
    gui.SearchResult("Gone", "Nobody", 0, "https://example.invalid/v=gone", "gone"),
    missing.preview_generation,
    pending,
)
assert missing.player.error, "a failed preview left the bar with nothing to say"
assert "ipod-fetch.sh --update" in missing.player.error, missing.player.error
assert missing.library.previews == [], missing.library.previews

print(
    json.dumps(
        {
            "cached_file": str(expected.relative_to(cache)),
            "state": previews[0].state,
            "played_after_download": window.player.track.path == str(expected),
            "redownloaded_after_leaving_cache": True,
            "failed_download_message": missing.player.error,
        },
        indent=2,
    )
)
