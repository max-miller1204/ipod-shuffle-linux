# A real MCP client against tools/mcp-server.py

Claude Code was pointed at the server exactly the way the README tells a client to launch it:
`/usr/bin/python3` plus the absolute path to `tools/mcp-server.py`. No package was installed for it.

## Client configuration (`--mcp-config`, `--strict-mcp-config`)

```json
{
    "mcpServers": {
        "ipod": {
            "command": "/usr/bin/python3",
            "args": [
                "/home/max/.no-mistakes/worktrees/f62f10c43f33/01KZTVKVPBYZ5EQJDY5Y7R3HAH/tools/mcp-server.py"
            ],
            "env": {
                "HOME": "/tmp/mcp-realclient/home",
                "XDG_CONFIG_HOME": "/tmp/mcp-realclient/home/config",
                "XDG_CACHE_HOME": "/tmp/mcp-realclient/home/cache"
            }
        }
    }
}
```

## Fixture iPod before the session

```
45838a51dd374b53fb4871a5ad0f96370f88be165217a94ae64b4818d892d058  iPod/iPod_Control/Device/SysInfo
2b8425c4d20e743705f4787b4dda39344b4242bc8636228a00b7d65378aa7694  iPod/iPod_Control/Music/Album/01 - Keep.mp3
106f964261df6a423e2347d132ff683ad2b89a10bc40e4b2bc2f23066c7213c8  iPod/iPod_Control/Music/Album/02 - Also Keep.mp3
2934d46a57407eb0d71f0ffd9dc3773905a1150aa2fd2ecf8947cb29b4218028  iPod/iPod_Control/Speakable/System/battery.wav
```

## What the client reported back

## 1) Tools exposed by the `ipod` server

**read (6):**
- `read_cache`
- `read_device`
- `read_library`
- `read_playlists`
- `read_search`

(That's 5 - listing corrected below.)

- read: `read_cache`, `read_device`, `read_library`, `read_playlists`, `read_search` - 5 tools
- plan: `plan_remove`, `plan_sync`, `plan_wipe` - 3 tools
- execute: `execute_remove`, `execute_sync`, `execute_wipe` - 3 tools

11 tools total. I did not call any execute tool.

## 2) `read_playlists` result

```json
{"schema": 1, "command": "playlists", "result": []}
```

Empty list - no playlists on the device.

## 3) `plan_remove` result

Called with `ipod=/tmp/mcp-realclient/iPod`, `targets=["Album/01 - Keep.mp3"]`:

```json
{"action":"remove","device":{"mount":"/tmp/mcp-realclient/iPod","identity":"uuid:7db76715-7b0b-4eb9-8aa0-3bcf3e3b27cc"},"destructive":true,"arguments":["playlist-mode=0","eject=0","tracks=1","/tmp/mcp-realclient/iPod/iPod_Control/Music/Album/01 - Keep.mp3"],"confirmationToken":"47d262255df402c408ddf3fcff429775e258c35637e4278c2623d52e4da14601"}
```

confirmationToken:
```
47d262255df402c408ddf3fcff429775e258c35637e4278c2623d52e4da14601
```

## 4) Was the track actually deleted?

No. `plan_remove` is a dry run - it returned the plan and a token but changed nothing. I confirmed with a directory listing: `/tmp/mcp-realclient/iPod/iPod_Control/Music/Album/` still contains both `01 - Keep.mp3` and `02 - Also Keep.mp3`. Deleting it would require a separate `execute_remove` call passing that confirmation token, which I did not make.

## Fixture iPod after the session

```
45838a51dd374b53fb4871a5ad0f96370f88be165217a94ae64b4818d892d058  iPod/iPod_Control/Device/SysInfo
2b8425c4d20e743705f4787b4dda39344b4242bc8636228a00b7d65378aa7694  iPod/iPod_Control/Music/Album/01 - Keep.mp3
106f964261df6a423e2347d132ff683ad2b89a10bc40e4b2bc2f23066c7213c8  iPod/iPod_Control/Music/Album/02 - Also Keep.mp3
2934d46a57407eb0d71f0ffd9dc3773905a1150aa2fd2ecf8947cb29b4218028  iPod/iPod_Control/Speakable/System/battery.wav
```

Byte-identical: the dry run the client made left the device alone, and the track it planned to remove is still there.
