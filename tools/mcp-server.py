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
PATH = {"type": "string", "description": "Absolute path"}

# Each device operation is declared with the arguments its script actually
# reads, so an argument meant for another one is refused rather than dropped.
OPERATIONS = {
    "sync": ("ipod-sync.sh", {"sources": {"type": "array", "items": PATH}}),
    "remove": ("ipod-remove.sh", {"targets": {"type": "array", "items": {"type": "string"}}}),
    "wipe": ("ipod-wipe.sh", {"backup": PATH}),
}


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


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
]
for _operation in OPERATIONS:
    _fields = OPERATIONS[_operation][1]
    TOOLS.append(
        {
            "name": f"plan_{_operation}",
            "description": "DRY RUN: return the exact device-changing plan and confirmation token; changes nothing.",
            "inputSchema": schema({"ipod": PATH, **_fields}, ("ipod",)),
        }
    )
    TOOLS.append(
        {
            "name": f"execute_{_operation}",
            "description": "DESTRUCTIVE: execute only the exact dry-run plan authorized by expectedDevice and confirmationToken.",
            "inputSchema": schema(
                {
                    "ipod": PATH,
                    "expectedDevice": {
                        "type": "string",
                        "description": f"Exact device.identity returned by plan_{_operation} for this execution.",
                    },
                    "confirmationToken": {
                        "type": "string",
                        "description": f"Exact confirmationToken returned by plan_{_operation} for this execution.",
                    },
                    **_fields,
                },
                ("ipod", "expectedDevice", "confirmationToken"),
            ),
        }
    )
TOOL_INDEX = {tool["name"]: tool for tool in TOOLS}


def validate(document, arguments):
    """Hold a call to the schema its tool advertises, before anything runs.

    A client that sends an argument no tool declares, omits a required one, or
    sends a string where an array belongs is told so, rather than having the
    value reach a command line that would read it as something else entirely.
    """
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    properties = document["properties"]
    for name in arguments:
        if name not in properties:
            raise ValueError(f"unknown argument: {name}")
    for name in document["required"]:
        if name not in arguments:
            raise ValueError(f"missing argument: {name}")
    for name, value in arguments.items():
        kind = properties[name]["type"]
        if kind == "string" and not (isinstance(value, str) and value):
            raise ValueError(f"{name} must be a non-empty string")
        if kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        if kind == "array" and not (
            isinstance(value, list) and all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError(f"{name} must be an array of non-empty strings")


def run(command):
    """Run one command with no way to ask this server's client a question.

    stdin is closed rather than inherited: the scripts prompt on a terminal,
    and the only thing on this process's stdin is the client's next request,
    which a child reading an answer would swallow.
    """
    return subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def cli(arguments):
    return run([sys.executable, "-m", "ipod_gui.cli", *arguments])


def script(name, arguments):
    """Build the argument vector for one device operation.

    Every option goes before the positional list and the two are separated by
    `--`, because the scripts stop reading options at the first path: a rail
    written after one would arrive as a track name instead of as a rail.
    """
    operation = name.removeprefix("plan_").removeprefix("execute_")
    executable, _ = OPERATIONS[operation]
    command = [str(REPO / executable), "--ipod", arguments["ipod"]]
    if name.startswith("plan_"):
        command.append("--dry-run")
    else:
        # The token is the consent a machine caller gives; --yes only answers
        # the prompts written for a person, and cannot answer for the token.
        command.extend(
            (
                "--yes",
                "--expect-device",
                arguments["expectedDevice"],
                "--confirm-token",
                arguments["confirmationToken"],
            )
        )
    if operation == "wipe":
        if "backup" in arguments:
            command.extend(("--backup", arguments["backup"]))
        return run(command)
    positionals = arguments.get("sources" if operation == "sync" else "targets", [])
    if positionals:
        command.append("--")
        command.extend(positionals)
    return run(command)


def call(name, arguments):
    tool = TOOL_INDEX.get(name)
    if tool is None:
        raise ValueError(f"unknown tool: {name}")
    validate(tool["inputSchema"], arguments)
    if name in READ_TOOLS:
        process = cli(list(READ_TOOLS[name]))
    elif name == "read_search":
        command = ["search", arguments["query"]]
        if arguments.get("youtube"):
            command.append("--youtube")
        process = cli(command)
    else:
        process = script(name, arguments)
    # A failed run keeps both streams: the plan or the report is on stdout,
    # while the sentence saying why a rail refused the run is on stderr, and
    # returning only the first would drop the answer the caller needs.
    parts = [process.stdout.strip()]
    if process.returncode != 0:
        parts.append(process.stderr.strip())
    text = "\n".join(part for part in parts if part) or f"command exited {process.returncode}"
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
        # One request that cannot be served is one answer, never the end of
        # the session: a missing script or an unreadable one raises from the
        # dispatch, and a server that died there would take every later
        # request with it.
        except Exception as exc:  # noqa: BLE001
            invalid = isinstance(exc, (ValueError, TypeError, KeyError))
            reply(
                request.get("id") if isinstance(request, dict) else None,
                error={
                    "code": -32602 if invalid else -32603,
                    "message": str(exc) if invalid else f"{type(exc).__name__}: {exc}",
                },
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
