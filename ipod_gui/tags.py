"""The tag reader that runs in another interpreter, and the scan around it.

Nothing here imports mutagen. It lives in install.sh's virtualenv while
PyGObject belongs to the system Python, so a scan starts that interpreter and
reads records back a line at a time as they arrive.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ART_CACHE, AUDIO_EXTENSIONS


def _tag_interpreter():
    """Pick an interpreter that can import mutagen, or None.

    PyGObject belongs to the system Python while mutagen lives in
    install.sh's virtualenv, so tag reading has to cross over to it unless
    this interpreter happens to have mutagen itself.
    """
    try:
        import mutagen  # noqa: F401

        return sys.executable
    except ImportError:
        pass
    venv = Path(
        os.environ.get("IPOD_VENV_PYTHON")
        or Path.home() / "ipod-tools" / "venv" / "bin" / "python"
    )
    return str(venv) if venv.exists() else None


TAG_PYTHON = None  # resolved lazily on first use


# Streams one JSON object per line rather than a single blob at exit. The scan
# touches every file over USB and now extracts cover art as well, so a device
# holding two gigabytes of music would sit behind one timeout with nothing to
# show; per-line output lets the grid fill in as results arrive.
_TAG_READER = r"""
import base64, hashlib, json, os, stat, sys
from pathlib import Path
try:
    import mutagen
except ImportError:
    mutagen = None

root = Path(sys.argv[1])
art_dir = Path(sys.argv[2])
suffixes = set(sys.argv[3].split(","))


def first(audio, key):
    value = audio.get(key)
    if not value:
        return None
    text = str(value[0]).strip()
    return text or None


def cover_bytes(path):
    # Cover art is unreachable through easy=True - EasyID3 has no APIC
    # mapping - so this is a second parse, done only for files we are
    # keeping and only until one picture is found.
    try:
        raw = mutagen.File(path)
    except Exception:
        return None
    if raw is None or not getattr(raw, "tags", None):
        return None
    tags = raw.tags
    try:
        pictures = tags.getall("APIC")
        if pictures:
            return bytes(pictures[0].data)
    except AttributeError:
        pass
    try:
        covr = tags.get("covr")
        if covr:
            return bytes(covr[0])
    except Exception:
        pass
    pictures = getattr(raw, "pictures", None)
    if pictures:
        return bytes(pictures[0].data)
    return None


def art_path(path):
    data = cover_bytes(path)
    if not data:
        return None
    name = hashlib.sha1(data).hexdigest() + ".img"
    target = art_dir / name
    if not target.exists():
        try:
            art_dir.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(target)
        except OSError:
            return None
    return str(target)


def emit(path, key, size=None):
    if size is None:
        size = path.stat().st_size
    fallback = {"path": key, "title": path.stem, "size": size}
    sys.stdout.write(json.dumps(fallback) + "\n")
    sys.stdout.flush()
    if mutagen is None:
        return
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return
    if audio is None:
        return
    record = {
        "path": key,
        "title": first(audio, "title"),
        "artist": first(audio, "artist"),
        "album": first(audio, "album"),
        "albumartist": first(audio, "albumartist"),
        "genre": first(audio, "genre"),
        "track": first(audio, "tracknumber"),
        "duration": float(getattr(getattr(audio, "info", None), "length", 0) or 0),
        "size": size,
        "art": art_path(path),
    }
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def walk_error(error):
    raise error


def identity(path):
    info = os.stat(path)
    return (info.st_dev, info.st_ino)


try:
    if len(sys.argv) > 4 and sys.argv[4] == "exact":
        for line in sys.stdin:
            value = json.loads(line)
            if not isinstance(value, str):
                raise ValueError
            path = Path(value)
            if path.suffix.lower() not in suffixes:
                continue
            if not path.is_file():
                raise OSError
            emit(path, value)
    else:
        if not root.is_dir():
            raise OSError
        # followlinks, to agree with the -L that ipod-sync.sh enumerates a
        # source with: what the grid counts has to be what the script copies,
        # or a sync finishes with the progress bar short of the end.
        #
        # os.walk has no loop detection of its own and would recurse forever
        # through a folder that links back into itself, so the ancestry of
        # each directory is carried along and one that is its own ancestor is
        # dropped. That is find's rule too. It deliberately does not dedupe
        # more widely than that: two links to one folder are two folders to
        # find, which copies both, so pruning the second here would put the
        # count back out of step with the copy.
        ancestries = {str(root): {identity(root)}}
        for current, dirs, files in os.walk(
            root, onerror=walk_error, followlinks=True
        ):
            ancestry = ancestries.pop(current)
            dirs.sort()
            for name in dirs[:]:
                path = Path(current, name)
                try:
                    here = identity(path)
                except OSError:
                    # A link to a folder that is not there. find reports it
                    # as a broken link and moves on rather than failing.
                    dirs.remove(name)
                    continue
                if here in ancestry:
                    dirs.remove(name)
                    continue
                ancestries[str(path)] = ancestry | {here}
            for name in sorted(files):
                path = Path(current, name)
                if path.suffix.lower() not in suffixes:
                    continue
                try:
                    info = os.stat(path)
                except OSError:
                    # A broken link named like a track. Skipping just this one
                    # keeps the rest of the library readable; raising here
                    # used to fail the whole scan, which showed the user an
                    # empty library because of one dangling link.
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                emit(path, str(path.relative_to(root)), info.st_size)
except (OSError, TypeError, ValueError):
    sys.exit(2)
"""


def _identity(path):
    info = os.stat(path)
    return (info.st_dev, info.st_ino)


def walk_following_links(root):
    """Yield (directory, filenames) under root, following symlinks safely.

    The rule the reader above applies, for callers on this side of the
    subprocess: symlinks are followed because ipod-sync.sh follows them, and a
    directory that is its own ancestor is dropped because os.walk would
    otherwise recurse through it forever. The reader cannot call this - it is
    source text run by whichever interpreter has mutagen - so the two are kept
    deliberately alike instead.
    """
    ancestries = {}
    try:
        ancestries[str(root)] = {_identity(root)}
    except OSError:
        return
    for current, dirs, files in os.walk(root, followlinks=True):
        ancestry = ancestries.pop(current)
        dirs.sort()
        for name in list(dirs):
            path = os.path.join(current, name)
            try:
                here = _identity(path)
            except OSError:
                dirs.remove(name)
                continue
            if here in ancestry:
                dirs.remove(name)
                continue
            ancestries[path] = ancestry | {here}
        yield current, sorted(files)


def scan_tracks(
    root=None,
    on_record=None,
    timeout=900,
    cancelled=None,
    files=None,
):
    """Read tags for every supported file under root.

    Returns (records, complete), with each record path relative to root.
    Symlinks are followed, the way ipod-sync.sh follows them when it copies.
    on_record, when given, is called with each record as it arrives so a caller
    can show progress rather than waiting for the whole tree. Partial records
    are returned with complete false after cancellation, timeout, or failure.
    """
    global TAG_PYTHON
    if TAG_PYTHON is None:
        TAG_PYTHON = _tag_interpreter()

    exact_files = (
        None
        if files is None
        else list(dict.fromkeys(str(path) for path in files))
    )
    exact_allowed = set(exact_files or ())
    root = Path(root) if root is not None else Path(".")
    records = {}
    reader_mode = "exact" if exact_files is not None else "recursive"

    try:
        proc = subprocess.Popen(
            [
                TAG_PYTHON or sys.executable,
                "-c",
                _TAG_READER,
                str(root),
                str(ART_CACHE),
                ",".join(sorted(AUDIO_EXTENSIONS)),
                reader_mode,
            ],
            stdin=(
                subprocess.PIPE
                if exact_files is not None
                else subprocess.DEVNULL
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except OSError:
        return [], False

    output = queue.Queue()
    output_done = object()
    input_failed = threading.Event()

    def read_output():
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    output.put(line)
        finally:
            output.put(output_done)

    threading.Thread(target=read_output, daemon=True).start()
    if exact_files is not None:
        def write_input():
            try:
                for path in exact_files:
                    proc.stdin.write(json.dumps(path) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                input_failed.set()
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        threading.Thread(target=write_input, daemon=True).start()
    deadline = time.monotonic() + timeout
    complete = False
    try:
        while True:
            if cancelled is not None and cancelled():
                raise subprocess.SubprocessError
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            try:
                wait = min(remaining, 0.1) if cancelled is not None else remaining
                line = output.get(timeout=wait)
            except queue.Empty as exc:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(proc.args, timeout) from exc
                continue
            if line is output_done:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                relpath = record["path"]
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if not isinstance(relpath, str):
                continue
            relative = Path(relpath)
            invalid_recursive = (
                exact_files is None
                and (relative.is_absolute() or ".." in relative.parts)
            )
            invalid_exact = exact_files is not None and relpath not in exact_allowed
            if (
                invalid_recursive
                or invalid_exact
                or relative.suffix.lower() not in AUDIO_EXTENSIONS
            ):
                continue
            records[relpath] = record
            if on_record is not None:
                on_record(record)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        complete = proc.wait(timeout=remaining) == 0 and not input_failed.is_set()
    except subprocess.SubprocessError:
        if proc.poll() is None:
            proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.SubprocessError:
            pass
    return list(records.values()), complete


def read_tags(mount_point):
    """Map each device track's relative path to its tag record.

    Returns an empty dict when the venv or mutagen is unavailable, in which
    case the interface falls back to showing filenames.
    """
    music = Path(mount_point, "iPod_Control", "Music")
    records, complete = scan_tracks(music)
    if not complete:
        return {}
    return {record["path"]: record for record in records}
