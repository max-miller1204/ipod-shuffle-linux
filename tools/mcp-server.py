#!/usr/bin/env python3
"""MCP stdio server over the headless CLI and destructive-operation rails."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROTOCOL = "2025-06-18"
READ_TOOLS = {
    "read_library": ("library",),
    "read_device": ("device",),
    "read_playlists": ("playlists", "list"),
    "read_cache": ("cache", "status"),
}
SCRIPT_TOOLS = {
    "plan_sync": "ipod-sync.sh",
    "execute_sync": "ipod-sync.sh",
    "plan_remove": "ipod-remove.sh",
    "execute_remove": "ipod-remove.sh",
    "plan_wipe": "ipod-wipe.sh",
    "execute_wipe": "ipod-wipe.sh",
}


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


PATH = {"type": "string", "description": "Absolute path"}
TOOLS = [
    *[
        {"name": name, "description": f"Read {name[5:].replace('_', ' ')} state. Read-only.", "inputSchema": schema({})}
        for name in READ_TOOLS
    ],
    {
        "name": "read_search",
        "description": "Search the local library and optionally YouTube. Read-only.",
        "inputSchema": schema({"query": {"type": "string"}, "youtube": {"type": "boolean"}}, ("query",)),
    },
    *[
        {
            "name": name,
            "description": (
                "DRY RUN: return the exact device-changing plan and confirmation token; changes nothing."
                if name.startswith("plan_")
                else "DESTRUCTIVE: execute only the exact dry-run plan authorized by expectedDevice and confirmationToken."
            ),
            "inputSchema": schema(
                {
                    "ipod": PATH,
                    "expectedDevice": {"type": "string"},
                    "confirmationToken": {"type": "string"},
                    "sources": {"type": "array", "items": PATH},
                    "targets": {"type": "array", "items": {"type": "string"}},
                    "backup": PATH,
                },
                ("ipod",) if name.startswith("plan_") else ("ipod", "expectedDevice", "confirmationToken"),
            ),
        }
        for name in SCRIPT_TOOLS
    ],
]


def cli(arguments):
    return subprocess.run(
        [sys.executable, "-m", "ipod_gui.cli", *arguments],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def script(name, arguments):
    command = [str(REPO / SCRIPT_TOOLS[name]), "--ipod", arguments["ipod"]]
    operation = name.removeprefix("plan_").removeprefix("execute_")
    if operation == "sync":
        command.extend(arguments.get("sources", []))
    elif operation == "remove":
        command.extend(arguments.get("targets", []))
    elif arguments.get("backup"):
        command.extend(("--backup", arguments["backup"]))
    if name.startswith("plan_"):
        command.append("--dry-run")
    else:
        command.extend(("--expect-device", arguments["expectedDevice"], "--confirm-token", arguments["confirmationToken"]))
    return subprocess.run(command, cwd=REPO, capture_output=True, text=True)


def call(name, arguments):
    if name in READ_TOOLS:
        process = cli(list(READ_TOOLS[name]))
    elif name == "read_search":
        command = ["search", arguments["query"]]
        if arguments.get("youtube"):
            command.append("--youtube")
        process = cli(command)
    elif name in SCRIPT_TOOLS:
        process = script(name, arguments)
    else:
        raise ValueError(f"unknown tool: {name}")
    output = process.stdout.strip()
    text = output or process.stderr.strip() or f"command exited {process.returncode}"
    return {"content": [{"type": "text", "text": text}], "isError": process.returncode != 0}


def reply(identifier, result=None, error=None):
    document = {"jsonrpc": "2.0", "id": identifier}
    if error is None:
        document["result"] = result
    else:
        document["error"] = error
    print(json.dumps(document, separators=(",", ":")), flush=True)


def main():
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            identifier = request.get("id")
            method = request.get("method")
            if identifier is None:
                continue
            if method == "initialize":
                reply(identifier, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "ipod-shuffle-linux", "version": "1"}})
            elif method == "ping":
                reply(identifier, {})
            elif method == "tools/list":
                reply(identifier, {"tools": TOOLS})
            elif method == "tools/call":
                params = request.get("params", {})
                reply(identifier, call(params.get("name", ""), params.get("arguments", {})))
            else:
                reply(identifier, error={"code": -32601, "message": f"method not found: {method}"})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            reply(request.get("id") if isinstance(request, dict) else None, error={"code": -32602, "message": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
