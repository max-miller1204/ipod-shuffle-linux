"""The library as the window sees it: tracks, albums and the index over them."""

import re
import urllib.parse
import urllib.request
from pathlib import Path

from .config import (
    AUDIO_EXTENSIONS,
    STATE_IPOD,
    STATE_LIBRARY,
    STATE_PREVIEW,
    music_roots,
)
from .text import plural
from .youtube import cached_thumbnail_for


def local_search_matches(tracks, query):
    """Library tracks matching every word of the query.

    Every word rather than the whole phrase, so "queen rhapsody" finds a track
    tagged "Bohemian Rhapsody" by "Queen"; a phrase match would need the words
    in whatever order the tagger happened to use.
    """
    terms = [term for term in (query or "").lower().split() if term]
    if not terms:
        return []
    matched = [
        track
        for track in tracks
        if all(
            term in f"{track.title} {track.artist} {track.album}".lower()
            for term in terms
        )
    ]
    return sorted(
        matched,
        key=lambda t: (
            t.artist.lower(),
            t.album.lower(),
            t.track_no or 999,
            t.title.lower(),
        ),
    )


def read_local_playlist_tracks(list_path):
    path = Path(list_path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], False
    if path.suffix.lower() == ".pls":
        numbered = []
        for line in lines:
            key, separator, value = line.partition("=")
            match = re.fullmatch(r"File([0-9]+)", key.strip(), re.IGNORECASE)
            if separator and match:
                numbered.append((int(match.group(1)), value.strip()))
        entries = [value for _index, value in sorted(numbered)]
    else:
        entries = [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]

    tracks = []
    for entry in entries:
        if entry.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(entry)
            if parsed.netloc not in ("", "localhost"):
                continue
            entry = urllib.parse.unquote(parsed.path)
        elif re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", entry):
            continue
        track = Path(entry) if Path(entry).is_absolute() else path.parent / entry
        if not track.exists() and "\\" in entry:
            slashed = entry.replace("\\", "/")
            candidate = (
                Path(slashed) if Path(slashed).is_absolute() else path.parent / slashed
            )
            if candidate.exists():
                track = candidate
        if track.is_file() and track.suffix.lower() in AUDIO_EXTENSIONS:
            tracks.append(str(track.absolute()))
    return tracks, True


def local_playlist_tracks(list_path):
    return read_local_playlist_tracks(list_path)[0]


class Track:
    """One audio file, wherever it happens to live."""

    __slots__ = (
        "path",
        "relpath",
        "title",
        "artist",
        "album",
        "genre",
        "duration",
        "track_no",
        "art",
        "size",
        "state",
        "on_ipod",
    )

    def __init__(self, path, record, state, relpath=None):
        self.path = str(path)
        self.relpath = relpath if relpath is not None else str(path)
        self.title = record.get("title") or Path(path).stem
        self.artist = record.get("artist") or "Unknown artist"
        self.album = record.get("album") or "Unknown album"
        self.genre = record.get("genre") or ""
        self.duration = float(record.get("duration") or 0)
        # Embedded art first, then the thumbnail cached for whichever video
        # this file was downloaded from. The fallback is what carries a search
        # result's artwork through into the track it becomes, and it is here
        # rather than at one producer because every producer needs it: the
        # preview that has just finished, the preview cache rescanned at
        # startup, and the copy in the music folder that keeping it made.
        self.art = record.get("art") or cached_thumbnail_for(path)
        self.size = int(record.get("size") or 0)
        self.state = state
        self.on_ipod = state == STATE_IPOD
        try:
            self.track_no = int(str(record.get("track") or "0").split("/")[0])
        except ValueError:
            self.track_no = 0

    @property
    def tagged(self):
        """Whether the file carried real tags, rather than being named only."""
        return self.album != "Unknown album" or self.artist != "Unknown artist"

    def identity(self):
        """A key that survives the trip onto the device.

        iPod_Control/Music filenames are scrambled four-letter codes, so a
        track copied there cannot be recognised by path. Tags plus a rounded
        duration are what remain of its identity.
        """
        return (
            (self.title or "").strip().lower(),
            (self.artist or "").strip().lower(),
            (self.album or "").strip().lower(),
            round(self.duration),
        )


class Album:
    __slots__ = ("title", "artist", "tracks", "art")

    def __init__(self, title, artist):
        self.title = title
        self.artist = artist
        self.tracks = []
        self.art = None

    def add(self, track):
        self.tracks.append(track)
        if self.art is None and track.art:
            self.art = track.art

    @property
    def state(self):
        """An album is only 'on iPod' when all of it is.

        A half-synced album badged "On iPod" would be a lie, and the shuffle
        gives you no way to discover which half is missing. The album view
        shows the ratio for the partial case.
        """
        states = {track.state for track in self.tracks}
        if states == {STATE_IPOD}:
            return STATE_IPOD
        if states == {STATE_PREVIEW}:
            return STATE_PREVIEW
        return STATE_LIBRARY

    @property
    def on_ipod_count(self):
        return sum(1 for track in self.tracks if track.on_ipod)

    def sorted_tracks(self):
        return sorted(self.tracks, key=lambda t: (t.track_no or 999, t.title.lower()))


class LibraryIndex:
    """Everything the app knows about, local folders and device combined."""

    def __init__(self):
        self.tracks = []
        # Tracks that exist on the device and nowhere else on this computer.
        # Kept apart from the scan results so a rescan can replace one without
        # disturbing the other.
        self.device_only = []
        # Downloads sitting in the preview cache. A third list rather than
        # tracks with a different state in the first, because the music folders
        # and the cache are rescanned independently and neither may drop the
        # other's tracks when it finishes.
        self.previews = []
        self.roots = music_roots()
        self.generation = 0

    def all_tracks(self):
        return self.tracks + self.device_only + self.previews

    def albums(self):
        grouped = {}
        for track in self.all_tracks():
            key = (track.album.lower(), track.artist.lower())
            album = grouped.get(key)
            if album is None:
                album = Album(track.album, track.artist)
                grouped[key] = album
            album.add(track)
        return sorted(
            grouped.values(), key=lambda a: (a.artist.lower(), a.title.lower())
        )

    def artists(self):
        """The library grouped by artist rather than by album.

        Reuses Album because the detail view only ever needs a title, a
        summary line, some artwork and a track list, and an artist is that
        same shape. The summary line carries the album count instead of a
        performer's name.
        """
        grouped = {}
        for track in self.all_tracks():
            key = track.artist.lower()
            collection = grouped.get(key)
            if collection is None:
                collection = Album(track.artist, "")
                grouped[key] = collection
            collection.add(track)
        for collection in grouped.values():
            albums = {track.album.lower() for track in collection.tracks}
            collection.artist = plural(len(albums), "album")
        return sorted(grouped.values(), key=lambda a: a.title.lower())

    def collections(self, by_artist=False):
        return self.artists() if by_artist else self.albums()

    def counts(self, by_artist=False):
        counts = {STATE_IPOD: 0, STATE_LIBRARY: 0, STATE_PREVIEW: 0}
        for collection in self.collections(by_artist):
            counts[collection.state] = counts.get(collection.state, 0) + 1
        return counts
