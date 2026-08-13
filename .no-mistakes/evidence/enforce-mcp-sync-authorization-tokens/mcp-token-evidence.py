#!/usr/bin/env python3
"""Drive tools/mcp-server.py over real MCP stdio and record the transcript.

Usage: mcp-token-evidence.py REPO_ROOT LABEL TRANSCRIPT_PATH

Every request and answer below is the actual JSON-RPC traffic an MCP client
would exchange with tools/mcp-server.py. After each device-changing call the
volume and the database-builder record are read back, so the transcript shows
what the run did rather than only what it said.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
label = sys.argv[2]
transcript_path = Path(sys.argv[3])
root = Path(tempfile.mkdtemp(prefix="mcp-token-evidence-")).resolve()
home = root / "home"
(home / "Music").mkdir(parents=True)
source = root / "New Album"
source.mkdir()
(source / "01 - New.mp3").write_text("new song\n")

mount = root / "target"
(mount / "iPod_Control" / "iTunes").mkdir(parents=True)
(mount / "iPod_Control" / "Music" / "Album").mkdir(parents=True)
(mount / "iPod_Control" / "Device").mkdir(parents=True)
(mount / "iPod_Control" / "Device" / "SysInfo").write_text("identity of target\n")
(mount / "iPod_Control" / "Music" / "Album" / "01 - Keep.mp3").write_text("keep me\n")
(mount / "iPod_Control" / "Speakable" / "System").mkdir(parents=True)
(mount / "iPod_Control" / "Speakable" / "System" / "battery.wav").write_text("prompt\n")
options_file = mount / "iPod_Control" / ".sync-options"
options_file.write_text("--playlist-voiceover\n")

record_path = root / "database-invocations.jsonl"
env = dict(
    os.environ,
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / "config"),
    XDG_CACHE_HOME=str(home / "cache"),
    IPOD_DB_TOOL=str(repo / "tests" / "fake-db-builder.py"),
    IPOD_VENV_PYTHON="/usr/bin/python3",
    FAKE_DB_RECORD=str(record_path),
)

server = subprocess.Popen(
    ["/usr/bin/python3", "tools/mcp-server.py"],
    cwd=repo,
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

lines = []
identifier = 0


def out(text=""):
    lines.append(text)


def device_state():
    state = []
    for path in sorted(mount.rglob("*")):
        name = str(path.relative_to(mount))
        state.append(f"d {name}" if path.is_dir() else f"f {name} {hashlib.sha256(path.read_bytes()).hexdigest()[:12]}")
    return state


def database_runs():
    return record_path.read_text().splitlines() if record_path.exists() else []


def request(method, params=None):
    global identifier
    identifier += 1
    document = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        document["params"] = params
    server.stdin.write(json.dumps(document) + "\n")
    server.stdin.flush()
    answer = json.loads(server.stdout.readline())
    out("--> " + json.dumps(document))
    if "result" in answer and "content" in answer["result"]:
        out(f"<-- isError={answer['result']['isError']}")
        for line in answer["result"]["content"][0]["text"].splitlines():
            out("    " + line)
    else:
        out("<-- " + json.dumps(answer))
    return answer


def observe(title):
    out(f"    [device] {len(device_state())} paths, tracks under Music: "
        + ", ".join(sorted(p.name for p in (mount / "iPod_Control" / "Music").rglob("*.mp3"))))
    out(f"    [database] builder invocations recorded: {len(database_runs())}")
    out(f"    [{title}]")
    out()


out(f"=== {label}: MCP execute_sync authorization over stdio ===")
out(f"repo under test: {repo}")
out()

request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "evidence", "version": "1"}})
out()

listing = request("tools/list")
execute_sync = next(tool for tool in listing["result"]["tools"] if tool["name"] == "execute_sync")
out()
out("execute_sync approval fields as a client sees them:")
out("  description: " + execute_sync["description"])
for field in ("expectedDevice", "confirmationToken"):
    schema = execute_sync["inputSchema"]["properties"][field]
    out(f"  {field}: required={field in execute_sync['inputSchema']['required']} "
        f"description={schema.get('description')!r}")
out()

out("--- 1. dry-run plan changes nothing and hands back the token ---")
before = device_state()
answer = request("tools/call", {"name": "plan_sync", "arguments": {"ipod": str(mount), "sources": [str(source)]}})
plan = json.loads(answer["result"]["content"][0]["text"])
assert device_state() == before, "the dry run changed the device"
observe("dry run left the volume byte-identical")
identity = plan["device"]["identity"]
token = plan["confirmationToken"]

out("--- 2. an invalid token must not copy tracks or rebuild the database ---")
before, database_before = device_state(), database_runs()
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": identity, "confirmationToken": "not-a-plan-token"}})
observe("device unchanged: " + str(device_state() == before)
        + ", database unchanged: " + str(database_runs() == database_before))

out("--- 3. the plan's own token does not authorize different sources ---")
before, database_before = device_state(), database_runs()
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source / "01 - New.mp3")],
    "expectedDevice": identity, "confirmationToken": token}})
observe("device unchanged: " + str(device_state() == before)
        + ", database unchanged: " + str(database_runs() == database_before))

out("--- 4. saved options changed after planning: the same request is now stale ---")
options_file.write_text("--track-voiceover\n")
before, database_before = device_state(), database_runs()
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": identity, "confirmationToken": token}})
observe("device unchanged: " + str(device_state() == before)
        + ", database unchanged: " + str(database_runs() == database_before))
options_file.write_text("--playlist-voiceover\n")

out("--- 5. an empty approval is refused as a protocol error, nothing is run ---")
before = device_state()
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": identity, "confirmationToken": ""}})
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": "", "confirmationToken": token}})
observe("device unchanged: " + str(device_state() == before))

out("--- 6. a token from another plan, and a device that is not the planned one ---")
before = device_state()
wipe_answer = request("tools/call", {"name": "plan_wipe", "arguments": {"ipod": str(mount), "backup": str(root / "backup")}})
wipe_token = json.loads(wipe_answer["result"]["content"][0]["text"])["confirmationToken"]
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": identity, "confirmationToken": wipe_token}})
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": "sysinfo:some-other-ipod", "confirmationToken": token}})
observe("device unchanged: " + str(device_state() == before))

out("--- 7. the plan's own token, unchanged arguments: the sync is allowed to run ---")
answer = request("tools/call", {"name": "plan_sync", "arguments": {"ipod": str(mount), "sources": [str(source)]}})
fresh = json.loads(answer["result"]["content"][0]["text"])["confirmationToken"]
database_before = database_runs()
request("tools/call", {"name": "execute_sync", "arguments": {
    "ipod": str(mount), "sources": [str(source)],
    "expectedDevice": identity, "confirmationToken": fresh}})
copied = (mount / "iPod_Control" / "Music" / "New Album" / "01 - New.mp3")
observe(f"track copied: {copied.exists()}, database rebuilt: {database_runs() != database_before}")

server.stdin.close()
server.wait(timeout=60)
out(f"server exited {server.returncode}, stderr: {server.stderr.read().strip()!r}")

transcript_path.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
