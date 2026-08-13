#!/usr/bin/env python3
"""Exercise the public display-free CLI through its module entry point.

Four shapes of run are checked, because the CLI promises exactly four: a
document and nothing else, a sentence on stderr and no document, the one that
is both - an answer that is real but partial, written and still leaving
non-zero - and a usage line from the argument parser, which reaches none of
the model at all.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
home = Path(tempfile.mkdtemp(prefix="headless-cli-home-")).resolve()
# PYTHONPATH so that the checks below can run the command from somewhere other
# than the repository, which is what a relative argument needs to mean anything.
# The yt-dlp double is the same one product-e2e.sh uses, named the way lib.sh
# resolves the searcher, so `search --youtube` answers without a network.
#
# The findmnt double is on PATH for every run rather than for the device checks
# alone, because `library` and `search` read the iPod as well as the folders
# now: left to the real findmnt, each of them would fold whatever this
# developer has plugged in into the answer and fail on their machine only. The
# double lists nothing until a run names a volume in FAKE_IPOD_MOUNT, so the
# checks that want an empty bus get one.
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / "config"),
    XDG_CACHE_HOME=str(home / "cache"),
    PATH=os.pathsep.join([str(repo / "tests" / "bin"), os.environ["PATH"]]),
    PYTHONPATH=str(repo),
    IPOD_VENV_YT_DLP=str(repo / "tests" / "bin" / "yt-dlp"),
)


def invoke(*args, cwd=None, **overrides):
    return subprocess.run(
        ["/usr/bin/python3", "-m", "ipod_gui.cli", *args],
        cwd=cwd or repo,
        env=dict(env, **overrides),
        capture_output=True,
        text=True,
    )


def run(*args, cwd=None, **overrides):
    proc = invoke(*args, cwd=cwd, **overrides)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert not proc.stderr, proc.stderr
    document = json.loads(proc.stdout)
    assert document["schema"] == 1
    return document


def refused(*args, cwd=None):
    """A run that did nothing: a sentence on stderr, no document, non-zero."""
    proc = invoke(*args, cwd=cwd)
    assert proc.returncode != 0, proc.stdout
    assert proc.stderr.strip(), "a refusal has to say what went wrong"
    assert not proc.stdout, proc.stdout
    return proc


def partial(*args, cwd=None):
    """A run that did part of what it was asked, and says which part."""
    proc = invoke(*args, cwd=cwd)
    assert proc.returncode != 0, proc.stdout
    assert proc.stderr.strip(), "a partial answer has to say it is partial"
    document = json.loads(proc.stdout)
    assert document["schema"] == 1
    assert document["result"]["complete"] is False
    return document


def unusable(*args):
    """Arguments the parser refused, so nothing was read or written."""
    proc = invoke(*args)
    assert proc.returncode == 2, f"{proc.returncode}: {proc.stderr}"
    assert proc.stderr.strip(), "a usage error has to print usage"
    assert not proc.stdout, proc.stdout
    return proc


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

# A track named relatively is stored as the absolute path it meant, because a
# playlist is read back against its own folder: written down as typed, the line
# would name a file beside the M3U, and the window and the sync would both drop
# an entry this command reported as added.
relative = run(
    "playlists", "--root", str(playlists), "create", "Relative", "Artist/Song.mp3",
    cwd=music,
)
assert relative["result"]["entries"] == [str(song)]
assert run(
    "playlists", "--root", str(playlists), "add", "Relative", "Artist/Other.mp3",
    cwd=music,
)["result"]["added"] == 1
assert run(
    "playlists", "--root", str(playlists), "remove", "Relative", "Artist/Song.mp3",
    cwd=music,
)["result"]["removed"] == 1
assert {
    playlist["name"]: playlist["entries"]
    for playlist in run("playlists", "--root", str(playlists), "list")["result"]
}["Relative"] == [str(other)]

# A list another program wrote may name its tracks relative to itself, and
# those are still removable as they are written.
(playlists / "Hand Written.m3u").write_text("#EXTM3U\nArtist/Song.mp3\n", encoding="utf-8")
assert run(
    "playlists", "--root", str(playlists), "remove", "Hand Written", "Artist/Song.mp3"
)["result"]["removed"] == 1

# A `~` reaches this from a caller that passes an argument list rather than a
# shell command, and nothing else will expand it.
assert run("playlists", "--root", "~/Playlists", "list")["result"] == run(
    "playlists", "--root", str(playlists), "list"
)["result"]
assert not (repo / "~").exists()

if os.geteuid() != 0:
    locked = run("playlists", "--root", str(playlists), "create", "Locked", str(song))
    Path(locked["result"]["path"]).chmod(0o000)
    # A playlist that is there and cannot be read is a failed edit, not an
    # edit that added nothing.
    refused("playlists", "--root", str(playlists), "add", "Locked", str(other))
    Path(locked["result"]["path"]).chmod(0o600)
    assert str(other) not in Path(locked["result"]["path"]).read_text()
    Path(locked["result"]["path"]).unlink()

external_cache = home / "preview"
(external_cache / "Old Artist").mkdir(parents=True)
(external_cache / "New Artist").mkdir(parents=True)
(external_cache / "Old Artist" / "old.mp3").write_bytes(b"old")
(external_cache / "New Artist" / "new.mp3").write_bytes(b"newer")
os.utime(external_cache / "Old Artist" / "old.mp3", (1, 1))
os.utime(external_cache / "New Artist" / "new.mp3", (2, 2))
status = run("cache", "status", "--root", str(external_cache))
assert status["result"]["sizeBytes"] == 8
assert status["result"]["removed"] == []
assert status["result"]["complete"] is True
assert run("cache", "status", "--root", "~/preview")["result"] == status["result"]

# An arbitrary music directory remains available to read as cache-shaped
# status, but neither mutating action may turn it into recursively deleted
# preview content.
for action in ("prune", "clear"):
    refused("cache", action, "--root", str(external_cache), "--limit", "0")
    assert sorted(path.name for path in external_cache.rglob("*.mp3")) == [
        "new.mp3",
        "old.mp3",
    ]

preview = home / "cache" / "ipod-shuffle-linux" / "previews"
(preview / "Old Artist").mkdir(parents=True)
(preview / "New Artist").mkdir(parents=True)
(preview / "Old Artist" / "old.mp3").write_bytes(b"old")
(preview / "New Artist" / "new.mp3").write_bytes(b"newer")
os.utime(preview / "Old Artist" / "old.mp3", (1, 1))
os.utime(preview / "New Artist" / "new.mp3", (2, 2))
# The configured cache named on the command line is the configured cache
# whatever it is spelled as, because what a mutation is allowed to delete is
# decided on the folder rather than on the characters: a caller passing the
# root it read back from `cache status` gets the prune it asked for.
spelt = str(preview / ".." / preview.name)
assert spelt != str(preview)
for named_root in (spelt, "~/cache/ipod-shuffle-linux/previews"):
    named = run("cache", "prune", "--root", named_root, "--limit", "5")
    assert named["result"]["root"] == str(preview)
    assert named["result"]["removed"] == [str(preview / "Old Artist" / "old.mp3")]
    assert not (preview / "Old Artist").exists()
    (preview / "Old Artist").mkdir()
    (preview / "Old Artist" / "old.mp3").write_bytes(b"old")
    os.utime(preview / "Old Artist" / "old.mp3", (1, 1))

pruned = run("cache", "prune", "--limit", "5")
assert [Path(entry["path"]).name for entry in pruned["result"]["entries"]] == ["new.mp3"]
assert pruned["result"]["removed"] == [str(preview / "Old Artist" / "old.mp3")]
# The artist folder goes with the last preview in it, the way the window's own
# prune takes it, rather than leaving the cache full of empty directories.
assert not (preview / "Old Artist").exists()
cleared = run("cache", "clear")
assert cleared["result"]["entries"] == []
assert cleared["result"]["removed"] == [str(preview / "New Artist" / "new.mp3")]
assert cleared["result"]["complete"] is True
assert not (preview / "New Artist").exists()
assert preview.is_dir()

# A preview that will not be deleted is the cache's own half-done answer: what
# did go is still written, marked, and the run leaves non-zero - the same rule
# a music folder that could not be read through follows.
if os.geteuid() != 0:
    stuck = preview
    (stuck / "Artist").mkdir(parents=True)
    kept = stuck / "Artist" / "kept.mp3"
    kept.write_bytes(b"kept")
    (stuck / "Artist").chmod(0o500)
    unremovable = partial("cache", "clear")
    assert unremovable["result"]["removed"] == []
    assert [Path(entry["path"]).name for entry in unremovable["result"]["entries"]] == ["kept.mp3"]
    (stuck / "Artist").chmod(0o700)
    kept.unlink()
    (stuck / "Artist").rmdir()

library = run("library")
assert library["result"]["complete"] is True
assert sorted(track["path"] for track in library["result"]["tracks"]) == sorted(
    str(path) for path in (song, other, third)
)
assert library["result"]["counts"]["library"] == 3
assert [album["trackCount"] for album in library["result"]["albums"]] == [3]

# The other half of the window's own toggle. Both groupings answer under the
# same key, so a caller that asked for artists and was handed albums would have
# nothing to tell them apart by: what says which arrived is the collection
# itself - titled by artist, with the album count where a performer's name sits
# under the other grouping.
by_artist = run("library", "--group", "artist")
assert [collection["trackCount"] for collection in by_artist["result"]["albums"]] == [3]
assert [collection["title"] for collection in by_artist["result"]["albums"]] == sorted(
    {track["artist"] for track in by_artist["result"]["tracks"]}
)
assert by_artist["result"]["albums"][0]["artist"] == "1 album"
assert by_artist["result"]["albums"] != library["result"]["albums"]
assert by_artist["result"]["counts"] == library["result"]["counts"]

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

# A music root reached through a symlink stays the name it was given, because
# the window stores the folder its chooser named and builds every track path
# from it. Resolved here instead, a track would be one path and the playlist
# entry naming the same file another, and the window's `track.path in
# playlist.entries` would never match.
linked = home / "Linked"
linked.symlink_to(music)
assert run("config", "--music-root", str(linked))["result"]["musicRoots"] == [str(linked)]
through_link = run("library")
assert sorted(track["path"] for track in through_link["result"]["tracks"]) == sorted(
    str(linked / "Artist" / name) for name in ("Song.mp3", "Other.mp3", "Third.mp3")
)
linked_mix = run(
    "playlists", "--root", str(playlists), "create", "Linked Mix",
    str(linked / "Artist" / "Song.mp3"),
)
assert linked_mix["result"]["entries"][0] in {
    track["path"] for track in through_link["result"]["tracks"]
}
run("config", "--music-root", str(music))

device = run("device")
assert set(device["result"]) == {"candidates", "mountPoint", "identity", "readable", "trackCount", "playlists", "storage"}

# A probe of an empty USB bus is the same document a command that read nothing
# would write, so detection is pointed at a folder shaped like a synced shuffle
# - the suite's own findmnt double, the way product-e2e.sh points it - and the
# reading has to be that folder rather than the shape of one.
shuffle = home / "ALEX IPOD"
(shuffle / "iPod_Control" / "Music" / "F00").mkdir(parents=True)
for name in ("Song.mp3", "Other.mp3", "Device Only.mp3"):
    (shuffle / "iPod_Control" / "Music" / "F00" / name).write_bytes(b"audio")
(shuffle / "Road Trip.m3u").write_text(
    "#EXTM3U\niPod_Control/Music/F00/Song.mp3\n", encoding="utf-8"
)
mounted = run("device", FAKE_IPOD_MOUNT=str(shuffle))
assert mounted["result"]["candidates"] == [str(shuffle)]
assert mounted["result"]["mountPoint"] == str(shuffle)
assert mounted["result"]["readable"] is True
assert mounted["result"]["identity"]
# Three files under iPod_Control/Music, and the playlist at the volume root
# named relative to the music folder, which is how the window lists what is on
# it.
assert mounted["result"]["trackCount"] == 3
assert mounted["result"]["playlists"] == [
    {"name": "Road Trip", "entries": ["F00/Song.mp3"], "spoken": False}
]
assert mounted["result"]["storage"]["totalBytes"] > 0
assert mounted["result"]["storage"]["usedBytes"] + mounted["result"]["storage"][
    "freeBytes"
] <= mounted["result"]["storage"]["totalBytes"]

# The display-free library is the window's merged model: matching local copies
# claim device tracks, device-only and preview tracks remain visible, and every
# state count describes the records in the same document.
preview_track = preview / "Preview Artist" / "Preview.mp3"
preview_track.parent.mkdir(parents=True)
preview_track.write_bytes(b"preview")
merged = run("library", FAKE_IPOD_MOUNT=str(shuffle))
merged_tracks = merged["result"]["tracks"]
assert len(merged_tracks) == 5
assert merged["result"]["counts"] == {
    "ipod": 3,
    "queued": 0,
    "library": 1,
    "preview": 1,
}
states = {track["title"]: track["state"] for track in merged_tracks}
assert states == {
    "Device Only": "ipod",
    "Other": "ipod",
    "Preview": "preview",
    "Song": "ipod",
    "Third": "library",
}
assert all(
    track["onIpod"] == (track["state"] == "ipod")
    for track in merged_tracks
)

# A search is the other half of that: the same iPod is plugged in and the same
# preview is cached, and neither is in the answer. What a query reads is the
# configured music folders, so a copy that is also on the device is still a
# `library` record here, and a track only the device or the cache holds is not
# a result at all.
searched = run("search", "Song", FAKE_IPOD_MOUNT=str(shuffle))
assert [track["path"] for track in searched["result"]["local"]] == [str(song)]
assert searched["result"]["local"][0]["state"] == "library"
assert searched["result"]["local"][0]["onIpod"] is False
assert searched["result"]["complete"] is True
for query in ("Device Only", "Preview"):
    assert run("search", query, FAKE_IPOD_MOUNT=str(shuffle))["result"]["local"] == []

# An iPod that has never been synced has no iPod_Control/Music at all, which is
# nothing there rather than something that could not be read - the answer
# count_files_present gives it in the scripts, and the one the device probe's
# own track count gives it. The library reads it the same way: a whole answer,
# with a zero in it, rather than a partial one that a caller has to distrust.
fresh = home / "NEW IPOD"
(fresh / "iPod_Control").mkdir(parents=True)
unsynced = run("library", FAKE_IPOD_MOUNT=str(fresh))
assert unsynced["result"]["complete"] is True
assert unsynced["result"]["counts"]["ipod"] == 0
assert {track["title"] for track in unsynced["result"]["tracks"]} == {
    "Song",
    "Other",
    "Third",
    "Preview",
}

# A reading that really did fall short says which of the three it was, because
# a music folder, the preview cache and the iPod are three different places to
# go and look.
if os.geteuid() != 0:
    shut = fresh / "iPod_Control" / "Music"
    shut.mkdir()
    shut.chmod(0o000)
    device_short = invoke("library", FAKE_IPOD_MOUNT=str(fresh))
    assert device_short.returncode != 0, device_short.stdout
    assert json.loads(device_short.stdout)["result"]["complete"] is False
    assert device_short.stderr.strip() == (
        "the connected iPod could not be read through: this answer is partial"
    )
    # Two of them short is both of them named, rather than the first one found
    # standing in for the rest.
    run("config", "--music-root", str(music), "--music-root", str(home / "Gone"))
    both_short = invoke("library", FAKE_IPOD_MOUNT=str(fresh))
    assert both_short.returncode != 0, both_short.stdout
    assert both_short.stderr.strip() == (
        "a music folder and the connected iPod could not be read through:"
        " this answer is partial"
    )
    # The same run as a search names the folder alone, because the iPod is not
    # one of the places a search reads and cannot be one it is short of.
    search_short = invoke("search", "Song", FAKE_IPOD_MOUNT=str(fresh))
    assert search_short.returncode != 0, search_short.stdout
    assert search_short.stderr.strip() == (
        "a music folder could not be read through: this answer is partial"
    )
    run("config", "--music-root", str(music))
    shut.chmod(0o700)
    shut.rmdir()

# An iPod that will not answer is short of a reading rather than an iPod
# holding nothing: the probe cannot see inside it, so the library says so
# instead of writing down a confident zero.
if os.geteuid() != 0:
    (fresh / "iPod_Control").chmod(0o000)
    silent = invoke("library", FAKE_IPOD_MOUNT=str(fresh))
    assert silent.returncode != 0, silent.stdout
    silent_document = json.loads(silent.stdout)
    assert silent_document["result"]["complete"] is False
    assert silent_document["result"]["counts"]["ipod"] == 0
    assert silent.stderr.strip() == (
        "the connected iPod could not be read through: this answer is partial"
    )
    (fresh / "iPod_Control").chmod(0o700)

# Ordinary machines mount vfat volumes this user may not look inside - /boot/efi
# is one, and it is mounted for root only on the runner this suite runs on - so
# detection has to pass over one rather than die on it and take every command
# that probes the device down with it.
if os.geteuid() != 0:
    forbidden = home / "Forbidden Volume"
    forbidden.mkdir()
    forbidden.chmod(0o000)
    beside = run(
        "device",
        FAKE_IPOD_MOUNT=os.pathsep.join([str(forbidden), str(shuffle)]),
    )
    assert beside["result"]["candidates"] == [str(shuffle)]
    assert beside["result"]["mountPoint"] == str(shuffle)
    assert beside["result"]["trackCount"] == 3
    forbidden.chmod(0o700)

# The searcher is asked for, so both answers it can give are checked: results
# that came back, and a search that could not reach YouTube - which is not the
# same as one that found nothing, and is the distinction the second field
# exists for.
found = run("search", "Song", "--youtube")
assert found["result"]["reachedYoutube"] is True
assert [video["video_id"] for video in found["result"]["youtube"]] == ["testvideo"]
assert found["result"]["youtube"][0]["uploader"] == "Test Artist"
assert found["result"]["youtube"][0]["duration"] == 180.0
assert found["result"]["youtube"][0]["url"] == "https://www.youtube.com/watch?v=testvideo"
assert "Song" in found["result"]["youtube"][0]["title"]
assert [track["path"] for track in found["result"]["local"]] == [str(song)]
unreachable = run("search", "Song", "--youtube", FAKE_YTDLP_SEARCH_FAILS="1")
assert unreachable["result"]["reachedYoutube"] is False
assert unreachable["result"]["youtube"] == []
assert [track["path"] for track in unreachable["result"]["local"]] == [str(song)]

# Stored absolute, because the window reads this same file from a working
# directory of its own.
assert run("config", "--music-root", "Music")["result"]["musicRoots"] == [str(repo / "Music")]
assert run("config", "--music-root", "~/Music")["result"]["musicRoots"] == [str(music)]

# Arguments the parser will not take reach none of the model, and say so with
# the code of their own that a caller reading the exit table meets first.
unusable()
unusable("playlists")
unusable("nonesuch")
unusable("playlists", "--root", str(playlists), "reorder", "Road Trip", "first", "0")
print("headless cli ok")
