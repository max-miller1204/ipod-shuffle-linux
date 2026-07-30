#!/usr/bin/env python3
"""Focused checks for GUI playlist state and command-line mapping."""

import importlib.util
import json
import sys
from pathlib import Path


repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ipod_gui", repo / "ipod-gui.py")
ipod_gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipod_gui)


class Value:
    def __init__(self, value):
        self.value = value

    def get_selected(self):
        return self.value

    def get_active(self):
        return self.value


class FakeWindow:
    loaded_playlist_mode = 0
    loaded_playlist_args = []
    playlist_mode = Value(3)
    track_voiceover = Value(True)
    playlist_voiceover = Value(True)


mount_point = Path(sys.argv[1])
state = ipod_gui.saved_sync_options(mount_point)
assert state == (
    3,
    ["--id3-playlists={genre}"],
    True,
    True,
), state

command_options = ipod_gui.IpodWindow._sync_options(FakeWindow())
assert command_options == [
    "--id3-playlists={genre}",
    "--voiceover",
    "--playlist-voiceover",
], command_options

print(
    json.dumps(
        {
            "restored_gui_state": {
                "playlist_mode": "By genre",
                "speak_tracks": state[2],
                "speak_playlists": state[3],
            },
            "sync_script_arguments": command_options,
        },
        indent=2,
    )
)
