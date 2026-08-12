#!/usr/bin/env python3
"""Drive the running window through its exported Gio actions."""

import json
import tempfile
from pathlib import Path

from harness import gui

home = Path(tempfile.mkdtemp(prefix="gio-actions-"))
song = home / "Song.mp3"
song.write_bytes(b"audio")

app = gui.IpodApp()
assert app.register(None)
app.activate()
window = app.props.active_window

assert {"navigate", "search", "queue", "refresh", "dump-state"}.issubset(
    set(app.list_actions())
)
app.activate_action("navigate", gui.GLib.Variant("s", "playlists"))
assert window.current_view() == "playlists"

app.activate_action("search", gui.GLib.Variant("s", "needle"))
assert window.current_view() == "search"
assert window.search_entry.get_text() == "needle"

window.mount_point = str(home)
queued = []
window._queue_paths = lambda paths, show_toast=True: queued.extend(paths)
app.activate_action("queue", gui.GLib.Variant("s", str(song)))
assert queued == [str(song)]
track = gui.Track(song, {"title": "Song", "size": 5}, gui.STATE_LIBRARY)
window.pending = {str(song)}
window.pending_sources = {str(song): {str(song)}}
window._pending_track_index = {str(song): track}

window.search_note = "Inline failure"
app.activate_action("dump-state", None)
state = json.loads(app.lookup_action("dump-state").get_state().get_string())
assert state["page"] == "search"
assert state["inlineError"] == "Inline failure"
assert state["staged"]["sources"] == [str(song)]
assert state["staged"]["changes"] == 1
assert set(state["visibleCounts"]) == {"ipod", "queued", "library", "preview"}
assert set(state["nowPlaying"]) == {"state", "track", "error"}
assert set(state["sync"]) == {"active", "title", "current", "count", "progress"}

window.close()
app.quit()
print("gio actions ok")
