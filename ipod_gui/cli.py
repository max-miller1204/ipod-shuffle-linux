"""Display-free JSON command surface over the ipod_gui package.

The window's model without the window: the same scan, device probe, search,
M3U store, preview cache and configuration file, answered as one document per
run so another program can act on any of it without reading prose.

A run either writes one `{schema, command, result}` object to stdout, or says
what went wrong as a sentence on stderr and leaves non-zero. The one case that
does both is a run that did part of what it was asked - a music folder that
could not be read through, a cached preview that would not be deleted - which
writes its document, marks it `complete: false`, and still leaves non-zero
rather than passing the part off as the whole.
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

from .config import (
    CONFIG_FILE,
    PLAYLIST_LIBRARY,
    PREVIEW_CACHE,
    PREVIEW_CACHE_LIMIT,
    STATE_LIBRARY,
    library_layout,
    music_roots,
    save_library_layout,
    save_music_roots,
)
from .device import probe_device
from .model import LibraryIndex, Track, local_search_matches
from .playlists import (
    PLAYLIST_GONE,
    TARGET_GONE,
    Playlist,
    add_entries,
    create_local_playlist,
    local_playlists,
    move_entry,
    name_problem,
    playlist_names_here,
    read_playlist_entries,
    remove_entry,
)
from .previews import (
    forget_empty_preview_folders,
    preview_cache_entries,
    prunable_previews,
)
from .tags import scan_tracks
from .youtube import search_youtube

SCHEMA = 1

# `python3 -m ipod_gui.cli` imports the package before it runs this module, and
# the package imports every one of its modules eagerly - it has to, since that
# is where the GTK versions are pinned - so runpy finds this one already
# imported and warns before running it. What it warns about is a body of
# imports, constants and definitions, run twice against nothing that is shared.
# Left alone, it lands on the stderr of every single run, which is the one
# place this command has to say what went wrong.
warnings.filterwarnings(
    "ignore",
    message=r".*found in sys\.modules after import of package.*",
    category=RuntimeWarning,
)


def _track(track):
    return {
        "path": track.path,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "genre": track.genre,
        "duration": track.duration,
        "trackNumber": track.track_no,
        "art": track.art,
        "size": track.size,
        "state": track.state,
        "onIpod": track.on_ipod,
    }


def _library():
    """The index the window builds, and whether it is the whole of it.

    Keyed by path across the roots, the way the window keys its own scan
    results, because the configured roots may overlap - ~/Music and the
    ~/Music/youtube that downloads land in is the ordinary case - and a file
    under both of them is one track rather than two.

    `complete` is scan_tracks' own answer carried out rather than dropped. A
    scan that timed out, or that met a folder it could not read, returns the
    records it did get, and a caller handed those without being told is one
    reading a truncated library as the library.
    """
    index = LibraryIndex()
    tracks = {}
    complete = True
    for root in index.roots:
        records, read_through = scan_tracks(root)
        complete = complete and read_through
        for record in records:
            track = Track(Path(root, record["path"]), record, STATE_LIBRARY)
            tracks[track.path] = track
    index.tracks = list(tracks.values())
    return index, complete


def _partial_exit(complete, sentence):
    """0 when the run did the whole of what it was asked, 1 when it did part.

    The document is written either way, carrying `complete`, because the part
    that was done is still worth having: which folders were read, which
    previews actually went. The code is what stops a caller from taking that
    part for the whole, and the sentence says which part is missing.
    """
    if complete:
        return 0
    print(sentence, file=sys.stderr)
    return 1


def _device(probe):
    return {
        "candidates": list(probe.candidates),
        "mountPoint": probe.mount_point,
        "identity": probe.identity,
        "readable": probe.readable,
        "trackCount": probe.track_count,
        "playlists": [
            {"name": name, "entries": entries, "spoken": name.lower() in probe.spoken}
            for name, entries in probe.playlists
        ],
        "storage": None
        if probe.usage is None
        else {
            "totalBytes": probe.usage[0],
            "usedBytes": probe.usage[1],
            "freeBytes": probe.usage[2],
        },
    }


def _video(video):
    return {name: getattr(video, name) for name in video.__slots__}


def _emit(command, result):
    print(json.dumps({"schema": SCHEMA, "command": command, "result": result}, ensure_ascii=True))


def _playlist(playlist):
    return {
        "name": playlist.name,
        "path": None if playlist.path is None else str(playlist.path),
        "editable": playlist.editable,
        "entries": list(playlist.entries),
    }


def _edited(outcome, playlist):
    """What an M3U edit counted, or the sentence for why it counted nothing.

    playlists.py tells its refusals apart, and both of the ones that reach here
    leave non-zero: a playlist deleted underneath the edit and a folder that
    would not have it rewritten are different places to go and look, and
    neither performed the edit that was asked for. A count of zero is not one
    of them - that is an edit that ran and found nothing to do - so it is a
    result like any other.
    """
    if outcome is PLAYLIST_GONE:
        raise SystemExit(f"playlist is no longer there: {playlist.path}")
    if outcome is None:
        raise SystemExit(f"could not rewrite playlist: {playlist.path}")
    return outcome


def _folder(value):
    """A folder named on the command line.

    expanduser here rather than at each use, because the caller this is for is
    another program: it passes an argument list to exec rather than a line to a
    shell, so a `~` in one arrives as a literal `~` that nothing else will
    expand. Left alone it would name a folder called `~` in whatever directory
    that program happened to be started from, and `playlists --root` would go
    on to create one.
    """
    return Path(value).expanduser()


def _entry(value):
    """One playlist line, in the form this store holds them: an absolute path.

    A playlist is read back against its own folder rather than against whoever
    wrote it (model.read_local_playlist_tracks), so a relative path written
    down as typed names a file beside the M3U, which is not where the caller
    meant and usually nothing at all - an entry the window and the sync would
    both silently drop while this command reported it added. Every other
    writer here already holds that invariant: the window writes a track's own
    path, and an imported list is resolved on the way in.

    Absolute rather than fully resolved, because the window's paths are its
    music roots as configured, symlinks and all, and an entry that resolved
    them would stop naming the same line the window wrote.
    """
    return os.path.abspath(os.path.expanduser(str(value)))


def _remove_previews(paths, root):
    """Delete cached previews, answering with the ones that actually went.

    An OSError is one preview that is still there - taken by a running window
    between the listing and the unlink, or sitting in a folder that will not
    give it up - and not a reason to abandon the ones after it, which is how
    the window's own prune reads it. Reporting what went rather than what was
    asked for is what lets the caller tell the two apart.
    """
    removed = []
    for path in paths:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
        forget_empty_preview_folders(path.parent, root)
    return removed


def _remove_entry(playlist, typed):
    """Drop a track from a playlist, however the file happens to name it.

    The argument is made absolute first, because that is the form every writer
    here stores. A list another program wrote may hold a path relative to
    itself, though, so an absolute argument that matched nothing is tried
    again exactly as it was typed rather than reported as a track the playlist
    does not hold.
    """
    absolute = _entry(typed)
    removed = _edited(remove_entry(playlist.path, absolute), playlist)
    if removed or absolute == str(typed):
        return removed
    return _edited(remove_entry(playlist.path, typed), playlist)


def build_parser():
    parser = argparse.ArgumentParser(prog="python3 -m ipod_gui.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    library = sub.add_parser("library")
    library.add_argument("--group", choices=("album", "artist"), default="album")
    device = sub.add_parser("device")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--youtube", action="store_true")
    playlists = sub.add_parser("playlists")
    playlists.add_argument("--root", type=_folder, default=None)
    playlist_sub = playlists.add_subparsers(dest="playlist_action", required=True)
    playlist_sub.add_parser("list")
    create = playlist_sub.add_parser("create")
    create.add_argument("name")
    create.add_argument("entries", nargs="*")
    add = playlist_sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("entries", nargs="+")
    remove = playlist_sub.add_parser("remove")
    remove.add_argument("name")
    remove.add_argument("entry")
    reorder = playlist_sub.add_parser("reorder")
    reorder.add_argument("name")
    reorder.add_argument("source", type=int)
    reorder.add_argument("target", type=int)
    cache = sub.add_parser("cache")
    cache.add_argument("action", choices=("status", "prune", "clear"))
    cache.add_argument("--root", type=_folder, default=PREVIEW_CACHE)
    cache.add_argument("--limit", type=int, default=PREVIEW_CACHE_LIMIT)
    config = sub.add_parser("config")
    config.add_argument("--music-root", action="append", type=_folder)
    config.add_argument("--group", choices=("album", "artist"))
    config.add_argument("--view", choices=("grid", "list"))
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "library":
        index, complete = _library()
        collections = index.collections(args.group == "artist")
        _emit(
            "library",
            {
                "tracks": [_track(track) for track in index.all_tracks()],
                "albums": [
                    {
                        "title": collection.title,
                        "artist": collection.artist,
                        "state": collection.state,
                        "trackCount": len(collection.tracks),
                    }
                    for collection in collections
                ],
                "counts": index.track_counts(),
                "complete": complete,
            },
        )
        return _partial_exit(
            complete,
            "a music folder could not be read through: this answer is partial",
        )
    if args.command == "device":
        _emit("device", _device(probe_device()))
        return 0
    if args.command == "search":
        index, complete = _library()
        result = {
            "local": [
                _track(track)
                for track in local_search_matches(index.all_tracks(), args.query)
            ],
            "complete": complete,
        }
        if args.youtube:
            videos, reached = search_youtube(args.query)
            result["youtube"] = [_video(video) for video in videos]
            result["reachedYoutube"] = reached
        _emit("search", result)
        return _partial_exit(
            complete,
            "a music folder could not be read through: this answer is partial",
        )
    if args.command == "playlists":
        root = args.root or PLAYLIST_LIBRARY
        if args.playlist_action == "list":
            result = [_playlist(playlist) for playlist in local_playlists(root)]
        elif args.playlist_action == "create":
            # The window's own rule for a name, applied to a name typed here
            # too: both write into the same folder, and a name FAT cannot hold
            # is one the sync mangles later on a device with no screen.
            problem = name_problem(args.name, playlist_names_here(root))
            if problem is not None:
                raise SystemExit(problem)
            entries = [_entry(entry) for entry in args.entries]
            path = create_local_playlist(root, args.name, entries)
            if path is None:
                raise SystemExit(f"could not create playlist: {args.name}")
            result = _playlist(Playlist(path.stem, path, read_playlist_entries(path)))
        else:
            found = {playlist.name: playlist for playlist in local_playlists(root)}
            playlist = found.get(args.name)
            if playlist is None:
                raise SystemExit(f"playlist not found: {args.name}")
            if args.playlist_action == "add":
                entries = [_entry(entry) for entry in args.entries]
                result = {"added": _edited(add_entries(playlist.path, entries), playlist)}
            elif args.playlist_action == "remove":
                result = {"removed": _remove_entry(playlist, args.entry)}
            else:
                moved = move_entry(playlist.path, args.source, args.target)
                if moved is False:
                    raise SystemExit(
                        f"playlist has no entry at position {args.source}: {args.name}"
                    )
                if moved is TARGET_GONE:
                    raise SystemExit(
                        f"playlist has no entry at position {args.target}: {args.name}"
                    )
                result = {"moved": _edited(moved, playlist) is True}
        _emit("playlists", result)
        return 0
    if args.command == "cache":
        entries = preview_cache_entries(args.root)
        if args.action == "prune":
            wanted = prunable_previews(entries, args.limit)
        elif args.action == "clear":
            wanted = [path for path, _size, _mtime in entries]
        else:
            wanted = []
        removed = _remove_previews(wanted, args.root)
        if wanted:
            entries = preview_cache_entries(args.root)
        left = len(wanted) - len(removed)
        _emit(
            "cache",
            {
                "root": str(args.root),
                "sizeBytes": sum(size for _path, size, _mtime in entries),
                "removed": [str(path) for path in removed],
                "entries": [
                    {"path": str(path), "size": size, "mtime": mtime}
                    for path, size, mtime in entries
                ],
                "complete": left == 0,
            },
        )
        return _partial_exit(
            left == 0,
            f"{left} cached previews could not be removed: this answer is partial",
        )
    if args.command == "config":
        if args.music_root is not None:
            # Absolute before it is stored, because this file is read by the
            # window as well, and the window is launched from a desktop entry
            # with a working directory of its own: a relative root saved here
            # would name a different folder there, or none at all.
            save_music_roots([root.resolve() for root in args.music_root])
        group, view = library_layout()
        if args.group is not None or args.view is not None:
            save_library_layout(args.group or group, args.view or view)
            group, view = library_layout()
        _emit(
            "config",
            {
                "file": str(CONFIG_FILE),
                "musicRoots": [str(path) for path in music_roots()],
                "group": group,
                "view": view,
            },
        )
        return 0
    raise SystemExit(f"no handler for command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
