#!/usr/bin/env python3
"""Exercise the public display-free CLI through its module entry point.

Three shapes of run are checked, because the CLI promises exactly three: a
document and nothing else, a sentence on stderr and no document, and the one
that is both - an answer that is real but partial, which is written and still
leaves non-zero.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
home = Path(tempfile.mkdtemp(prefix="headless-cli-home-")).resolve()
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"))


def invoke(*args):
    return subprocess.run(
        ["/usr/bin/python3", "-m", "ipod_gui.cli", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def run(*args):
    proc = invoke(*args)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert not proc.stderr, proc.stderr
    document = json.loads(proc.stdout)
    assert document["schema"] == 1
    return document


def refused(*args):
    """A run that did nothing: a sentence on stderr, no document, non-zero."""
    proc = invoke(*args)
    assert proc.returncode != 0, proc.stdout
    assert proc.stderr.strip(), "a refusal has to say what went wrong"
    assert not proc.stdout, proc.stdout
    return proc


def partial(*args):
    """A run that answered, but not for everything it was asked about."""
    proc = invoke(*args)
    assert proc.returncode != 0, proc.stdout
    assert proc.stderr.strip(), "a partial answer has to say it is partial"
    document = json.loads(proc.stdout)
    assert document["schema"] == 1
    return document


music = home / "Music"
song = music / "Artist" / "Song.mp3"
other = music / "Artist" / "Other.mp3"
third = music / "Artist" / "Third.mp3"
song.parent.mkdir(parents=True)
for track in (song, other, third):
    track.write_bytes(b"audio")

config = run("config", "--music-root", str(music), "--group", "artist", "--view", "list")
assert config["result"]["musicRoots"] == [str(music)]
assert config["result"]["group"] == "artist"
assert config["result"]["view"] == "list"

playlists = home / "Playlists"
created = run("playlists", "--root", str(playlists), "create", "Road Trip", str(song))
assert created["result"]["entries"] == [str(song)]
listed = run("playlists", "--root", str(playlists), "list")
assert listed["result"][0]["name"] == "Road Trip"

added = run("playlists", "--root", str(playlists), "add", "Road Trip", str(other), str(third))
assert added["result"]["added"] == 2
# Adding what a playlist already lists is an edit that ran and had nothing to
# do, which is a count of nothing rather than a refusal.
assert run("playlists", "--root", str(playlists), "add", "Road Trip", str(other))["result"]["added"] == 0
assert run("playlists", "--root", str(playlists), "list")["result"][0]["entries"] == [
    str(song), str(other), str(third)
]

assert run("playlists", "--root", str(playlists), "reorder", "Road Trip", "0", "2")["result"]["moved"] is True
assert run("playlists", "--root", str(playlists), "list")["result"][0]["entries"] == [
    str(other), str(third), str(song)
]
# A position the playlist does not have is refused rather than reported as a
# reorder that quietly moved nothing.
refused("playlists", "--root", str(playlists), "reorder", "Road Trip", "9", "0")
refused("playlists", "--root", str(playlists), "reorder", "Road Trip", "0", "9")
assert run("playlists", "--root", str(playlists), "list")["result"][0]["entries"] == [
    str(other), str(third), str(song)
]
assert run("playlists", "--root", str(playlists), "remove", "Road Trip", str(song))["result"]["removed"] == 1

refused("playlists", "--root", str(playlists), "reorder", "Nothing Here", "0", "1")
# The window's naming rule holds here too, so a name FAT cannot store never
# reaches the folder - and a separator in one never escapes it.
refused("playlists", "--root", str(playlists), "create", "AC/DC")
assert not (playlists / "AC").exists()

if os.geteuid() != 0:
    locked = run("playlists", "--root", str(playlists), "create", "Locked", str(song))
    Path(locked["result"]["path"]).chmod(0o000)
    # A playlist that is there and cannot be read is a failed edit, not an
    # edit that added nothing.
    refused("playlists", "--root", str(playlists), "add", "Locked", str(other))
    Path(locked["result"]["path"]).chmod(0o600)
    assert str(other) not in Path(locked["result"]["path"]).read_text()
    Path(locked["result"]["path"]).unlink()

preview = home / "preview"
(preview / "Old Artist").mkdir(parents=True)
(preview / "New Artist").mkdir(parents=True)
(preview / "Old Artist" / "old.mp3").write_bytes(b"old")
(preview / "New Artist" / "new.mp3").write_bytes(b"newer")
os.utime(preview / "Old Artist" / "old.mp3", (1, 1))
os.utime(preview / "New Artist" / "new.mp3", (2, 2))
status = run("cache", "status", "--root", str(preview))
assert status["result"]["sizeBytes"] == 8
assert status["result"]["removed"] == []
pruned = run("cache", "prune", "--root", str(preview), "--limit", "5")
assert [Path(entry["path"]).name for entry in pruned["result"]["entries"]] == ["new.mp3"]
assert pruned["result"]["removed"] == [str(preview / "Old Artist" / "old.mp3")]
# The artist folder goes with the last preview in it, the way the window's own
# prune takes it, rather than leaving the cache full of empty directories.
assert not (preview / "Old Artist").exists()
cleared = run("cache", "clear", "--root", str(preview))
assert cleared["result"]["entries"] == []
assert cleared["result"]["removed"] == [str(preview / "New Artist" / "new.mp3")]
assert not (preview / "New Artist").exists()
assert preview.is_dir()

library = run("library")
assert library["result"]["complete"] is True
assert sorted(track["path"] for track in library["result"]["tracks"]) == sorted(
    str(path) for path in (song, other, third)
)
assert library["result"]["counts"]["library"] == 3
assert [album["trackCount"] for album in library["result"]["albums"]] == [3]
search = run("search", "Song")
assert [track["path"] for track in search["result"]["local"]] == [str(song)]
assert search["result"]["complete"] is True

# Roots that contain one another are the ordinary case - ~/Music and the
# ~/Music/youtube downloads land in - and a file under both is one track.
run("config", "--music-root", str(music), "--music-root", str(song.parent))
overlapping = run("library")
assert sorted(track["path"] for track in overlapping["result"]["tracks"]) == sorted(
    str(path) for path in (song, other, third)
)
assert overlapping["result"]["counts"]["library"] == 3

# A folder that cannot be read through is not an empty one: what was read is
# still written, marked partial, and the run leaves non-zero.
run("config", "--music-root", str(music), "--music-root", str(home / "Gone"))
truncated = partial("library")
assert truncated["result"]["complete"] is False
assert sorted(track["path"] for track in truncated["result"]["tracks"]) == sorted(
    str(path) for path in (song, other, third)
)
assert partial("search", "Song")["result"]["complete"] is False
run("config", "--music-root", str(music))

device = run("device")
assert set(device["result"]) == {"candidates", "mountPoint", "identity", "readable", "trackCount", "playlists", "storage"}

# Stored absolute, because the window reads this same file from a working
# directory of its own.
assert run("config", "--music-root", "Music")["result"]["musicRoots"] == [
    str((repo / "Music").resolve())
]
assert run("config", "--music-root", "~/Music")["result"]["musicRoots"] == [str(music)]
print("headless cli ok")
