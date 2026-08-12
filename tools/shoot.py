#!/usr/bin/env python3
"""Render a deterministic Shuffle page from a demo-library fixture."""

import argparse
import os
import sys
import time
from pathlib import Path

# The height every shot is taken at. The width is the caller's, floored by
# whatever the window itself asks for once it has been built.
HEIGHT = 760

# The budget the other GUI checks give the same work: a tag read over every
# track in the fixture, on another thread, and a device probe that shells out.
SETTLE_SECONDS = 30

# A resize is a round trip to the display server, so each ask is given long
# enough for one on a loaded machine. Two are enough for every display seen so
# far - the first lands whatever frame the desktop draws around the surface,
# the second corrects for it - and the rest are for a desktop that answers a
# resize with a size of its own that then has to be corrected in turn.
SIZE_ATTEMPTS = 6
SIZE_ATTEMPT_SECONDS = 2


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--page", choices=("library", "playlists", "settings"), required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--scale", type=int, choices=(1, 2), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


args = arguments()
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
    # Pinned at 1 rather than set from --scale, and pinned rather than left
    # to the developer's session, because it is the display server's number
    # and not the shot's. GDK_SCALE divides the logical space a window may
    # occupy, and the window's breakpoints are conditions on that logical
    # width: at 2 on an ordinary screen the requested width no longer fits,
    # GTK hands the window a narrower one, and the shot comes out in a layout
    # nobody at that width sees. --scale is applied to the render node
    # instead, where it is the raster density it is asked for and nothing
    # else, and where the same layout comes out of both scales.
    GDK_SCALE="1",
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


def settle(done, budget=SETTLE_SECONDS):
    """Run the main loop until `done` answers, or run out of clock.

    Driven off the clock rather than a count of pumps, so a loaded machine
    gets the whole budget instead of a shorter one as each pump grows slower.
    The sleep is not padding: some of what is waited on here only advances on
    a frame clock tick, which never arrives while this thread never yields.
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
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
# Registering is what runs the application's startup, so this is the first
# moment GTK has settings to pin. Both of these are clocks running under the
# window rather than state it is in, and a shot is one instant of whatever
# they happened to be showing: the caret in the search entry blinks, and a
# spinner painted while the frame is being taken freezes at whatever angle it
# had reached. Neither says anything about the window, and each of them alone
# is enough to make two runs of one command two different files.
settings = Gtk.Settings.get_default()
if settings is None:
    sys.exit("GTK came up without settings, so the shot cannot be pinned")
settings.props.gtk_enable_animations = False
settings.props.gtk_cursor_blink = False
application.activate()
pump(20)
window = application.props.active_window
if window is None:
    sys.exit("activating the application built no window")
# Asked of the window rather than kept as a number here, which would be the
# one copy left behind if the window ever advertises a different minimum.
minimum_width = window.get_size_request()[0]
if args.width < minimum_width:
    sys.exit(f"--width must be at least the window's own minimum, {minimum_width}")
window.set_default_size(args.width, HEIGHT)
application.activate_action("navigate", app_module.GLib.Variant("s", args.page))


def sized():
    return (window.get_width(), window.get_height()) == (args.width, HEIGHT)


# The window driven to the size that was asked for, rather than allocated over
# the top of whatever it came up as. The window's own width is what its Adw
# breakpoints are conditions on - the sidebar folds away under one of them -
# so the requested width has to reach the window itself or the shot is of a
# layout nobody at that width sees. Allocating the window by hand does not
# get there: it is one frame's answer, and the next turn of the main loop
# takes the window back to the size its surface has, which leaves the two
# fighting each other whenever they fall on opposite sides of a breakpoint.
#
# So the size is asked for and then read back, in the window's own terms. The
# gap between the two is the frame the display server draws around the
# surface, which is a different number on every desktop and none at all
# without a compositor, so it is measured here rather than assumed: whatever
# the window came up short or long is added to the next request until the
# window itself is the size the shot is of.
requested = [args.width, HEIGHT]
answered = None
for _ in range(SIZE_ATTEMPTS):
    if settle(sized, budget=SIZE_ATTEMPT_SECONDS):
        break
    given = (window.get_width(), window.get_height())
    if given == answered:
        # Asking for more room got the same window back, so this display has
        # no more to give and asking again would only spend the budget.
        break
    if not all(given):
        continue
    answered = given
    requested = [
        requested[0] + args.width - given[0],
        requested[1] + HEIGHT - given[1],
    ]
    window.set_default_size(*requested)
if not sized():
    sys.exit(
        f"the display never gave the window {args.width}x{HEIGHT}: the most "
        f"it would show was {window.get_width()}x{window.get_height()}, so "
        "this shot needs a display with more room on it"
    )

# Settled on what the window has finished doing, not only on what it has
# finished reading. The device walk sets `_device_snapshot_ready` and then
# asks for a repaint that is deliberately coalesced, so the two scan flags go
# quiet an interval before the grid they were read for is redrawn: a shot
# taken on those two alone is of the library from before the device was
# merged into it - "On iPod 0" over a device the same window already knows
# holds a track. The spinner is the other half of the same instant. It is
# held visible for a minimum so that an instant scan still looks like work,
# and that minimum outlasts the scan it is reporting.
if not settle(
    lambda: (
        not window._library_scan_running
        and window._device_snapshot_ready
        and window._refresh_timer is None
        and not window.refresh_spinner.get_spinning()
    )
):
    sys.exit(f"the demo library and device did not settle in {SETTLE_SECONDS}s")

content = window.toasts
# Waited on until the layout has caught up, because a width that crosses one
# of the window's breakpoints is not laid out in a single pass: the breakpoint
# bin applies the new setters, unmaps its child and asks for another
# allocation on the next frame. Snapshotting before that lands captures
# nothing at all - which is every width from the sidebar's collapse threshold
# down, including the 760px shot this tool is asked for.
if not settle(lambda: content.get_mapped()):
    sys.exit(
        f"the window's content never came up mapped at {args.width}x{HEIGHT}: "
        f"it needs at least "
        f"{content.measure(Gtk.Orientation.HORIZONTAL, -1)[0]}px of width"
    )

# Read back at the last moment as well as waited for above: the layout that
# settled in between is free to have asked the window for more room, and a
# window that grew out from under the size it was driven to is showing a
# different set of breakpoints than the shot is labelled with.
if not sized():
    sys.exit(
        f"the window ended up {window.get_width()}x{window.get_height()} "
        f"rather than the {args.width}x{HEIGHT} it was given"
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

# The size the shot is of, allocated rather than inherited: the window above
# is the right size and the content fills it, but what is rendered below is
# this widget and not the window, so the box it is measured and painted in is
# pinned here rather than taken on trust from its parent.
content.allocate(args.width, HEIGHT, -1, None)

snapshot = Gtk.Snapshot.new()
# The scale is a transform on the render node, and the only place it is
# applied: nothing here goes through a compositor, so the density is this
# tool's to choose rather than the display's, and choosing it here is what
# keeps it out of the layout the node was built from.
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
