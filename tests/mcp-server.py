#!/usr/bin/env python3
"""Exercise the MCP stdio handshake, tools, and destructive boundary."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
home = Path(tempfile.mkdtemp(prefix="mcp-server-"))
(home / "Music").mkdir()
env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"), XDG_CACHE_HOME=str(home / "cache"))
requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_library", "arguments": {}}},
]
process = subprocess.run(
    ["/usr/bin/python3", "tools/mcp-server.py"],
    cwd=repo,
    env=env,
    input="".join(json.dumps(request) + "\n" for request in requests),
    capture_output=True,
    text=True,
    check=True,
)
assert not process.stderr, process.stderr
answers = [json.loads(line) for line in process.stdout.splitlines()]
assert answers[0]["result"]["protocolVersion"] == "2025-06-18"
tools = answers[1]["result"]["tools"]
names = [tool["name"] for tool in tools]
assert names[:5] == ["read_library", "read_device", "read_playlists", "read_cache", "read_search"]
assert names[5:] == ["plan_sync", "execute_sync", "plan_remove", "execute_remove", "plan_wipe", "execute_wipe"]
for tool in tools[:5]:
    assert "Read-only" in tool["description"]
for tool in tools[5::2]:
    assert tool["description"].startswith("DRY RUN")
for tool in tools[6::2]:
    assert tool["description"].startswith("DESTRUCTIVE")
result = json.loads(answers[2]["result"]["content"][0]["text"])
assert result["command"] == "library"
assert result["result"]["complete"] is True
print("mcp server ok")
