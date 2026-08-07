"""Where things live: the scripts to drive, the caches, the track states.

Nothing in here reads a file or starts a process at import time, so a test can
point the cache or the music folder at something disposable before any of the
real ones have been touched.
"""

import json
import os
from pathlib import Path


APP_ID = "io.github.max_miller1204.IpodShuffle"
SCRIPT_DIR = Path(__file__).resolve().parents[1]
LIB_SCRIPT = SCRIPT_DIR / "lib.sh"
SYNC_SCRIPT = SCRIPT_DIR / "ipod-sync.sh"
WIPE_SCRIPT = SCRIPT_DIR / "ipod-wipe.sh"
REMOVE_SCRIPT = SCRIPT_DIR / "ipod-remove.sh"
FETCH_SCRIPT = SCRIPT_DIR / "ipod-fetch.sh"

# Mirrors ipod-fetch.sh's default --output. Named here as well because the GUI
# queues newly downloaded tracks from that folder once the download finishes.
YOUTUBE_LIBRARY = Path.home() / "Music" / "youtube"

# Playlists made in the app, one M3U each. Beside the music rather than in a
# cache or a config directory, because these are the user's own lists: they
# outlive this app, other music players read them, and a folder nobody can find
# is a folder whose contents nobody can back up. Nothing scans it for audio, so
# a playlist folder inside a music root costs the library scan nothing.
PLAYLIST_LIBRARY = Path.home() / "Music" / "Playlists"

CACHE_DIR = Path(
    os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
) / "ipod-shuffle-linux"
ART_CACHE = CACHE_DIR / "art"
CONFIG_FILE = Path(
    os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
) / "ipod-shuffle-linux" / "config.json"

# Hearing a YouTube result costs a file: a yt-dlp media URL is short-lived and
# bound to the address that asked for it, seeking over one is unreliable, and
# streaming would throw the work away so that adding the track fetched it a
# second time. The file lands here rather than in a music folder, because
# previewing twenty songs must not add twenty of them to the library.
PREVIEW_CACHE = CACHE_DIR / "previews"

# Each download runs into a directory of its own under here and is moved into
# the cache once it has finished. Downloading straight into the cache would
# leave yt-dlp's --download-archive recording a video as fetched in a folder
# it can be pruned out of, and the next preview of that video would then
# download nothing at all.
PREVIEW_INCOMING = PREVIEW_CACHE / ".incoming"

# About seventy previews at the 256k bitrate this project downloads. Large
# enough that an evening of listening never evicts anything, small enough that
# a folder nobody ever looks at cannot grow without bound.
PREVIEW_CACHE_LIMIT = 512 * 1024 * 1024

# Where a track lives, which decides its marker in every view. "preview" is a
# track downloaded purely so it could be heard: it sits in the preview cache
# rather than in a music folder, and stays there until it is added, which moves
# it into the library and out of the cache.
STATE_IPOD = "ipod"
STATE_LIBRARY = "library"
STATE_PREVIEW = "preview"

STATE_LABELS = {
    STATE_IPOD: "On iPod",
    STATE_LIBRARY: "In library",
    STATE_PREVIEW: "Previewed",
}


# Kept in step with the canonical SUPPORTED_EXT in lib.sh. The GUI cannot
# source shell, so a test asserts that this necessary copy has not drifted.
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav"}
PLAYLIST_EXTENSIONS = {".m3u", ".pls"}


# How the library grid was last being read. Stored as words rather than as the
# dropdown's index and the stack's child name, so the file stays readable and a
# renamed widget cannot silently change what an old config means.
GROUP_MODES = ("album", "artist")
VIEW_MODES = ("grid", "list")


def _read_config():
    """Everything in the config file, or nothing if it cannot be read."""
    try:
        stored = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _write_config(**changes):
    """Merge changes in, leaving every other setting alone.

    Read-modify-write rather than a plain dump, because each setting is saved
    by whichever widget owns it: writing only what that widget knows about
    would drop the music folders the moment the grouping changed.
    """
    stored = _read_config()
    stored.update(changes)
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(stored, indent=2))
    except OSError:
        pass


def music_roots():
    """Folders searched for local music, newest configuration winning."""
    stored = _read_config().get("music_roots")
    if isinstance(stored, list) and stored:
        return [Path(p).expanduser() for p in stored]
    return [Path.home() / "Music"]


def save_music_roots(roots):
    _write_config(music_roots=[str(p) for p in roots])


def library_layout():
    """The grouping and grid/table the library was last left showing.

    Anything unrecognised falls back to the default rather than being repaired
    in place, so a hand-edited or newer config cannot leave the window pointing
    at a view that does not exist.
    """
    stored = _read_config()
    group = stored.get("group_mode")
    view = stored.get("view_mode")
    return (
        group if group in GROUP_MODES else GROUP_MODES[0],
        view if view in VIEW_MODES else VIEW_MODES[0],
    )


def save_library_layout(group_mode, view_mode):
    _write_config(group_mode=group_mode, view_mode=view_mode)
