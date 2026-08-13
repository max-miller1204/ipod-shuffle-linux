#!/usr/bin/env python3
"""Exercise the MCP stdio handshake, tools, and destructive boundary.

The boundary is checked by driving a real session over the protocol against a
fake mount, and by reading the volume afterwards rather than the sentence the
run printed: a refusal that deletes anyway prints the same words as one that
does not. A dry run has to leave the device byte-identical and hand back a
token, an execute has to be refused when the identity or the token is not the
one that plan printed, and the plan's own token has to carry the run through.

One server answers the whole file, because the session is part of what is
being checked: a request that cannot be served is one answer, and a child that
could reach this client's pipe would answer with the next request. The one
exception is the short second server driving the read tools that shell out to
findmnt and yt-dlp, which are answered by this suite's doubles - reached
through a PATH that the device-changing scripts must not be run under.
"""

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix="mcp-server-")).resolve()
home = root / "home"
(home / "Music").mkdir(parents=True)
source = root / "New Album"
source.mkdir()
(source / "01 - New.mp3").write_text("new song\n")
backup = root / "backup"
# A library, a playlist store and a preview cache under this fixture home, so
# that each read tool has something of its own to find. A read tool checked
# only for running would pass while answering from somebody else's machine or
# from nothing at all; what is asserted below is that the document coming back
# describes these files.
library_track = home / "Music" / "Album" / "01 - Song.mp3"
library_track.parent.mkdir(parents=True)
library_track.write_text("song\n")
playlist = home / "Music" / "Playlists" / "Road Trip.m3u"
playlist.parent.mkdir(parents=True)
playlist.write_text(f"{library_track}\n")
preview = home / "cache" / "ipod-shuffle-linux" / "previews" / "testvideo" / "preview.m4a"
preview.parent.mkdir(parents=True)
preview.write_bytes(b"preview bytes")
# The database builder is the test double the end-to-end suite uses, named the
# way lib.sh resolves it, so an approved run finishes without the upstream
# writer and records what it was asked to build.
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / "config"),
    XDG_CACHE_HOME=str(home / "cache"),
    IPOD_DB_TOOL=str(repo / "tests" / "fake-db-builder.py"),
    IPOD_VENV_PYTHON="/usr/bin/python3",
    FAKE_DB_RECORD=str(root / "database-invocations.jsonl"),
)


class Session:
    """A running server, driven one request at a time over its pipes."""

    def __init__(self, environment=env):
        self.process = subprocess.Popen(
            ["/usr/bin/python3", "tools/mcp-server.py"],
            cwd=repo,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        self.stdin = self.process.stdin
        self.stdout = self.process.stdout
        self.identifier = 0

    def send(self, document):
        self.stdin.write(json.dumps(document) + "\n")
        self.stdin.flush()

    def request(self, method, params=None):
        self.identifier += 1
        document = {"jsonrpc": "2.0", "id": self.identifier, "method": method}
        if params is not None:
            document["params"] = params
        self.send(document)
        # A server that let a child read this pipe would leave the answer to
        # be written by nobody, so the wait is bounded and reported rather
        # than left to hang whichever runner it happens on.
        watchdog = threading.Timer(180, self.process.kill)
        watchdog.start()
        try:
            line = self.stdout.readline()
        finally:
            watchdog.cancel()
        assert line, f"no answer to {method} {params}"
        answer = json.loads(line)
        assert answer["id"] == self.identifier, answer
        return answer

    def call(self, name, arguments):
        answer = self.request("tools/call", {"name": name, "arguments": arguments})
        assert "result" in answer, answer
        result = answer["result"]
        assert len(result["content"]) == 1, result
        return result["isError"], result["content"][0]["text"]

    def refuse(self, name, arguments):
        """A call the server declines to make at all, as a protocol error."""
        answer = self.request("tools/call", {"name": name, "arguments": arguments})
        assert "result" not in answer, answer
        assert answer["error"]["code"] == -32602, answer
        return answer["error"]["message"]

    def close(self):
        # communicate() flushes this pipe and closes it, and that close is the
        # end of input the server's read loop stops on. Closing it here first
        # would leave communicate() flushing a file that is already shut, which
        # every interpreter before 3.13 raises on, including the 3.12 CI runs.
        trailing, complaints = self.process.communicate(timeout=180)
        assert not trailing, trailing
        assert not complaints, complaints
        assert self.process.returncode == 0, self.process.returncode


def make_mount(name, speakable=True):
    mount = root / name
    (mount / "iPod_Control" / "iTunes").mkdir(parents=True)
    (mount / "iPod_Control" / "Music" / "Album").mkdir(parents=True)
    (mount / "iPod_Control" / "Device").mkdir(parents=True)
    (mount / "iPod_Control" / "Device" / "SysInfo").write_text(f"identity of {name}\n")
    (mount / "iPod_Control" / "Music" / "Album" / "01 - Keep.mp3").write_text("keep me\n")
    (mount / "iPod_Control" / "Music" / "Album" / "02 - Also Keep.mp3").write_text("keep me too\n")
    if speakable:
        (mount / "iPod_Control" / "Speakable" / "System").mkdir(parents=True)
        (mount / "iPod_Control" / "Speakable" / "System" / "battery.wav").write_text("prompt\n")
    return mount


def device_state(mount):
    """Every path on the volume and the bytes of every file on it."""
    state = []
    for path in sorted(mount.rglob("*")):
        name = str(path.relative_to(mount))
        if path.is_dir():
            state.append(f"d {name}")
        else:
            state.append(f"f {name} {hashlib.sha256(path.read_bytes()).hexdigest()}")
    return state


def database_state():
    """The bytes the database-builder double has recorded, when it has run."""
    record = Path(env["FAKE_DB_RECORD"])
    return record.read_bytes() if record.exists() else None


session = Session()

answer = session.request(
    "initialize",
    {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
)
assert answer["result"]["protocolVersion"] == "2025-06-18", answer
# A notification carries no id and gets no answer; the next request still does.
session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
assert session.request("ping")["result"] == {}

tools = session.request("tools/list")["result"]["tools"]
names = [tool["name"] for tool in tools]
assert names[:5] == ["read_library", "read_device", "read_playlists", "read_cache", "read_search"]
assert names[5:] == ["plan_sync", "execute_sync", "plan_remove", "execute_remove", "plan_wipe", "execute_wipe"]
for tool in tools[:5]:
    assert "Read-only" in tool["description"]
for tool in tools[5::2]:
    assert tool["description"].startswith("DRY RUN")
for tool in tools[6::2]:
    assert tool["description"].startswith("DESTRUCTIVE")

# What the listing promises about the boundary, read off the schemas a client
# generates its calls from: a plan takes no approval because it never needs
# one, and an execute cannot be called without both halves of the handshake.
schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
for operation, field in (("sync", "sources"), ("remove", "targets"), ("wipe", "backup")):
    plan_schema = schemas[f"plan_{operation}"]
    execute_schema = schemas[f"execute_{operation}"]
    assert plan_schema["required"] == ["ipod"], plan_schema
    assert set(plan_schema["properties"]) == {"ipod", field}, plan_schema
    assert set(execute_schema["required"]) == {"ipod", "expectedDevice", "confirmationToken"}, execute_schema
    assert set(execute_schema["properties"]) == {"ipod", "expectedDevice", "confirmationToken", field}, execute_schema
    assert execute_schema["properties"]["expectedDevice"]["description"] == (
        f"Exact device.identity returned by plan_{operation} for this execution."
    )
    assert execute_schema["properties"]["confirmationToken"]["description"] == (
        f"Exact confirmationToken returned by plan_{operation} for this execution."
    )
    assert plan_schema["additionalProperties"] is False
    assert execute_schema["additionalProperties"] is False

failed, text = session.call("read_search", {"query": "Song"})
assert not failed, text
found = json.loads(text)["result"]
assert [track["path"] for track in found["local"]] == [str(library_track)], found
# The flag is not sent, so the command must not have gone looking for YouTube.
assert "youtube" not in found, found

failed, text = session.call("read_playlists", {})
assert not failed, text
stored = json.loads(text)["result"]
assert [(entry["name"], entry["entries"]) for entry in stored] == [
    ("Road Trip", [str(library_track)])
], stored

failed, text = session.call("read_cache", {})
assert not failed, text
cache = json.loads(text)["result"]
assert cache["root"] == str(preview.parents[1]), cache
assert [entry["path"] for entry in cache["entries"]] == [str(preview)], cache
assert cache["sizeBytes"] == preview.stat().st_size, cache

# The remaining answers are not this machine's to give: the library reads
# whatever iPod is plugged in as well as this computer, through findmnt, and
# the YouTube half of the search calls yt-dlp, so both are exercised against
# the doubles the rest of the suite uses, reached by putting tests/bin first on
# PATH. A second server rather than the one above, because that PATH would
# reach every script the device operations below run.
attached = make_mount("attached")
(attached / "Favourites.m3u").write_text("iPod_Control/Music/Album/01 - Keep.mp3\n")
attached_before = device_state(attached)
doubled = Session(
    dict(
        env,
        PATH=os.pathsep.join([str(repo / "tests" / "bin"), env["PATH"]]),
        FAKE_IPOD_MOUNT=str(attached),
    )
)
doubled.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

failed, text = doubled.call("read_device", {})
assert not failed, text
device = json.loads(text)["result"]
assert device["candidates"] == [str(attached)], device
assert device["mountPoint"] == str(attached), device
assert device["identity"], device
assert device["readable"] is True, device
assert device["trackCount"] == 2, device
assert [(entry["name"], entry["entries"]) for entry in device["playlists"]] == [
    ("Favourites", ["Album/01 - Keep.mp3"])
], device

# The library a client reads is the merged one the window shows: this
# computer's music root, the download waiting in the preview cache, and the two
# tracks only the attached iPod holds, each carrying the state it is in.
failed, text = doubled.call("read_library", {})
assert not failed, text
merged = json.loads(text)
assert merged["command"] == "library", merged
assert merged["result"]["complete"] is True, merged
album = attached / "iPod_Control" / "Music" / "Album"
assert {
    track["path"]: track["state"] for track in merged["result"]["tracks"]
} == {
    str(library_track): "library",
    str(preview): "preview",
    str(album / "01 - Keep.mp3"): "ipod",
    str(album / "02 - Also Keep.mp3"): "ipod",
}, merged
# No window, so no queue: the fourth state is one this process cannot be in.
assert merged["result"]["counts"] == {
    "ipod": 2,
    "queued": 0,
    "library": 1,
    "preview": 1,
}, merged

# A search with that same iPod plugged in answers about this computer only:
# the tracks it holds and this one does not are what read_library and
# read_device are for, and a query pays for neither.
failed, text = doubled.call("read_search", {"query": "Keep"})
assert not failed, text
assert json.loads(text)["result"]["local"] == [], text

failed, text = doubled.call("read_search", {"query": "Song", "youtube": True})
assert not failed, text
found = json.loads(text)["result"]
assert [track["path"] for track in found["local"]] == [str(library_track)], found
assert found["reachedYoutube"] is True, found
assert [video["url"] for video in found["youtube"]] == [
    "https://www.youtube.com/watch?v=testvideo"
], found
doubled.close()

# Every tool above is listed as read-only, and the preview cache is the one
# thing the CLI behind them can also be told to delete from, so what they
# answered about is read back rather than taken on the description's word.
assert library_track.read_text() == "song\n"
assert playlist.read_text() == f"{library_track}\n"
assert preview.read_bytes() == b"preview bytes"
assert device_state(attached) == attached_before, "a read tool changed the device"

# Arguments that do not fit the schema are refused before anything is run, so
# a string where an array belongs cannot reach a command line one character at
# a time, and a field belonging to another operation is an error rather than
# something silently dropped. The mount named here does not exist: a call that
# reached a script would come back as a tool result instead of an error.
assert "sources must be an array" in session.refuse("plan_sync", {"ipod": "/nowhere", "sources": "/home/u/Music"})
assert "unknown argument: sources" in session.refuse("plan_wipe", {"ipod": "/nowhere", "sources": ["/home/u/Music"]})
assert "unknown argument: confirmationToken" in session.refuse(
    "plan_remove", {"ipod": "/nowhere", "confirmationToken": "x"}
)
assert "missing argument: confirmationToken" in session.refuse(
    "execute_wipe", {"ipod": "/nowhere", "expectedDevice": "x"}
)
assert "ipod must be a non-empty string" in session.refuse("plan_wipe", {"ipod": ""})
assert "youtube must be a boolean" in session.refuse("read_search", {"query": "x", "youtube": "yes"})
assert "unknown tool" in session.refuse("execute_everything", {})
# An empty approval is the one spelling that would reach a script as no
# approval at all, since both halves of the handshake are a token the scripts
# compare only when it is not empty. Required is therefore not enough on its
# own, and each half is held here rather than left to the other's error.
assert "confirmationToken must be a non-empty string" in session.refuse(
    "execute_sync", {"ipod": "/nowhere", "expectedDevice": "sysinfo:some-ipod", "confirmationToken": ""}
)
assert "expectedDevice must be a non-empty string" in session.refuse(
    "execute_sync", {"ipod": "/nowhere", "expectedDevice": "", "confirmationToken": "x"}
)

# A device the scripts would ask a question about. The server has no answer to
# give and must not go looking for one on the client's pipe: the run is
# declined, and the session keeps answering the client afterwards.
unattended = make_mount("not-a-shuffle", speakable=False)
failed, text = session.call("plan_wipe", {"ipod": str(unattended)})
assert failed, text
assert "Aborted" in text, text
assert session.request("ping")["result"] == {}

mount = make_mount("target")
# Options a previous sync saved, so that a refused run has something on stdout
# as well as the refusal on stderr, and both have to survive the round trip.
(mount / "iPod_Control" / ".sync-options").write_text("--playlist-voiceover\n")
before = device_state(mount)


def plan(name, arguments):
    failed, text = session.call(name, arguments)
    assert not failed, text
    document = json.loads(text)
    assert document["device"]["mount"] == str(mount), document
    assert document["device"]["identity"], document
    assert document["confirmationToken"], document
    return document


remove_plan = plan("plan_remove", {"ipod": str(mount), "targets": ["Album/01 - Keep.mp3"]})
sync_plan = plan("plan_sync", {"ipod": str(mount), "sources": [str(source)]})
wipe_plan = plan("plan_wipe", {"ipod": str(mount), "backup": str(backup)})
plans = (remove_plan, sync_plan, wipe_plan)
assert [document["action"] for document in plans] == ["remove", "sync", "wipe"]
assert len({document["confirmationToken"] for document in plans}) == 3, plans
assert len({document["device"]["identity"] for document in plans}) == 1, plans
assert remove_plan["destructive"] is True, remove_plan
assert wipe_plan["destructive"] is True, wipe_plan
assert any(argument.endswith("Album/01 - Keep.mp3") for argument in remove_plan["arguments"]), remove_plan
assert str(source) in sync_plan["arguments"], sync_plan
assert f"backup={backup}" in wipe_plan["arguments"], wipe_plan
assert device_state(mount) == before, "a dry run changed the device"
assert not backup.exists(), "a dry run made a backup"

identity = remove_plan["device"]["identity"]
removal = {"ipod": str(mount), "targets": ["Album/01 - Keep.mp3"]}

sync = {"ipod": str(mount), "sources": [str(source)]}

# Supplying a token opts every sync into the exact dry-run plan, even though a
# normal interactive sync is not destructive and may run without one.
sync_before = device_state(mount)
database_before = database_state()
failed, text = session.call(
    "execute_sync",
    {**sync, "expectedDevice": identity, "confirmationToken": "not-a-plan-token"},
)
assert failed, text
assert "Destructive action refused: confirmation is not authorization" in text, text
assert device_state(mount) == sync_before, "a sync with an invalid token wrote to the device"
assert database_state() == database_before, "a sync with an invalid token rebuilt the database"

failed, text = session.call(
    "execute_sync",
    {
        "ipod": str(mount),
        "sources": [str(source / "01 - New.mp3")],
        "expectedDevice": identity,
        "confirmationToken": sync_plan["confirmationToken"],
    },
)
assert failed, text
assert "Destructive action refused: confirmation is not authorization" in text, text
assert device_state(mount) == sync_before, "a sync with changed sources wrote to the device"
assert database_state() == database_before, "a sync with changed sources rebuilt the database"

# Saved options are effective sync arguments. A plan made before they change
# is stale even when the MCP request itself is byte-for-byte identical.
options_file = mount / "iPod_Control" / ".sync-options"
options_file.write_text("--track-voiceover\n")
stale_before = device_state(mount)
failed, text = session.call(
    "execute_sync",
    {**sync, "expectedDevice": identity, "confirmationToken": sync_plan["confirmationToken"]},
)
assert failed, text
assert "Destructive action refused: confirmation is not authorization" in text, text
assert device_state(mount) == stale_before, "a sync with a stale options token wrote to the device"
assert database_state() == database_before, "a sync with a stale options token rebuilt the database"
options_file.write_text("--playlist-voiceover\n")

failed, text = session.call(
    "execute_remove",
    {**removal, "expectedDevice": "sysinfo:some-other-ipod", "confirmationToken": remove_plan["confirmationToken"]},
)
assert failed, text
assert "Expected device" in text, text
assert device_state(mount) == before, "a refused removal deleted something"

failed, text = session.call(
    "execute_remove",
    {**removal, "expectedDevice": identity, "confirmationToken": wipe_plan["confirmationToken"]},
)
assert failed, text
assert "Destructive action refused: confirmation is not authorization" in text, text
assert device_state(mount) == before, "a removal approved by another plan's token deleted something"

# The refusal arrives even though the run had already printed to stdout.
failed, text = session.call(
    "execute_sync",
    {
        "ipod": str(mount),
        "sources": [str(source)],
        "expectedDevice": "sysinfo:some-other-ipod",
        "confirmationToken": sync_plan["confirmationToken"],
    },
)
assert failed, text
assert "Reusing saved options: --playlist-voiceover" in text, text
assert "Expected device" in text, text
assert device_state(mount) == before, "a refused sync wrote to the device"

failed, text = session.call(
    "execute_remove",
    {**removal, "expectedDevice": identity, "confirmationToken": remove_plan["confirmationToken"]},
)
assert not failed, text
assert not (mount / "iPod_Control" / "Music" / "Album" / "01 - Keep.mp3").exists()
assert (mount / "iPod_Control" / "Music" / "Album" / "02 - Also Keep.mp3").exists()

# The device changed, so the plan is made again: what an execute is authorized
# to do is what the dry run in front of it described. This is also the run that
# makes the database comparisons above evidence: the record moves for a sync
# that is authorized, so the refused ones leaving it alone is a rebuild that did
# not happen rather than two absent files agreeing with each other.
sync_plan = plan("plan_sync", {"ipod": str(mount), "sources": [str(source)]})
database_before_authorized = database_state()
failed, text = session.call(
    "execute_sync",
    {
        "ipod": str(mount),
        "sources": [str(source)],
        "expectedDevice": identity,
        "confirmationToken": sync_plan["confirmationToken"],
    },
)
assert not failed, text
assert (mount / "iPod_Control" / "Music" / "New Album" / "01 - New.mp3").read_text() == "new song\n"
rebuilt = database_state()
assert rebuilt is not None, "the authorized sync did not rebuild the database"
assert rebuilt != database_before_authorized, "the authorized sync did not rebuild the database"

wipe_plan = plan("plan_wipe", {"ipod": str(mount), "backup": str(backup)})
failed, text = session.call(
    "execute_wipe",
    {
        "ipod": str(mount),
        "backup": str(backup),
        "expectedDevice": identity,
        "confirmationToken": wipe_plan["confirmationToken"],
    },
)
assert not failed, text
assert sorted(path.name for path in (mount / "iPod_Control" / "Music").rglob("*")) == []
assert (mount / "iPod_Control" / "Speakable" / "System" / "battery.wav").exists()
assert (backup / "Music" / "New Album" / "01 - New.mp3").read_text() == "new song\n"

session.close()
print("mcp server ok")
