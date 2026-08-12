#!/usr/bin/env python3
"""Exercise the public display-free CLI through its module entry point."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
home = Path(tempfile.mkdtemp(prefix="headless-cli-home-"))
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"))


def run(*args):
    proc = subprocess.run(
        ["/usr/bin/python3", "-m", "ipod_gui.cli", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not proc.stderr, proc.stderr
    document = json.loads(proc.stdout)
    assert document["schema"] == 1
    return document


config = run("config", "--music-root", str(home / "Music"), "--group", "artist", "--view", "list")
assert config["result"]["musicRoots"] == [str(home / "Music")]
assert config["result"]["group"] == "artist"
assert config["result"]["view"] == "list"

playlists = home / "Playlists"
song = home / "Music" / "Artist" / "Song.mp3"
song.parent.mkdir(parents=True)
song.write_bytes(b"audio")
created = run("playlists", "--root", str(playlists), "create", "Road Trip", str(song))
assert created["result"]["entries"] == [str(song)]
listed = run("playlists", "--root", str(playlists), "list")
assert listed["result"][0]["name"] == "Road Trip"
assert run("playlists", "--root", str(playlists), "remove", "Road Trip", str(song))["result"]["removed"] == 1

preview = home / "preview"
preview.mkdir()
(preview / "old.mp3").write_bytes(b"old")
(preview / "new.mp3").write_bytes(b"newer")
os.utime(preview / "old.mp3", (1, 1))
os.utime(preview / "new.mp3", (2, 2))
status = run("cache", "status", "--root", str(preview))
assert status["result"]["sizeBytes"] == 8
pruned = run("cache", "prune", "--root", str(preview), "--limit", "5")
assert [Path(entry["path"]).name for entry in pruned["result"]["entries"]] == ["new.mp3"]
assert run("cache", "clear", "--root", str(preview))["result"]["entries"] == []

library = run("library")
assert library["result"]["tracks"][0]["path"] == str(song)
search = run("search", "Song")
assert search["result"]["local"][0]["title"] == "Song"

device = run("device")
assert set(device["result"]) == {"candidates", "mountPoint", "identity", "readable", "trackCount", "playlists", "storage"}
print("headless cli ok")
