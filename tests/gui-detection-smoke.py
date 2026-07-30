#!/usr/bin/env python3
"""Checks that the GUI refuses to guess between several connected iPods.

Add Music and Wipe both act destructively on whichever device is selected, so
picking the first of several silently is a data-loss path. The command line
already refuses to guess; this asserts the GUI holds the same invariant.

The window is never instantiated, because that would need a display. Its
methods are called unbound against a stand-in recording what the real widgets
would have been told, which is the same approach gui-state-smoke.py uses.
"""

import importlib.util
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ipod_gui", repo / "ipod-gui.py")
ipod_gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipod_gui)


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
        self.empty_page = Recorder()
        self.mount_button = Recorder()
        self.stack = Recorder()


def refresh_with(mounts):
    """Run the detection path with find_ipods returning the given mounts."""
    original = ipod_gui.find_ipods
    ipod_gui.find_ipods = lambda: list(mounts)
    try:
        window = FakeWindow()
        ipod_gui.IpodWindow.refresh(window)
        return window
    finally:
        ipod_gui.find_ipods = original


# Two connected iPods must select neither, and must say why rather than
# silently showing the generic empty state.
two = refresh_with(
    ["/media/alex/Alex's iPod", "/media/alex/MAX_SHUFFLE"]
)
assert two.mount_point is None, two.mount_point
assert two.stack.child == "empty", two.stack.child
assert two.empty_page.title == "Multiple iPods Connected", two.empty_page.title
assert "Disconnect" in (two.empty_page.description or ""), two.empty_page.description

# Offering to mount would be meaningless here, and acting on it would have to
# pick one of the two, which is the behaviour being prevented.
assert two.mount_button.visible is False, two.mount_button.visible

# None connected stays distinguishable from the ambiguous case, so the user is
# not told to disconnect devices they do not have.
none = refresh_with([])
assert none.mount_point is None, none.mount_point
assert none.empty_page.title == "No iPod Connected", none.empty_page.title
assert none.mount_button.visible is True, none.mount_button.visible

print(
    json.dumps(
        {
            "two_connected": {
                "selected": two.mount_point,
                "title": two.empty_page.title,
                "mount_offered": two.mount_button.visible,
            },
            "none_connected": {
                "selected": none.mount_point,
                "title": none.empty_page.title,
                "mount_offered": none.mount_button.visible,
            },
        },
        indent=2,
    )
)
