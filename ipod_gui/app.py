"""The Adw.Application the launcher starts."""

import sys

from gi.repository import Adw, Gio

from .config import APP_ID
from .theme import load_css
from .window import IpodWindow


class IpodApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self._provider = None

    def do_activate(self):
        if self._provider is None:
            self._provider = load_css()
        window = self.props.active_window or IpodWindow(application=self)
        window.present()


def main(argv=None):
    """Run the application and return the status to exit with."""
    return IpodApp().run(sys.argv if argv is None else argv)
