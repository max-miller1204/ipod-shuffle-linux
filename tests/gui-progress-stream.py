#!/usr/bin/env python3
"""The window running a real script and reading its progress stream.

Every other check of the bar hands it events that were written by hand. This
one is the seam where the two halves actually meet: a descriptor opened here,
passed to a script that was told its number, read back on a thread while the
script's human output is read on another, and delivered to the bar through the
main loop. A wrong flag, a descriptor the child never inherited, or an end of
stream that never arrives are all invisible to a check that starts from the
JSON, and all of them would stop a sync from working at all.

Needs a main loop but no display, so nothing here builds a widget: the rows the
bar appends are Gtk boxes, and tests/gui-window-build.py is where those are
built for real, under a display server.
"""

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from gi.repository import GLib

from harness import REPO, gui


class FakeWidget:
    def __init__(self):
        self.text = None
        self.fraction = 0.0

    def set_text(self, value):
        self.text = value

    def set_fraction(self, value):
        self.fraction = value


class StreamWindow:
    """Enough of the window to run a script and watch it report."""

    _run = gui.IpodWindow._run
    _note_progress = gui.IpodWindow._note_progress
    _show_progress_counts = gui.IpodWindow._show_progress_counts
    _device_command_is_current = gui.IpodWindow._device_command_is_current

    def __init__(self, mount_point):
        self.mount_point = mount_point
        # Read from the volume rather than made up, as the real window reads
        # it: every device command carries the identity on to the script,
        # which asks the volume the same question itself and refuses a run
        # whose answer disagrees. A stand-in value would be a window that can
        # only ever drive a script that was never given the flag.
        self.device_identity = gui.volume_identity(mount_point)
        self.progress = FakeWidget()
        self.sync_count = FakeWidget()
        self.sync_current = FakeWidget()
        self.rows = []
        self.log = []
        self.code = None
        self.stream_ended = threading.Event()
        self.read_to_the_end = False
        self.finished = threading.Event()

    # The bar's own plumbing, which has checks of its own.
    def _log_progress_row(self, name, status):
        self.rows.append((name, status))

    def _clear_log(self):
        pass

    def _set_busy(self, busy, message=""):
        pass

    def _toast(self, message):
        pass

    def _log(self, text):
        self.log.append(text)
        return False

    def _read_progress(self, descriptor):
        """The real reader, with a note of when it reached the end of it."""
        gui.IpodWindow._read_progress(self, descriptor)
        self.stream_ended.set()

    def _finish(self, code, *_args, **_kwargs):
        # Stands in for the real one, which repaints half the window. What is
        # being read here is when it runs: the worker joins the reader before
        # calling it, so everything the script said has to have arrived by now.
        self.code = code
        self.read_to_the_end = self.stream_ended.is_set()
        self.finished.set()
        return False


failures = []
root = Path(tempfile.mkdtemp())
ipod = root / "iPod"
source = root / "Odd Album"
for directory in (
    ipod / "iPod_Control" / "iTunes",
    ipod / "iPod_Control" / "Music",
    ipod / "iPod_Control" / "Speakable",
    ipod / "iPod_Control" / "Device",
    source,
):
    directory.mkdir(parents=True)
# What a volume with no filesystem UUID calls itself instead, so this reads
# the same on a machine whose temporary directory has none.
(ipod / "iPod_Control" / "Device" / "SysInfo").write_bytes(b"device identity\n")
(source / "01 - Plain.mp3").write_bytes(b"first")
(source / '02 - Say "hi".mp3').write_bytes(b"second")
(source / "cover.flac").write_bytes(b"art")

os.environ["IPOD_DB_TOOL"] = str(REPO / "tests" / "fake-db-builder.py")
os.environ["IPOD_VENV_PYTHON"] = "/usr/bin/python3"
os.environ["FAKE_DB_RECORD"] = str(root / "database-invocations.jsonl")

window = StreamWindow(str(ipod))
gui.resolve_device = lambda mount, identity, require_block=False: gui.DeviceHandle(
    mount, identity, "/dev/sdz"
)

# A window opened from the app grid has no terminal behind it, which is what
# makes a destructive run below need the token the window fetches for it: the
# scripts refuse one there is nobody to ask about. Pointed at /dev/null rather
# than left as whatever ran this file, so the checks read the same from a
# terminal as they do in CI.
_devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(_devnull, 0)
os.close(_devnull)

# Every descriptor open now, so one the window opened for the stream and never
# closed shows up as a leak rather than as a run that happened to work.
descriptors_before = len(os.listdir("/proc/self/fd"))

started = window._run(
    [
        str(gui.SYNC_SCRIPT),
        "--ipod",
        str(ipod),
        "--yes",
        "--",
        str(source),
    ],
    "Copying to iPod",
    "Sync complete",
)
if not started:
    raise SystemExit("the window refused to run the script at all")

# The events reach the bar through GLib.idle_add, so the loop has to run for
# any of this to happen; it is stopped by the worker's own last callback.
loop = GLib.MainLoop()
GLib.timeout_add(50, lambda: loop.quit() if window.finished.is_set() else True)
GLib.timeout_add_seconds(60, lambda: failures.append("the script never finished")
                         or loop.quit())
loop.run()

# A run is not finished until the stream is. Both ways of getting this wrong
# leave the window calling a sync over while the script is still describing it:
# not waiting for the reader, and holding a copy of the writing end here so the
# reader waits for an end that only this process can give it.
if not window.read_to_the_end:
    failures.append("the run was called finished with the stream still open")
if window.code != 0:
    failures.append(f"the script exited {window.code}: {''.join(window.log)}")

# What the script actually did, which is what the bar has to have shown by the
# time the run is called finished.
if window.progress.fraction != 1.0:
    failures.append(f"the bar stopped at {window.progress.fraction}")
if window.sync_count.text != "2 of 2":
    failures.append(f"the bar counted {window.sync_count.text!r}")
if window.sync_current.text != "Rebuilding the database":
    failures.append(f"the bar ended saying {window.sync_current.text!r}")
copied = sorted(name for name, status in window.rows if status == "Copied")
if copied != ["01 - Plain.mp3", '02 - Say "hi".mp3']:
    failures.append(f"the details pane listed {copied}")
# The artwork is not playable, so it was never counted into the work; the
# human output is where it is reported, and that has to still arrive.
if any("cover.flac" in name for name, _status in window.rows):
    failures.append(f"unplayable files reached the bar: {window.rows}")
if not any("Skipped 1 unsupported file(s)" in line for line in window.log):
    failures.append("the script's own output never reached the log view")

# The other half of the same seam: a script that deletes something. With no
# terminal behind it the script refuses a removal that arrives without the
# token from its own plan, so the window has to ask for that plan and hand the
# token back - and it has to know a removal when it holds one. The track here
# is named exactly the flag that would have made this a listing rather than a
# deletion, because names come off the device and are passed after the `--`
# that says so; a window reading one as a flag of its own would send a removal
# it never authorized and leave the user unable to delete that track at all.
awkward_track = ipod / "iPod_Control" / "Music" / "--list"
awkward_track.write_bytes(b"third")

removal = StreamWindow(str(ipod))
started = removal._run(
    [
        str(gui.REMOVE_SCRIPT),
        "--ipod",
        str(ipod),
        "--yes",
        "--",
        "--list",
    ],
    "Removing track",
    "Track removed",
)
if not started:
    failures.append("the window refused to run the removal at all")
else:
    loop = GLib.MainLoop()
    GLib.timeout_add(50, lambda: loop.quit() if removal.finished.is_set() else True)
    GLib.timeout_add_seconds(
        60, lambda: failures.append("the removal never finished") or loop.quit()
    )
    loop.run()
    if removal.code != 0:
        failures.append(
            f"the removal exited {removal.code}: {''.join(removal.log)}"
        )
    if awkward_track.exists():
        failures.append("the track named like a flag is still on the device")
    if ("--list", "Removed") not in removal.rows:
        failures.append(f"the details pane listed {removal.rows}")


# A run that cannot even open its stream still has to report an outcome. The
# window has no way back from a worker that dies before its last callback: the
# bar keeps turning and every control it made insensitive stays that way until
# the app is restarted, with nothing said about why.
class NoDescriptors:
    """The os module on a process with no descriptors left to give."""

    def __getattr__(self, name):
        return getattr(os, name)

    def pipe(self):
        raise OSError(24, "Too many open files")


exhausted = StreamWindow(str(ipod))
gui.os = NoDescriptors()
try:
    if not exhausted._run(
        [str(gui.SYNC_SCRIPT), "--ipod", str(ipod), "--yes", "--", str(source)],
        "Copying to iPod",
        "Sync complete",
    ):
        failures.append("the window refused to run the script at all")
    loop = GLib.MainLoop()
    GLib.timeout_add(50, lambda: loop.quit() if exhausted.finished.is_set() else True)
    GLib.timeout_add_seconds(
        20,
        lambda: failures.append("a run that could not open its stream never finished")
        or loop.quit(),
    )
    loop.run()
finally:
    gui.os = os

if exhausted.finished.is_set():
    if exhausted.code == 0:
        failures.append("a run that never started was reported as a success")
    # Said rather than swallowed: the log view is the only place the window
    # has to tell anyone why the sync it was asked for did not happen.
    if not any("failed to run" in line for line in exhausted.log):
        failures.append(f"nothing said why the run failed: {exhausted.log}")

descriptors_after = len(os.listdir("/proc/self/fd"))
if descriptors_after > descriptors_before:
    failures.append(
        f"the run leaked {descriptors_after - descriptors_before} descriptor(s)"
    )

# And the sync really happened, rather than the bar having been driven by a
# script that copied nothing.
on_device = sorted(
    path.name
    for path in (ipod / "iPod_Control" / "Music").rglob("*")
    if path.is_file()
)
if on_device != ["01 - Plain.mp3", '02 - Say "hi".mp3']:
    failures.append(f"the device holds {on_device}")

shutil.rmtree(root, ignore_errors=True)

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("the window ran a real sync and read its progress stream")
