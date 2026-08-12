"""Display-free JSON command surface over the ipod_gui package."""

import argparse
import json
from pathlib import Path

from .config import (
    CONFIG_FILE,
    PREVIEW_CACHE,
    PREVIEW_CACHE_LIMIT,
    library_layout,
    music_roots,
    save_library_layout,
    save_music_roots,
)
from .device import probe_device
from .model import LibraryIndex, Track, local_search_matches
from .playlists import (
    add_entries,
    create_local_playlist,
    local_playlists,
    move_entry,
    remove_entry,
)
from .previews import preview_cache_entries, prunable_previews
from .tags import scan_tracks
from .youtube import search_youtube

SCHEMA = 1


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
    index = LibraryIndex()
    tracks = []
    for root in index.roots:
        records, _complete = scan_tracks(root)
        tracks.extend(
            Track(Path(root, record["path"]), record, "library")
            for record in records
        )
    index.tracks = tracks
    return index


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
    playlists.add_argument("--root", type=Path, default=None)
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
    cache.add_argument("--root", type=Path, default=PREVIEW_CACHE)
    cache.add_argument("--limit", type=int, default=PREVIEW_CACHE_LIMIT)
    config = sub.add_parser("config")
    config.add_argument("--music-root", action="append", type=Path)
    config.add_argument("--group", choices=("album", "artist"))
    config.add_argument("--view", choices=("grid", "list"))
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "library":
        index = _library()
        collections = index.collections(args.group == "artist")
        _emit("library", {"tracks": [_track(t) for t in index.all_tracks()], "albums": [{"title": c.title, "artist": c.artist, "state": c.state, "trackCount": len(c.tracks)} for c in collections], "counts": index.track_counts()})
        return 0
    if args.command == "device":
        _emit("device", _device(probe_device()))
        return 0
    if args.command == "search":
        index = _library()
        result = {
            "local": [_track(t) for t in local_search_matches(index.all_tracks(), args.query)]
        }
        if args.youtube:
            videos, reached = search_youtube(args.query)
            result["youtube"] = [_video(video) for video in videos]
            result["reachedYoutube"] = reached
        _emit("search", result)
        return 0
    if args.command == "playlists":
        from .config import PLAYLIST_LIBRARY
        root = args.root or PLAYLIST_LIBRARY
        found = {p.name: p for p in local_playlists(root)}
        if args.playlist_action == "list":
            result = [_playlist(p) for p in found.values()]
        elif args.playlist_action == "create":
            path = create_local_playlist(root, args.name, args.entries)
            if path is None:
                raise SystemExit(f"could not create playlist: {args.name}")
            result = _playlist(local_playlists(root)[[p.name for p in local_playlists(root)].index(args.name)])
        else:
            playlist = found.get(args.name)
            if playlist is None:
                raise SystemExit(f"playlist not found: {args.name}")
            if args.playlist_action == "add":
                result = {"added": add_entries(playlist.path, args.entries)}
            elif args.playlist_action == "remove":
                result = {"removed": remove_entry(playlist.path, args.entry)}
            else:
                result = {"moved": move_entry(playlist.path, args.source, args.target) is True}
        _emit("playlists", result)
        return 0
    if args.command == "cache":
        entries = preview_cache_entries(args.root)
        if args.action == "prune":
            for path in prunable_previews(entries, args.limit):
                path.unlink()
            entries = preview_cache_entries(args.root)
        elif args.action == "clear":
            for path, _size, _mtime in entries:
                path.unlink()
            entries = []
        _emit("cache", {"root": str(args.root), "sizeBytes": sum(size for _path, size, _mtime in entries), "entries": [{"path": str(path), "size": size, "mtime": mtime} for path, size, mtime in entries]})
        return 0
    if args.music_root is not None:
        save_music_roots(args.music_root)
    group, view = library_layout()
    if args.group is not None or args.view is not None:
        save_library_layout(args.group or group, args.view or view)
    group, view = library_layout()
    _emit("config", {"file": str(CONFIG_FILE), "musicRoots": [str(p) for p in music_roots()], "group": group, "view": view})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
