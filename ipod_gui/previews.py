"""The preview cache: what is in it, what may be pruned, what is promoted.

Hearing a result costs a file, and that file lands here rather than in a music
folder, so previewing twenty songs never adds twenty of them to the library.
"""

from pathlib import Path

from .config import AUDIO_EXTENSIONS


def preview_cache_entries(root):
    """Every finished preview under root, as (path, size, mtime).

    Oldest first, which is the order they are pruned in: a preview is written
    once and never rewritten, so its mtime is when it was downloaded.

    Read from the filesystem rather than from an index kept alongside it. The
    files are the cache, so a cache that was pruned, cleared, or emptied by
    hand cannot disagree with a record of what it is supposed to hold.
    """
    root = Path(root)
    try:
        candidates = sorted(root.rglob("*"))
    except OSError:
        return []
    entries = []
    for path in candidates:
        # A download still in flight lives under .incoming and is not a
        # preview yet; it is moved into place only once it has finished.
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        entries.append((path, info.st_size, info.st_mtime))
    entries.sort(key=lambda entry: (entry[2], str(entry[0])))
    return entries


def cached_preview_path(video_id, root):
    """The already-downloaded file for a video id, or None.

    ipod-fetch.sh puts the id in every filename it writes, so the cache can be
    asked whether it holds a search result without keeping a second record of
    which id became which file.
    """
    video_id = (video_id or "").strip()
    if not video_id:
        return None
    marker = f"[{video_id}]"
    for path, _size, _mtime in preview_cache_entries(root):
        if marker in path.name:
            return path
    return None


def prunable_previews(entries, limit, keep=()):
    """Which cached previews to drop to bring the cache back under limit.

    Oldest first, excluding paths the caller marks to keep. The window uses
    that exclusion for the track being played and the one that just arrived.
    """
    kept = {str(path) for path in keep}
    total = sum(size for _path, size, _mtime in entries)
    dropped = []
    for path, size, _mtime in entries:
        if total <= limit:
            break
        if str(path) in kept:
            continue
        dropped.append(path)
        total -= size
    return dropped


def forget_empty_preview_folders(folder, root):
    """Take the artist folder that the last preview in it left.

    Upwards from where a preview has just gone, and never the cache root
    itself: that folder is the cache, not something in it. The first folder
    that will not go stops the walk, because it either still holds something
    or was never ours to take.

    Here rather than in the window, because the cache is pruned from two
    places now - the window as it plays, and the display-free command over
    the same cache - and when an emptied folder goes is one rule.
    """
    folder = Path(folder)
    root = Path(root)
    while root in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def promote_destination(source, cache_root, library_root):
    """Where a previewed file belongs once it is kept.

    The cache mirrors the artist folders ipod-fetch.sh writes, so keeping a
    preview is a move that lands it exactly where downloading it in the first
    place would have put it.
    """
    source = Path(source)
    try:
        relative = source.relative_to(cache_root)
    except ValueError:
        relative = Path(source.name)
    return Path(library_root) / relative
