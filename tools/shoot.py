#!/usr/bin/env python3
"""Render a deterministic Shuffle page from a demo-library fixture."""

import argparse
import os
import sys
import time
from pathlib import Path

# The window's own screenshot height, and the minimum width it advertises.
HEIGHT = 760
MINIMUM_WIDTH = 660

# The budget the other GUI checks give the same work: a tag read over every
# track in the fixture, on another thread, and a device probe that shells out.
SETTLE_SECONDS = 30


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--page", choices=("library", "playlists", "settings"), required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--scale", type=int, choices=(1, 2), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


args = arguments()
if args.width < MINIMUM_WIDTH:
    sys.exit(f"--width must be at least the window minimum, {MINIMUM_WIDTH}")
root = args.fixture.resolve()
home = root / "home"
host_home = Path.home()
if not (root / ".demo-library").is_file():
    sys.exit(f"not a tools/demo-library.py fixture: {root}")

os.environ.update(
    HOME=str(home),
    XDG_CONFIG_HOME=str(home / ".config"),
    XDG_CACHE_HOME=str(home / ".cache"),
    FAKE_IPOD_MOUNT=str(root / "MAX SHUFFLE"),
    GDK_SCALE=str(args.scale),
)
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
venv_python = host_home / "ipod-tools/venv/bin/python"
if venv_python.is_file():
    os.environ.setdefault("IPOD_VENV_PYTHON", str(venv_python))
os.environ["PATH"] = f"{repo / 'tests/bin'}:{os.environ['PATH']}"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

from ipod_gui import app as app_module  # noqa: E402


def pump(rounds=3):
    context = app_module.GLib.MainContext.default()
    for _ in range(rounds):
        while context.pending():
            context.iteration(False)


def settle(done, step=lambda: None):
    """Run `step` and the main loop until `done` answers, or run out of clock.

    Driven off the clock rather than a count of pumps, so a loaded machine
    gets the whole budget instead of a shorter one as each pump grows slower.
    The sleep is not padding: some of what is waited on here only advances on
    a frame clock tick, which never arrives while this thread never yields.
    """
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        step()
        pump()
        if done():
            return True
        time.sleep(0.01)
    return False


application = app_module.IpodApp()
if not application.register(None):
    sys.exit("could not register the application")
# Registering says the bus name was resolved, not that this process won it.
# The app is single-instance, so against a running Shuffle the activation
# below is forwarded to that one and this process never builds a window.
if application.get_is_remote():
    sys.exit(
        "a Shuffle is already running and owns this application's name, so "
        "this process never gets a window of its own: close the running "
        "window, or run this on a session bus of its own"
    )
application.activate()
pump(20)
window = application.props.active_window
if window is None:
    sys.exit("activating the application built no window")
window.set_default_size(args.width, HEIGHT)
window.allocate(args.width, HEIGHT, -1, None)
application.activate_action("navigate", app_module.GLib.Variant("s", args.page))

if not settle(
    lambda: not window._library_scan_running and window._device_snapshot_ready
):
    sys.exit(f"the demo library and device did not settle in {SETTLE_SECONDS}s")

content = window.toasts
# Allocated until the layout has caught up rather than once, because a width
# that crosses one of the window's breakpoints is not laid out in a single
# pass: the breakpoint bin applies the new setters, unmaps its child and asks
# for another allocation on the next frame. Snapshotting after the one pass
# captures nothing at all - which is every width from the sidebar's collapse
# threshold down, including the 760px shot this tool is asked for.
#
# Waited on by whether the content came back mapped, and nothing else: what
# the window passes down to it is the window's business, and on a composited
# desktop the shadow around the frame makes it not the window's own width.
if not settle(
    lambda: content.get_mapped(),
    step=lambda: window.allocate(args.width, HEIGHT, -1, None),
):
    sys.exit(
        f"the window's content never came up mapped at {args.width}x{HEIGHT}: "
        f"it needs at least "
        f"{content.measure(Gtk.Orientation.HORIZONTAL, -1)[0]}px of width"
    )

# What the window says it is showing, read back before anything is written: a
# shot of the wrong page or of a library that scanned to nothing is the one
# failure a PNG on disk cannot be told apart from a good one afterwards.
state = window.dump_state()
if state["page"] != args.page:
    sys.exit(
        f"asked for the {args.page} page, but the window is showing "
        f"{state['page']}"
    )
if not sum(state["visibleCounts"].values()):
    sys.exit(
        f"the library under {root} scanned to no tracks, so this would be a "
        "shot of an empty window"
    )

# The size the shot is of, forced rather than read back, which is the whole
# point of allocating instead of asking a window manager for a window: the
# layout above settles the widget tree, and this pins the box it is measured
# and painted in to the one that was asked for.
content.allocate(args.width, HEIGHT, -1, None)

snapshot = Gtk.Snapshot.new()
# The scale is a transform on the render node, not the surface: nothing here
# goes through a compositor, so GDK_SCALE alone only picks the 2x icon assets
# and would leave the raster at its logical size.
snapshot.scale(args.scale, args.scale)
content.do_snapshot(content, snapshot)
node = snapshot.to_node()
if node is None:
    sys.exit("the window rendered no content")
# What the tree actually paints into, which is where a dropped scale shows up:
# the viewport below fixes the PNG's size from the same two arguments, so it
# cannot tell 2x apart from 1x painted into a corner, and this can. The width
# is the allocation exactly; the height is only bounded by it, because the
# content stops a few pixels short of the box it is given.
painted = node.get_bounds().size
if round(painted.width) != args.width * args.scale:
    sys.exit(
        f"the snapshot paints {round(painted.width)}px across, not the "
        f"{args.width * args.scale}px {args.width} at {args.scale}x asks for"
    )
if round(painted.height) > HEIGHT * args.scale:
    sys.exit(
        f"the snapshot paints {round(painted.height)}px down, past the "
        f"{HEIGHT * args.scale}px it would be cropped to"
    )
renderer = Gsk.CairoRenderer.new()
renderer.realize(None)
# An explicit viewport rather than the node's own bounds, which are the union
# of whatever the children happened to paint and so would let the output size
# drift with the content.
viewport = Graphene.Rect().init(0, 0, args.width * args.scale, HEIGHT * args.scale)
texture = renderer.render_texture(node, viewport)
args.output.parent.mkdir(parents=True, exist_ok=True)
if not texture.save_to_png(str(args.output)):
    sys.exit(f"could not write {args.output}")
renderer.unrealize()
window.close()
application.quit()
print(args.output)
