"""The Adw.Application the launcher starts, and the actions it exports.

The actions are how another program drives the window a person already has
open, on the bus name GTK publishes for this application anyway. Their shapes
and the document `dump-state` answers with are an interface rather than an
implementation detail, and docs/machine-interface.md is where that is written
down for the clients that read it.
"""

import json
import sys

from gi.repository import Adw, Gio, GLib

from .config import APP_ID
from .theme import load_css
from .window import IpodWindow


class IpodApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self._provider = None
        self._install_actions()

    def _install_actions(self):
        """Publish the window's machine-driving surface on org.gtk.Actions."""
        for name, parameter, callback in (
            ("navigate", "s", self._navigate),
            ("search", "s", self._search),
            ("queue", "s", self._queue),
            ("refresh", None, self._refresh),
        ):
            action = Gio.SimpleAction.new(
                name,
                None if parameter is None else GLib.VariantType.new(parameter),
            )
            action.connect("activate", callback)
            self.add_action(action)
        # Stateful, because a reply is what this one is for: activating it is
        # how a client asks, and the state it leaves behind is the answer. The
        # action itself arrives back as the callback's first argument, so it is
        # added here and not kept.
        state = Gio.SimpleAction.new_stateful(
            "dump-state", None, GLib.Variant("s", "{}")
        )
        state.connect("activate", self._dump_state)
        self.add_action(state)

    def _window(self):
        return self.props.active_window

    def _navigate(self, _action, parameter):
        window = self._window()
        if window is not None:
            window.navigate(parameter.get_string())

    def _search(self, _action, parameter):
        window = self._window()
        if window is not None:
            window.show_view("search")
            window.search_entry.set_text(parameter.get_string())
            window.focus_search()

    def _queue(self, _action, parameter):
        window = self._window()
        if window is not None:
            window.queue_paths([parameter.get_string()])

    def _refresh(self, _action, _parameter):
        window = self._window()
        if window is not None:
            window.on_refresh_clicked(None)

    def _dump_state(self, action, _parameter):
        window = self._window()
        document = {} if window is None else window.dump_state()
        action.set_state(GLib.Variant("s", json.dumps(document, ensure_ascii=True)))

    def do_activate(self):
        if self._provider is None:
            self._provider = load_css()
        window = self.props.active_window or IpodWindow(application=self)
        window.present()


def main(argv=None):
    """Run the application and return the status to exit with."""
    return IpodApp().run(sys.argv if argv is None else argv)
