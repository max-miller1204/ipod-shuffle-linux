"""The application window: its chrome, its state, and the mixins it is made of.

IpodWindow has to stay one Adw.ApplicationWindow - the views share a stack, a
toast overlay and a set of bottom bars, and GTK gives a widget one parent - so
the split is by mixin rather than by object. What each mixin owns and what it
borrows is stated in its own docstring; tools/mixin-contract.py is architecture
tooling that holds them to it, so a method that drifts into another's state
fails rather than quietly turning the package back into one namespace with
seven filenames.

What is left here is the part no mixin can own: the widgets every view sits
inside, the state they all read, and the assembly order between them.
"""

import threading

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .shell import (
    has_speech_engine,
    youtube_search_unavailable_reason,
    youtube_unavailable_reason,
)
from .model import LibraryIndex
from .widgets import label
from .player import PreviewPlayer, UNPAINTED, preview_unavailable_reason
from .library_view import LibraryViewMixin
from .search_view import SearchViewMixin
from .playlist_view import PlaylistViewMixin
from .playback_view import PlaybackViewMixin
from .device_view import DeviceViewMixin
from .queue import QueueMixin
from .commands import CommandsMixin


class IpodWindow(
    # Ordered as the window reads: the views first, then the queue they stage
    # into, then the commands that carry it out. No two mixins define the same
    # method, so this order documents the design rather than resolving a
    # conflict - and tools/mixin-contract.py keeps it that way as tooling.
    LibraryViewMixin,
    SearchViewMixin,
    PlaylistViewMixin,
    PlaybackViewMixin,
    DeviceViewMixin,
    QueueMixin,
    CommandsMixin,
    Adw.ApplicationWindow,
):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Shuffle")
        self.set_default_size(1180, 760)
        # Below this the sidebar has already folded away and the now-playing
        # bar has nothing left to give, so stop rather than clipping.
        self.set_size_request(640, 560)
        self.add_css_class("shuffle")

        self.mount_point = None
        self.device_identity = None
        self.busy = False
        self.track_names = {}
        self.tag_generation = 0
        self.scan_generation = 0
        self.source_generation = 0
        # What the last completed probe found on the device. Painting reads
        # these rather than the device, so a repaint costs nothing.
        self.device_track_count = 0
        self.device_usage = None
        # Bumped per probe, so an unplug arriving while the previous device is
        # still being walked cannot repaint the window with what it held.
        self.probe_generation = 0
        # Whether any probe has answered yet. Only the first one paints a
        # searching state; doing it on every refresh would flash it over a
        # device sitting right there, once per mount event and once after
        # every command.
        self.probe_answered = False
        self.discovering_sources = False
        self.loading_options = False
        self.loaded_playlist_mode = 0
        self.loaded_playlist_args = []
        self.speech_engine_available = has_speech_engine()
        self.playlist_unavailable = (
            None if self.speech_engine_available else "No speech engine installed"
        )
        self.youtube_unavailable = youtube_unavailable_reason()
        self.youtube_search_unavailable = youtube_search_unavailable_reason()
        self.preview_unavailable = preview_unavailable_reason()

        # Built before the device page, which mounts the bar the player drives.
        self.player = PreviewPlayer(self._update_now_playing)
        # Which cover the bar is showing, so a 250ms poll does not rebuild a
        # texture four times a second to draw the same artwork.
        self._painted_art = UNPAINTED

        self.search_query = ""
        self.search_generation = 0
        self.search_results = []
        self.search_loading = False
        # What the YouTube half of the search is currently unable to give, said
        # in place of the rows rather than in a toast: a toast has gone by the
        # time the eye returns to the empty space it was explaining.
        self.search_note = ""
        self.search_add_buttons = []
        self._search_timeout = None

        # Bumped whenever a preview is asked for, so a download that is still
        # running when the next one starts cannot land in the bar behind it.
        self.preview_generation = 0
        self._preview_process = None
        self._preview_lock = threading.Lock()
        self._preview_closed = False

        self.library = LibraryIndex()
        self.device_tracks = []
        self._device_scan_tracks = {}
        self._device_scan_active = False
        self._device_snapshot_ready = False
        self._library_scan_tracks = {}
        self._library_by_path = {}
        self.pending = set()
        self.pending_sources = {}
        self.pending_records = {}
        self._pending_track_index = {}
        self.pending_device_identity = None
        self.sync_total = 0
        # Setting a filter pill active while the view is still being built
        # fires its handler, which would repaint a grid that does not exist
        # yet. Flipped once the last widget is in place.
        self._library_ready = False
        self.current_album = None
        self.current_playlist = None
        self.playlists = []
        self.spoken = set()
        self.album_filter = "all"
        self.view_mode = "grid"

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.stack)
        self.stack.add_named(self._build_empty_page(), "empty")
        self.stack.add_named(self._build_device_page(), "device")

        # Every widget an in-flight script must not be able to race. Collected
        # once rather than named individually in _set_busy, which previously
        # had to be edited every time the window grew a control.
        self._busy_widgets = [
            self.refresh_button,
            self.sync_button,
            self.add_button,
            self.playlist_button,
            self.youtube_button,
            self.rebuild_button,
            self.wipe_button,
            self.eject_button,
            self.sidebar_eject,
            self.new_playlist_button,
            # Whole views, because every track and playlist row carries a
            # button that would otherwise start a second script against the
            # same device while one is still running.
            self.library_view,
            self.search_view,
            self.album_view,
            self.playlists_view,
            # Typing while a script runs would repaint a view that is already
            # insensitive, which reads as the search being broken rather than
            # as the window being busy.
            self.search_entry,
        ]

        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_a: self._apply_theme())
        self._apply_theme()

        # React to the device being plugged in or unplugged, rather than making
        # the user press refresh. Polling would work but wastes wakeups.
        self.monitor = Gio.VolumeMonitor.get()
        for event in ("mount-added", "mount-removed"):
            self.monitor.connect(event, lambda *_a: GLib.idle_add(self.refresh))

        # A pipeline left in PLAYING outlives the window it was started from,
        # so closing the window would keep playing audio nothing on screen
        # could stop.
        self.connect("close-request", self._on_close_request)

        self._populate_cache_card()
        self.refresh()
        self._rescan_library()

    def _on_close_request(self, _window):
        self._shutdown_previews()
        return False

    def _apply_theme(self):
        if Adw.StyleManager.get_default().get_dark():
            self.remove_css_class("light")
        else:
            self.add_css_class("light")

    # ------------------------------------------------------------ empty page

    def _build_empty_page(self):
        self.empty_page = Adw.StatusPage(
            icon_name="multimedia-player-symbolic",
            title="No iPod Connected",
            description="Plug in an iPod shuffle and it will appear here.\n"
            "If it is already connected, it may need mounting.",
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER
        )
        self.mount_button = Gtk.Button(label="Mount Connected iPod")
        self.mount_button.set_halign(Gtk.Align.CENTER)
        self.mount_button.add_css_class("pill")
        self.mount_button.add_css_class("suggested-action")
        self.mount_button.connect("clicked", self.on_mount_clicked)
        box.append(self.mount_button)
        box.append(
            label(
                "Your library, search and preview playback do not need a device.",
                "sf-caption",
                xalign=0.5,
            )
        )
        self.empty_page.set_child(box)
        return self.empty_page

    # ----------------------------------------------------------- device page

    def _build_device_page(self):
        toolbar = Adw.ToolbarView()

        self.split = Adw.OverlaySplitView(
            sidebar_width_fraction=0.2,
            min_sidebar_width=236,
            max_sidebar_width=236,
        )
        self.split.set_sidebar(self._build_sidebar())
        toolbar.set_content(self.split)

        self.views = Adw.ViewStack()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.add_css_class("sf-content")
        content.append(self._build_header())
        content.append(self.views)
        self.views.set_vexpand(True)
        self.split.set_content(content)

        self.views.add_named(self._build_library_view(), "library")
        self.views.add_named(self._build_search_view(), "search")
        self.views.add_named(self._build_album_view(), "album")
        self.views.add_named(self._build_playlists_view(), "playlists")
        self.views.add_named(self._build_settings_view(), "settings")

        toolbar.add_bottom_bar(self._build_sync_bar())
        toolbar.add_bottom_bar(self._build_now_playing_bar())

        # Below this width the sidebar stops being furniture and starts being
        # most of the window, so it folds away behind the toggle instead.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 780px")
        )
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        return toolbar

    def _build_header(self):
        header = Gtk.Box(spacing=12)
        header.set_size_request(-1, 48)
        header.set_margin_start(18)
        header.set_margin_end(12)

        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.add_css_class("flat")
        self.sidebar_toggle.set_valign(Gtk.Align.CENTER)
        # Only worth showing once the sidebar has folded away; while it is
        # visible the button would toggle something already on screen.
        self.split.bind_property(
            "collapsed",
            self.sidebar_toggle,
            "visible",
            GObject.BindingFlags.SYNC_CREATE,
        )
        self.split.bind_property(
            "show-sidebar",
            self.sidebar_toggle,
            "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        header.append(self.sidebar_toggle)

        self.view_title = label("Your Library", "sf-section-heading")
        self.view_title.set_valign(Gtk.Align.CENTER)
        header.append(self.view_title)

        header.append(Gtk.Box(hexpand=True))

        header.append(self._build_search_entry())
        header.append(self._build_library_controls())

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_button.add_css_class("flat")
        self.refresh_button.set_valign(Gtk.Align.CENTER)
        self.refresh_button.set_tooltip_text("Rescan the device and your music folders")
        self.refresh_button.connect("clicked", self.on_refresh_clicked)
        header.append(self.refresh_button)

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrapper.append(header)
        line = Gtk.Separator()
        wrapper.append(line)
        return wrapper

    # -------------------------------------------------------------- sidebar

    def _build_sidebar(self):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.add_css_class("sf-sidebar")
        sidebar.set_size_request(236, -1)

        brand = Gtk.Box(spacing=9)
        brand.set_margin_start(14)
        brand.set_margin_end(14)
        brand.set_margin_top(12)
        brand.set_margin_bottom(6)
        badge = label("S", "sf-brand", xalign=0.5, yalign=0.5)
        badge.set_size_request(24, 24)
        brand.append(badge)
        brand.append(label("Shuffle", "sf-row-title", valign=Gtk.Align.CENTER))
        sidebar.append(brand)

        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        nav.set_margin_start(10)
        nav.set_margin_end(10)
        nav.set_margin_top(6)
        self.nav_buttons = {}
        for name, icon, title in (
            ("library", "view-grid-symbolic", "Your Library"),
            ("playlists", "view-list-symbolic", "Playlists"),
        ):
            button = self._nav_button(name, icon, title)
            nav.append(button)
            self.nav_buttons[name] = button
        sidebar.append(nav)

        sidebar.append(
            self._sidebar_label("Playlists", margin_top=14)
        )
        sidebar.append(self._build_sidebar_rail())

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)

        footer.append(self._build_device_card())
        settings_button = self._nav_button(
            "settings", "emblem-system-symbolic", "Device & Settings"
        )
        self.nav_buttons["settings"] = settings_button
        footer.append(settings_button)
        sidebar.append(footer)

        return sidebar

    def _sidebar_label(self, text, margin_top=0):
        widget = label(text.upper(), "sf-section-label")
        widget.set_margin_start(20)
        widget.set_margin_end(20)
        widget.set_margin_top(margin_top)
        widget.set_margin_bottom(6)
        return widget

    def _nav_button(self, name, icon, title):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-nav-row")
        box = Gtk.Box(spacing=11)
        image = Gtk.Image.new_from_icon_name(icon)
        box.append(image)
        box.append(label(title))
        button.set_child(box)
        button.connect("clicked", lambda _b, n=name: self._navigate(n))
        return button

    def _navigate(self, name):
        """Follow a sidebar row, ending the search that row is leaving.

        Leaving the results up behind a field that still holds the query would
        make the next keystroke reopen a view the user had just navigated out
        of, and the sidebar would show a destination that is not on screen.
        """
        self._clear_search()
        self.show_view(name)

    # ---------------------------------------------------------------- state

    def current_view(self):
        """Which view is on screen.

        A verb rather than letting each mixin reach into the stack: repainting
        only what is visible is something four of them need, and the widget
        holding that answer is the window's own.
        """
        return self.views.get_visible_child_name()

    def show_view(self, name):
        self.views.set_visible_child_name(name)
        titles = {
            "library": "Your Library",
            "search": "Search",
            "album": "Album",
            "playlists": "Playlists",
            "settings": "Device & Settings",
        }
        self.view_title.set_text(titles.get(name, name.title()))
        for key, button in self.nav_buttons.items():
            if key == name or (name == "album" and key == "library"):
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")
        self.library_controls.set_visible(name == "library")

    def on_refresh_clicked(self, _button):
        self.refresh()
        self._rescan_library()

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))
        return False

    @staticmethod
    def _children(container):
        child = container.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()
