#!/usr/bin/env python3
"""One tree, one sync, and what the window's sync bar had to show for it.

Run by driver-regression.sh once per tree: the commit this branch started
from, with ipod-sync.sh as it shipped and then with one line of it reworded,
and the branch itself with the same reword. Each run is its own process
because the two trees are two different `ipod_gui` packages.

Before the change the bar was driven by a regex over the copy lines in the
script's terminal output, so this is that regex, fed the real output of a real
run. After it, the bar is driven by the JSON the script writes on a descriptor
the window opened, so this is the window's own `_run`, reading it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

TREE, VARIANT = sys.argv[1], sys.argv[2]
SCRATCH = Path(os.environ["SCRATCH"])
REPO = SCRATCH / TREE
SCRIPT = REPO / ("ipod-sync.shipped.sh" if VARIANT == "shipped" else "ipod-sync.sh")

sys.path.insert(0, str(REPO / "tests"))
os.environ["IPOD_DB_TOOL"] = str(REPO / "tests" / "fake-db-builder.py")
os.environ["IPOD_VENV_PYTHON"] = "/usr/bin/python3"
os.environ["HOME"] = tempfile.mkdtemp(prefix="regression-home-")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from harness import gui  # noqa: E402

Gtk.init_check()

root = Path(tempfile.mkdtemp(prefix="regression-"))
album = root / "Odd Album"
ipod = root / "iPod"
for directory in (
    ipod / "iPod_Control" / "iTunes",
    ipod / "iPod_Control" / "Music",
    ipod / "iPod_Control" / "Speakable",
    album,
):
    directory.mkdir(parents=True)
(album / "01 - Plain.mp3").write_bytes(b"first")
(album / '02 - Say "hi".mp3').write_bytes(b"second")
(album / "03 - Line\nbreak.mp3").write_bytes(b"third")


class FakeWidget:
    """Stands in for the labels and the bar, which hold what they are told."""

    def __init__(self):
        self.text = ""
        self.fraction = 0.0

    def set_text(self, value):
        self.text = value

    def get_text(self):
        return self.text

    def set_fraction(self, value):
        self.fraction = value


def report(title, command, code, output, count, current, rows):
    print()
    print(f"=== {title} " + "=" * max(0, 62 - len(title)))
    print()
    print(f"$ {command}")
    for line in output:
        print(f"    {gui.strip_ansi(line).rstrip()}")
    print(f"[exit {code}]")
    print()
    print("the sync bar showed:")
    print(f"    count:   {count!r}")
    print(f"    current: {current!r}")
    print(f"    rows:    {rows}")


COMMAND = f"ipod-sync.sh --ipod ./iPod --yes -- './Odd Album'   # {TREE}, {VARIANT}"

if TREE == "before":
    # The bar as it was: every line of the script's terminal output went
    # through _log, which matched it against COPIED_LINE and counted the ones
    # that matched. The window predicted the total itself, from its own index.
    class Bar:
        _children = staticmethod(gui.IpodWindow._children)

        def __init__(self):
            self.progress = FakeWidget()
            self.sync_count = FakeWidget()
            self.sync_current = FakeWidget()
            self.sync_file_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.sync_total = 3

    bar = Bar()
    proc = subprocess.Popen(
        [str(SCRIPT), "--ipod", str(ipod), "--yes", "--", str(album)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = []
    for line in proc.stdout:
        lines.append(line)
        gui.IpodWindow._note_progress(bar, gui.strip_ansi(line))
    code = proc.wait()
    rows = len(list(bar._children(bar.sync_file_list)))
    report(
        f"{TREE} the change, ipod-sync.sh {VARIANT}",
        COMMAND,
        code,
        lines,
        bar.sync_count.text,
        bar.sync_current.text,
        f"{rows} appended",
    )
else:
    # The bar as it is: the window opens a descriptor, hands the script its
    # number and reads the JSON it writes there, while the terminal output
    # goes to the log view as before.
    class StreamWindow:
        _run = gui.IpodWindow._run
        _note_progress = gui.IpodWindow._note_progress
        _show_progress_counts = gui.IpodWindow._show_progress_counts
        _read_progress = gui.IpodWindow._read_progress
        _device_command_is_current = gui.IpodWindow._device_command_is_current

        def __init__(self):
            self.mount_point = str(ipod)
            self.device_identity = "uuid:synthetic"
            self.progress = FakeWidget()
            self.sync_count = FakeWidget()
            self.sync_current = FakeWidget()
            self.rows = []
            self.lines = []
            self.code = None
            self.finished = threading.Event()

        def _log_progress_row(self, name, status):
            self.rows.append((name, status))

        def _clear_log(self):
            pass

        def _set_busy(self, busy, message=""):
            pass

        def _toast(self, message):
            pass

        def _log(self, text):
            self.lines.append(text)
            return False

        def _finish(self, code, *_args, **_kwargs):
            self.code = code
            self.finished.set()
            return False

    gui.resolve_device = lambda mount, identity, require_block=False: (
        gui.DeviceHandle(mount, identity, "/dev/sdz")
    )
    window = StreamWindow()
    window._run(
        [str(SCRIPT), "--ipod", str(ipod), "--yes", "--", str(album)],
        "Copying to iPod",
        "Sync complete",
    )
    loop = GLib.MainLoop()
    GLib.timeout_add(50, lambda: loop.quit() if window.finished.is_set() else True)
    GLib.timeout_add_seconds(60, lambda: loop.quit())
    loop.run()
    report(
        f"{TREE} the change, ipod-sync.sh {VARIANT}",
        COMMAND,
        window.code,
        window.lines,
        window.sync_count.text,
        window.sync_current.text,
        window.rows,
    )

shutil.rmtree(root, ignore_errors=True)
