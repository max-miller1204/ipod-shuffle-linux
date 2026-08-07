#!/usr/bin/env python3
"""Holds the refresh button's spinner on screen long enough to be seen.

Pressing refresh over a small library finishes the scan in well under a tenth
of a second. A spinner that appears and vanishes inside that reads as a button
that did nothing - which is the complaint the animation exists to answer - so
it is held to a minimum before it is allowed to stop.

The minimum runs from the most recent press, not from the first one. A user
who pressed once and saw nothing presses again, and that second press lands
while the first spin is still finishing: measured from the first start, the
spinner would go out a few milliseconds after the second click and read worse
than no animation at all.

Whether it is spinning is derived from the two scan flags rather than counted,
because a scan superseded by a newer one returns without ever finishing and
would strand a counter. So both flags are driven here, together and apart.

Needs no display: the timing is a main-loop concern, so this drives a GLib
loop against a stand-in and watches the button's child.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import gui  # noqa: E402

from gi.repository import GLib  # noqa: E402

failures = []

MIN_MS = gui.REFRESH_SPINNER_MIN_MS


class Spinner:
    def __init__(self):
        self.spinning = False

    def get_spinning(self):
        return self.spinning

    def start(self):
        self.spinning = True

    def stop(self):
        self.spinning = False


class Button:
    def __init__(self):
        self.child = None

    def set_child(self, child):
        self.child = child


class Window:
    """Enough of the window for the spinner's own timing to run against."""

    _update_refresh_spinner = gui.IpodWindow._update_refresh_spinner
    _stop_refresh_spinner = gui.IpodWindow._stop_refresh_spinner

    def __init__(self):
        self.refresh_spinner = Spinner()
        self.refresh_icon = "icon"
        self.refresh_button = Button()
        self._refresh_stop_timer = None
        self._refresh_spinner_since = 0
        self._library_scan_running = False
        self._device_scan_active = False

    def scan(self, running=True, device=False):
        """What a scan starting or ending does, as the window does it."""
        if device:
            self._device_scan_active = running
        else:
            self._library_scan_running = running
        self._update_refresh_spinner()

    def showing_spinner(self):
        """What the button has on it, which is what the user actually sees."""
        return (
            self.refresh_button.child is self.refresh_spinner
            and self.refresh_spinner.get_spinning()
        )


def check(name, condition, detail):
    if not condition:
        failures.append(f"{name}: {detail}")


def run(steps):
    """Drive a GLib loop through timed steps and stop.

    Each step runs its action and then waits its delay, so a delay is the gap
    before the step below it rather than a time to run at.
    """
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


# A scan that is over before the eye can follow it still shows a spinner.
brief = Window()
seen = []
run(
    [
        (20, lambda: brief.scan(True)),
        (MIN_MS // 2, lambda: brief.scan(False)),
        (MIN_MS, lambda: seen.append(brief.showing_spinner())),
        (0, lambda: seen.append(brief.showing_spinner())),
    ]
)
check(
    "a scan that finishes at once",
    seen[0],
    f"the spinner was gone {MIN_MS // 2}ms after a press, which is the button "
    "looking like it did nothing",
)
check(
    "the minimum running out",
    not seen[1],
    "the spinner was still going long after the scan behind it had finished",
)
check(
    "the icon coming back",
    brief.refresh_button.child is brief.refresh_icon,
    f"the button was left showing {brief.refresh_button.child!r}",
)

# Pressing again while the last spin is finishing restarts the minimum, rather
# than putting the spinner out a few milliseconds after the click.
again = Window()
late = []
run(
    [
        (20, lambda: again.scan(True)),
        (MIN_MS - 100, lambda: again.scan(False)),
        # The second press, with the stop timer already armed and about to run.
        (20, lambda: again.scan(True)),
        (300, lambda: again.scan(False)),
        (500, lambda: late.append(again.showing_spinner())),
        (0, lambda: late.append(again.showing_spinner())),
    ]
)
check(
    "a second press in the tail of the first",
    late[0],
    "the spinner went out barely after the second press, because the minimum "
    "was still being measured from the first one",
)
check(
    "the restarted minimum running out too",
    not late[1],
    "the spinner never stopped after the second scan finished",
)

# The two scans are independent, and the spinner belongs to both: the device
# walk outliving the library scan has to keep it going.
both = Window()
overlap = []
run(
    [
        (20, lambda: both.scan(True)),
        (20, lambda: both.scan(True, device=True)),
        (MIN_MS, lambda: both.scan(False)),
        (0, lambda: overlap.append(both.showing_spinner())),
        (0, lambda: both.scan(False, device=True)),
        (0, lambda: overlap.append(both.showing_spinner())),
    ]
)
check(
    "one scan finishing under another",
    overlap[0],
    "the spinner stopped while the device was still being read",
)
check(
    "both scans finishing",
    not overlap[1],
    "the spinner kept going after both scans had finished",
)

# A scan superseded by a newer one returns without finishing, so the flag is
# set rather than counted down - the state the spinner is derived from cannot
# be left believing in work that is not there.
superseded = Window()
stranded = []
run(
    [
        (20, lambda: superseded.scan(True)),
        # A second scan starts while the first is still in flight; the first
        # never reports back, and the second is the one that ends.
        (20, lambda: superseded.scan(True)),
        (MIN_MS + 200, lambda: superseded.scan(False)),
        (0, lambda: stranded.append(superseded.showing_spinner())),
    ]
)
check(
    "a superseded scan",
    not stranded[0],
    "the spinner was left running by a scan that never reported finishing",
)

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)

print("the refresh spinner is held on screen, and restarts on a second press")
