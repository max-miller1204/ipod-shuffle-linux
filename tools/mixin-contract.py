#!/usr/bin/env python3
"""Architecture tool that holds the window's mixins to their intended split.

IpodWindow stays one Adw.ApplicationWindow, so its parts are mixins over one
`self` rather than objects with their own state. That is the arrangement's one
real hazard: nothing in Python stops a method in one mixin from reaching into
another's attributes, and a few of those would turn seven files back into one
namespace that merely happens to be spread across a directory.

So what they share is written down here rather than left to whoever reads the
diff. SHARED_STATE is every attribute more than one module touches, and it is
checked in both directions: a new one fails, and one that stops being shared
fails too, so the table cannot quietly describe a split the code has left.

Adding to it is allowed. Doing it on purpose is the point - the table is what
makes "these two parts share this" a decision rather than an accident.

This repo tool runs at lint time rather than as a check in the test suite. No
window is created, because that would need a display; the modules are read as
source, which is also the only way to see which module a method was written in
rather than which one the MRO resolves it from.
"""

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import ipod_gui  # noqa: E402

# attribute -> the modules allowed to touch it. Ordered by how far each one
# reaches, because that ordering is the argument for the split: three quarters
# of the table is two modules sharing one thing, and only the device and the
# library are genuinely window-wide.
SHARED_STATE = {
    # The device the window is pointed at, and whether a script is changing it.
    # Every part of the window has to know: it decides what is sensitive, what
    # can be queued, and what a repaint is allowed to say.
    "mount_point": (
        "commands", "device_view", "library_view", "playback_view",
        "playlist_view", "queue", "search_view",
    ),
    "device_identity": (
        "commands", "device_view", "playback_view", "playlist_view", "queue",
        "search_view",
    ),
    "busy": (
        "commands", "device_view", "playback_view", "playlist_view",
        "queue", "search_view",
    ),
    "device_tracks": ("commands", "device_view", "library_view", "playlist_view"),
    "discovering_sources": (
        "device_view", "playlist_view", "queue", "search_view",
    ),
    # The two models. Read widely and rebuilt in one place each.
    "library": (
        "device_view", "library_view", "playback_view", "playlist_view",
        "search_view",
    ),
    "player": ("playback_view", "search_view"),
    # What the machine cannot do, worked out once at startup.
    "youtube_unavailable": ("playback_view", "search_view"),
    "preview_unavailable": ("playback_view", "search_view"),
    "speech_engine_available": ("commands", "device_view", "playlist_view"),
    # The queue, held by queue.py and shown by the device summary.
    "pending": ("device_view", "queue"),
    "pending_device_identity": ("device_view", "queue"),
    "pending_records": ("device_view", "queue"),
    "pending_sources": ("device_view", "queue"),
    # Whether either scan is still running, which is the whole state behind
    # the spinner the refresh button shows. The window owns the widget and
    # both scans have to be able to say they have started or finished, so the
    # two flags are read where the chrome is and written where the work is.
    # The playlists page reads them too: copying a device playlist here is
    # worked out from both readings, so it waits for both to land.
    "_library_scan_running": ("library_view", "playlist_view", "window"),
    # What a device read found, which a running script invalidates.
    "_device_scan_active": ("commands", "device_view", "window"),
    "_device_scan_tracks": ("commands", "device_view"),
    "_device_snapshot_ready": ("commands", "device_view", "playlist_view"),
    "device_track_count": ("commands", "device_view"),
    "track_names": ("commands", "device_view"),
    # Generation counters, each bumped by the seam that supersedes the work.
    "probe_generation": ("commands", "device_view"),
    "source_generation": ("device_view", "queue"),
    "tag_generation": ("commands", "device_view"),
    # Indexes the library scan builds and the queue reads.
    "_library_by_path": ("library_view", "queue"),
    "_library_scan_tracks": ("library_view", "playback_view"),
    "_pending_track_index": ("library_view", "queue"),
    # Playlists: the device knows them, the playlist view draws them.
    "current_playlist": ("device_view", "library_view", "playlist_view"),
    "playlists": ("device_view", "playlist_view"),
    "spoken": ("device_view", "playlist_view"),
    # Controls one module builds and another decides the state of.
    "library_controls": ("library_view", "window"),
    "new_playlist_button": ("device_view", "playlist_view"),
    "search_add_buttons": ("device_view", "search_view"),
}

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


# The mixins are whatever IpodWindow is actually built from, read off the class
# rather than listed again here: a mixin added to the package and left out of
# the bases would otherwise be checked and never used.
bases = [
    base.__module__.rsplit(".", 1)[1]
    for base in ipod_gui.window.IpodWindow.__mro__[1:]
    if base.__module__.startswith("ipod_gui.")
]
modules = [*bases, "window"]

# Every *_view module in the package, plus the two that are not views. A mixin
# added to the package and never mixed in would pass every other check here
# while doing nothing.
declared = {
    path.stem for path in (REPO / "ipod_gui").glob("*_view.py")
} | {"queue", "commands"}
check(
    declared == set(bases),
    f"the mixin modules and IpodWindow's bases disagree: "
    f"{sorted(declared ^ set(bases))} is in one but not the other",
)

# Where each method is written, and what each module touches on self.
home = {}
reads = {}
writes = set()
touched = {}
for name in modules:
    tree = ast.parse((REPO / "ipod_gui" / f"{name}.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            check(
                method.name not in home,
                f"{method.name} is defined in both {home.get(method.name)} and "
                f"{name}; one silently overrides the other through the MRO",
            )
            home[method.name] = name
            for sub in ast.walk(method):
                if not (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"
                ):
                    continue
                if isinstance(sub.ctx, ast.Store):
                    writes.add(sub.attr)
                else:
                    reads.setdefault(sub.attr, set()).add(name)
                # __init__ creates every attribute and collects widgets from
                # every view, so counting it as a toucher would make the window
                # share everything with everyone and hide the real coupling.
                if method.name != "__init__":
                    touched.setdefault(sub.attr, set()).add(name)

shared = {
    attr: holders
    for attr, holders in touched.items()
    if len(holders) > 1 and attr not in home
}
for attr in sorted(set(shared) | set(SHARED_STATE)):
    declared_holders = SHARED_STATE.get(attr)
    actual = shared.get(attr)
    if declared_holders is None:
        check(
            False,
            f"self.{attr} is now shared by {sorted(actual)} and is not in "
            f"SHARED_STATE. Either move the method that reached for it into "
            f"the module that owns it, or add the attribute here on purpose.",
        )
    elif actual is None:
        check(
            False,
            f"self.{attr} is listed in SHARED_STATE but only "
            f"{sorted(touched.get(attr, set())) or 'nothing'} touches it now; "
            f"drop the entry so the table still describes the split.",
        )
    else:
        check(
            set(declared_holders) == actual,
            f"self.{attr} is shared by {sorted(actual)}, but SHARED_STATE says "
            f"{sorted(declared_holders)}",
        )

# A name read but never assigned is a typo or a leftover from a move: the mixin
# would raise the first time that line ran, which for an error path can be long
# after the change that caused it.
inherited = set(dir(ipod_gui.window.IpodWindow)) - set(home)
for attr, readers in sorted(reads.items()):
    check(
        attr in writes or attr in home or attr in inherited,
        f"self.{attr} is read in {sorted(readers)} but never assigned, and "
        f"Adw.ApplicationWindow does not provide it",
    )

# The opposite: state written and never read is state the window is paying to
# keep and nothing consults. One attribute is held for its lifetime rather than
# its value, which is a reason - but it has to be given rather than assumed, or
# the check stops catching the dead state it exists for.
HELD_ALIVE = {
    # Dropping the last reference to a GVolumeMonitor collects it, and the
    # mount-added and mount-removed handlers stop firing with it. Nothing reads
    # it; the window just has to keep it.
    "monitor",
}
for attr in sorted(writes):
    check(
        attr in reads or attr in inherited or attr in HELD_ALIVE,
        f"self.{attr} is assigned but never read; nothing consults it",
    )

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)

state = {attr for attr in touched if attr not in home}
print(
    f"{len(home)} methods across {len(modules)} modules; "
    f"{len(SHARED_STATE)} of {len(state)} attributes shared"
)
