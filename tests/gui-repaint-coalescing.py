#!/usr/bin/env python3
"""Holds the scan to one repaint per interval, and none over an open menu.

A scan publishes a batch every 25 tracks, and a device read publishes one per
read it finishes. Each of those used to rebuild every card in the grid and
every row in the table, several times a second.

That was not only wasted work. Rebuilding destroys the widget the focus is on,
and GTK moves the focus out of a popup when that happens; a Wayland compositor
reads a popup that has lost the focus as dismissed and destroys it. So a
repaint landing while a menu was open closed that menu about ten milliseconds
after it appeared, and the grouping drop-down could not be used at all until
the first scan finished. The protocol trace that showed this is in the goal
`stop-startup-repaints-from-dismissing`.

Needs no display: the coalescing is a main-loop concern, so this drives a
GLib loop against a stand-in and counts repaints.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import gui  # noqa: E402

from gi.repository import GLib  # noqa: E402

failures = []


class Window:
    """Enough of the window for the repaint queue to run against."""

    def __init__(self):
        self.repaints = []
        self._refresh_timer = None
        self.popover_open = False

    def _refresh_current_view(self, scan_complete=True):
        self.repaints.append(scan_complete)

    def _popover_is_open(self):
        return self.popover_open

    _request_refresh = gui.IpodWindow._request_refresh
    _flush_refresh = gui.IpodWindow._flush_refresh
    _cancel_refresh = gui.IpodWindow._cancel_refresh


def check(name, condition, detail):
    if not condition:
        failures.append(f"{name}: {detail}")


def run(steps):
    """Drive a GLib loop through timed steps and stop."""
    loop = GLib.MainLoop()
    queue = list(steps)

    def step():
        if not queue:
            loop.quit()
            return False
        delay, action = queue.pop(0)
        action()
        GLib.timeout_add(delay, step)
        return False

    GLib.timeout_add(1, step)
    loop.run()


# A burst of batches, the way a scan delivers them, costs one rebuild.
storm = Window()
run(
    [
        (0, lambda: [storm._request_refresh() for _ in range(20)]),
        (gui.REFRESH_COALESCE_MS * 3, lambda: None),
    ]
)
check(
    "a burst of batches",
    len(storm.repaints) == 1,
    f"20 batches produced {len(storm.repaints)} repaints, expected 1",
)
check(
    "a coalesced repaint",
    storm.repaints == [False],
    f"repainted as {storm.repaints}, expected one mid-scan repaint",
)

# Nothing repaints underneath an open menu, and the repaint is not dropped
# either: it lands once the menu closes.
held = Window()
held.popover_open = True
run(
    [
        (0, held._request_refresh),
        (gui.REFRESH_COALESCE_MS * 3, lambda: None),
    ]
)
check(
    "a menu held open",
    not held.repaints,
    f"repainted {len(held.repaints)} times while a popover had the focus",
)

released = held
run(
    [
        (0, lambda: setattr(released, "popover_open", False)),
        (gui.REFRESH_COALESCE_MS * 3, lambda: None),
    ]
)
check(
    "a menu closed again",
    len(released.repaints) == 1,
    f"after the popover closed the pending repaint ran {len(released.repaints)} "
    "times, expected once",
)

# The last word on what the library holds is never coalesced away.
finished = Window()
run(
    [
        (0, finished._request_refresh),
        (0, lambda: finished._request_refresh(scan_complete=True)),
        (gui.REFRESH_COALESCE_MS * 3, lambda: None),
    ]
)
check(
    "the scan finishing",
    finished.repaints == [True],
    f"repainted as {finished.repaints}, expected a single completed-scan repaint",
)

# A window on its way out leaves no repaint queued behind it.
closing = Window()
closing._request_refresh()
closing._cancel_refresh()
check(
    "a cancelled queue",
    closing._refresh_timer is None,
    "a repaint was still queued after being cancelled",
)
run([(gui.REFRESH_COALESCE_MS * 3, lambda: None)])
check(
    "a cancelled repaint",
    not closing.repaints,
    f"a cancelled repaint still ran {len(closing.repaints)} times",
)

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("repaints coalesce, wait for open menus, and stop when cancelled")
