#!/usr/bin/env python3
"""Checks that the GUI refuses to guess between several connected iPods.

Add Music and Wipe both act destructively on whichever device is selected, so
picking the first of several silently is a data-loss path. The command line
already refuses to guess; this asserts the GUI holds the same invariant.

Detection runs on a worker thread now, so the check drives the two halves as
the window does: probe_device off the main loop, then _apply_probe painting
what it brought back. Calling refresh() here instead would assert against a
window whose thread had not answered yet, and pass whatever the probe later
decided.

The window is never instantiated, because that would need a display. Its
methods are called unbound against a stand-in recording what the real widgets
would have been told, which is the same approach gui-state-smoke.py uses.
"""

import json
import tempfile
import threading
import time
from pathlib import Path

from harness import gui


class Recorder:
    """Stands in for a widget, remembering what it was asked to display."""

    def __init__(self):
        self.title = None
        self.description = None
        self.visible = None
        self.child = None

    def set_title(self, value):
        self.title = value

    def set_description(self, value):
        self.description = value

    def set_visible(self, value):
        self.visible = value

    def set_visible_child_name(self, value):
        self.child = value


class FakeWindow:
    def __init__(self):
        self.busy = False
        self.mount_point = "/media/alex/SHOULD BE CLEARED"
        self.probe_generation = 1
        self.probe_answered = False
        self.playlists = ["should be cleared"]
        self.spoken = {"should be cleared"}
        self.current_playlist = "should be cleared"
        self.local_playlists = []
        self.empty_page = Recorder()
        self.mount_button = Recorder()
        self.stack = Recorder()
        self.disconnected_message = None
        self.mount_offered = None
        self.searching = False

    def _select_mount(self, mount_point, identity):
        self.mount_point = mount_point
        self.device_identity = identity

    def _populate_disconnected_summary(self, message, offer_mount):
        self.disconnected_message = message
        self.mount_offered = offer_mount

    def _populate_searching_summary(self):
        self.searching = True

    # The real one, because refresh() posts it back through GLib and a
    # stand-in window that cannot answer that call makes its worker die on a
    # thread, where the failure is a traceback beside a passing check.
    _apply_probe = gui.IpodWindow._apply_probe
    _local_playlist = gui.IpodWindow._local_playlist

    def _populate_playlist_rail(self):
        pass


def probe_with(mounts):
    """Read a device with find_ipods returning the given mounts."""
    original = gui.find_ipods
    gui.find_ipods = lambda: list(mounts)
    try:
        return gui.probe_device()
    finally:
        gui.find_ipods = original


def refresh_with(mounts):
    """Run the detection path end to end, as refresh() drives it."""
    window = FakeWindow()
    gui.IpodWindow._apply_probe(window, window.probe_generation, probe_with(mounts))
    return window


# Two connected iPods must select neither, and must say why rather than
# silently showing the generic empty state.
two = refresh_with(
    ["/media/alex/Alex's iPod", "/media/alex/MAX_SHUFFLE"]
)
assert two.mount_point is None, two.mount_point
assert two.stack.child == "device", two.stack.child
assert "Disconnect" in (two.disconnected_message or ""), two.disconnected_message

# Offering to mount would be meaningless here, and acting on it would have to
# pick one of the two, which is the behaviour being prevented.
assert two.mount_offered is False, two.mount_offered

# None connected stays distinguishable from the ambiguous case, so the user is
# not told to disconnect devices they do not have.
none = refresh_with([])
assert none.mount_point is None, none.mount_point
assert none.stack.child == "device", none.stack.child
assert "No iPod" in (none.disconnected_message or ""), none.disconnected_message
assert none.mount_offered is True, none.mount_offered

# A single mount point that findmnt reported and that then cannot be read is
# what unplugging during the walk produces. It must not arrive as a connected
# iPod holding nothing, which would show an empty library for a device that is
# actually full, and offer Wipe against it.
vanished = probe_with(["/media/alex/GONE BEFORE IT WAS READ"])
assert vanished.mount_point is None, vanished.mount_point
assert vanished.readable is False, vanished.readable
assert vanished.track_count == 0, vanished.track_count

with tempfile.TemporaryDirectory() as temporary:
    mount_point = Path(temporary)
    control = mount_point / "iPod_Control"
    control.mkdir()
    original_count_tracks = gui.count_tracks

    def remove_control(_mount_point, cancelled=None):
        control.rmdir()
        return 0

    gui.count_tracks = remove_control
    try:
        vanished_after_defaults = probe_with([str(mount_point)])
    finally:
        gui.count_tracks = original_count_tracks

assert vanished_after_defaults.mount_point is None, vanished_after_defaults.mount_point
assert vanished_after_defaults.readable is False, vanished_after_defaults.readable

with tempfile.TemporaryDirectory() as temporary:
    mount_point = Path(temporary)
    (mount_point / "iPod_Control").mkdir()
    identities = iter(("uuid:first", "uuid:replacement"))
    original_volume_identity = gui.volume_identity
    gui.volume_identity = lambda _mount_point: next(identities)
    try:
        replaced = probe_with([str(mount_point)])
    finally:
        gui.volume_identity = original_volume_identity

assert replaced.mount_point is None, replaced.mount_point
assert replaced.readable is False, replaced.readable

# A playlist made here is a file of your own, so it outlives the iPod being
# unplugged. A probe that finds no device must leave it selected: a refresh
# runs on every plug, unplug and finished command, and clearing the selection
# hands the Playlists view to whichever list happens to sort first.
kept = FakeWindow()
kept.local_playlists = [gui.Playlist("Zebra", "/music/Playlists/Zebra.m3u", [])]
kept.current_playlist = "Zebra"
gui.IpodWindow._apply_probe(kept, kept.probe_generation, probe_with([]))
assert kept.mount_point is None, kept.mount_point
assert kept.current_playlist == "Zebra", kept.current_playlist

# One that only ever existed on the device goes with it: its entries name
# scrambled files on an iPod that is no longer there.
device_only = FakeWindow()
device_only.local_playlists = [gui.Playlist("Made Here", "/music/Made Here.m3u", [])]
device_only.current_playlist = "Genres"
gui.IpodWindow._apply_probe(
    device_only, device_only.probe_generation, probe_with([])
)
assert device_only.current_playlist is None, device_only.current_playlist

unplugged = FakeWindow()
gui.IpodWindow._apply_probe(unplugged, unplugged.probe_generation, vanished)
assert unplugged.mount_point is None, unplugged.mount_point
assert "stopped responding" in (unplugged.disconnected_message or ""), (
    unplugged.disconnected_message
)
assert unplugged.mount_offered is True, unplugged.mount_offered

# A probe that a newer one has already superseded must paint nothing at all:
# unplugging one iPod and plugging in another is exactly the sequence where
# the slower answer arrives last and would otherwise win.
stale = FakeWindow()
stale.probe_generation = 7
gui.IpodWindow._apply_probe(stale, 6, probe_with([]))
assert stale.mount_point == "/media/alex/SHOULD BE CLEARED", stale.mount_point
assert stale.disconnected_message is None, stale.disconnected_message
assert stale.probe_answered is False, stale.probe_answered

# So must one that finished while a script was changing the device: that
# script ends with a refresh of its own, and repainting under it would show
# the device as it was before the command ran.
during_command = FakeWindow()
during_command.busy = True
gui.IpodWindow._apply_probe(
    during_command, during_command.probe_generation, probe_with([])
)
assert during_command.disconnected_message is None, during_command.disconnected_message

first_cancel_check = threading.Event()
cancel_queued_probe = threading.Event()
detection_calls = []
queued_probe_result = []
original_find_ipods = gui.find_ipods


def queued_probe_cancelled():
    first_cancel_check.set()
    return cancel_queued_probe.is_set()


gui.find_ipods = lambda: detection_calls.append(True) or []
with gui.DEVICE_IO_LOCK.write():
    queued_probe = threading.Thread(
        target=lambda: queued_probe_result.append(
            gui.probe_device(cancelled=queued_probe_cancelled)
        )
    )
    queued_probe.start()
    assert first_cancel_check.wait(5), "queued probe did not check cancellation"
    cancel_queued_probe.set()
queued_probe.join(5)
gui.find_ipods = original_find_ipods
assert not queued_probe.is_alive(), "cancelled probe remained queued"
assert detection_calls == [], detection_calls
assert queued_probe_result and queued_probe_result[0].candidates == []

# refresh() must hand the device off to a thread and return. Reading a device
# means a findmnt subprocess and a walk of every file on it over USB, and a
# refresh fires on every plug, unplug and finished command, so doing any of it
# here freezes the window for as long as the device takes to answer.
probe_started = threading.Event()
release_probe = threading.Event()


def blocking_probe(cancelled=None):
    probe_started.set()
    release_probe.wait(30)
    return gui.DeviceProbe([])


original_probe_device = gui.probe_device
gui.probe_device = blocking_probe
try:
    threaded = FakeWindow()
    began = time.monotonic()
    gui.IpodWindow.refresh(threaded)
    returned_after = time.monotonic() - began
    assert probe_started.wait(30), "refresh never reached the probe"
    assert returned_after < 1, returned_after
    # Nothing but the searching state is on screen: the probe is still running,
    # so any answer painted here would be one the window invented. "No iPod" is
    # such an answer, which is why it has a state of its own.
    assert threaded.searching is True, threaded.searching
    assert threaded.disconnected_message is None, threaded.disconnected_message
    assert threaded.stack.child == "device", threaded.stack.child
    assert threaded.probe_answered is False, threaded.probe_answered
finally:
    release_probe.set()
    # Joined rather than left to the daemon flag: the worker posts its result
    # back through GLib on the way out, and racing that against interpreter
    # shutdown aborts the process after every assertion has already passed.
    for worker in threading.enumerate():
        if worker is not threading.current_thread():
            worker.join(30)
    gui.probe_device = original_probe_device

print(
    json.dumps(
        {
            "two_connected": {
                "selected": two.mount_point,
                "message": two.disconnected_message,
                "mount_offered": two.mount_offered,
            },
            "none_connected": {
                "selected": none.mount_point,
                "message": none.disconnected_message,
                "mount_offered": none.mount_offered,
            },
            "vanished_mid_read": {
                "selected": unplugged.mount_point,
                "readable": vanished.readable,
                "message": unplugged.disconnected_message,
                "mount_offered": unplugged.mount_offered,
            },
            "superseded_probe_painted": stale.disconnected_message is not None,
            "probe_during_command_painted": (
                during_command.disconnected_message is not None
            ),
            "refresh_while_device_unread": {
                "returned_after_ms": round(returned_after * 1000, 1),
                "showed_searching": threaded.searching,
                "claimed_no_ipod": threaded.disconnected_message is not None,
            },
        },
        indent=2,
    )
)
