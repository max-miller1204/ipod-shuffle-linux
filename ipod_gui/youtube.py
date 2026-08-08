"""Everything that reaches YouTube: search, artwork, and the fetch command.

A result is artwork and a title before it is a file, and the same video id
names the thumbnail, the download and the copy that keeping it promotes into
the music folder, so the three stay together.
"""

import http.client
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from .config import ART_CACHE, AUDIO_EXTENSIONS, FETCH_SCRIPT
from .shell import lib_function_output
from .text import plural


def is_downloadable_url(text):
    """Whether text looks like a link that can be handed to yt-dlp."""
    try:
        parsed = urllib.parse.urlsplit((text or "").strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def short_link(url):
    """A link as it reads in a sentence: youtube.com/watch?v=abc.

    The scheme and the www. are the two parts of a URL that never distinguish
    one link from another, and a suggestion strip has one line to say which
    link it is offering. Everything that identifies the video is kept.
    """
    url = (url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    host = parsed.netloc[4:] if parsed.netloc.startswith("www.") else parsed.netloc
    shortened = f"{host}{parsed.path}"
    return f"{shortened}?{parsed.query}" if parsed.query else shortened


# Three, which is exactly what the reserved skeleton makes room for. The
# section is a shortlist to add from rather than a browser - for a song the
# answer is almost always the first row - and a skeleton shorter than the
# result set would let the layout jump at the moment the results land, which
# is the moment the user is reading it.
YOUTUBE_SEARCH_RESULTS = 3

# A flat search answers in about a second. This is the point at which waiting
# longer is worse than saying it did not come back.
YOUTUBE_SEARCH_TIMEOUT = 20

# Below this a query matches most of a library and none of it usefully, and
# every keystroke of a real query would spend a round trip to YouTube.
SEARCH_MIN_QUERY = 2

# Long enough that typing a phrase costs one search rather than one per
# letter, short enough that it still reads as a response to the typing. On top
# of the ~150ms GtkSearchEntry already waits before it reports a change at all,
# which is why this is shorter than a debounce written from nothing would be.
SEARCH_DEBOUNCE_MS = 300


class SearchResult:
    """One YouTube hit, before anything has been downloaded.

    Not a Track: it has no file, no size and none of the three states, and
    giving it one would put a thing that does not exist yet into the album
    grid and into the storage meter.
    """

    __slots__ = (
        "title",
        "uploader",
        "duration",
        "url",
        "video_id",
        "thumbnail",
        "playlist_title",
        "playlist_count",
    )

    def __init__(
        self,
        title,
        uploader,
        duration,
        url,
        video_id="",
        thumbnail="",
        playlist_title="",
        playlist_count=0,
    ):
        self.title = title
        self.uploader = uploader
        self.duration = duration
        self.url = url
        self.video_id = video_id
        # Where the artwork can be fetched from, not the artwork itself: the
        # search answers in about a second and downloading three images before
        # showing anything would trade that away for decoration.
        self.thumbnail = thumbnail
        # What the entry said about the list it was one of. Carried on the
        # result because it is the only thing that can tell three rows of a
        # forty-track playlist from a playlist with three tracks in it, and
        # yt-dlp says it once per entry rather than once per run.
        self.playlist_title = playlist_title
        # 0 means yt-dlp did not say, which it does not for a plain search.
        self.playlist_count = playlist_count


def youtube_search_target(query, limit=YOUTUBE_SEARCH_RESULTS):
    """What to hand yt-dlp for this query.

    A pasted link is looked up as itself rather than searched for: searching
    for a URL returns whatever YouTube makes of that text as a phrase, which
    is never the video that was on the clipboard.
    """
    query = (query or "").strip()
    if is_downloadable_url(query):
        return query
    return f"ytsearch{max(1, int(limit))}:{query}"


def youtube_search_command(yt_dlp, query, limit=YOUTUBE_SEARCH_RESULTS):
    """The argv that lists candidates without downloading any of them.

    --flat-playlist is the difference between an answer in about a second and
    one in half a minute: without it yt-dlp resolves every hit's media URLs,
    which is the expensive half of the work and the half a list of titles does
    not need. --playlist-items caps a linked playlist to the same shortlist the
    search returns, so pasting an album link cannot flood the section.
    """
    limit = max(1, int(limit))
    return [
        str(yt_dlp),
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--playlist-items",
        f"1-{limit}",
        "--",
        youtube_search_target(query, limit),
    ]


def playlist_length(entry):
    """How long the list this entry came out of really is, or 0.

    playlist_count is the whole list. n_entries is how much of it this run was
    asked for, which --playlist-items has already capped at the shortlist, so
    reading that instead would report the truncation as the length - which is
    the one thing the playlist header exists to correct.
    """
    try:
        count = int(entry.get("playlist_count") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def parse_search_results(lines, limit=YOUTUBE_SEARCH_RESULTS):
    """Turn --dump-json output into results, skipping anything unusable.

    yt-dlp prints one JSON object per line, and a line that is neither is a
    notice that escaped --no-warnings rather than a reason to show nothing.
    """
    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        video_id = video_id if isinstance(video_id, str) else ""
        url = entry.get("webpage_url") or entry.get("url")
        if not isinstance(url, str) or not is_downloadable_url(url):
            # A flat search entry sometimes carries only the id, and the watch
            # URL built from one is what ipod-fetch.sh would be given anyway.
            url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        if not is_downloadable_url(url):
            continue
        try:
            duration = float(entry.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        results.append(
            SearchResult(
                title=str(entry.get("title") or video_id or "Untitled"),
                uploader=str(
                    entry.get("uploader") or entry.get("channel") or "YouTube"
                ),
                duration=duration,
                url=url,
                video_id=video_id,
                thumbnail=thumbnail_from_entry(entry),
                playlist_title=str(
                    entry.get("playlist_title") or entry.get("playlist") or ""
                ),
                playlist_count=playlist_length(entry),
            )
        )
        if len(results) >= limit:
            break
    return results


class LinkedPlaylist:
    """The list a pasted link resolved to, and how much of it is on screen."""

    __slots__ = ("title", "count", "url", "shown")

    def __init__(self, title, count, url, shown):
        self.title = title
        # 0 when yt-dlp did not say how long the list is, which is different
        # from a list whose length happens to be what is already shown.
        self.count = count
        self.url = url
        self.shown = shown

    def length_known(self):
        """Whether this list has an end yt-dlp was willing to name.

        A Mix, a channel and a /videos tab all come back with a title and no
        count, because they are paginated rather than finite. Nothing can be
        said about how much of one a whole-list download would fetch, so the
        header names it and stops there.
        """
        return self.count > 0

    def summary(self):
        """What the header says: which list this is, and what is left out."""
        named = f"Playlist: {self.title}" if self.title else "Playlist"
        if self.count > self.shown:
            return (
                f"{named}, {plural(self.count, 'track')}, "
                f"showing the first {self.shown}"
            )
        if self.count:
            return f"{named}, {plural(self.count, 'track')}"
        return f"{named}, showing the first {plural(self.shown, 'track')}"


def linked_playlist(query, results, limit=YOUTUBE_SEARCH_RESULTS):
    """The playlist a pasted link resolved to, or None.

    Only ever for a link. `ytsearchN:` is a playlist to yt-dlp too, and every
    entry of a text search comes back with playlist_title set to the query
    itself, so a header derived from the entries alone would announce every
    search as a playlist named after what was typed.

    A link that came back with fewer rows than it asked for is exactly as long
    as what it showed, whatever yt-dlp reported: --playlist-items would have
    taken more if the list had held them.
    """
    query = (query or "").strip()
    if not results or not is_downloadable_url(query):
        return None
    title = next(
        (result.playlist_title for result in results if result.playlist_title), ""
    )
    count = max(result.playlist_count for result in results)
    if len(results) < max(1, int(limit)):
        count = len(results)
    if not title and count <= len(results):
        # A linked video carries neither, and a list with nothing more to it
        # than the rows already up has nothing a header would add.
        return None
    return LinkedPlaylist(title, count, query, len(results))


def search_youtube(
    query, limit=YOUTUBE_SEARCH_RESULTS, timeout=YOUTUBE_SEARCH_TIMEOUT
):
    """Ask YouTube for candidates. Returns (results, reached_youtube).

    A search that finds nothing exits 0 having printed nothing, and one that
    could not reach YouTube exits non-zero, so the empty case and the offline
    or rate-limited case are told apart by the exit code. Without that they
    both look like no results, and the user retypes a query that was fine.
    """
    yt_dlp = lib_function_output("yt_dlp_bin")
    if yt_dlp is None:
        return [], False
    try:
        finished = subprocess.run(
            youtube_search_command(yt_dlp, query, limit),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return [], False
    if finished.returncode != 0:
        return [], False
    return parse_search_results(finished.stdout.splitlines(), limit), True


# A search result has no file to read a cover out of, so its artwork is the
# video's thumbnail, fetched separately into the same cache embedded covers
# land in. ipod-fetch.sh keeps passing --no-embed-thumbnail: cover art really
# is pure waste on a 2GB device with no screen, and embedding it would put it
# on the iPod while still leaving a result that has not been downloaded yet
# with nothing to show.
#
# Cached under the video id rather than under the image's own checksum, which
# is what the tag reader uses, because the id is what a result carries before
# anything has been downloaded. It is also in the name of every file
# ipod-fetch.sh writes, so a previewed track, the copy that keeping it moves
# into the music folder, and a library rescanned days later all find the
# artwork the search already fetched.

# The largest square artwork is drawn at is the album page's 180px, and a
# 16:9 thumbnail is cropped to its short edge, so 360 is the first standard
# size with pixels to spare at every size the window uses.
YOUTUBE_ART_SIZE = 360

# Bounded because this writes a file from a URL that came off the network. A
# YouTube thumbnail is tens of kilobytes; nothing this size is one.
THUMBNAIL_MAX_BYTES = 4 * 1024 * 1024

# Short, because artwork decorates a list that is already on screen. A slow
# image must not outlive the search it belongs to.
THUMBNAIL_TIMEOUT = 10

# The only thing standing between an id that arrived over the network and a
# filename, so it is a whitelist of what yt-dlp ids are made of rather than an
# attempt to escape whatever turns up.
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

# The id ipod-fetch.sh writes into every filename, read as the last bracketed
# group: a title can carry brackets of its own - "Song [Live] [dQw4w9WgXcQ]" -
# and --output puts the id last.
VIDEO_ID_IN_NAME = re.compile(r"\[([A-Za-z0-9_-]{1,64})\]")


def youtube_art_file(video_id, root):
    """Where this video's thumbnail belongs in the art cache, or None."""
    video_id = (video_id or "").strip()
    if not VIDEO_ID.fullmatch(video_id):
        return None
    # The prefix cannot collide with the tag reader's names in the same
    # directory, which are the SHA-1 of the image: forty hex characters.
    return Path(root) / f"yt-{video_id}.img"


def youtube_art_path(video_id, root):
    """The cached thumbnail for a video id, or None if it is not cached."""
    target = youtube_art_file(video_id, root)
    if target is None or not target.is_file():
        return None
    return str(target)


def video_id_from_name(name):
    """The YouTube id in a downloaded file's name, or ""."""
    found = VIDEO_ID_IN_NAME.findall(Path(name).stem)
    return found[-1] if found else ""


def cached_thumbnail_for(path):
    """The cached YouTube artwork for a file on disk, or None."""
    return youtube_art_path(video_id_from_name(Path(path).name), ART_CACHE)


def downloaded_file(video_id, library):
    """The file a video id was downloaded to, or None.

    ipod-fetch.sh writes the id into every filename, so the folder can be asked
    what became of one video without keeping a second record beside it - the
    same question cached_preview_path asks of the preview cache. Adding a
    search result to a playlist needs this rather than the download's own
    report: a video already in the music folder downloads nothing and reports
    nothing, and the file it would have written is already sitting there.
    """
    video_id = (video_id or "").strip()
    if not video_id:
        return None
    marker = f"[{video_id}]"
    try:
        candidates = sorted(Path(library).rglob("*"))
    except OSError:
        return None
    for path in candidates:
        if marker not in path.name:
            continue
        if path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file():
            return path
    return None


def thumbnail_from_entry(entry, want=YOUTUBE_ART_SIZE):
    """The thumbnail to cache for one search hit, or "".

    yt-dlp offers several sizes, worst first. The smallest one that still
    covers the largest square the artwork is drawn at is the right trade:
    below that it is visibly soft on the album page, and the maximum-
    resolution version is a quarter of a megabyte to show at 36 pixels in a
    list of three.
    """
    sized = []
    unsized = []
    for item in entry.get("thumbnails") or ():
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not is_downloadable_url(url):
            continue
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        # The short edge, because a 16:9 thumbnail is cropped to a square and
        # the sides are what the crop throws away.
        edge = min(width, height) if width and height else max(width, height)
        (sized if edge > 0 else unsized).append((edge, url))
    if sized:
        # The smallest that is big enough, and failing that the largest there
        # is, which is what the two keys order.
        return min(
            sized,
            key=lambda found: (0, found[0]) if found[0] >= want else (1, -found[0]),
        )[1]
    if unsized:
        # Nothing said how big any of them are, so the last one, which is the
        # best yt-dlp knows of.
        return unsized[-1][1]
    fallback = entry.get("thumbnail")
    if isinstance(fallback, str) and is_downloadable_url(fallback):
        return fallback
    return ""


def fetch_thumbnail(url, destination, timeout=THUMBNAIL_TIMEOUT):
    """Download one thumbnail to destination. True if it landed.

    Never raises. Artwork is the one part of a result that is allowed to be
    missing, so a network that half-answers has to leave the row showing its
    placeholder rather than an error the user cannot do anything about.
    """
    # Checked rather than trusted: this URL came off the network, and urlopen
    # is as happy to read file:// as https://.
    if not is_downloadable_url(url):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = response.read(THUMBNAIL_MAX_BYTES + 1)
    except (OSError, ValueError, http.client.HTTPException):
        return False
    if not data or len(data) > THUMBNAIL_MAX_BYTES:
        return False

    destination = Path(destination)
    staging = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Written under a name of its own and renamed into place, so neither a
        # fetch cut off halfway nor a second fetch of the same image running
        # beside it can leave a half-written file behind for every later paint
        # to try to load.
        handle, staging = tempfile.mkstemp(dir=destination.parent, suffix=".part")
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(staging, destination)
    except OSError:
        if staging is not None:
            try:
                os.unlink(staging)
            except OSError:
                pass
        return False
    return True


def cache_thumbnail(video_id, url, root):
    """Put this video's thumbnail in the art cache. Its path, or None.

    Already cached counts as cached: an image is fetched once and then belongs
    to every later search, preview and library scan naming the same video.
    """
    target = youtube_art_file(video_id, root)
    if target is None:
        return None
    if target.is_file():
        return str(target)
    return str(target) if fetch_thumbnail(url, target) else None


def fetched_sources(list_path, library):
    """What to queue after a download.

    ipod-fetch.sh writes the path of each newly downloaded track, so this is
    normally those tracks alone and nothing else the library already held.
    It deletes the file instead when its yt-dlp is too old to report them, and
    the artist folders are then the closest honest source set. An empty list
    is the third case and means the video had been downloaded before.
    """
    try:
        lines = Path(list_path).read_text().splitlines()
    except OSError:
        return sorted(str(p) for p in Path(library).glob("*") if p.is_dir())
    return [line.strip() for line in lines if line.strip()]


def fetch_command(url, output, new_tracks=None, single=True):
    """The ipod-fetch.sh invocation behind every download the GUI starts.

    One builder keeps the shared flags consistent for the link dialog, a
    search result's Add, the Add all beside a pasted playlist and a preview
    while letting each supply its output, new-track manifest and playlist
    scope. Add all is the only one that always asks for the whole list, which
    the dialog does only with its Whole playlist switch on and a result's Add
    never does.
    """
    command = [str(FETCH_SCRIPT), "--output", str(output)]
    if new_tracks is not None:
        command += ["--new-tracks", str(new_tracks)]
    if single:
        command.append("--single")
    command.append(url)
    return command
