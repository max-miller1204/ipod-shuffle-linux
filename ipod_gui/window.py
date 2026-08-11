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

from .config import library_layout
from .shell import (
    has_speech_engine,
    youtube_search_unavailable_reason,
    youtube_unavailable_reason,
)
from .model import LibraryIndex
from .widgets import ELLIPSIZE_END, label
from .player import PreviewPlayer, UNPAINTED, preview_unavailable_reason
from .library_view import LibraryViewMixin
from .search_view import SearchViewMixin
from .playlist_view import PlaylistViewMixin
from .playback_view import PlaybackViewMixin
from .device_view import DeviceViewMixin
from .queue import QueueMixin
from .commands import CommandsMixin


# The width the sidebar folds away below, and the narrower one the view title
# gives up at. Named here because they are load-bearing rather than cosmetic:
# the first has to stay above the sidebar and the content added together, which
# is what tests/gui-window-minimum.py checks it against.
#
# 940 rather than something rounder. A library with a device attached needs
# around 860 of it, measured with every page built and the view title counted,
# and the rest is the margin that keeps the same window clear of this on a
# desktop whose fonts are not the ones the check ran against. Above 960 would
# also take the sidebar away from a window tiled to half of a 1920px screen,
# which is the most ordinary way this app is ever half-sized.
SIDEBAR_COLLAPSE_WIDTH = 940
TITLE_HIDE_WIDTH = 700

# How long the refresh button keeps spinning once the scans behind it have
# finished. A rescan of a small library is over in a few dozen milliseconds,
# and a spinner that brief is a flicker rather than an answer.
REFRESH_SPINNER_MIN_MS = 600


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
        #
        # The width is set by the playlist page while it is showing a playlist
        # whose tracks are on the iPod: every row's button then reads "Remove"
        # rather than "Add", which is the widest that column gets. It stood at
        # 640 while nothing measured that page in that state, so the window was
        # advertising eight pixels it did not have - and a window allocated
        # less than it needs is not merely cramped, it paints its controls
        # somewhere other than where the clicks land.
        self.set_size_request(660, 560)
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
        # The list a pasted link resolved to, which is what the rows are three
        # of. None for a typed query, which resolves to no list at all.
        self.search_playlist = None
        self.search_loading = False
        # The link currently being offered under the field, and every link
        # already offered: a suggestion is made once per link rather than
        # every time the empty field is reached for again.
        self.clipboard_offer_url = ""
        self._offered_links = set()
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
        self._library_scan_running = False
        self._refresh_spinner_since = 0
        self._refresh_stop_timer = None
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
        # A repaint asked for by a scan batch, waiting to be coalesced with
        # whatever follows it rather than rebuilding the grid per batch.
        self._refresh_timer = None
        self.current_album = None
        self.current_playlist = None
        # The two halves of what the Playlists view shows: the lists made here,
        # which are files in a folder of the user's own, and the ones the probe
        # found on the device. Kept apart because only the first can be edited
        # and only the second says what the iPod currently holds.
        self.local_playlists = []
        self.playlists = []
        self.spoken = set()
        self.album_filter = "all"
        # How the library was last being read. Restored here rather than inside
        # the library mixin because the header carries both controls and is
        # built before the grid and the table they switch between exist.
        self._saved_group_mode, self.view_mode = library_layout()

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
            # as the window being busy. The clipboard strip is the same field
            # by another route: its button fills that field and searches.
            self.search_entry,
            self.clipboard_offer,
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

        # A window that has opened is already somewhere, and the sidebar is
        # where it says so. The stack shows whatever was added first, which is
        # the library, but the row saying so is only marked by show_view - so
        # without this the app opens on a sidebar with nothing current until
        # the first click, and every screenshot of a fresh launch shows that.
        self.show_view("library")

        self._populate_cache_card()
        self.refresh()
        self._rescan_library()

    def _on_close_request(self, _window):
        self._shutdown_previews()
        # A coalesced repaint still queued would fire against a window that is
        # on its way out, rebuilding a grid nobody will see.
        self._cancel_refresh()
        if self._refresh_stop_timer is not None:
            GLib.source_remove(self._refresh_stop_timer)
            self._refresh_stop_timer = None
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
        #
        # It is a floor before it is a preference: while the sidebar is shown
        # it takes its width out of the content's, so this has to stay above
        # the two of them added together or there is a band of widths where the
        # window is showing a sidebar it has no room for. That band does not
        # look cramped, it looks broken - GTK paints the content past the edge
        # of the window and clicks land where it was measured rather than where
        # it was drawn. tests/gui-window-minimum.py holds this number to it.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f"max-width: {SIDEBAR_COLLAPSE_WIDTH}px")
        )
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        # Narrower again and the title has been ellipsised down to a word and a
        # half, which reads as something broken rather than as a heading. The
        # sidebar it folds away with is one click from the toggle beside it,
        # and every view names itself in its own first heading anyway.
        #
        # This one repeats the setter above rather than adding to it: only one
        # breakpoint is ever applied, the last one that matches, so down here
        # it replaces the wider one instead of joining it. A narrower
        # breakpoint that leaves out a wider one's setters silently undoes
        # them, and here that meant the sidebar staying open in a window too
        # narrow to hold it and the content being painted off the edge.
        narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f"max-width: {TITLE_HIDE_WIDTH}px")
        )
        narrow.add_setter(self.split, "collapsed", True)
        narrow.add_setter(self.view_title, "visible", False)
        self.add_breakpoint(narrow)

        return toolbar

    def _build_header(self):
        """The window's one header bar, and its only titlebar.

        An Adw.ApplicationWindow has no titlebar of its own: whatever the
        content puts at the top is it. A plain box there leaves the window with
        nothing to drag it by and no minimise, maximise or close button, so
        this is a box that is given both explicitly - a Gtk.WindowControls for
        the buttons, wrapped in a Gtk.WindowHandle for the drag. An
        Adw.HeaderBar would carry the two of them for free but lays its
        children out in a way this row cannot use, for the reason recorded
        below where the controls are added.

        Everything in it is allowed to shrink. The window offers a 660px
        minimum, and a header that cannot go that narrow makes the whole window
        narrower than its own contents, which GTK resolves by allocating
        widgets somewhere other than where it paints them - clicks then land
        beside the control that appears to be under the pointer.
        """
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

        self.view_title = label(
            "Your Library", "sf-section-heading", ellipsize=ELLIPSIZE_END
        )
        self.view_title.set_valign(Gtk.Align.CENTER)
        # Long enough for every view's name, short enough that the title gives
        # way before the controls do when the window is dragged narrow.
        self.view_title.set_max_width_chars(18)
        header.append(self.view_title)

        header.append(Gtk.Box(hexpand=True))

        header.append(self._build_search_entry())
        header.append(self._build_library_controls())

        # The icon is swapped for a spinner while the rescan runs, because
        # otherwise pressing this does nothing anybody can see: the only
        # progress the window had to offer was the status line under the grid,
        # and that line is hidden whenever the library has anything in it.
        self.refresh_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        self.refresh_spinner = Gtk.Spinner()
        self.refresh_button = Gtk.Button(child=self.refresh_icon)
        self.refresh_button.add_css_class("flat")
        # Given rather than inherited: GTK only adds this to a button it built
        # the icon for, and this one is handed its child so the spinner can
        # take the icon's place. Without it the button keeps a text button's
        # wider padding and stops matching the icon buttons either side of it.
        self.refresh_button.add_css_class("image-button")
        self.refresh_button.set_valign(Gtk.Align.CENTER)
        self.refresh_button.set_tooltip_text("Rescan the device and your music folders")
        self.refresh_button.connect("clicked", self.on_refresh_clicked)
        header.append(self.refresh_button)

        # The buttons a titlebar would have carried. An Adw.HeaderBar would
        # bring its own, but it centres whatever widget it is given as a title
        # and hands its end pack a natural width before anything else, which
        # moves this row's contents around and overlaps them once the window is
        # narrow. Its two useful parts are separable: these are the buttons.
        self.window_controls = Gtk.WindowControls(side=Gtk.PackType.END)
        self.window_controls.set_decoration_layout(":minimize,maximize,close")
        self.window_controls.set_valign(Gtk.Align.CENTER)
        header.append(self.window_controls)

        # ...and this is the rest: the strip is now something the window can be
        # dragged by, double-clicked to maximise and right-clicked for the
        # window menu. Interactive children keep their own clicks.
        handle = Gtk.WindowHandle()
        handle.set_child(header)

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrapper.append(handle)
        line = Gtk.Separator()
        wrapper.append(line)
        # Under the whole strip rather than beside the field: it is as wide as
        # the content it is offering to fill, and it must not be inside the
        # window handle, where a click meant for its buttons would be the
        # start of a window drag.
        wrapper.append(self._build_clipboard_offer())
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
        # The header bar only spans the content pane, so without this the strip
        # above the sidebar is the one part of the top edge the window cannot
        # be dragged by.
        brand_handle = Gtk.WindowHandle()
        brand_handle.set_child(brand)
        sidebar.append(brand_handle)

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

    def _update_refresh_spinner(self):
        """Spin while either scan is still running.

        Derived from the two scans rather than counted up and down around
        them: a scan superseded by a newer one returns without finishing, and
        a counter would be left believing work was still in flight forever.
        """
        running = self._library_scan_running or self._device_scan_active
        if running:
            if self._refresh_stop_timer is not None:
                GLib.source_remove(self._refresh_stop_timer)
                self._refresh_stop_timer = None
                # The spinner was already on its way out when this work
                # arrived, so the minimum below runs from here rather than
                # from the start it was about to end. Pressing refresh while
                # the last spin is finishing is the one press most likely to
                # be a second try, and it is the one that would otherwise put
                # the spinner out again a few milliseconds after the click.
                self._refresh_spinner_since = GLib.get_monotonic_time()
            if not self.refresh_spinner.get_spinning():
                self._refresh_spinner_since = GLib.get_monotonic_time()
                self.refresh_button.set_child(self.refresh_spinner)
                self.refresh_spinner.start()
            return

        if not self.refresh_spinner.get_spinning():
            return
        if self._refresh_stop_timer is not None:
            return
        # A scan over a small library finishes in well under a tenth of a
        # second, and a spinner that appears and vanishes inside that reads as
        # a glitch rather than as work having been done. Held to a minimum so
        # pressing the button always looks like it did something.
        spent = (GLib.get_monotonic_time() - self._refresh_spinner_since) // 1000
        remaining = REFRESH_SPINNER_MIN_MS - spent
        if remaining <= 0:
            self._stop_refresh_spinner()
            return
        self._refresh_stop_timer = GLib.timeout_add(
            remaining, self._stop_refresh_spinner
        )

    def _stop_refresh_spinner(self):
        # Stopped as well as swapped out: a spinner left running is an
        # animation frame clock ticking for a widget nobody can see.
        self._refresh_stop_timer = None
        self.refresh_spinner.stop()
        self.refresh_button.set_child(self.refresh_icon)
        return False

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))
        return False

    @staticmethod
    def _children(container):
        child = container.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()
