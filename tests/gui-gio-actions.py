#!/usr/bin/env python3
"""Drives the real window through the Gio actions it exports to a machine.

Every other GUI check calls methods unbound against stand-ins, so none of them
would notice an action that was never added, one wired to the wrong window
method, or a state dump reporting back what the caller asked for rather than
what the window did with it. This one activates each action the way `gdbus
call` activates it, on a real Adw.Application holding a real window, and reads
the answer off the stateful action afterwards.

Nothing on the window is replaced, and the queue action is why. Staging a path
goes out to a tag-reading thread and lands back on the main loop, so a check
that swapped that path for a recorder would be asserting the recorder: the
wiring between `_queue_paths`, `_queue_sources` and `_commit_queue_sources`
could break underneath it and every assertion would still hold. So the loop is
run until the window has finished instead, which is also what a client of these
actions has to do - they are fire-and-forget, and dump-state activated straight
after one can honestly answer from before the work landed.

Needs a display, so CI runs it under xvfb. Hermetic: HOME is a temporary
directory set before the package is imported, so the scan the window starts at
startup reads the music folder written below rather than the real one, and the
counts in the dump are a library this file knows the whole of.
"""

import json
import os
import tempfile
import time
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="ipod-gio-actions-")
os.environ["HOME"] = _SANDBOX
os.environ["XDG_CACHE_HOME"] = str(Path(_SANDBOX, "cache"))
os.environ["XDG_CONFIG_HOME"] = str(Path(_SANDBOX, "config"))
os.environ["XDG_DATA_HOME"] = str(Path(_SANDBOX, ".local/share"))

# The music folder the window scans at startup, written before the package is
# imported so the roots it reads are these. Two files rather than none, because
# the counts in the dump are one of the things being checked and a library of
# nothing agrees with every possible way of counting it.
MUSIC = Path(_SANDBOX, "Music")
MUSIC.mkdir(parents=True, exist_ok=True)
LIBRARY_SONGS = []
for _stem in ("01 - Kept", "02 - Held"):
    _song = MUSIC / f"{_stem}.mp3"
    _song.write_bytes(b"a song in the library")
    LIBRARY_SONGS.append(_song)

from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# Gtk.init_check() answers True with no display at all, so the display is asked
# for directly. Refused rather than skipped: a check that quietly does nothing
# reads as coverage that is not there.
Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit(
        "no display: run this under `xvfb-run -a`, or on a desktop session"
    )

# Detection is gui-detection-smoke's subject. Here it only has to answer the
# same way every run: a real probe could find a real iPod and move the mount
# point and identity the queue action is staged against out from under it.
gui.find_ipods = lambda: []

# The iPod the queue is staged against. The queue holds paths rather than
# copying anything, so the mount only has to be a folder that is really there -
# what would touch the device is the sync, which this never runs.
MOUNT = Path(_SANDBOX, "mount")
MOUNT.mkdir(parents=True, exist_ok=True)
SOURCE = Path(_SANDBOX, "Elsewhere")
SOURCE.mkdir(parents=True, exist_ok=True)
SONG = SOURCE / "01 - Highway.mp3"
SONG.write_bytes(b"a staged song")

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def settle(condition, seconds=30):
    """Run the loop until the window has caught up, or give up on it.

    These actions are fire-and-forget by design: activating one hands the
    window some work and returns, and the queue's half of that work is a tag
    read on another thread. Polled rather than waited on for a specific signal,
    because what a client of this interface can see is the same thing - the
    state dump - and this is what reading it until it answers looks like.
    """
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        if not context.iteration(False):
            time.sleep(0.01)
    return bool(condition())


def dump(app):
    """Activate dump-state and read the document it leaves behind."""
    app.activate_action("dump-state", None)
    return json.loads(app.lookup_action("dump-state").get_state().get_string())


app = gui.IpodApp()
if not app.register(None):
    raise SystemExit("the application would not register")
if app.get_is_remote():
    raise SystemExit(
        "another instance already owns this application's bus name: close the "
        "running window, or run this on a session bus of its own"
    )
app.activate()
window = app.props.active_window
if window is None:
    raise SystemExit("activating the application built no window")

# The window probes for a device and scans the music folders as it opens, and
# both land on the main loop some time later. The probe is the one that matters
# here: finding no iPod clears the mount point and identity and supersedes any
# queueing in flight, so a check that staged before it landed would be racing
# the window's own startup rather than testing the action.
if not settle(lambda: window.probe_answered):
    raise SystemExit("the window's first device probe never landed")
if not settle(lambda: not window._library_scan_running):
    raise SystemExit("the window's first library scan never finished")

# What the dump says about a library this file wrote every file of. Asserted as
# the numbers themselves rather than against the window's own tally, which
# would be the check asking the implementation to agree with itself. The
# distinction it pins is the one worth pinning: these are tracks, and the pills
# the library page draws count collections whenever the grid is up, so an
# implementation that answered with `counts()` would be reporting one album
# here where a machine client asked how many songs there are.
QUIET_LIBRARY = {"ipod": 0, "queued": 0, "library": len(LIBRARY_SONGS), "preview": 0}
state = dump(app)
check(
    set(state["visibleCounts"]) == set(gui.STATE_LABELS),
    f"the counts are keyed {sorted(state['visibleCounts'])} rather than by the "
    f"states the pills are labelled with, {sorted(gui.STATE_LABELS)}",
)
check(
    state["visibleCounts"] == QUIET_LIBRARY,
    f"over {len(LIBRARY_SONGS)} files in the music folder and nothing staged, "
    f"the dump counts {state['visibleCounts']}",
)

# The exported surface as a client meets it: the names, and the argument each
# one takes. A client passes a string blind over D-Bus, so an action that
# stopped taking one would fail at the call rather than at the window.
EXPECTED_PARAMETERS = {
    "navigate": "s",
    "search": "s",
    "queue": "s",
    "refresh": None,
    "dump-state": None,
}
missing = set(EXPECTED_PARAMETERS) - set(app.list_actions())
check(not missing, f"the application exports no {sorted(missing)} action")
for name, expected in EXPECTED_PARAMETERS.items():
    action = app.lookup_action(name)
    if action is None:
        continue
    parameter = action.get_parameter_type()
    actual = None if parameter is None else parameter.dup_string()
    check(
        actual == expected,
        f"the {name} action takes {actual!r} rather than {expected!r}",
    )

# ------------------------------------------------------------------ navigate

app.activate_action("navigate", GLib.Variant("s", "playlists"))
check(
    window.current_view() == "playlists",
    f"navigate left the window on {window.current_view()!r}",
)
check(
    dump(app)["page"] == "playlists",
    "the state dump disagreed with the window about which page is up",
)

# ------------------------------------------- a page the window does not have

# The action carries a page name in from outside the window, so this is the one
# argument nothing on screen can be trusted to have produced. Read from the
# library page, which is the one that wears every piece of chrome a refused
# name used to be able to move: it is the selected sidebar row, its title is in
# the header, and the sort and grouping controls belong to it alone.
app.activate_action("navigate", GLib.Variant("s", "library"))
app.activate_action("navigate", GLib.Variant("s", "bogus"))
check(
    window.current_view() == "library",
    f"navigating to a page that does not exist moved the window to "
    f"{window.current_view()!r}",
)
check(
    window.view_title.get_text() == "Your Library",
    f"the header reads {window.view_title.get_text()!r} after navigating to a "
    f"page that does not exist",
)
check(
    "selected" in window.nav_buttons["library"].get_css_classes(),
    "no sidebar row is selected after navigating to a page that does not exist",
)
check(
    window.library_controls.get_visible(),
    "the library's own controls were hidden by navigating to a page that does "
    "not exist",
)

# -------------------------------------------------------------------- search

app.activate_action("search", GLib.Variant("s", "a"))
check(
    window.current_view() == "search",
    f"search left the window on {window.current_view()!r}",
)
check(
    window.search_entry.get_text() == "a",
    f"the search field holds {window.search_entry.get_text()!r}",
)

# A query below the length YouTube is asked at, so the page puts a sentence up
# in place of results without a network round trip, and the same sentence every
# run. It arrives on the field's own debounce rather than with the action.
settle(lambda: window.search_note)
note = window.search_note
check(bool(note), "the search page put no note up for 'a'")
state = dump(app)
check(
    state["inlineError"] == note,
    f"the dump reports {state['inlineError']!r} as the inline error while the "
    f"search page is showing {note!r}",
)

# The other half of the refusal: a name that goes nowhere must not end the
# search on its way there. Ending it is the first thing following a real
# sidebar row does, and it empties the field and leaves the library up.
app.activate_action("navigate", GLib.Variant("s", "bogus"))
check(
    window.current_view() == "search",
    f"navigating to a page that does not exist left the search page for "
    f"{window.current_view()!r}",
)
check(
    window.search_entry.get_text() == "a",
    "navigating to a page that does not exist emptied the search field",
)

# And a page that outlives the note it left behind. Following a sidebar row
# would prove nothing here, because that ends the search on its way out and
# empties the note first; the shape this field is gated for is the one the
# window reaches from a result's own ⋯ menu, where creating a playlist runs
# _select_playlist and shows the playlists page with the search untouched. So
# the page is changed the way that path changes it, and the note stays set
# behind it while nothing on screen is showing it.
window.show_view("playlists")
check(
    window.search_note == note,
    f"leaving the search page this way was supposed to leave the note alone, "
    f"but it now reads {window.search_note!r}",
)
check(
    dump(app)["inlineError"] == "",
    f"the dump reports {note!r} as an inline error while the playlists page is "
    f"up and nothing is showing it",
)

# --------------------------------------------------------------------- queue

window.mount_point = str(MOUNT)
window.device_identity = "fixture-ipod"
app.activate_action("queue", GLib.Variant("s", str(SONG)))
settle(lambda: window.pending_sources)
check(
    set(window.pending_sources) == {str(SONG)},
    f"queueing staged {sorted(window.pending_sources)}",
)
check(
    window.pending == {str(SONG)},
    f"the queue holds {sorted(window.pending)}",
)

staged = dump(app)["staged"]
check(staged["sources"] == [str(SONG)], f"the dump staged {staged['sources']}")
check(
    [track["path"] for track in staged["tracks"]] == [str(SONG)],
    f"the dump's staged tracks are {staged['tracks']}",
)
check(
    staged["tracks"] and staged["tracks"][0]["title"] == "01 - Highway",
    "the staged track carries none of the serialization the CLI writes",
)
check(staged["changes"] == 1, f"the dump counts {staged['changes']} changes")
check(
    staged["bytes"] == SONG.stat().st_size,
    f"the dump counts {staged['bytes']} bytes staged",
)
check(
    staged["deviceIdentity"] == "fixture-ipod",
    f"the queue is staged against {staged['deviceIdentity']!r}",
)

# And the counts moved with it. The staged song is outside every music root, so
# it is a track the queue is the only holder of: it joins the tally as queued
# without leaving the library count, which is what makes this a reading of the
# merge rather than of the scan.
check(
    dump(app)["visibleCounts"] == {**QUIET_LIBRARY, "queued": 1},
    f"with one song staged the dump counts {dump(app)['visibleCounts']}",
)

# ------------------------------------------------------------------- refresh

# Waited out first, because the window starts a scan of its own as it opens and
# the spinner it leaves running would answer for that one. What refresh does is
# start another, which is what makes the spinner an effect of the action.
settle(lambda: not window.refresh_spinner.get_spinning())
check(
    not window.refresh_spinner.get_spinning(),
    "the startup scan never finished, so refresh has nothing to show",
)
app.activate_action("refresh", None)
check(
    window.refresh_spinner.get_spinning(),
    "refresh started no scan: the button is not spinning",
)

# --------------------------------------------------------------- the document

state = dump(app)
check(state["schema"] == 1, f"the document says schema {state['schema']}")
check(
    set(state) == {
        "schema", "page", "visibleCounts", "staged", "nowPlaying", "sync",
        "inlineError",
    },
    f"the document's fields are {sorted(state)}",
)
check(
    state["nowPlaying"]["track"] is None
    and state["nowPlaying"]["state"] == gui.PLAY_IDLE,
    f"nothing has been played, but now-playing reads {state['nowPlaying']}",
)
check(
    set(state["sync"]) == {"active", "title", "current", "count", "progress"},
    f"the sync bar's fields are {sorted(state['sync'])}",
)
check(
    state["sync"]["active"] is False,
    "no script is running, but the dump says one is",
)

window.close()
app.quit()

if failures:
    for failure in failures:
        print(f"FAIL: {failure}")
    raise SystemExit(1)
print("gio actions ok")
