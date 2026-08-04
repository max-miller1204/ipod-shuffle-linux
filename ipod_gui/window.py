"""The application window: every view, and the work behind each of them."""

import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .config import (
    ART_CACHE,
    AUDIO_EXTENSIONS,
    PLAYLIST_EXTENSIONS,
    PREVIEW_CACHE,
    PREVIEW_CACHE_LIMIT,
    PREVIEW_INCOMING,
    REMOVE_SCRIPT,
    STATE_IPOD,
    STATE_LABELS,
    STATE_LIBRARY,
    STATE_PREVIEW,
    SYNC_SCRIPT,
    WIPE_SCRIPT,
    YOUTUBE_LIBRARY,
    save_music_roots,
)
from .text import (
    COPIED_LINE,
    home_relative,
    human_duration,
    human_size,
    plural,
    strip_ansi,
)
from .tags import scan_tracks
from .device import (
    count_tracks,
    find_ipods,
    list_playlists,
    playlist_file,
    resolve_device,
    saved_sync_options,
    spoken_playlists,
    udisks_filesystem_call,
    unmounted_vfat_devices,
    volume_identity,
    write_playlist,
)
from .shell import (
    has_speech_engine,
    youtube_search_unavailable_reason,
    youtube_unavailable_reason,
)
from .youtube import (
    SEARCH_DEBOUNCE_MS,
    SEARCH_MIN_QUERY,
    YOUTUBE_SEARCH_RESULTS,
    cache_thumbnail,
    cached_thumbnail_for,
    fetch_command,
    fetched_sources,
    is_downloadable_url,
    search_youtube,
    youtube_art_path,
)
from .previews import (
    cached_preview_path,
    preview_cache_entries,
    promote_destination,
    prunable_previews,
)
from .model import (
    LibraryIndex,
    Track,
    local_playlist_tracks,
    local_search_matches,
    read_local_playlist_tracks,
)
from .widgets import (
    ALBUM_COVER,
    ELLIPSIZE_END,
    StorageMeter,
    fill_tracks,
    label,
    make_cover,
    state_dot,
    track_column_view,
    track_list_view,
)
from .player import (
    PLAY_FETCHING,
    PLAY_IDLE,
    PLAY_LOADING,
    PLAY_PLAYING,
    PREVIEW_FAILED,
    PreviewPlayer,
    UNPAINTED,
    preview_unavailable_reason,
)


class IpodWindow(Adw.ApplicationWindow):
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
        self.pending_skipped_symlinks = {}
        self._pending_track_index = {}
        self.pending_device_identity = None
        self.sync_files = []
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
        self.player.shutdown()
        # A download outlives the window that started it otherwise, writing
        # into a cache nothing is left to show or prune.
        with self._preview_lock:
            self._preview_closed = True
        self._supersede_preview_fetch()
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

        # One field over both sources. Which of them a result came from is
        # said by the section it lands in, not by asking the user to choose a
        # source before they have typed anything.
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search your library and YouTube"
        )
        self.search_entry.add_css_class("sf-search")
        self.search_entry.set_size_request(260, -1)
        self.search_entry.set_valign(Gtk.Align.CENTER)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", lambda _e: self._clear_search())
        header.append(self.search_entry)

        # Only meaningful on the library, which is the only view with a grid
        # to group or to swap for a table.
        self.library_controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)

        self.group_mode = Gtk.DropDown.new_from_strings(["Album", "Artist"])
        self.group_mode.add_css_class("flat")
        self.group_mode.set_tooltip_text("Group the library by album or by artist")
        self.group_mode.connect("notify::selected", lambda *_a: self._populate_albums())
        self.library_controls.append(self.group_mode)

        modes = Gtk.Box()
        modes.add_css_class("linked")
        self.mode_buttons = {}
        for mode, icon, tip in (
            ("grid", "view-grid-symbolic", "Show covers as a grid"),
            ("list", "view-list-symbolic", "Show every track as a sortable table"),
        ):
            button = Gtk.ToggleButton(icon_name=icon)
            button.set_tooltip_text(tip)
            button.connect("toggled", self._on_view_mode_toggled, mode)
            modes.append(button)
            self.mode_buttons[mode] = button
        self.mode_buttons["grid"].set_active(True)
        self.library_controls.append(modes)
        header.append(self.library_controls)

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
        self.playlist_rail = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, vexpand=True
        )
        self.playlist_rail.set_margin_start(10)
        self.playlist_rail.set_margin_end(10)
        rail_scroll = Gtk.ScrolledWindow(vexpand=True)
        rail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroll.set_child(self.playlist_rail)
        sidebar.append(rail_scroll)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        footer.set_margin_start(10)
        footer.set_margin_end(10)
        footer.set_margin_top(10)
        footer.set_margin_bottom(10)

        self.device_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.device_card.add_css_class("sf-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        inner.set_margin_top(11)
        inner.set_margin_bottom(11)
        self.device_card.append(inner)

        top = Gtk.Box(spacing=8)
        self.device_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.device_dot.add_css_class("sf-dot")
        self.device_dot.add_css_class("library")
        top.append(self.device_dot)
        self.device_name = label("No iPod", "sf-row-title", hexpand=True, ellipsize=ELLIPSIZE_END)
        top.append(self.device_name)
        self.sidebar_eject = Gtk.Button(icon_name="media-eject-symbolic")
        self.sidebar_eject.add_css_class("flat")
        self.sidebar_eject.set_valign(Gtk.Align.CENTER)
        self.sidebar_eject.set_tooltip_text("Unmount safely before unplugging")
        self.sidebar_eject.connect("clicked", self.on_eject)
        top.append(self.sidebar_eject)
        inner.append(top)

        self.sidebar_meter = StorageMeter()
        inner.append(self.sidebar_meter)

        figures = Gtk.Box()
        self.device_free = label("", "sf-caption", hexpand=True)
        figures.append(self.device_free)
        self.device_count = label("", "sf-caption", "sf-mono")
        figures.append(self.device_count)
        inner.append(figures)

        self.queued_row = Gtk.Box(spacing=6)
        queued_swatch = Gtk.Box(valign=Gtk.Align.CENTER)
        queued_swatch.add_css_class("sf-dot")
        queued_swatch.add_css_class("ipod")
        self.queued_row.append(queued_swatch)
        self.queued_label = label("", "sf-caption", "sf-accent")
        self.queued_row.append(self.queued_label)
        self.queued_row.set_visible(False)
        inner.append(self.queued_row)

        footer.append(self.device_card)
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

    # ---------------------------------------------------------- library view

    def _build_library_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.library_view = scroller

        shelf_head = Gtk.Box(spacing=10)
        shelf_head.append(label("Playlists", "sf-section-heading"))
        shelf_head.append(
            label(
                "Names exist on the device only as spoken audio",
                "sf-body",
                valign=Gtk.Align.BASELINE,
                wrap=True,
            )
        )
        self.shelf_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        self.shelf_section.append(shelf_head)
        self.playlist_shelf = Gtk.Box(spacing=12)
        shelf_scroll = Gtk.ScrolledWindow()
        shelf_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        shelf_scroll.set_child(self.playlist_shelf)
        self.shelf_section.append(shelf_scroll)
        box.append(self.shelf_section)

        # Built before the filter pills, whose set_active below fires the
        # toggled handler immediately and repaints the grid.
        self.album_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=8,
            min_children_per_line=2,
            column_spacing=16,
            row_spacing=16,
            homogeneous=True,
            valign=Gtk.Align.START,
        )
        self.album_flow.add_css_class("sf-flow")

        albums_head = Gtk.Box(spacing=8)
        self.collection_heading = label("Albums", "sf-section-heading")
        albums_head.append(self.collection_heading)
        self.album_filters = {}
        for key, text in (
            ("all", "All"),
            (STATE_IPOD, "On iPod"),
            (STATE_LIBRARY, "In library"),
            (STATE_PREVIEW, "Previewed"),
        ):
            pill = Gtk.ToggleButton()
            pill.add_css_class("sf-pill")
            pill.set_valign(Gtk.Align.CENTER)
            pill.set_child(label(text, xalign=0.5))
            pill.connect("toggled", self._on_filter_toggled, key)
            albums_head.append(pill)
            self.album_filters[key] = pill
        self.album_filters["all"].add_css_class("selected")
        self.album_filters["all"].set_active(True)
        box.append(albums_head)

        # The grid and the flat table are two ways of reading the same
        # library, so they share a stack rather than a scroll position.
        self.library_modes = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE
        )
        self.library_modes.add_named(self.album_flow, "grid")
        self.library_table = track_column_view(self)
        self.library_table.set_vexpand(True)
        self.library_modes.add_named(self.library_table, "list")
        box.append(self.library_modes)

        self.library_status = label("Reading your music folders…", "sf-body")
        box.append(self.library_status)
        self._library_ready = True
        return scroller

    def _on_view_mode_toggled(self, button, mode):
        if not button.get_active():
            # Refuse to leave neither selected; this is a choice of two.
            if self.view_mode == mode:
                button.set_active(True)
            return
        self.view_mode = mode
        # The header is built before the views it controls, so the initial
        # set_active arrives before there is a stack to switch.
        if not self._library_ready:
            return
        for name, other in self.mode_buttons.items():
            if name != mode:
                other.set_active(False)
        self.library_modes.set_visible_child_name(mode)
        # Grouping is a property of the grid; the table is always every track.
        self.group_mode.set_sensitive(mode == "grid")
        self._populate_albums()

    def _on_filter_toggled(self, button, key):
        if not button.get_active():
            if self.album_filter == key:
                button.set_active(True)
            return
        self.album_filter = key
        for name, pill in self.album_filters.items():
            if name == key:
                pill.add_css_class("selected")
            else:
                pill.remove_css_class("selected")
                pill.set_active(False)
        self._populate_albums()

    # ----------------------------------------------------------- search view

    def _build_search_view(self):
        """Both sources on one page, local first because it is instant.

        Two sections rather than one merged list: a file you already have and
        a video you would have to download are not the same offer, and sorting
        them together would bury the free half under the expensive one.
        """
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.search_view = scroller

        local = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        head = Gtk.Box(spacing=8)
        head.append(label("In your library", "sf-section-heading"))
        self.search_local_count = label(
            "", "sf-caption", valign=Gtk.Align.BASELINE
        )
        head.append(self.search_local_count)
        local.append(head)
        self.search_local_note = label("", "sf-body", wrap=True)
        local.append(self.search_local_note)
        self.search_local_table = track_column_view(
            self, columns=("title", "album", "state", "duration", "action")
        )
        local.append(self.search_local_table)
        box.append(local)

        remote = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        head = Gtk.Box(spacing=8)
        head.append(label("From YouTube", "sf-section-heading"))
        self.search_youtube_count = label(
            "", "sf-caption", valign=Gtk.Align.BASELINE
        )
        head.append(self.search_youtube_count)
        remote.append(head)
        self.search_youtube_note = label("", "sf-body", wrap=True)
        remote.append(self.search_youtube_note)
        self.search_youtube_rows = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        remote.append(self.search_youtube_rows)
        box.append(remote)
        return scroller

    @staticmethod
    def _skeleton_row():
        """A placeholder exactly as tall as the result that replaces it.

        The two lines are real labels carrying a result's own text classes with
        the grey bar drawn over them, rather than bars of a chosen height: the
        row has to come out the same number of pixels tall as what lands in its
        place, and that number belongs to the font rather than to anything this
        code could hardcode and keep correct.
        """
        row = Gtk.Box(spacing=12)
        row.add_css_class("sf-track-row")
        row.add_css_class("sf-result-row")
        art = Gtk.Box(valign=Gtk.Align.CENTER)
        art.add_css_class("sf-skeleton")
        art.set_size_request(36, 36)
        row.append(art)

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            hexpand=True,
        )
        for width, style in ((210, "sf-row-title"), (120, "sf-body")):
            line = Gtk.Overlay()
            line.set_child(label(" ", style))
            bar = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
            bar.add_css_class("sf-skeleton")
            bar.set_size_request(width, 9)
            line.add_overlay(bar)
            text.append(line)
        row.append(text)
        return row

    def _youtube_row(self, result):
        row = Gtk.Box(spacing=12)
        row.add_css_class("sf-track-row")
        row.add_css_class("sf-result-row")
        row.append(self._preview_cover(result))

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        text.append(label(result.title, "sf-row-title", ellipsize=ELLIPSIZE_END))
        text.append(label(result.uploader, "sf-body", ellipsize=ELLIPSIZE_END))
        row.append(text)

        row.append(
            label(
                human_duration(result.duration) if result.duration else "--:--",
                "sf-caption",
                "sf-mono",
                width_chars=5,
                xalign=1.0,
                valign=Gtk.Align.CENTER,
            )
        )

        add = Gtk.Button(label="Add")
        add.add_css_class("sf-button")
        add.add_css_class("accent")
        add.set_valign(Gtk.Align.CENTER)
        add.connect("clicked", lambda _b, r=result: self._download_result(r))
        add.set_sensitive(self._can_download())
        add.set_tooltip_text(self._youtube_download_tooltip())
        row.append(add)
        self.search_add_buttons.append(add)
        return row

    def _preview_cover(self, result):
        """A result's artwork, which plays it under the pointer.

        The video's thumbnail once it has been fetched, and until then the
        same generated placeholder a local track with no embedded cover gets.
        Behind the same hover-to-play button a library row has, so the two
        halves of the page read as one list rather than as two widgets that
        happen to be stacked.
        """
        overlay = Gtk.Overlay(valign=Gtk.Align.CENTER)
        overlay.set_child(
            make_cover(
                youtube_art_path(result.video_id, ART_CACHE),
                36,
                result.video_id or result.title,
                "small",
            )
        )
        play = Gtk.Button(icon_name="media-playback-start-symbolic")
        play.add_css_class("sf-cover-play")
        play.set_size_request(36, 36)
        reason = self._preview_unavailable_reason(result)
        if reason:
            play.set_sensitive(False)
            play.set_tooltip_text(reason)
        else:
            play.set_tooltip_text(f"Preview {result.title} on this computer")
        play.connect("clicked", lambda _b, r=result: self.preview_result(r))
        overlay.add_overlay(play)
        return overlay

    def _preview_unavailable_reason(self, result):
        """Why this result cannot be previewed, or None.

        A result already in the cache needs nothing but GStreamer to play. One
        that is not has to be downloaded first, so it needs everything a
        download needs - and saying so on the button is the difference between
        a disabled control and one that fails several steps later.
        """
        if self.preview_unavailable:
            return self.preview_unavailable
        if cached_preview_path(result.video_id, PREVIEW_CACHE) is not None:
            return None
        return self.youtube_unavailable

    def _youtube_download_tooltip(self):
        if self.youtube_unavailable:
            return self.youtube_unavailable
        if not self.mount_point:
            return "Connect an iPod to download and queue a track"
        return None

    def _can_download(self):
        """Whether a search result could be fetched and queued right now."""
        return bool(
            self.mount_point
            and self.device_identity is not None
            and not self.busy
            and not self.discovering_sources
            and not self.youtube_unavailable
        )

    def _paint_local_results(self):
        matches = local_search_matches(
            self.library.all_tracks(), self.search_query
        )
        fill_tracks(self.search_local_table, matches)
        self.search_local_count.set_text(
            plural(len(matches), "track") if matches else ""
        )
        self.search_local_table.set_visible(bool(matches))
        self.search_local_note.set_text(
            "" if matches else "Nothing in your music folders matches this search."
        )
        self.search_local_note.set_visible(not matches)

    def _paint_youtube_section(self):
        """Repaint the YouTube half from whatever state it is in.

        One function for the skeleton, the results and every failure, because
        all of them are alternatives for the same space: the section shows rows
        or it shows the sentence saying why it cannot, and the sentence lives
        in search_note rather than in an argument so that a repaint triggered
        by something else - a device appearing, a sync finishing - cannot
        quietly erase it.
        """
        self.search_add_buttons = []
        child = self.search_youtube_rows.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.search_youtube_rows.remove(child)
            child = nxt

        if self.search_loading:
            for _ in range(YOUTUBE_SEARCH_RESULTS):
                self.search_youtube_rows.append(self._skeleton_row())
            self.search_youtube_count.set_text("Searching…")
            self.search_youtube_note.set_visible(False)
            return

        for result in self.search_results:
            self.search_youtube_rows.append(self._youtube_row(result))
        self.search_youtube_count.set_text(
            plural(len(self.search_results), "result") if self.search_results else ""
        )

        note = self.search_note
        # Results can be listed and still not be addable. Metadata needs no
        # JavaScript runtime and no ffmpeg; the download needs both, and
        # letting the user press Add and watch it fail several steps later is
        # exactly the confusion the capability checks exist to prevent.
        if not note and self.search_results and self.youtube_unavailable:
            note = (
                f"Downloads are unavailable: {self.youtube_unavailable}. "
                "These are search results only."
            )
        self.search_youtube_note.set_text(note)
        self.search_youtube_note.set_visible(bool(note))

    def _set_search_note(self, text):
        self.search_note = text
        self._paint_youtube_section()

    def _cancel_search_timeout(self):
        if self._search_timeout is not None:
            GLib.source_remove(self._search_timeout)
            self._search_timeout = None

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        if query == self.search_query:
            return
        self.search_query = query
        self._cancel_search_timeout()
        # Bumped on every keystroke so a search still in flight for an older
        # query cannot land on a newer one, the same guard the library scan uses.
        self.search_generation += 1
        if not query:
            self._clear_search()
            return

        self.show_view("search")
        self._paint_local_results()

        self.search_results = []
        if self.youtube_search_unavailable:
            self.search_loading = False
            self._set_search_note(
                f"{self.youtube_search_unavailable}. Your music folders are "
                "still searched."
            )
            return
        if len(query) < SEARCH_MIN_QUERY:
            self.search_loading = False
            self._set_search_note("Type a little more to search YouTube.")
            return

        # The library filters on every keystroke because it is already in
        # memory; YouTube costs a network round trip, so it waits for a pause.
        self.search_loading = True
        self._set_search_note("")
        self._search_timeout = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS,
            self._start_youtube_search,
            self.search_generation,
            query,
        )

    def _start_youtube_search(self, generation, query):
        self._search_timeout = None
        if generation != self.search_generation:
            return False

        def worker():
            results, reached = search_youtube(query)
            GLib.idle_add(
                self._finish_youtube_search, generation, results, reached
            )

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _finish_youtube_search(self, generation, results, reached):
        if generation != self.search_generation:
            return False
        self.search_loading = False
        self.search_results = results
        if not reached:
            self._set_search_note(
                "Could not reach YouTube. Check the connection, or wait a "
                "moment if several searches have just run in a row."
            )
        elif not results:
            self._set_search_note("No YouTube results for this search.")
        else:
            self._set_search_note("")
        self._start_thumbnail_fetch(generation, results)
        return False

    def _start_thumbnail_fetch(self, generation, results):
        """Fetch the results' artwork behind the rows that are already up.

        After painting rather than before it: the titles are what the search
        was for, and holding a list of results back for a set of images would
        spend the fast half of the search on the slow half. Nothing moves when
        they land either - each cover drops into a frame that was already
        exactly its size, holding the placeholder.
        """
        wanted = [
            result
            for result in results
            if result.thumbnail
            and youtube_art_path(result.video_id, ART_CACHE) is None
        ]
        if not wanted:
            return

        def worker():
            landed = False
            for result in wanted:
                if cache_thumbnail(result.video_id, result.thumbnail, ART_CACHE):
                    landed = True
            # One repaint for the set rather than one per image: rebuilding the
            # rows three times under the pointer would flicker the hover state
            # of a button the user may already be reaching for.
            if landed:
                GLib.idle_add(self._finish_thumbnail_fetch, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_thumbnail_fetch(self, generation):
        """Repaint the results with the artwork that has arrived for them."""
        tracks = {
            id(track): track
            for track in (
                *self.library.all_tracks(),
                *self.player.queue,
                self.player.track,
            )
            if track is not None
        }
        art_changed = False
        for track in tracks.values():
            if track.art is None:
                track.art = cached_thumbnail_for(track.path)
                art_changed = art_changed or track.art is not None
        if art_changed:
            self._refresh_current_view()
            self._update_now_playing()
        if generation == self.search_generation:
            self._paint_youtube_section()
        return False

    def _clear_search(self):
        self._cancel_search_timeout()
        self.search_generation += 1
        self.search_query = ""
        self.search_loading = False
        self.search_results = []
        self.search_note = ""
        # Emptied last, so the search-changed this fires finds the state it
        # would have set already in place and stops rather than recursing.
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        if self.views.get_visible_child_name() == "search":
            self.show_view("library")

    def _download_result(self, result):
        if not self._can_download():
            return
        # Clears whatever an earlier attempt left behind, so a retry that works
        # does not sit under the sentence saying the last one failed.
        self._set_search_note("")
        self._start_youtube_download(
            result.url,
            single=True,
            busy_message=f"Downloading {result.title}",
            on_failure=lambda: self._report_download_failure(result),
        )

    def _report_download_failure(self, result):
        """Say in the section that this particular download did not finish.

        The toast is gone by the time the eye returns to the row that was being
        added, and that row is what the user is looking at.
        """
        if self.views.get_visible_child_name() != "search":
            return
        self._set_search_note(
            f"Could not finish downloading {result.title}. Details has what "
            "yt-dlp reported; ./ipod-fetch.sh --update is the usual fix when "
            "downloads stop working."
        )

    # ------------------------------------------------------------ album view

    def _build_album_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.album_view = scroller

        back = Gtk.Button(label="‹  Your Library", halign=Gtk.Align.START)
        back.add_css_class("flat")
        back.add_css_class("sf-caption")
        back.connect("clicked", lambda _b: self.show_view("library"))
        box.append(back)

        header = Gtk.Box(spacing=20)
        self.album_art_holder = Gtk.Box()
        header.append(self.album_art_holder)
        meta = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.END
        )
        self.album_heading = label("", "sf-album-title", ellipsize=ELLIPSIZE_END)
        meta.append(self.album_heading)
        self.album_subheading = label("", "sf-body")
        meta.append(self.album_subheading)
        self.album_actions = Gtk.Box(spacing=8)
        meta.append(self.album_actions)
        header.append(meta)
        box.append(header)

        self.album_tracks = track_column_view(
            self, columns=("number", "title", "state", "duration", "action")
        )
        box.append(self.album_tracks)
        return scroller

    # -------------------------------------------------------- playlists view

    def _build_playlists_view(self):
        outer = Gtk.Box(spacing=0, vexpand=True)
        self.playlists_view = outer

        rail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rail_box.set_size_request(240, -1)
        rail_box.set_margin_start(22)
        rail_box.set_margin_end(12)
        rail_box.set_margin_top(20)
        rail_box.set_margin_bottom(20)
        head = Gtk.Box(spacing=8)
        head.append(label("Playlists", "sf-section-heading", hexpand=True))
        self.new_playlist_button = Gtk.Button(label="＋ New")
        self.new_playlist_button.add_css_class("sf-button")
        self.new_playlist_button.set_valign(Gtk.Align.CENTER)
        self.new_playlist_button.connect("clicked", self.on_add_playlist)
        head.append(self.new_playlist_button)
        rail_box.append(head)

        self.playlist_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        rail_scroll = Gtk.ScrolledWindow(vexpand=True)
        rail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroll.set_child(self.playlist_list)
        rail_box.append(rail_scroll)
        outer.append(rail_box)

        outer.append(Gtk.Separator())

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        detail.set_margin_start(18)
        detail.set_margin_end(22)
        detail.set_margin_top(20)
        detail.set_margin_bottom(20)
        self.playlist_heading = label(
            "", "sf-heading", ellipsize=ELLIPSIZE_END, max_width_chars=24
        )
        detail.append(self.playlist_heading)
        self.playlist_voice_note = Gtk.Box(spacing=7)
        self.playlist_voice_note.set_halign(Gtk.Align.START)
        detail.append(self.playlist_voice_note)
        self.playlist_tracks = track_list_view(self, self._reorder_playlist)
        detail_scroll = Gtk.ScrolledWindow(vexpand=True)
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_child(self.playlist_tracks)
        detail.append(detail_scroll)
        outer.append(detail)
        return outer

    # --------------------------------------------------------- settings view

    def _build_settings_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(18)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        self.settings_view = scroller

        box.append(self._build_device_summary())
        columns = Gtk.Box(spacing=16, homogeneous=True)
        left = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True
        )
        right = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True
        )
        columns.append(left)
        columns.append(right)
        box.append(columns)

        left.append(self._build_sync_options())
        right.append(self._build_folders_card())
        right.append(self._build_cache_card())
        right.append(self._build_destructive_card())
        return scroller

    def _card(self, title, note=""):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("sf-card")
        head = Gtk.Box(spacing=7)
        head.set_margin_start(14)
        head.set_margin_end(14)
        head.set_margin_top(12)
        head.set_margin_bottom(10)
        head.append(label(title, "sf-row-title"))
        if note:
            head.append(
                label(note, "sf-caption", valign=Gtk.Align.BASELINE, wrap=True)
            )
        card.append(head)
        card.append(Gtk.Separator())
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.append(body)
        return card, body

    def _build_device_summary(self):
        card = Gtk.Box(spacing=20)
        card.add_css_class("sf-card")
        inner = Gtk.Box(spacing=20, hexpand=True)
        inner.set_margin_start(16)
        inner.set_margin_end(16)
        inner.set_margin_top(16)
        inner.set_margin_bottom(16)
        card.append(inner)

        icon = Gtk.Image.new_from_icon_name("multimedia-player-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("sf-dim")
        inner.append(icon)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        name_row = Gtk.Box(spacing=9)
        self.settings_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.settings_dot.add_css_class("sf-dot")
        self.settings_dot.add_css_class("library")
        name_row.append(self.settings_dot)
        self.settings_name = label(
            "", "sf-heading", ellipsize=ELLIPSIZE_END, max_width_chars=20
        )
        name_row.append(self.settings_name)
        self.settings_path = label(
            "",
            "sf-caption",
            "sf-mono",
            valign=Gtk.Align.END,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            max_width_chars=28,
        )
        name_row.append(self.settings_path)
        meta.append(name_row)

        self.settings_meter = StorageMeter(height=9)
        meta.append(self.settings_meter)

        self.settings_figures = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=4,
            min_children_per_line=1,
            column_spacing=18,
            row_spacing=6,
        )
        self.settings_figures.add_css_class("sf-flow")
        meta.append(self.settings_figures)
        inner.append(meta)

        self.sync_button = Gtk.Button(label="Sync")
        self.sync_button.add_css_class("sf-button")
        self.sync_button.add_css_class("accent")
        self.sync_button.set_valign(Gtk.Align.CENTER)
        self.sync_button.connect("clicked", self.on_sync_pending)
        inner.append(self.sync_button)

        self.device_banner = Gtk.Box(spacing=9)
        self.device_banner.add_css_class("sf-warn-card")
        self.device_banner.set_visible(False)
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        wrapper.append(card)
        wrapper.append(self.device_banner)
        return wrapper

    def _build_sync_options(self):
        card, body = self._card(
            "Sync options", "· applied on every sync and rebuild"
        )
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        # Playlist names are stored only as spoken audio, never as text, since
        # the device has no screen. Choosing a grouping therefore implies
        # wanting the names read aloud, so that switch follows along.
        self.playlist_mode = Adw.ComboRow(
            title="Playlist grouping",
            subtitle="How tracks are grouped into playlists when syncing",
            model=Gtk.StringList.new(
                ["None", "One per folder", "By artist", "By genre"]
            ),
        )
        self.playlist_mode.connect("notify::selected", self._on_playlist_mode_changed)
        listbox.append(self.playlist_mode)

        self.track_voiceover = Adw.SwitchRow(
            title="Speak track names",
            subtitle="Announced when you press the VoiceOver button",
        )
        listbox.append(self.track_voiceover)

        self.playlist_voiceover = Adw.SwitchRow(
            title="Speak playlist names",
            subtitle="Without this, playlists cannot be told apart on a "
            "screenless device",
        )
        listbox.append(self.playlist_voiceover)

        for row in (self.track_voiceover, self.playlist_voiceover):
            row.connect("notify::active", self._on_voiceover_changed)

        if not self.speech_engine_available:
            for row in (self.track_voiceover, self.playlist_voiceover):
                row.set_sensitive(False)

        listbox.set_margin_start(14)
        listbox.set_margin_end(14)
        listbox.set_margin_top(10)
        listbox.set_margin_bottom(12)
        body.append(listbox)

        if not self.speech_engine_available:
            warn = Gtk.Box(spacing=9)
            warn.add_css_class("sf-warn-card")
            warn.set_margin_start(14)
            warn.set_margin_end(14)
            warn.set_margin_bottom(12)
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.set_margin_start(11)
            icon.set_margin_top(11)
            icon.set_valign(Gtk.Align.START)
            warn.append(icon)
            text = label(
                "No speech engine installed, so spoken names cannot be "
                "generated. Install pico2wave, espeak or say to enable the "
                "two switches above.",
                "sf-caption",
                wrap=True,
                hexpand=True,
            )
            text.set_margin_end(11)
            text.set_margin_top(11)
            text.set_margin_bottom(11)
            warn.append(text)
            body.append(warn)

        # A plain row of three buttons sets a minimum width the whole window
        # then has to honour, so they wrap instead.
        actions = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=3,
            min_children_per_line=1,
            column_spacing=8,
            row_spacing=8,
        )
        actions.add_css_class("sf-flow")
        actions.set_margin_start(14)
        actions.set_margin_end(14)
        actions.set_margin_bottom(14)
        self.add_button = Gtk.Button(label="Add music folder…")
        self.add_button.add_css_class("sf-button")
        self.add_button.connect("clicked", self.on_add_music)
        actions.append(self.add_button)

        self.playlist_button = Gtk.Button(label="Add playlist file…")
        self.playlist_button.add_css_class("sf-button")
        self.playlist_button.connect("clicked", self.on_add_playlist)
        if self.playlist_unavailable:
            self.playlist_button.set_tooltip_text(self.playlist_unavailable)
        actions.append(self.playlist_button)

        self.youtube_button = Gtk.Button(label="Add from YouTube…")
        self.youtube_button.add_css_class("sf-button")
        self.youtube_button.connect("clicked", self.on_add_youtube)
        # Better to say why than to let the user paste a link and watch the
        # download fail several steps later for a reason the log explains only
        # to someone who already knows what to look for.
        if self.youtube_unavailable:
            self.youtube_button.set_tooltip_text(self.youtube_unavailable)
        actions.append(self.youtube_button)
        body.append(actions)
        return card

    def _build_folders_card(self):
        card, body = self._card("Music folders", "· searched for local results")
        self.folder_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.folder_list)
        add = Gtk.Button(label="＋ Add folder…")
        add.add_css_class("flat")
        add.add_css_class("sf-accent")
        add.set_halign(Gtk.Align.START)
        add.set_margin_start(10)
        add.set_margin_bottom(8)
        add.connect("clicked", self.on_add_folder)
        body.append(add)
        return card

    def _build_cache_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("sf-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_margin_start(14)
        inner.set_margin_end(14)
        inner.set_margin_top(14)
        inner.set_margin_bottom(14)
        card.append(inner)

        head = Gtk.Box(spacing=8)
        head.append(label("Preview cache", "sf-row-title"))
        head.append(
            label(
                "auto-downloaded so you could hear a result",
                "sf-caption",
                valign=Gtk.Align.BASELINE,
                wrap=True,
            )
        )
        inner.append(head)

        row = Gtk.Box(spacing=12)
        self.cache_meter = StorageMeter(height=6)
        row.append(self.cache_meter)
        self.cache_figure = label("0 B · 0 files", "sf-caption", "sf-mono")
        row.append(self.cache_figure)
        self.cache_clear = Gtk.Button(label="Clear")
        self.cache_clear.add_css_class("sf-button")
        self.cache_clear.set_sensitive(False)
        self.cache_clear.connect("clicked", self.on_clear_cache)
        row.append(self.cache_clear)
        inner.append(row)

        inner.append(
            label(
                f"Adding a previewed track moves it into "
                f"{home_relative(YOUTUBE_LIBRARY)} and out of this cache. Past "
                f"{human_size(PREVIEW_CACHE_LIMIT)} the oldest previews are "
                "dropped to make room; nothing here is on your iPod.",
                "sf-caption",
                wrap=True,
            )
        )
        return card

    def _populate_cache_card(self):
        previews = self.library.previews
        total = sum(track.size for track in previews)
        self.cache_figure.set_text(
            f"{human_size(total)} · {plural(len(previews), 'file')}"
        )
        self.cache_meter.set_fractions(
            total / PREVIEW_CACHE_LIMIT if PREVIEW_CACHE_LIMIT else 0.0, 0.0
        )
        self.cache_clear.set_sensitive(bool(previews) and not self.busy)

    def on_clear_cache(self, _button):
        """Throw the whole cache away.

        No confirmation, unlike everything in the destructive card: nothing
        here is on the iPod, nothing here is in a music folder, and every one
        of these files can be downloaded again. The tree is removed rather than
        the tracks that are listed, so anything a half-finished download left
        behind goes with them.
        """
        previews = self.library.previews
        freed = sum(track.size for track in previews)
        removed = len(previews)
        # Everything in the cache is about to go, including whatever the bar is
        # playing out of it and anything queued behind that.
        self.player.forget(track.path for track in previews)
        # Generation as well as the process: a download that finished a moment
        # ago has already handed its result to the main loop, and adding that
        # file back would leave a track in the grid that was just deleted.
        self._supersede_preview_fetch()
        try:
            shutil.rmtree(PREVIEW_CACHE)
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._toast(f"Could not clear the preview cache: {exc}")
            return
        self.library.previews = []
        self._populate_cache_card()
        self._refresh_current_view()
        self._toast(
            f"Cleared {plural(removed, 'preview')} · {human_size(freed)} freed"
        )

    def _build_destructive_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        card.add_css_class("sf-danger-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        inner.set_margin_start(14)
        inner.set_margin_end(14)
        inner.set_margin_top(14)
        inner.set_margin_bottom(14)
        card.append(inner)
        inner.append(label("Destructive", "sf-row-title", "sf-alert"))

        rebuild = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label("Rebuild database", "sf-row-title"))
        text.append(
            label(
                "Re-scan the iPod if tracks are not playing", "sf-caption", wrap=True
            )
        )
        rebuild.append(text)
        self.rebuild_button = Gtk.Button(label="Rebuild")
        self.rebuild_button.add_css_class("sf-button")
        self.rebuild_button.set_valign(Gtk.Align.CENTER)
        self.rebuild_button.connect("clicked", self.on_rebuild)
        rebuild.append(self.rebuild_button)
        inner.append(rebuild)

        wipe = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label("Wipe iPod", "sf-row-title"))
        self.wipe_note = label("", "sf-caption", wrap=True)
        text.append(self.wipe_note)
        wipe.append(text)
        self.wipe_button = Gtk.Button(label="Wipe…")
        self.wipe_button.add_css_class("sf-button")
        self.wipe_button.add_css_class("danger")
        self.wipe_button.set_valign(Gtk.Align.CENTER)
        self.wipe_button.connect("clicked", self.on_wipe)
        wipe.append(self.wipe_button)
        inner.append(wipe)

        eject = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label("Eject", "sf-row-title"))
        text.append(
            label("Unmount safely before unplugging", "sf-caption", wrap=True)
        )
        eject.append(text)
        self.eject_button = Gtk.Button(label="⏏ Eject")
        self.eject_button.add_css_class("sf-button")
        self.eject_button.set_valign(Gtk.Align.CENTER)
        self.eject_button.connect("clicked", self.on_eject)
        eject.append(self.eject_button)
        inner.append(eject)
        return card

    # ------------------------------------------------------------ bottom bars

    def _build_sync_bar(self):
        self.sync_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP
        )
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("sf-sync-bar")
        self.sync_revealer.set_child(outer)

        row = Gtk.Box(spacing=14)
        row.set_size_request(-1, 52)
        row.set_margin_start(18)
        row.set_margin_end(18)

        self.sync_spinner = Gtk.Spinner()
        self.sync_spinner.set_valign(Gtk.Align.CENTER)
        row.append(self.sync_spinner)

        self.sync_title = label("Working", "sf-row-title", valign=Gtk.Align.CENTER)
        self.sync_title.set_size_request(150, -1)
        row.append(self.sync_title)

        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        self.progress.set_size_request(-1, 5)
        row.append(self.progress)

        self.sync_count = label("", "sf-caption", "sf-mono", valign=Gtk.Align.CENTER)
        row.append(self.sync_count)

        self.sync_current = label(
            "", "sf-body", hexpand=True, ellipsize=ELLIPSIZE_END, valign=Gtk.Align.CENTER
        )
        row.append(self.sync_current)

        self.details_toggle = Gtk.ToggleButton(label="Details")
        self.details_toggle.add_css_class("sf-button")
        self.details_toggle.set_valign(Gtk.Align.CENTER)
        self.details_toggle.connect(
            "toggled", lambda b: self.details_revealer.set_reveal_child(b.get_active())
        )
        row.append(self.details_toggle)
        outer.append(row)

        self.details_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP
        )
        details = Gtk.Box(spacing=0)
        details.set_size_request(-1, 250)
        details.append(Gtk.Separator())

        file_scroll = Gtk.ScrolledWindow(hexpand=True)
        file_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sync_file_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.sync_file_list.set_margin_start(18)
        self.sync_file_list.set_margin_end(18)
        self.sync_file_list.set_margin_top(10)
        self.sync_file_list.set_margin_bottom(10)
        file_scroll.set_child(self.sync_file_list)
        details.append(file_scroll)

        details.append(Gtk.Separator())
        log_side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        log_side.set_size_request(340, -1)
        log_head = Gtk.Box(spacing=9)
        log_head.set_margin_start(14)
        log_head.set_margin_end(14)
        log_head.set_margin_top(9)
        log_head.set_margin_bottom(9)
        log_head.append(label("SCRIPT OUTPUT", "sf-section-label", hexpand=True))
        copy = Gtk.Button(label="Copy")
        copy.add_css_class("flat")
        copy.add_css_class("sf-caption")
        copy.connect("clicked", self.on_copy_log)
        log_head.append(copy)
        log_side.append(log_head)
        log_side.append(Gtk.Separator())

        self.log_view = Gtk.TextView(
            editable=False,
            monospace=True,
            cursor_visible=False,
            # The lines carry mount points and library paths, which are long
            # enough that without wrapping the interesting half of the message
            # sits off the right edge behind a scrollbar nobody drags.
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=14,
            right_margin=14,
            top_margin=10,
            bottom_margin=10,
        )
        self.log_view.add_css_class("sf-log")
        log_scroll = Gtk.ScrolledWindow(vexpand=True)
        log_scroll.set_child(self.log_view)
        log_side.append(log_scroll)
        details.append(log_side)

        self.details_revealer.set_child(details)
        outer.append(self.details_revealer)
        return self.sync_revealer

    def _build_now_playing_bar(self):
        """The transport, which previews on this computer and never on the iPod.

        All four states share these widgets - nothing playing, a track still
        opening, a file from the library and a previewed download - and differ
        only in what the labels say and what is sensitive. Building them once
        keeps the bar exactly as tall in every state, so the window does not
        resize under the pointer at the moment a track starts.
        """
        bar = Gtk.Box(spacing=16)
        bar.add_css_class("sf-bottom-bar")
        bar.set_size_request(-1, 84)
        bar.set_margin_start(18)
        bar.set_margin_end(18)

        left = Gtk.Box(spacing=11)
        left.set_size_request(150, -1)
        self.playing_art = Gtk.Box(valign=Gtk.Align.CENTER)
        self.playing_art.set_size_request(52, 52)
        left.append(self.playing_art)

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2
        )
        self.playing_title = label(
            "Nothing playing", "sf-row-title", "sf-dim", ellipsize=ELLIPSIZE_END
        )
        self.playing_subtitle = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        self.playing_state_dot = state_dot(STATE_LIBRARY)
        self.playing_subtitle.append(self.playing_state_dot)
        self.playing_artist = label("", "sf-body", ellipsize=ELLIPSIZE_END)
        self.playing_subtitle.append(self.playing_artist)
        self.playing_subtitle.set_visible(False)
        for line in (self.playing_title, self.playing_artist):
            # Capped rather than merely ellipsized: an ellipsizing label still
            # asks for its whole natural width, so one long title would set
            # the width of this column and squeeze the transport out of the
            # middle of the bar.
            line.set_max_width_chars(22)
            line.set_width_chars(0)
        text.append(self.playing_title)
        text.append(self.playing_subtitle)
        left.append(text)
        bar.append(left)

        # A stack rather than a box whose children are hidden in turn: when
        # preview playback is impossible or a file will not decode, the reason
        # belongs in place of the controls it is explaining, and the stack
        # keeps that sentence from being any taller than the transport it
        # replaces.
        self.playing_stack = Gtk.Stack(hexpand=True, valign=Gtk.Align.CENTER)
        transport = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=7, hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        controls = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        self.transport_buttons = {}
        for key, icon in (
            ("previous", "media-skip-backward-symbolic"),
            ("play", "media-playback-start-symbolic"),
            ("next", "media-skip-forward-symbolic"),
        ):
            button = Gtk.Button(icon_name=icon)
            button.add_css_class("flat")
            if key == "play":
                button.add_css_class("circular")
            button.set_sensitive(False)
            button.connect("clicked", getattr(self, f"on_{key}_clicked"))
            controls.append(button)
            self.transport_buttons[key] = button
        transport.append(controls)

        seek = Gtk.Box(spacing=10, halign=Gtk.Align.FILL, hexpand=True)
        self.seek_elapsed = label("0:00", "sf-caption", "sf-mono")
        seek.append(self.seek_elapsed)
        self.seek_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 1, 0.01
        )
        self.seek_scale.set_draw_value(False)
        # A request rather than a fixed width, so the bar can still be laid
        # out when the window is dragged down to its minimum.
        self.seek_scale.set_size_request(160, -1)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.set_sensitive(False)
        self.seek_scale.connect("change-value", self._on_seek)
        seek.append(self.seek_scale)
        self.seek_total = label("0:00", "sf-caption", "sf-mono")
        seek.append(self.seek_total)
        transport.append(seek)
        self.playing_stack.add_named(transport, "transport")

        self.playing_message = label(
            "", "sf-body", wrap=True, justify=Gtk.Justification.CENTER, xalign=0.5
        )
        self.playing_stack.add_named(self.playing_message, "message")
        bar.append(self.playing_stack)

        right = Gtk.Box(spacing=12, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        right.set_size_request(150, -1)
        # Ellipsized but uncapped: the minimum width of an ellipsizing label is
        # tiny, so this shrinks away gracefully without widening the window.
        self.playing_status = label(
            "Preview on this computer", "sf-caption", ellipsize=ELLIPSIZE_END
        )
        right.append(self.playing_status)
        bar.append(right)

        self.now_playing_bar = bar
        self._update_now_playing()
        return bar

    # ------------------------------------------------------------- playback

    def play_from(self, view, track):
        """Play one track, queueing the list it was clicked in behind it.

        The queue comes from the view's model rather than from the library, so
        next moves through the album or playlist on screen and in the order it
        is displayed in, which is what the user just sorted it into.
        """
        # Whatever was being fetched is no longer what the bar is for. Without
        # this it would arrive several seconds later and take the bar back off
        # the track the user has just started.
        self._supersede_preview_fetch()
        model = view.get_model()
        tracks = [model.get_item(i).track for i in range(model.get_n_items())]
        for index, candidate in enumerate(tracks):
            if candidate is track:
                self.player.play(tracks, index)
                return
        # Clicked in a view that has already been repainted out from under the
        # row. Playing the one track alone is better than refusing.
        self.player.play([track], 0)

    def preview_result(self, result):
        """Hear a YouTube result, downloading it into the cache if need be."""
        if self.preview_unavailable:
            return
        cached = cached_preview_path(result.video_id, PREVIEW_CACHE)
        if cached is not None:
            self._supersede_preview_fetch()
            self.player.play([self._preview_track(cached)], 0)
            return
        if self.youtube_unavailable:
            return
        self._start_preview_fetch(result)

    def _preview_track(self, path):
        """The Track for a cached file, indexing it if the scan has not.

        A file can be in the cache without being in the list - it was
        downloaded while the scan that would have found it was already
        running - and refusing to play it then would be refusing to play a
        file that is right there.
        """
        path = str(path)
        for track in self.library.previews:
            if track.path == path:
                return track
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        track = Track(path, {"title": Path(path).stem, "size": size}, STATE_PREVIEW)
        self.library.previews.append(track)
        return track

    def _start_preview_fetch(self, result):
        """Download one result into the cache, then play it.

        Deliberately not run through _run: that marks the whole window busy for
        the several seconds a download takes, and previewing needs neither an
        iPod attached nor the rest of the window to stop working. The only
        thing it locks is the bar it is filling.
        """
        self._supersede_preview_fetch()
        generation = self.preview_generation
        # Named from the search result, so the bar says what is arriving rather
        # than staying blank until it has. Replaced by the real thing, tags and
        # all, once the file is on disk.
        pending = Track(
            "",
            {
                "title": result.title,
                "artist": result.uploader,
                "duration": result.duration,
            },
            STATE_PREVIEW,
        )
        self.player.fetch(pending)
        threading.Thread(
            target=self._preview_fetch_worker,
            args=(result, generation, pending),
            daemon=True,
        ).start()

    def _preview_fetch_worker(self, result, generation, pending):
        """Fetch one result into the cache. Runs off the main loop."""
        try:
            PREVIEW_INCOMING.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=PREVIEW_INCOMING))
        except OSError as exc:
            GLib.idle_add(
                self._fail_preview_fetch,
                generation,
                pending,
                f"Could not write to the preview cache: {exc}",
            )
            return
        try:
            self._run_preview_fetch(
                fetch_command(result.url, staging), generation, pending, staging
            )
        finally:
            # Whatever it left behind goes with it, finished or interrupted.
            # A partial download kept under .incoming would be counted by
            # nothing and cleared by nothing short of emptying the cache.
            shutil.rmtree(staging, ignore_errors=True)

    def _run_preview_fetch(self, command, generation, pending, staging):
        """Run the download and hand what it produced back to the main loop."""
        launch_error = None
        with self._preview_lock:
            if generation != self.preview_generation or self._preview_closed:
                return
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                launch_error = exc
            else:
                self._preview_process = proc
        if launch_error is not None:
            GLib.idle_add(self._log, f"failed to run: {launch_error}\n")
            GLib.idle_add(
                self._fail_preview_fetch, generation, pending, PREVIEW_FAILED
            )
            return
        if proc.stdout is not None:
            for line in proc.stdout:
                GLib.idle_add(self._log, line)
        proc.wait()
        with self._preview_lock:
            if self._preview_process is proc:
                self._preview_process = None
            stale = (
                generation != self.preview_generation or self._preview_closed
            )
        if stale:
            return

        # The staging directory is this run's manifest: it was empty, and
        # nothing else writes to it. That is more reliable than parsing what
        # yt-dlp reported, which older versions cannot report at all.
        produced = sorted(
            path
            for path in staging.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        )
        if not produced:
            GLib.idle_add(
                self._fail_preview_fetch, generation, pending, PREVIEW_FAILED
            )
            return

        source = produced[0]
        destination = PREVIEW_CACHE / source.relative_to(staging)
        move_error = None
        with self._preview_lock:
            if generation != self.preview_generation or self._preview_closed:
                return
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            except OSError as exc:
                move_error = exc
        if move_error is not None:
            GLib.idle_add(
                self._fail_preview_fetch,
                generation,
                pending,
                f"Could not move the preview into the cache: {move_error}",
            )
            return

        # Read here rather than on the main loop: the tags come from a second
        # process, and the whole point of downloading off the main loop is not
        # to hand it the slow half afterwards.
        records, _complete, _skipped = scan_tracks(files=[str(destination)])
        GLib.idle_add(
            self._finish_preview_fetch,
            generation,
            str(destination),
            records[0] if records else {},
        )

    def _finish_preview_fetch(self, generation, path, record):
        if generation != self.preview_generation:
            return False
        track = Track(path, record, STATE_PREVIEW)
        self.library.previews = [
            existing for existing in self.library.previews if existing.path != path
        ] + [track]
        # Kept out of the pruning it triggers: it is over the limit only
        # because it just arrived, and dropping the file the user is waiting
        # to hear would be the one deletion they would notice.
        self._prune_preview_cache(keep=[path])
        self._populate_cache_card()
        self._refresh_current_view()
        self.player.play([track], 0)
        return False

    def _fail_preview_fetch(self, generation, track, message):
        if generation != self.preview_generation:
            return False
        self.player.fail(track, message)
        return False

    def _supersede_preview_fetch(self):
        """Disown a download because something else is taking the bar."""
        with self._preview_lock:
            self.preview_generation += 1
            proc = self._preview_process
            self._preview_process = None
        self._terminate_preview_process(proc)

    def _cancel_preview_fetch(self):
        """Stop a download whose result nothing is waiting for any more."""
        with self._preview_lock:
            proc = self._preview_process
            self._preview_process = None
        self._terminate_preview_process(proc)

    @staticmethod
    def _terminate_preview_process(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass

    def _prune_preview_cache(self, keep=()):
        """Drop the oldest previews once the cache has passed its limit."""
        playing = self.player.track
        protected = list(keep) + ([playing.path] if playing else [])
        entries = preview_cache_entries(PREVIEW_CACHE)
        dropped = set()
        for path in prunable_previews(entries, PREVIEW_CACHE_LIMIT, protected):
            try:
                path.unlink()
            except OSError:
                continue
            dropped.add(str(path))
            self._forget_empty_preview_folders(path.parent)
        if dropped:
            self.library.previews = [
                track for track in self.library.previews if track.path not in dropped
            ]
            self.player.forget(dropped)

    @staticmethod
    def _forget_empty_preview_folders(folder):
        """Take the artist folder with the last preview that left it."""
        folder = Path(folder)
        while PREVIEW_CACHE in folder.parents:
            try:
                folder.rmdir()
            except OSError:
                return
            folder = folder.parent

    def _promote_preview(self, track):
        """Keep a previewed track: out of the cache, into the library.

        Add means the same thing here as everywhere else - this is a track I
        want - but a previewed file lives in a cache that gets pruned, so
        keeping it has to move it before anything is queued.
        """
        if track.state != STATE_PREVIEW:
            return
        source = Path(track.path)
        if not source.is_file():
            # Pruned, or cleared from another window, between the row being
            # drawn and the button being pressed. Saying so beats an error
            # about a path the user never saw.
            self.library.previews = [
                existing for existing in self.library.previews if existing is not track
            ]
            self._populate_cache_card()
            self._refresh_current_view()
            self._toast("That preview is no longer in the cache")
            return
        destination = promote_destination(source, PREVIEW_CACHE, YOUTUBE_LIBRARY)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # The same video, downloaded directly on an earlier day. The
                # copy already in the library is the one to keep; overwriting
                # it with the cached duplicate would gain nothing and could
                # lose tags edited since.
                source.unlink()
            else:
                shutil.move(str(source), str(destination))
        except OSError as exc:
            self._toast(f"Could not move the preview into your library: {exc}")
            return
        self._forget_empty_preview_folders(source.parent)

        track.path = str(destination)
        track.relpath = str(destination)
        track.state = STATE_LIBRARY
        track.on_ipod = False
        self.library.previews = [
            existing for existing in self.library.previews if existing is not track
        ]
        # Into the scan's own index as well, so that a library scan finishing
        # afterwards does not drop the track that was just kept.
        self._library_scan_tracks[track.path] = track
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        self._populate_cache_card()
        self._update_now_playing()

        kept = f"Kept in {home_relative(YOUTUBE_LIBRARY)}"
        if self.mount_point and self.device_identity is not None:
            queued = self._queue_sources({track.path: [track]}, show_toast=False)
            self._toast(f"{kept} and queued for sync" if queued else kept)
            return
        self._refresh_current_view()
        self._toast(kept)

    def on_play_clicked(self, _button):
        self.player.toggle()

    def on_previous_clicked(self, _button):
        self.player.previous()

    def on_next_clicked(self, _button):
        self.player.next()

    def _on_seek(self, scale, _scroll, value):
        """A drag or a keypress on the timeline.

        GtkRange emits change-value for user input only and never for
        set_value, so the poll that walks the thumb along cannot be mistaken
        for a seek. Handled here rather than by the default handler because the
        player is the one that decides where the thumb ends up.
        """
        fraction = max(0.0, min(1.0, value))
        scale.set_value(fraction)
        self.player.seek(fraction)
        return True

    def _update_now_playing(self):
        """Repaint the bar from the player. Cheap enough to run per poll."""
        player = self.player
        track = player.track
        loaded = track is not None and player.state != PLAY_IDLE

        painted_art = (
            None if track is None else (track.path, track.state, track.art)
        )
        if self._painted_art != painted_art:
            self._painted_art = painted_art
            child = self.playing_art.get_first_child()
            if child is not None:
                self.playing_art.remove(child)
            if track is None:
                art = label("♪", "sf-idle-art", xalign=0.5, yalign=0.5)
                art.set_size_request(52, 52)
            else:
                art = make_cover(track.art, 52, track.album)
                if track.state == STATE_PREVIEW:
                    # The same dimming a previewed album card gets, so "this is
                    # only here so you could hear it" reads the same everywhere.
                    art.set_opacity(0.6)
            self.playing_art.append(art)

        if track is None:
            self.playing_title.set_text("Nothing playing")
            self.playing_title.add_css_class("sf-dim")
            self.playing_subtitle.set_visible(False)
        else:
            self.playing_title.set_text(track.title)
            self.playing_title.remove_css_class("sf-dim")
            self.playing_artist.set_text(track.artist)
            for name in STATE_LABELS:
                self.playing_state_dot.remove_css_class(name)
            self.playing_state_dot.add_css_class(track.state)
            self.playing_subtitle.set_visible(True)

        message = player.error or self.preview_unavailable
        if message:
            self.playing_message.set_text(message)
            self.playing_stack.set_visible_child_name("message")
        else:
            self.playing_stack.set_visible_child_name("transport")

        playing = player.state == PLAY_PLAYING
        # A download has nothing behind it to drive yet, so the transport reads
        # as the idle one until the file lands. The title and the "Fetching
        # preview…" caption are what say the bar is busy.
        ready = loaded and player.state != PLAY_FETCHING
        self.transport_buttons["play"].set_icon_name(
            "media-playback-pause-symbolic" if playing
            else "media-playback-start-symbolic"
        )
        self.transport_buttons["play"].set_sensitive(
            ready or (bool(player.queue) and player.state != PLAY_FETCHING)
        )
        self.transport_buttons["previous"].set_sensitive(ready)
        self.transport_buttons["next"].set_sensitive(
            ready and player.index + 1 < len(player.queue)
        )
        # Dimmed while there is nothing to drive, which is the design's idle
        # bar: insensitive controls alone still read as controls you could use.
        self.playing_stack.set_opacity(1.0 if ready else 0.32)

        seekable = player.seekable
        self.seek_scale.set_sensitive(seekable)
        self.seek_scale.set_value(
            min(1.0, player.position / player.duration) if seekable else 0.0
        )
        self.seek_elapsed.set_text(human_duration(player.position))
        if player.duration > 0:
            self.seek_total.set_text(human_duration(player.duration))
        else:
            # Unknown only once there is a track whose length has not been
            # worked out yet. An idle bar knows the length perfectly well:
            # there is nothing playing, and "--:--" there reads as a fault.
            self.seek_total.set_text("--:--" if track is not None else "0:00")
        self.playing_status.set_text(self._playing_status())

    def _playing_status(self):
        """The one line on the right, which says what kind of playback this is.

        Preview playback is easy to mistake for the device playing something,
        and a previewed download is easy to mistake for a track already kept,
        so the distinction is stated rather than implied by a dot.
        """
        player = self.player
        if self.preview_unavailable or player.error:
            # The middle of the bar is already carrying the reason; repeating a
            # truncated copy of it here would only compete with it.
            return ""
        if player.state == PLAY_FETCHING:
            return "Fetching preview…"
        if player.state == PLAY_LOADING:
            # Opening, not fetching, even for a preview: by this point the file
            # is on disk and this is the same second a library track takes.
            return "Opening…"
        if player.track is not None and player.track.state == STATE_PREVIEW:
            return "Previewed - add to keep"
        return "Preview on this computer"

    # ---------------------------------------------------------------- state

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

    def refresh(self):
        """Re-detect the iPod and repaint. Safe to call from the main loop."""
        if self.busy:
            return False

        candidates = find_ipods()
        if len(candidates) != 1:
            self._select_mount(None)
            self.playlists = []
            self.spoken = set()
            self.current_playlist = None
            if len(candidates) > 1:
                self._populate_disconnected_summary(
                    "Multiple iPods connected. Disconnect all but the one you "
                    "want to manage.",
                    False,
                )
            else:
                self._populate_disconnected_summary(
                    "No iPod connected. Plug one in, or mount it if it is "
                    "already attached.",
                    True,
                )
            self._populate_playlist_rail()
            self.stack.set_visible_child_name("device")
            return False

        self._select_mount(candidates[0])
        self.stack.set_visible_child_name("device")
        self._load_sync_options()
        self.playlists = list_playlists(self.mount_point)
        self.spoken = spoken_playlists(self.mount_point)
        self._populate_device_summary()
        self._populate_playlist_rail()
        self._load_device_tracks_async()
        return False

    def _select_mount(self, mount_point):
        identity = volume_identity(mount_point) if mount_point is not None else None
        if mount_point == self.mount_point and identity == self.device_identity:
            return
        if self.discovering_sources and identity != self.device_identity:
            self.source_generation += 1
            self.discovering_sources = False
        self.tag_generation += 1
        self.mount_point = mount_point
        self.device_identity = identity
        self._device_scan_tracks = {}
        self.device_tracks = []
        self.track_names = {}
        self._device_scan_active = False
        self._device_snapshot_ready = False
        if (
            mount_point is not None
            and self.pending_sources
            and self.pending_device_identity != identity
        ):
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_skipped_symlinks.clear()
            self.pending_device_identity = None
            self._toast(
                "Queued changes were discarded because a different iPod was connected"
            )
        self._merge_states()
        self._refresh_current_view()

    def _populate_disconnected_summary(self, message, offer_mount):
        self.device_name.set_text("No iPod")
        self.settings_name.set_text("No iPod connected")
        self.settings_path.set_text(message)
        self.device_dot.remove_css_class("ipod")
        self.device_dot.add_css_class("library")
        self.settings_dot.remove_css_class("ipod")
        self.settings_dot.add_css_class("library")
        self.device_free.set_text("Not connected")
        self.device_count.set_text("")
        self.wipe_note.set_text("Connect an iPod before using device controls.")
        for meter in (self.sidebar_meter, self.settings_meter):
            meter.set_fractions(0, 0, False)
        self._set_settings_figures(None, 0, 0, False)

        child = self.device_banner.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.device_banner.remove(child)
            child = nxt
        warning = label(message, "sf-caption", wrap=True, hexpand=True)
        warning.set_margin_start(11)
        warning.set_margin_top(10)
        warning.set_margin_bottom(10)
        self.device_banner.append(warning)
        if offer_mount:
            mount = Gtk.Button(label="Mount Connected iPod")
            mount.add_css_class("sf-button")
            mount.set_margin_end(10)
            mount.set_valign(Gtk.Align.CENTER)
            mount.connect("clicked", self.on_mount_clicked)
            self.device_banner.append(mount)
        self.device_banner.set_visible(True)

        _tracks, changes, queued_bytes = self._pending_accounting()
        if self.pending_sources:
            self.queued_row.set_visible(True)
            self.queued_label.set_text(
                f"{human_size(queued_bytes)} queued{self._pending_symlink_note()} "
                "— reconnect the same iPod"
            )
            self.sync_button.set_label(
                f"Sync {plural(changes, 'change')}"
            )
        else:
            self.queued_row.set_visible(False)
            self.sync_button.set_label("Nothing queued")
        self._update_device_controls()

    def _populate_device_summary(self):
        name = Path(self.mount_point).name
        self.device_name.set_text(name)
        self.settings_name.set_text(name)
        self.settings_path.set_text(self.mount_point)
        self.device_dot.remove_css_class("library")
        self.device_dot.add_css_class("ipod")
        self.settings_dot.remove_css_class("library")
        self.settings_dot.add_css_class("ipod")
        self.device_banner.set_visible(False)

        total_tracks = count_tracks(self.mount_point)
        self.device_count.set_text(plural(total_tracks, "track"))
        self.wipe_note.set_text(
            f"Removes all {plural(total_tracks, 'track')}. Filenames on the device are "
            "scrambled codes, so back up first."
        )

        _tracks, changes, queued_bytes = self._pending_accounting()
        try:
            usage = shutil.disk_usage(self.mount_point)
            used_fraction = usage.used / usage.total if usage.total else 0
            queued_fraction = queued_bytes / usage.total if usage.total else 0
            over = queued_bytes > usage.free
            self.device_free.set_text(f"{human_size(usage.free)} free")
            for meter in (self.sidebar_meter, self.settings_meter):
                meter.set_fractions(used_fraction, queued_fraction, over)
            self._set_settings_figures(usage, queued_bytes, total_tracks, over)
        except OSError:
            self.device_free.set_text("size unknown")
            self._set_settings_figures(None, queued_bytes, total_tracks, False)

        if self.pending_sources:
            self.queued_row.set_visible(True)
            self.queued_label.set_text(
                f"+{human_size(queued_bytes)} queued to sync"
                f"{self._pending_symlink_note()}"
            )
            self.sync_button.set_label(f"Sync {plural(changes, 'change')}")
            self.sync_button.set_sensitive(not self.busy)
        else:
            self.queued_row.set_visible(False)
            self.sync_button.set_label("Nothing queued")
            self.sync_button.set_sensitive(False)
        self._update_device_controls()

    def _update_device_controls(self):
        connected = bool(self.mount_point)
        enabled = connected and not self.busy
        queue_enabled = (
            enabled
            and self.device_identity is not None
            and not self.discovering_sources
        )
        self.add_button.set_sensitive(queue_enabled)
        self.playlist_button.set_sensitive(
            queue_enabled and self.speech_engine_available
        )
        self.youtube_button.set_sensitive(
            queue_enabled and not self.youtube_unavailable
        )
        # Collected as they are built rather than repainted from here: a
        # repaint would discard the skeleton mid-search, and a device
        # appearing while YouTube is still answering is exactly when that
        # happens.
        downloadable = self._can_download()
        for button in self.search_add_buttons:
            button.set_sensitive(downloadable)
            button.set_tooltip_text(self._youtube_download_tooltip())
        self.new_playlist_button.set_sensitive(
            queue_enabled and self.speech_engine_available
        )
        self.rebuild_button.set_sensitive(enabled)
        self.wipe_button.set_sensitive(enabled)
        self.eject_button.set_sensitive(enabled)
        self.sidebar_eject.set_sensitive(enabled)
        self.playlist_mode.set_sensitive(enabled)
        self.track_voiceover.set_sensitive(enabled and self.speech_engine_available)
        self.playlist_voiceover.set_sensitive(
            enabled and self.speech_engine_available
        )
        accounting_ready = (
            not self._device_scan_active or self._device_snapshot_ready
        )
        self.sync_button.set_sensitive(
            queue_enabled and accounting_ready and bool(self.pending_sources)
        )

    def _set_settings_figures(self, usage, queued_bytes, total_tracks, over):
        child = self.settings_figures.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.settings_figures.remove(child)
            child = nxt

        def figure(value, suffix, *classes):
            box = Gtk.Box(spacing=5)
            box.append(label(value, "sf-row-title", *classes))
            box.append(label(suffix, "sf-body"))
            return box

        if usage is not None:
            self.settings_figures.append(figure(human_size(usage.used), "used"))
            if queued_bytes:
                self.settings_figures.append(
                    figure(
                        human_size(queued_bytes),
                        "queued",
                        "sf-alert" if over else "sf-accent",
                    )
                )
            self.settings_figures.append(
                figure(human_size(usage.free), f"free of {human_size(usage.total)}")
            )
        self.settings_figures.append(
            label(
                plural(total_tracks, "track")
                + " · "
                + plural(len(self.playlists), "playlist"),
                "sf-caption",
                "sf-mono",
                valign=Gtk.Align.CENTER,
            )
        )

    def _populate_playlist_rail(self):
        for container in (self.playlist_rail, self.playlist_list):
            child = container.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                container.remove(child)
                child = nxt

        for name, entries in self.playlists:
            self.playlist_rail.append(self._rail_row(name, entries, compact=True))
            self.playlist_list.append(self._rail_row(name, entries, compact=False))

        playlist_names = {name for name, _entries in self.playlists}
        if not self.playlists:
            self.current_playlist = None
            self._clear_playlist_detail()
        elif self.current_playlist not in playlist_names:
            self.current_playlist = self.playlists[0][0]
        if self.current_playlist is not None:
            self._show_playlist(self.current_playlist)
        self._populate_playlist_shelf()

    def _rail_row(self, name, entries, compact):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-nav-row")
        row = Gtk.Box(spacing=9)
        row.append(make_cover(None, 22 if compact else 34, name, "tiny"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(label(name, ellipsize=ELLIPSIZE_END))
        if not compact:
            text.append(label(plural(len(entries), "track"), "sf-caption"))
        row.append(text)
        spoken = name.lower() in self.spoken
        marker = label("◉" if spoken else "◌", "sf-accent" if spoken else "sf-dim")
        marker.set_tooltip_text(
            "Spoken name available" if spoken else "No spoken name on the device"
        )
        row.append(marker)
        button.set_child(row)
        button.connect("clicked", lambda _b, n=name: self._select_playlist(n))
        return button

    def _reorder_playlist(self, source, target):
        """Move a track within the playlist and write the new order out.

        The order is the playlist, so it has to survive the app closing: the
        list on the device is rewritten and the database rebuilt, rather than
        the change living only in this window.
        """
        if self.busy or self.current_playlist is None:
            return False
        if source == target:
            return True

        entries = list(dict(self.playlists).get(self.current_playlist, []))
        if not 0 <= source < len(entries) or not 0 <= target < len(entries):
            return False
        moved = entries.pop(source)
        entries.insert(target, moved)

        path = playlist_file(self.mount_point, self.current_playlist)
        if path is None or not self._confirmed_device(self.device_identity):
            self._toast("Could not rewrite the playlist on the device")
            return False
        if not write_playlist(
            self.mount_point, self.device_identity, path, entries
        ):
            self._toast("Could not rewrite the playlist on the device")
            return False

        self.playlists = [
            (name, entries if name == self.current_playlist else current)
            for name, current in self.playlists
        ]
        self._show_playlist(self.current_playlist)
        # The device reads the order out of its database, not out of the file,
        # so without this the reorder would show here and nowhere else.
        self._run(
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                "--rebuild-only",
                *self._sync_options(),
            ],
            "Saving playlist order",
            "Playlist reordered",
        )
        return True

    def _select_playlist(self, name):
        self.current_playlist = name
        self._show_playlist(name)
        self.show_view("playlists")

    def _populate_playlist_shelf(self):
        child = self.playlist_shelf.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.playlist_shelf.remove(child)
            child = nxt

        self.shelf_section.set_visible(bool(self.playlists))
        for name, _entries in self.playlists[:5]:
            tile = Gtk.Button()
            tile.add_css_class("flat")
            tile.add_css_class("sf-card")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.append(make_cover(None, 128, name))
            box.append(label(name, "sf-row-title", ellipsize=ELLIPSIZE_END))
            spoken = name.lower() in self.spoken
            note = Gtk.Box(spacing=6)
            note.append(label("◉" if spoken else "◌", "sf-accent" if spoken else "sf-dim"))
            note.append(
                label("spoken name" if spoken else "no spoken name", "sf-caption")
            )
            box.append(note)
            tile.set_child(box)
            tile.connect("clicked", lambda _b, n=name: self._select_playlist(n))
            self.playlist_shelf.append(tile)

        new_tile = Gtk.Button()
        new_tile.add_css_class("sf-new-tile")
        new_tile.set_size_request(150, -1)
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=7,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        inner.append(label("＋", xalign=0.5))
        inner.append(label("New playlist", "sf-row-title", xalign=0.5))
        new_tile.set_child(inner)
        new_tile.connect("clicked", self.on_add_playlist)
        new_tile.set_sensitive(
            bool(self.mount_point)
            and self.device_identity is not None
            and self.speech_engine_available
            and not self.busy
            and not self.discovering_sources
        )
        self.playlist_shelf.append(new_tile)

    def _show_playlist(self, name):
        entries = dict(self.playlists).get(name, [])
        self.playlist_heading.set_text(name)

        child = self.playlist_voice_note.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.playlist_voice_note.remove(child)
            child = nxt
        spoken = name.lower() in self.spoken
        self.playlist_voice_note.append(
            label("◉" if spoken else "◌", "sf-accent" if spoken else "sf-dim")
        )
        self.playlist_voice_note.append(
            label(
                plural(len(entries), "track")
                + " · "
                + (
                    "the device can announce this playlist"
                    if spoken
                    else "no spoken name, so the device cannot announce it"
                ),
                "sf-body",
                wrap=True,
                max_width_chars=34,
            )
        )
        remove = Gtk.Button(label="Remove playlist")
        remove.add_css_class("sf-button")
        remove.set_margin_start(12)
        remove.connect("clicked", self.on_remove_playlist, name)
        remove.set_sensitive(
            bool(self.mount_point) and self.device_identity is not None and not self.busy
        )
        self.playlist_voice_note.append(remove)

        by_relpath = {track.relpath: track for track in self.device_tracks}
        resolved = []
        for relpath in entries:
            track = by_relpath.get(relpath)
            if track is None:
                track = Track(relpath, {"title": Path(relpath).stem}, STATE_IPOD,
                              relpath=relpath)
            resolved.append(track)
        fill_tracks(self.playlist_tracks, resolved)

    def _clear_playlist_detail(self):
        self.playlist_heading.set_text(
            "No playlists" if self.mount_point else "No iPod connected"
        )
        child = self.playlist_voice_note.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.playlist_voice_note.remove(child)
            child = nxt
        self.playlist_voice_note.append(
            label(
                "Add a playlist to this iPod"
                if self.mount_point
                else "Connect an iPod to manage its playlists",
                "sf-body",
                "sf-dim",
            )
        )
        fill_tracks(self.playlist_tracks, [])

    # ---------------------------------------------------------- library scan

    def _rescan_library(self):
        """Index the configured music folders in the background."""
        self.scan_generation += 1
        generation = self.scan_generation
        roots = self.library.roots
        previous_tracks = list(self.library.tracks)
        self._library_scan_tracks = {}
        self.library_status.set_text("Reading your music folders…")

        def worker():
            batch = []

            def publish(root, record):
                batch.append(
                    Track(Path(root, record["path"]), record, STATE_LIBRARY)
                )
                if len(batch) >= 25:
                    ready = batch[:]
                    batch.clear()
                    GLib.idle_add(self._apply_library_batch, generation, ready)

            # The cache first, because it is small and holds the tracks the
            # window is most likely to be asked about again. A cache that does
            # not exist yet fails this scan, which is the same as holding
            # nothing.
            records, complete, _skipped = scan_tracks(
                PREVIEW_CACHE,
                cancelled=lambda: generation != self.scan_generation,
            )
            if generation != self.scan_generation:
                return
            if complete:
                GLib.idle_add(
                    self._apply_preview_scan,
                    generation,
                    [
                        Track(PREVIEW_CACHE / record["path"], record, STATE_PREVIEW)
                        for record in records
                        if not any(
                            part.startswith(".")
                            for part in Path(record["path"]).parts
                        )
                    ],
                )

            for root in roots:
                _records, complete, _skipped_symlinks = scan_tracks(
                    root,
                    on_record=lambda record, r=root: publish(r, record),
                    cancelled=lambda: generation != self.scan_generation,
                )
                if generation != self.scan_generation:
                    return
                if not complete:
                    GLib.idle_add(
                        self._fail_library_scan, generation, previous_tracks
                    )
                    return
            if batch:
                GLib.idle_add(self._apply_library_batch, generation, batch[:])
            GLib.idle_add(self._finish_library_scan, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_preview_scan(self, generation, previews):
        """Take what the cache holds, keeping anything that landed meanwhile.

        Merged rather than replaced: a preview downloaded while this scan was
        running is on disk but was not there when it started, and dropping it
        would take a track out of the grid the moment it arrived in it.
        """
        if generation != self.scan_generation:
            return False
        merged = {
            track.path: track for track in previews if Path(track.path).is_file()
        }
        for track in self.library.previews:
            if track.path not in merged and Path(track.path).exists():
                merged[track.path] = track
        self.library.previews = list(merged.values())
        self._populate_cache_card()
        self._refresh_current_view(scan_complete=False)
        return False

    def _apply_library_batch(self, generation, tracks):
        if generation != self.scan_generation:
            return False
        for track in tracks:
            self._library_scan_tracks[track.path] = track
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        self._refresh_current_view(scan_complete=False)
        return False

    def _finish_library_scan(self, generation):
        if generation != self.scan_generation:
            return False
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        if self.mount_point:
            self._populate_device_summary()
        self._refresh_current_view()
        self._populate_folders()
        return False

    def _fail_library_scan(self, generation, previous_tracks):
        if generation != self.scan_generation:
            return False
        self.library.tracks = previous_tracks
        self._library_scan_tracks = {track.path: track for track in previous_tracks}
        self._merge_states()
        self._refresh_current_view()
        self._populate_folders()
        self.library_status.set_text(
            "Could not finish reading your music folders; the previous library is shown."
        )
        self.library_status.set_visible(True)
        return False

    def _load_device_tracks_async(self):
        """Read the device's tags without blocking the main loop.

        Every one of these files is read over USB, which is far too slow to do
        while the window is trying to draw.
        """
        self.tag_generation += 1
        generation = self.tag_generation
        mount_point = self.mount_point
        previous_tracks = list(self.device_tracks)
        previous_ready = self._device_snapshot_ready
        self._device_scan_tracks = {}
        self._device_scan_active = True
        self._update_device_controls()
        if self.pending_sources and not self._device_snapshot_ready:
            self.queued_label.set_text(
                "Checking which queued tracks are already on this iPod"
            )
            self.sync_button.set_label("Checking iPod…")

        def worker():
            music = Path(mount_point, "iPod_Control", "Music")
            batch = []

            def publish(record):
                batch.append(record)
                if len(batch) >= 25:
                    ready = batch[:]
                    batch.clear()
                    GLib.idle_add(
                        self._apply_device_track_batch,
                        generation,
                        mount_point,
                        ready,
                    )

            _records, complete, _skipped_symlinks = scan_tracks(
                music,
                on_record=publish,
                cancelled=lambda: generation != self.tag_generation,
            )
            if generation != self.tag_generation:
                return
            if not complete:
                GLib.idle_add(
                    self._fail_device_scan,
                    generation,
                    mount_point,
                    previous_tracks,
                    previous_ready,
                )
                return
            if batch:
                GLib.idle_add(
                    self._apply_device_track_batch,
                    generation,
                    mount_point,
                    batch[:],
                )
            GLib.idle_add(self._finish_device_scan, generation, mount_point)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_device_track_batch(
        self, generation, mount_point, records, scan_complete=False
    ):
        if (
            generation != self.tag_generation
            or mount_point is None
            or mount_point != self.mount_point
        ):
            return False
        music = Path(mount_point, "iPod_Control", "Music")
        for record in records:
            relpath = record["path"]
            self._device_scan_tracks[relpath] = Track(
                music / relpath, record, STATE_IPOD, relpath=relpath
            )
        if scan_complete:
            self.device_tracks = sorted(
                self._device_scan_tracks.values(), key=lambda track: track.relpath
            )
            self.track_names = {
                track.relpath: track.title for track in self.device_tracks
            }
            if scan_complete:
                self._device_scan_active = False
                self._device_snapshot_ready = True
            self._merge_states()
            self._refresh_current_view(scan_complete=scan_complete)
        return False

    def _finish_device_scan(self, generation, mount_point):
        result = self._apply_device_track_batch(
            generation, mount_point, [], scan_complete=True
        )
        if generation == self.tag_generation and mount_point == self.mount_point:
            self._populate_device_summary()
            self._update_device_controls()
        return result

    def _fail_device_scan(
        self, generation, mount_point, previous_tracks, previous_ready
    ):
        if generation != self.tag_generation or mount_point != self.mount_point:
            return False
        self._device_scan_active = False
        self._device_snapshot_ready = previous_ready
        self.device_tracks = previous_tracks if previous_ready else []
        self._device_scan_tracks = {
            track.relpath: track for track in self.device_tracks
        }
        self.track_names = {
            track.relpath: track.title for track in self.device_tracks
        }
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()
        self._update_device_controls()
        self._toast("Could not finish reading tracks from this iPod")
        return False

    def _apply_device_tracks(self, generation, records, mount_point=None):
        captured_mount = self.mount_point if mount_point is None else mount_point
        if captured_mount is None:
            return False
        self._device_scan_tracks = {}
        return self._apply_device_track_batch(
            generation, captured_mount, records, scan_complete=True
        )

    def _resolve_current_album(self):
        if self.current_album is None:
            return None
        by_artist = self.group_mode.get_selected() == 1
        for collection in self.library.collections(by_artist):
            if by_artist:
                matches = collection.title.lower() == self.current_album.title.lower()
            else:
                matches = (
                    collection.title.lower(),
                    collection.artist.lower(),
                ) == (
                    self.current_album.title.lower(),
                    self.current_album.artist.lower(),
                )
            if matches:
                return collection
        return None

    def _refresh_current_view(self, scan_complete=True):
        self._populate_albums()
        visible = self.views.get_visible_child_name()
        if visible == "album":
            album = self._resolve_current_album()
            if album is None and scan_complete:
                self.current_album = None
                self.show_view("library")
            elif album is not None:
                self._show_album(album)
        elif visible == "playlists" and self.current_playlist is not None:
            self._show_playlist(self.current_playlist)
        elif visible == "search":
            # A rescan changes which of the results are already on the iPod, so
            # the local half is re-derived rather than left showing the states
            # it had when the query was typed.
            self._paint_local_results()
        return False

    def _merge_states(self):
        """Decide which local tracks are already on the device.

        Matched on tags rather than path: the device stores every track under a
        scrambled four-letter name, so nothing about its location survives.
        """
        on_device = {}
        for track in self.device_tracks:
            on_device.setdefault(track.identity(), []).append(track)
        matched = set()
        self._library_by_path = {
            track.path: track for track in self.library.tracks
        }
        for track in self.library.tracks:
            track.relpath = track.path
            matches = on_device.get(track.identity(), [])
            if matches:
                device_track = matches.pop(0)
                track.state = STATE_IPOD
                track.on_ipod = True
                track.relpath = device_track.relpath
                matched.add(id(device_track))
            elif track.state == STATE_IPOD:
                track.state = STATE_LIBRARY
                track.on_ipod = False

        pending_index = dict(self._library_by_path)
        records = getattr(self, "pending_records", {})
        for path in getattr(self, "pending", set()):
            if path in pending_index or Path(path).suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            record = dict(records.get(path, {}))
            record.setdefault("title", Path(path).stem)
            try:
                record["size"] = Path(path).stat().st_size
            except OSError:
                record["size"] = 0
            track = Track(path, record, STATE_LIBRARY)
            matches = on_device.get(track.identity(), [])
            if matches:
                device_track = matches.pop(0)
                track.state = STATE_IPOD
                track.on_ipod = True
                track.relpath = device_track.relpath
            pending_index[path] = track
        self._pending_track_index = pending_index

        # Device tracks with no local counterpart still belong in the grid, or
        # music copied from another machine would simply not appear. Held
        # separately from the scan results rather than appended to them, which
        # would re-add the same tracks on every refresh.
        self.library.device_only = [
            track for track in self.device_tracks if id(track) not in matched
        ]

    # -------------------------------------------------------------- painting

    def _populate_albums(self):
        if not self._library_ready:
            return
        child = self.album_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.album_flow.remove(child)
            child = nxt

        by_artist = self.group_mode.get_selected() == 1
        collections = self.library.collections(by_artist)
        counts = self.library.counts(by_artist)
        for key, pill in self.album_filters.items():
            text = {
                "all": "All",
                STATE_IPOD: "On iPod",
                STATE_LIBRARY: "In library",
                STATE_PREVIEW: "Previewed",
            }[key]
            total = len(collections) if key == "all" else counts.get(key, 0)
            pill.set_child(label(f"{text} {total}", xalign=0.5))

        shown = [
            collection
            for collection in collections
            if self.album_filter == "all" or collection.state == self.album_filter
        ]
        for collection in shown:
            self.album_flow.append(self._album_card(collection))

        # The table ignores the grouping, since it is every track either way,
        # but it does honour the state filter.
        tracks = [
            track
            for collection in shown
            for track in collection.sorted_tracks()
        ]
        fill_tracks(self.library_table, tracks)

        noun = "artists" if by_artist else "albums"
        self.collection_heading.set_text(noun.capitalize())
        if not collections:
            self.library_status.set_text(
                "No music found. Add a folder under Device & Settings, or "
                "install mutagen with ./install.sh so tags can be read."
            )
            self.library_status.set_visible(True)
        elif not shown:
            self.library_status.set_text(f"No {noun} match this filter.")
            self.library_status.set_visible(True)
        else:
            self.library_status.set_visible(False)

    def _album_card(self, album):
        button = Gtk.Button()
        button.add_css_class("flat")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_size_request(ALBUM_COVER, -1)
        box.set_halign(Gtk.Align.CENTER)

        overlay = Gtk.Overlay()
        # Sized to the cover rather than the cell, so the state badge sits on
        # the artwork instead of floating beside it.
        overlay.set_halign(Gtk.Align.CENTER)
        cover = make_cover(album.art, ALBUM_COVER, f"{album.artist}/{album.title}")
        if album.state == STATE_PREVIEW:
            cover.set_opacity(0.6)
        overlay.set_child(cover)

        badge = Gtk.Box(spacing=5)
        badge.add_css_class("sf-badge")
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.END)
        badge.set_margin_start(7)
        badge.set_margin_bottom(7)
        badge.append(state_dot(album.state))
        badge.append(label(STATE_LABELS[album.state], "sf-caption"))
        overlay.add_overlay(badge)
        box.append(overlay)

        title = label(album.title, "sf-row-title", ellipsize=ELLIPSIZE_END)
        artist = label(album.artist, "sf-body", ellipsize=ELLIPSIZE_END)
        for text in (title, artist):
            # Without a cap, one long album title sets the natural width of
            # every cell in a homogeneous grid and the columns collapse.
            text.set_max_width_chars(16)
            text.set_width_chars(0)
        box.append(title)
        box.append(artist)
        button.set_child(box)
        button.connect("clicked", lambda _b, a=album: self._show_album(a))
        return button

    def _show_album(self, album):
        self.current_album = album
        child = self.album_art_holder.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.album_art_holder.remove(child)
            child = nxt
        self.album_art_holder.append(
            make_cover(album.art, 180, f"{album.artist}/{album.title}")
        )

        self.album_heading.set_text(album.title)
        tracks = album.sorted_tracks()
        total = sum(track.duration for track in tracks)
        size = sum(track.size for track in tracks)
        self.album_subheading.set_text(
            f"{album.artist} · {plural(len(tracks), 'track')} · "
            f"{human_duration(total)} · {human_size(size)} · "
            f"{album.on_ipod_count} of {len(tracks)} on iPod"
        )

        child = self.album_actions.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.album_actions.remove(child)
            child = nxt
        missing = [t for t in tracks if not t.on_ipod]
        if missing and self.mount_point:
            add_all = Gtk.Button(label=f"Queue {plural(len(missing), 'track')}")
            add_all.add_css_class("sf-button")
            add_all.add_css_class("accent")
            add_all.connect("clicked", lambda _b, ts=missing: self._queue_tracks(ts))
            self.album_actions.append(add_all)

        fill_tracks(self.album_tracks, tracks)
        self.show_view("album")

    def _populate_folders(self):
        child = self.folder_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.folder_list.remove(child)
            child = nxt

        counts = {}
        for track in self.library.tracks:
            for root in self.library.roots:
                if str(track.path).startswith(str(root)):
                    counts[root] = counts.get(root, 0) + 1
                    break

        for root in self.library.roots:
            row = Gtk.Box(spacing=10)
            row.set_margin_start(14)
            row.set_margin_end(10)
            row.set_margin_top(9)
            row.set_margin_bottom(9)
            row.append(
                label(home_relative(root), "sf-mono", hexpand=True, ellipsize=Pango.EllipsizeMode.START)
            )
            row.append(label(plural(counts.get(root, 0), "file"), "sf-caption"))
            remove = Gtk.Button(icon_name="window-close-symbolic")
            remove.add_css_class("flat")
            remove.set_valign(Gtk.Align.CENTER)
            remove.set_sensitive(len(self.library.roots) > 1)
            remove.connect("clicked", lambda _b, r=root: self.on_remove_folder(r))
            row.append(remove)
            self.folder_list.append(row)

    # --------------------------------------------------------- staged syncing

    def _pending_track(self, path):
        path = str(path)
        track = self._pending_track_index.get(path)
        if track is not None:
            return track
        record = dict(self.pending_records.get(path, {}))
        try:
            record["size"] = Path(path).stat().st_size
        except OSError:
            record["size"] = 0
        record.setdefault("title", Path(path).stem)
        return Track(path, record, STATE_LIBRARY)

    @staticmethod
    def _record_for_track(track):
        return {
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "genre": track.genre,
            "duration": track.duration,
            "track": track.track_no,
            "art": track.art,
            "size": track.size,
        }

    def _pending_accounting(self):
        tracks = []
        queued_bytes = 0
        for path in self.pending:
            if Path(path).suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            track = self._pending_track_index.get(path)
            if track is None:
                track = self._pending_track(path)
            if not track.on_ipod:
                tracks.append(track)
                queued_bytes += track.size
        playlist_changes = sum(
            Path(source).suffix.lower() in PLAYLIST_EXTENSIONS
            for source in self.pending_sources
        )
        return tracks, len(tracks) + playlist_changes, queued_bytes

    def _pending_symlink_note(self):
        skipped = sum(getattr(self, "pending_skipped_symlinks", {}).values())
        return f" · {plural(skipped, 'symlinked item')} skipped" if skipped else ""

    def _pending_copy_tracks(self):
        return self._pending_accounting()[0]

    def _pending_change_count(self):
        return self._pending_accounting()[1]

    def _queue_sources(
        self,
        sources,
        show_toast=True,
        metadata_complete=False,
        skipped_symlinks=None,
    ):
        sources = {str(source): list(tracks) for source, tracks in sources.items()}
        pending_only = {
            track.path
            for tracks in sources.values()
            for track in tracks
            if Path(track.path).suffix.lower() in AUDIO_EXTENSIONS
            and track.path not in self._library_by_path
        }
        if pending_only and not metadata_complete:
            self.source_generation += 1
            generation = self.source_generation
            device_identity = self.device_identity
            self.discovering_sources = True
            self._update_device_controls()

            def worker():
                enriched, complete = self._scan_pending_tracks(
                    pending_only, generation
                )
                GLib.idle_add(
                    self._finish_pending_enrichment,
                    generation,
                    device_identity,
                    sources,
                    enriched,
                    complete,
                    show_toast,
                    skipped_symlinks,
                )

            threading.Thread(target=worker, daemon=True).start()
            return None
        return self._commit_queue_sources(
            sources,
            show_toast=show_toast,
            skipped_symlinks=skipped_symlinks,
        )

    def _scan_pending_tracks(self, paths, generation):
        paths = set(str(path) for path in paths)
        enriched = {}
        records, complete, _skipped_symlinks = scan_tracks(
            files=paths,
            cancelled=lambda: generation != self.source_generation,
        )
        if not complete:
            return {}, False
        for record in records:
            path = record["path"]
            enriched[path] = Track(path, record, STATE_LIBRARY)
        for path in paths:
            enriched.setdefault(path, self._pending_track(path))
        return enriched, True

    def _finish_pending_enrichment(
        self,
        generation,
        device_identity,
        sources,
        enriched,
        complete,
        show_toast,
        skipped_symlinks,
    ):
        if generation != self.source_generation:
            return False
        self.discovering_sources = False
        self._update_device_controls()
        if device_identity != self.device_identity or not self.mount_point:
            self._toast("The connected iPod changed, so nothing was queued")
            return False
        if not complete:
            self._toast("Could not finish reading those tracks; nothing was queued")
            return False
        resolved = {
            source: [enriched.get(track.path, track) for track in tracks]
            for source, tracks in sources.items()
        }
        self._commit_queue_sources(
            resolved,
            show_toast=show_toast,
            skipped_symlinks=skipped_symlinks,
        )
        return False

    def _commit_queue_sources(
        self,
        sources,
        show_toast=True,
        replace=False,
        skipped_symlinks=None,
    ):
        if not self.mount_point:
            self._toast("Connect an iPod to queue tracks")
            return 0
        if self.device_identity is None:
            self._toast("Could not identify this iPod, so nothing was queued")
            return 0
        before = self._pending_change_count()
        retained_skipped = {
            source: count
            for source, count in self.pending_skipped_symlinks.items()
            if source not in self.pending_sources
        }
        if replace:
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_skipped_symlinks.clear()
            self.pending_skipped_symlinks.update(retained_skipped)
        if not self.pending_sources:
            self.pending_device_identity = self.device_identity
        elif self.pending_device_identity != self.device_identity:
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_skipped_symlinks.clear()
            self.pending_device_identity = self.device_identity

        for source, tracks in sources.items():
            source = str(source)
            members = set()
            for track in tracks:
                members.add(track.path)
                self.pending.add(track.path)
                self.pending_records[track.path] = self._record_for_track(track)
            if members:
                self.pending_sources[source] = members
            else:
                self.pending_sources.pop(source, None)
        for source, count in (skipped_symlinks or {}).items():
            source = str(source)
            if count:
                self.pending_skipped_symlinks[source] = count
            else:
                self.pending_skipped_symlinks.pop(source, None)
        owned = (
            set().union(*self.pending_sources.values())
            if self.pending_sources
            else set()
        )
        self.pending.intersection_update(owned)
        self.pending_records = {
            path: record
            for path, record in self.pending_records.items()
            if path in self.pending
        }
        if not self.pending_sources:
            self.pending_device_identity = None
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()
        added = max(0, self._pending_change_count() - before)
        if show_toast:
            self._toast(
                f"{plural(added, 'change')} queued" if added else "Already queued"
            )
        return added

    def _queue_paths(self, paths, show_toast=True):
        sources = {}
        expanded = []
        for path in dict.fromkeys(str(path) for path in paths):
            expanded.extend(self._audio_files(path) if Path(path).is_dir() else [path])
        for path in dict.fromkeys(expanded):
            if Path(path).is_file():
                sources[path] = [self._pending_track(path)]
        return self._queue_sources(sources, show_toast=show_toast)

    def _queue_tracks(self, tracks):
        return self._queue_sources(
            {track.path: [track] for track in tracks}, show_toast=True
        )

    def _queue_playlist(self, path):
        tracks = [self._pending_track(item) for item in local_playlist_tracks(path)]
        tracks.append(self._pending_track(path))
        return self._queue_sources({str(path): tracks}, show_toast=True)

    def _unqueue_track(self, track):
        key = track.path
        affected = [
            source
            for source, members in self.pending_sources.items()
            if key in members
        ]
        removed_directory = False
        for source in affected:
            members = self.pending_sources.pop(source)
            self.pending_skipped_symlinks.pop(source, None)
            removed_directory = removed_directory or source not in members
        owned = (
            set().union(*self.pending_sources.values())
            if self.pending_sources
            else set()
        )
        self.pending.intersection_update(owned)
        self.pending_records = {
            path: record
            for path, record in self.pending_records.items()
            if path in self.pending
        }
        if not self.pending_sources:
            self.pending_skipped_symlinks.clear()
            self.pending_device_identity = None
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()
        if removed_directory:
            self._toast("The whole folder was removed from the queue")

    def on_sync_pending(self, _button):
        if not self.pending_sources or not self.mount_point:
            return
        if self.pending_device_identity != self.device_identity:
            self._toast("Queued changes belong to a different iPod")
            return
        if not self._confirmed_device(self.pending_device_identity):
            return
        self.source_generation += 1
        generation = self.source_generation
        device_identity = self.device_identity
        paths = sorted(self.pending_sources)
        self.discovering_sources = True
        self._set_busy(True, "Checking queued sources")

        def worker():
            sources, complete, skipped_symlinks = self._scan_queued_sources(
                paths, generation
            )
            GLib.idle_add(
                self._finish_pending_source_scan,
                generation,
                device_identity,
                sources,
                complete,
                skipped_symlinks,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _scan_queued_sources(self, sources, generation):
        refreshed = {}
        skipped_symlinks = {}
        for source in sources:
            path = Path(source)
            if path.is_dir():
                records, complete, skipped = scan_tracks(
                    path,
                    cancelled=lambda: generation != self.source_generation,
                    skip_symlinks=True,
                )
                skipped_symlinks[source] = skipped
                tracks = [
                    Track(path / record["path"], record, STATE_LIBRARY)
                    for record in records
                ]
            elif path.suffix.lower() in PLAYLIST_EXTENSIONS:
                members, complete = read_local_playlist_tracks(path)
                if complete:
                    records, complete, _skipped = scan_tracks(
                        files=members,
                        cancelled=lambda: generation != self.source_generation,
                    )
                tracks = (
                    [
                        Track(record["path"], record, STATE_LIBRARY)
                        for record in records
                    ]
                    if complete
                    else []
                )
                if complete:
                    try:
                        size = path.stat().st_size
                    except OSError:
                        complete = False
                    else:
                        tracks.append(
                            Track(
                                path,
                                {"title": path.stem, "size": size},
                                STATE_LIBRARY,
                            )
                        )
            elif path.suffix.lower() in AUDIO_EXTENSIONS:
                records, complete, _skipped = scan_tracks(
                    files=[path],
                    cancelled=lambda: generation != self.source_generation,
                )
                tracks = [
                    Track(record["path"], record, STATE_LIBRARY)
                    for record in records
                ]
            else:
                tracks, complete = [], False
            if not complete:
                return {}, False, {}
            if tracks:
                refreshed[source] = tracks
        return refreshed, True, skipped_symlinks

    def _finish_pending_source_scan(
        self,
        generation,
        device_identity,
        sources,
        complete,
        skipped_symlinks,
    ):
        if generation != self.source_generation:
            self._set_busy(False)
            return False
        self.discovering_sources = False
        if not complete:
            self._set_busy(False)
            self._toast("Could not re-read queued sources, so sync was cancelled")
            return False
        if not self._confirmed_device(device_identity):
            self._set_busy(False)
            return False
        self._commit_queue_sources(
            sources,
            show_toast=False,
            replace=True,
            skipped_symlinks=skipped_symlinks,
        )
        if not self.pending_sources:
            self._set_busy(False)
            message = "Nothing remains in the queued sources"
            skipped = sum(skipped_symlinks.values())
            if skipped:
                message += f"; {plural(skipped, 'symlinked item')} skipped"
            self._toast(message)
            self.pending_skipped_symlinks.clear()
            return False
        self._set_busy(False)
        skipped = sum(skipped_symlinks.values())
        if skipped:
            self._toast(
                f"{plural(skipped, 'symlinked item')} skipped because links "
                "are not copied"
            )
        self._launch_pending_sync()
        return False

    def _launch_pending_sync(self):
        paths = sorted(self.pending_sources)
        copy_tracks, changes, _queued_bytes = self._pending_accounting()
        self.sync_files = [Path(p).name for p in paths]
        self.sync_total = len(copy_tracks)
        self._run(
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                *self._sync_options(),
                # A track title can start with a dash, and the shell script
                # would read that as a flag rather than a path.
                "--",
                *paths,
            ],
            "Copying to iPod",
            f"{plural(changes, 'change')} synced",
            then=self._clear_pending,
        )

    def _clear_pending(self):
        self.pending.clear()
        self.pending_sources.clear()
        self.pending_records.clear()
        self.pending_skipped_symlinks.clear()
        self._pending_track_index = dict(self._library_by_path)
        self.pending_device_identity = None
        return "Sync complete"

    # ------------------------------------------------------- options plumbing

    def _on_playlist_mode_changed(self, *_args):
        if self.loading_options:
            return
        if self.playlist_mode.get_selected() != 0 and self.speech_engine_available:
            self.playlist_voiceover.set_active(True)

    def _on_voiceover_changed(self, row, *_args):
        if not self.speech_engine_available:
            row.set_sensitive(row.get_active())

    def _load_sync_options(self):
        mode, playlist_args, track_voiceover, playlist_voiceover = saved_sync_options(
            self.mount_point
        )
        self.loading_options = True
        try:
            self.playlist_mode.set_selected(mode)
            self.track_voiceover.set_active(track_voiceover)
            self.playlist_voiceover.set_active(playlist_voiceover)
        finally:
            self.loading_options = False
        self.loaded_playlist_mode = mode
        self.loaded_playlist_args = playlist_args

    def _sync_options(self):
        """Flags for the selected playlist and voiceover options."""
        args = []
        mode = self.playlist_mode.get_selected()
        if mode == self.loaded_playlist_mode and self.loaded_playlist_args:
            args.extend(self.loaded_playlist_args)
        elif mode == 1:
            args.append("--dir-playlists")
        elif mode == 2:
            args.append("--id3-playlists={artist}")
        elif mode == 3:
            args.append("--id3-playlists={genre}")
        if self.track_voiceover.get_active():
            args.append("--voiceover")
        if self.playlist_voiceover.get_active():
            args.append("--playlist-voiceover")
        return args or ["--forget-options"]

    # --------------------------------------------------------- busy plumbing

    def _set_busy(self, busy, message=""):
        self.busy = busy
        for widget in self._busy_widgets:
            widget.set_sensitive(not busy)
        # Not in _busy_widgets, because whether it is sensitive depends on
        # there being something to clear as well as on nothing running.
        self._populate_cache_card()
        if not busy:
            self._update_device_controls()

        self.sync_revealer.set_reveal_child(busy)
        self.progress.set_visible(busy)
        if busy:
            self.sync_title.set_text(message)
            self.sync_spinner.start()
            self.progress.set_fraction(0)
            self.sync_count.set_text("")
            self.sync_current.set_text("")
        else:
            self.sync_spinner.stop()

    def _log(self, text):
        text = strip_ansi(text)
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), text)
        # Follow the output as it arrives. A pane that stays at the first line
        # of a copy shows the least useful part of a running operation.
        end = buf.create_mark(None, buf.get_end_iter(), False)
        self.log_view.scroll_to_mark(end, 0, False, 0, 0)
        buf.delete_mark(end)
        self._note_progress(text)
        return False

    def _note_progress(self, text):
        match = COPIED_LINE.match(text.rstrip("\n"))
        if match is None:
            return
        name = match.group("name")
        self.sync_current.set_text(name)
        row = Gtk.Box(spacing=11)
        row.append(label("✓", "sf-caption", width_chars=2, xalign=0.5))
        row.append(label(name, "sf-body", hexpand=True, ellipsize=ELLIPSIZE_END))
        row.append(label("Copied", "sf-caption"))
        self.sync_file_list.append(row)

        done = len(list(self._children(self.sync_file_list)))
        if self.sync_total:
            self.progress.set_fraction(min(1.0, done / self.sync_total))
            self.sync_count.set_text(f"{done} of {self.sync_total}")
        else:
            self.sync_count.set_text(f"{done} copied")

    @staticmethod
    def _children(container):
        child = container.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    def _clear_log(self):
        self.log_view.get_buffer().set_text("")
        child = self.sync_file_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.sync_file_list.remove(child)
            child = nxt

    def on_copy_log(self, _button):
        buf = self.log_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set_content(
                Gdk.ContentProvider.new_for_value(GObject.Value(str, text))
            )
            self._toast("Output copied")

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))
        return False

    # ------------------------------------------------------------- commands

    def _device_command_is_current(self, argv):
        if "--ipod" not in argv:
            return True
        index = argv.index("--ipod") + 1
        if index >= len(argv) or argv[index] is None:
            self._toast("Connect an iPod before running this action")
            return False
        mount_point = str(argv[index])
        if (
            mount_point != self.mount_point
            or resolve_device(mount_point, self.device_identity) is None
        ):
            self._toast("The connected iPod changed, so the action was cancelled")
            return False
        return True

    def _run(
        self,
        argv,
        busy_message,
        done_message,
        then=None,
        clear=True,
        on_failure=None,
    ):
        """Run a script in a worker thread, streaming output into the log.

        then, when given, is called on success and returns either the next
        command as (argv, busy_message, done_message) or a string to report as
        the outcome when there is nothing further to do. The YouTube flow uses
        this callback to queue only the tracks that a successful download says
        it produced.

        on_failure is its opposite, for a caller that has somewhere better to
        report the failure than the toast: a search result reports it inline in
        the section where the user pressed Add, which remains on screen.
        """
        if any(part is None for part in argv):
            self._toast("Connect an iPod before running this action")
            return False
        if not self._device_command_is_current(argv):
            return False
        device_command = "--ipod" in argv
        expected_identity = self.device_identity if device_command else None
        if clear:
            self._clear_log()
        self._set_busy(True, busy_message)

        def worker():
            code = -1
            if device_command:
                mount_index = argv.index("--ipod") + 1
                mount_point = str(argv[mount_index])
                if (
                    mount_point != self.mount_point
                    or resolve_device(mount_point, expected_identity) is None
                ):
                    GLib.idle_add(self._cancel_device_command)
                    return
            try:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if proc.stdout is not None:
                    for line in proc.stdout:
                        GLib.idle_add(self._log, line)
                code = proc.wait()
            except (OSError, TypeError, ValueError) as exc:
                GLib.idle_add(self._log, f"failed to run: {exc}\n")
            GLib.idle_add(
                self._finish, code, done_message, then, device_command, on_failure
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _cancel_device_command(self):
        self._set_busy(False)
        self._toast("The connected iPod changed, so the action was cancelled")
        return False

    def _invalidate_device_snapshot(self):
        self.tag_generation += 1
        self._device_scan_active = bool(self.mount_point)
        self._device_snapshot_ready = False
        self._device_scan_tracks = {}
        self.device_tracks = []
        self.track_names = {}
        self._merge_states()
        self._populate_device_summary()
        self._update_device_controls()

    def _finish(
        self,
        code,
        done_message,
        then=None,
        device_command=False,
        on_failure=None,
    ):
        if code == 0 and device_command:
            self._invalidate_device_snapshot()
        if code == 0 and then is not None:
            outcome = then()
            if isinstance(outcome, tuple):
                # Straight into the next command, staying busy, so the two
                # read as one action rather than appearing to finish twice.
                self._run(*outcome, clear=False)
                return False
            done_message = outcome

        self._set_busy(False)
        if code == 0:
            self._toast(done_message)
        else:
            if on_failure is None:
                self._toast(f"Failed (exit {code}) - see Details")
            self.details_toggle.set_active(True)
            self.sync_revealer.set_reveal_child(True)
            if on_failure is not None:
                on_failure()
        self.sync_total = 0
        self.refresh()
        self._rescan_library()
        return False

    def on_add_music(self, _button):
        dialog = Gtk.FileDialog(title="Choose a music folder")

        def chosen(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path()
            if not path:
                self._toast("That location is not a local folder")
                return
            self._discover_music_folder(path)

        dialog.select_folder(self, None, chosen)

    def _discover_music_folder(self, path):
        self.source_generation += 1
        generation = self.source_generation
        device_identity = self.device_identity
        self.discovering_sources = True
        self._update_device_controls()

        def worker():
            tracks, complete, skipped_symlinks = self._scan_source_tracks(
                path, generation
            )
            GLib.idle_add(
                self._finish_music_folder_discovery,
                generation,
                device_identity,
                path,
                tracks,
                complete,
                skipped_symlinks,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _scan_source_tracks(self, path, generation):
        records, complete, skipped_symlinks = scan_tracks(
            path,
            cancelled=lambda: generation != self.source_generation,
            skip_symlinks=True,
        )
        tracks = [
            Track(Path(path, record["path"]), record, STATE_LIBRARY)
            for record in records
        ]
        return tracks, complete, skipped_symlinks

    def _finish_music_folder_discovery(
        self,
        generation,
        device_identity,
        path,
        tracks,
        complete,
        skipped_symlinks,
    ):
        if generation != self.source_generation:
            return False
        self.discovering_sources = False
        self._update_device_controls()
        if device_identity != self.device_identity or not self.mount_point:
            self._toast("The connected iPod changed, so the folder was not queued")
            return False
        if not complete:
            self._toast("Could not finish reading that folder; nothing was queued")
            return False
        if not tracks:
            message = "No supported audio found in that folder"
            if skipped_symlinks:
                message += f"; {plural(skipped_symlinks, 'symlinked item')} skipped"
            self._toast(message)
            return False
        self._queue_sources(
            {str(path): tracks},
            metadata_complete=True,
            skipped_symlinks={str(path): skipped_symlinks},
        )
        return False

    def on_add_folder(self, _button):
        dialog = Gtk.FileDialog(title="Add a folder to search")

        def chosen(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path()
            if not path:
                self._toast("That location is not a local folder")
                return
            root = Path(path)
            if root in self.library.roots:
                return
            self.library.roots.append(root)
            save_music_roots(self.library.roots)
            self._rescan_library()

        dialog.select_folder(self, None, chosen)

    def on_remove_folder(self, root):
        if len(self.library.roots) <= 1:
            return
        self.library.roots = [r for r in self.library.roots if r != root]
        save_music_roots(self.library.roots)
        self._rescan_library()

    def on_add_playlist(self, _button):
        dialog = Gtk.FileDialog(title="Choose a playlist file")
        playlist_filter = Gtk.FileFilter()
        playlist_filter.set_name("Playlists (M3U, PLS)")
        playlist_filter.add_suffix("m3u")
        playlist_filter.add_suffix("pls")
        dialog.set_default_filter(playlist_filter)

        def chosen(dlg, result):
            try:
                chosen_file = dlg.open_finish(result)
            except GLib.Error:
                return
            path = chosen_file.get_path()
            if not path:
                self._toast("That location is not a local file")
                return
            self._add_playlist(path)

        dialog.open(self, None, chosen)

    def _add_playlist(self, path):
        # A named playlist implies wanting the name read aloud, exactly as
        # choosing a grouping under Sync options does: with no screen, the
        # spoken name is the only way to find the playlist again.
        if not self.speech_engine_available:
            self._toast("No speech engine installed")
            return
        self.playlist_voiceover.set_active(True)
        self._queue_playlist(path)

    def on_add_youtube(self, _button):
        url_entry = Adw.EntryRow(title="YouTube link")
        url_entry.set_activates_default(True)
        whole_playlist = Adw.SwitchRow(
            title="Whole playlist",
            subtitle="Off downloads only the linked video",
        )

        fields = Adw.PreferencesGroup()
        fields.add(url_entry)
        fields.add(whole_playlist)

        dialog = Adw.AlertDialog(
            heading="Add from YouTube",
            body=(
                "The audio is converted to MP3, tagged with its artist and "
                f"title, kept in {home_relative(YOUTUBE_LIBRARY)}, and queued "
                "for the next sync."
            ),
        )
        dialog.set_extra_child(fields)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("download", "Download")
        dialog.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("download")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_youtube_response, url_entry, whole_playlist)
        dialog.present(self)

        # Pasting a link is the entire interaction, so offer the one already on
        # the clipboard rather than making the user paste it by hand.
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().read_text_async(
                None, self._offer_clipboard_url, url_entry
            )

    @staticmethod
    def _offer_clipboard_url(clipboard, result, url_entry):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if is_downloadable_url(text) and not url_entry.get_text():
            url_entry.set_text(text.strip())

    def _on_youtube_response(self, _dialog, response, url_entry, whole_playlist):
        if response != "download":
            return

        url = url_entry.get_text().strip()
        if not is_downloadable_url(url):
            self._toast("Enter a link starting with http:// or https://")
            return
        self._start_youtube_download(
            url,
            single=not whole_playlist.get_active(),
            busy_message="Downloading from YouTube",
        )

    def _start_youtube_download(self, url, single, busy_message, on_failure=None):
        """Fetch one link and queue whatever that run produced.

        Shared by the dialog and by a search result so both queue exactly the
        tracks the download reported rather than the folder it wrote into.
        """
        # Written by the download and read when it completes, so only what this
        # run actually fetched enters the queue. Without it the whole library
        # would be queued every time.
        handle, new_tracks = tempfile.mkstemp(prefix="ipod-fetch-", suffix=".list")
        os.close(handle)

        fetch = fetch_command(url, YOUTUBE_LIBRARY, new_tracks, single)

        def failed():
            try:
                os.unlink(new_tracks)
            except OSError:
                pass
            if on_failure is not None:
                on_failure()

        if not self._run(
            fetch,
            busy_message,
            "Downloaded",
            then=lambda: self._sync_downloaded(new_tracks),
            on_failure=failed,
        ):
            # Refused before anything ran, so the list file it would have read
            # is left behind unless it is cleaned up here.
            failed()
        return fetch

    def _sync_downloaded(self, new_tracks):
        """Queue what the download produced, or say why there is nothing to."""
        sources = fetched_sources(new_tracks, YOUTUBE_LIBRARY)
        try:
            os.unlink(new_tracks)
        except OSError:
            pass

        if not sources:
            return "Already downloaded - nothing new to add"
        queued = self._queue_paths(sources, show_toast=False)
        if queued is None:
            return "Downloaded; reading track details before queueing"
        return (
            f"{plural(queued, 'track')} queued for sync"
            if queued
            else "Downloaded, but nothing was queued"
        )

    def on_remove_track(self, _button, relpath):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before removing tracks")
            return
        name = self.track_names.get(relpath, Path(relpath).name)
        dialog = Adw.AlertDialog(
            heading="Remove this track?",
            body=(
                f"{name}\n\n"
                "It is deleted from the iPod and the database is rebuilt. "
                "Any copy in your own music folder is left alone."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", self._on_remove_response, relpath, self.device_identity
        )
        dialog.present(self)

    def _on_remove_response(self, _dialog, response, relpath, device_identity):
        if response != "remove":
            return
        if not self._confirmed_device(device_identity):
            return
        self._run(
            [
                str(REMOVE_SCRIPT),
                "--ipod",
                self.mount_point,
                "--yes",
                # Track names are whatever the tags said, dashes included.
                "--",
                relpath,
            ],
            "Removing track",
            "Track removed",
        )

    def on_remove_playlist(self, _button, name):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before removing playlists")
            return
        dialog = Adw.AlertDialog(
            heading="Remove this playlist?",
            body=(
                f"{name}\n\n"
                "Only the playlist is removed and the database is rebuilt. "
                "The songs it lists stay on the iPod."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            self._on_playlist_remove_response,
            name,
            self.device_identity,
        )
        dialog.present(self)

    def _on_playlist_remove_response(
        self, _dialog, response, name, device_identity
    ):
        if response != "remove":
            return
        if not self._confirmed_device(device_identity):
            return
        self._run(
            [
                str(REMOVE_SCRIPT),
                "--ipod",
                self.mount_point,
                "--yes",
                "--playlist",
                # A playlist named after a song can start with a dash too.
                "--",
                name,
            ],
            "Removing playlist",
            "Playlist removed",
        )

    @staticmethod
    def _audio_files(path):
        found = []
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                candidate = Path(root, name)
                if candidate.suffix.lower() in AUDIO_EXTENSIONS:
                    found.append(str(candidate))
        return found

    def on_rebuild(self, _button):
        # --rebuild-only rather than passing the Music directory as a source,
        # which would copy the iPod's own library into a subfolder of itself.
        self._run(
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                "--rebuild-only",
                *self._sync_options(),
            ],
            "Rebuilding database",
            "Database rebuilt",
        )

    def on_wipe(self, _button):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before wiping it")
            return
        total = count_tracks(self.mount_point)
        dialog = Adw.AlertDialog(
            heading="Wipe this iPod?",
            body=(
                f"All {total} track(s) will be removed from the device.\n\n"
                "Backing up first is strongly recommended: iPod filenames are "
                "scrambled codes, and the database that maps them back to real "
                "song titles is deleted too."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("wipe", "Wipe Without Backup")
        dialog.add_response("backup", "Back Up and Wipe")
        dialog.set_response_appearance("wipe", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("backup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("backup")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_wipe_response, self.device_identity)
        dialog.present(self)

    def _confirmed_device(self, device_identity):
        if resolve_device(self.mount_point, device_identity) is None:
            self._toast("The connected iPod changed, so the action was cancelled")
            return False
        return True

    def _on_wipe_response(self, _dialog, response, device_identity):
        if response == "cancel":
            return
        if not self._confirmed_device(device_identity):
            return
        argv = [str(WIPE_SCRIPT), "--ipod", self.mount_point, "--yes"]
        if response == "backup":
            target = Path(os.path.expanduser("~"), "ipod-backup")
            argv += ["--backup", str(target)]
        self._run(argv, "Wiping", "iPod wiped")

    def on_eject(self, _button):
        expected_identity = self.device_identity
        device = resolve_device(
            self.mount_point, expected_identity, require_block=True
        )
        if device is None:
            self._toast("Could not determine the device to unmount")
            return

        self._set_busy(True, "Ejecting")

        def worker():
            current = resolve_device(
                self.mount_point, expected_identity, require_block=True
            )
            if current is None:
                GLib.idle_add(
                    self._finish_dbus,
                    False,
                    "",
                    "the connected iPod changed; eject was cancelled",
                )
                return
            ok, message = udisks_filesystem_call(
                current.block_device, "Unmount"
            )
            GLib.idle_add(
                self._finish_dbus, ok, "Safe to unplug", message, True
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_dbus(
        self, ok, success_message, error_message, invalidate_snapshot=False
    ):
        self._set_busy(False)
        self._toast(success_message if ok else f"Failed: {error_message}")
        if ok and invalidate_snapshot:
            self._invalidate_device_snapshot()
        self.refresh()
        return False

    def on_mount_clicked(self, _button):
        """Mount an iPod that is plugged in but not mounted."""
        candidates = unmounted_vfat_devices()
        if not candidates:
            self._toast("No unmounted iPod found")
            return

        self._set_busy(True, "Mounting")

        def worker():
            for device in candidates:
                ok, message = udisks_filesystem_call(device, "Mount")
                if not ok:
                    continue
                if Path(message, "iPod_Control").is_dir():
                    GLib.idle_add(self._finish_dbus, True, "iPod mounted", "")
                    return
                # Something else on the bus. Put it back as it was rather
                # than leaving an unrelated volume mounted.
                udisks_filesystem_call(device, "Unmount")
            GLib.idle_add(
                self._finish_dbus, False, "", "no iPod among the connected volumes"
            )

        threading.Thread(target=worker, daemon=True).start()
