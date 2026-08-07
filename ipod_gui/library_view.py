"""The library grid, the album page behind it, and the scan that fills them.

Owns the album flow and its filter pills, the flat track table, the album
detail page, the music-folder scan and `scan_generation` that supersedes a
stale one, and `_merge_states`, which decides which local track is already on
the device.

Borrows from the window: `library`, `device_tracks` and `mount_point` to merge
against, `current_view` and `show_view` to know and change what is on screen,
and `_populate_device_summary`, `_populate_cache_card`, `_populate_folders`,
`_show_playlist`, `_paint_local_results` and `_queue_tracks` to repaint or act
on what the merge changed. The playlist shelf at the top of the library page is
the playlist view's, built and filled there.
"""

import threading
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .config import (
    AUDIO_EXTENSIONS,
    GROUP_MODE_LABELS,
    GROUP_MODES,
    PREVIEW_CACHE,
    STATE_IPOD,
    STATE_LABELS,
    STATE_LIBRARY,
    STATE_PREVIEW,
    save_library_layout,
)
from .text import human_duration, human_size, plural
from .tags import scan_tracks
from .model import Track
from .widgets import (
    ALBUM_COVER,
    ELLIPSIZE_END,
    TRACK_PAGE_WIDTH,
    clear_children,
    fill_tracks,
    label,
    make_cover,
    state_dot,
    tooltip_beside_popover,
    track_column_view,
)

# How long repaints are allowed to queue behind each other while a scan is
# still publishing batches. Long enough that a burst of them costs one rebuild
# rather than a dozen, short enough that the grid still visibly fills in.
REFRESH_COALESCE_MS = 250


class LibraryViewMixin:
    def _build_library_controls(self):
        """The grouping and grid/table controls the header carries.

        Only meaningful on the library, which is the only view with a grid to
        group or to swap for a table, so the window hides the whole box
        elsewhere and never has to know what is inside it.
        """
        self.library_controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)

        # Once suspected of being at fault here, and it never was: on a
        # floating window the list flashed open and shut because mutter
        # refused to place the popup, taking a legacy HiDPI path that
        # multiplies every positioner coordinate by the monitor scale. Nothing
        # in this file and nothing in GTK needed changing; the goal
        # `stop-startup-repaints-from-dismissing` carries the diagnosis.
        self.group_mode = Gtk.DropDown.new_from_strings(list(GROUP_MODE_LABELS))
        self.group_mode.add_css_class("flat")
        # A separate defect, app-side and on every display: a tooltip is its
        # own surface, which GTK goes on showing while the list opens beneath
        # it, so it draws over the options and takes the click meant for one
        # of them.
        tooltip_beside_popover(
            self.group_mode, "Group the library by album or by artist"
        )
        # Selected before the handler is connected, so reopening the app on the
        # grouping it was left in does not count as a change and repaint a grid
        # that has not been filled yet.
        self.group_mode.set_selected(GROUP_MODES.index(self._saved_group_mode))
        self.group_mode.connect("notify::selected", self._on_group_mode_changed)
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
        self.mode_buttons[self.view_mode].set_active(True)
        self.library_controls.append(modes)
        return self.library_controls

    # --------------------------------------------------------- library view

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

        # The playlists it holds are the playlist view's, so it builds and
        # fills its own section and this page only gives it the space.
        box.append(self._build_shelf_section())

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
        self.library_status.set_wrap(True)
        self.library_status.set_max_width_chars(60)
        box.append(self.library_status)
        self._library_ready = True

        # The header, and the mode restored onto its buttons, were built before
        # this stack existed, so the toggle that ran then had nothing to switch.
        self.library_modes.set_visible_child_name(self.view_mode)
        self.group_mode.set_sensitive(self.view_mode == "grid")
        return scroller

    def _group_mode_name(self):
        # Read off the one index, so the order lives in GROUP_MODE_CHOICES and
        # nowhere else. Nothing selected answers Gtk.INVALID_LIST_POSITION
        # rather than a usable position, which is the default grouping here.
        selected = self.group_mode.get_selected()
        if selected >= len(GROUP_MODES):
            return GROUP_MODES[0]
        return GROUP_MODES[selected]

    def _grouped_by_artist(self):
        return self._group_mode_name() == "artist"

    def _on_group_mode_changed(self, *_args):
        # The header is built before the grid it groups, so the selection
        # restored above arrives before there is anything to repaint.
        if not self._library_ready:
            return
        self._populate_albums()
        self._save_library_layout()

    def _save_library_layout(self):
        save_library_layout(self._group_mode_name(), self.view_mode)

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
        self._save_library_layout()

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

    # ----------------------------------------------------------- album view

    def _build_album_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        # Everything on this page is one column of text against a track table,
        # neither of which gains anything from a wider window: past a point
        # the title sits at one edge and the cluster that follows it at the
        # other. The header is clamped with the table so the two stay aligned.
        clamp = Adw.Clamp(
            maximum_size=TRACK_PAGE_WIDTH, tightening_threshold=TRACK_PAGE_WIDTH
        )
        clamp.set_child(box)
        scroller.set_child(clamp)
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
        # Six facts joined into one sentence, and a long artist name makes it
        # the widest thing on the page. Wrapped rather than ellipsised: the
        # track count and what is already on the iPod are the reason to read
        # the line at all, and they sit at the end of it.
        self.album_subheading.set_wrap(True)
        meta.append(self.album_subheading)
        self.album_actions = Gtk.Box(spacing=8)
        meta.append(self.album_actions)
        header.append(meta)
        box.append(header)

        self.album_tracks = track_column_view(
            self, columns=("number", "title", "state", "duration", "action", "menu")
        )
        box.append(self.album_tracks)
        return scroller

    # --------------------------------------------------------- library scan

    def _rescan_library(self):
        """Index the configured music folders in the background."""
        self.scan_generation += 1
        generation = self.scan_generation
        roots = self.library.roots
        previous_tracks = list(self.library.tracks)
        self._library_scan_tracks = {}
        self.library_status.set_text("Reading your music folders…")
        self._library_scan_running = True
        self._update_refresh_spinner()

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
            records, complete = scan_tracks(
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
                _records, complete = scan_tracks(
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
        self._request_refresh()
        return False

    def _apply_library_batch(self, generation, tracks):
        if generation != self.scan_generation:
            return False
        for track in tracks:
            self._library_scan_tracks[track.path] = track
        self.library.tracks = list(self._library_scan_tracks.values())
        self._merge_states()
        self._request_refresh()
        return False

    def _finish_library_scan(self, generation):
        if generation != self.scan_generation:
            return False
        self._library_scan_running = False
        self._update_refresh_spinner()
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
        self._library_scan_running = False
        self._update_refresh_spinner()
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

    # ------------------------------------------------------------- painting

    def _resolve_current_album(self):
        if self.current_album is None:
            return None
        by_artist = self._grouped_by_artist()
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

    def _popover_is_open(self):
        """Whether a popover currently holds the focus.

        Repainting rebuilds the grid, which destroys the widget the focus is
        on, and GTK moves the focus out of the popup when that happens, so a
        repaint landing while a menu is open churns the widgets that menu is
        standing over. A scan does that several times a second; holding its
        batches back until the menu closes takes the rebuilds under one from
        19 to 0.

        Not the reason the Album/Artist control could not be opened on a 2x
        display: that one is the compositor dismissing any popup a floating
        window maps there, reproduced in stock GTK with no repaints of any
        kind, and it is recorded in the goal
        `stop-startup-repaints-from-dismissing`.
        """
        widget = self.get_focus()
        while widget is not None:
            if isinstance(widget, Gtk.Popover):
                return True
            widget = widget.get_parent()
        return False

    def _request_refresh(self, scan_complete=False):
        """Repaint soon, rather than once per batch.

        A library scan publishes every 25 tracks, and each of those batches
        used to rebuild every card in the grid and every row in the table.
        Coalescing them costs the grid nothing - it is filling in either way -
        and takes the repaint rate from several a second down to one per
        interval. The device walk is the other caller and never needed it: it
        collects its reads and asks for a single repaint at the end, which is
        the request that is never coalesced away.

        The final repaint is not coalesced away: it is the one that says what
        the library actually holds, so it replaces whatever is queued rather
        than being dropped into it. It still waits out an interval, and still
        waits for an open menu, because a repaint that skipped either of those
        would close the menu it was avoiding.
        """
        if scan_complete:
            self._cancel_refresh()
        elif self._refresh_timer is not None:
            return
        self._refresh_timer = GLib.timeout_add(
            REFRESH_COALESCE_MS, self._flush_refresh, scan_complete
        )

    def _flush_refresh(self, scan_complete):
        if self._popover_is_open():
            # Wait rather than repaint underneath it. Returning True keeps
            # this timer running, so the repaint lands as soon as the menu is
            # closed instead of being dropped.
            return True
        self._refresh_timer = None
        self._refresh_current_view(scan_complete=scan_complete)
        return False

    def _cancel_refresh(self):
        if self._refresh_timer is not None:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None

    def _refresh_current_view(self, scan_complete=True):
        """Repaint now, and let this be the last word.

        A caller reaching straight for this has state the coalesced queue does
        not know about - the end of a scan, a device appearing, a track being
        queued - so anything still waiting on the timer is describing a library
        that no longer exists. Left armed it lands up to an interval later and
        undoes the caller: a failed scan sets its status line here and then
        watches the stale repaint hide it again, leaving a library that looks
        like it read fine.
        """
        self._cancel_refresh()
        self._populate_albums()
        visible = self.current_view()
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

    def _populate_albums(self):
        if not self._library_ready:
            return
        clear_children(self.album_flow)

        by_artist = self._grouped_by_artist()
        collections = self.library.collections(by_artist)
        # The table is every track, so in list mode the heading, the counts on
        # the pills and what they filter are all track-shaped. Counting
        # collections above a list of tracks says "In library 43" over several
        # hundred rows, and filtering by collection would drop a synced track
        # whose album is still "In library" because the rest of it is not
        # there - the one row the filter was asked for.
        listing = self.view_mode == "list"
        counts = (
            self.library.track_counts() if listing else self.library.counts(by_artist)
        )
        everything = len(self.library.all_tracks()) if listing else len(collections)
        for key, pill in self.album_filters.items():
            text = {
                "all": "All",
                STATE_IPOD: "On iPod",
                STATE_LIBRARY: "In library",
                STATE_PREVIEW: "Previewed",
            }[key]
            total = everything if key == "all" else counts.get(key, 0)
            pill.set_child(label(f"{text} {total}", xalign=0.5))

        shown = [
            collection
            for collection in collections
            if self.album_filter == "all" or collection.state == self.album_filter
        ]
        for collection in shown:
            self.album_flow.append(self._album_card(collection))

        # The table ignores the grouping, since it is every track either way,
        # and takes its order from it.
        tracks = [
            track
            for collection in collections
            for track in collection.sorted_tracks()
            if self.album_filter == "all" or track.state == self.album_filter
        ]
        fill_tracks(self.library_table, tracks)

        noun = "tracks" if listing else "artists" if by_artist else "albums"
        self.collection_heading.set_text(noun.capitalize())
        if not collections:
            self.library_status.set_text(
                "No music found. Add a folder under Device & Settings, or "
                "install mutagen with ./install.sh so tags can be read."
            )
            self.library_status.set_visible(True)
        elif not (tracks if listing else shown):
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
        clear_children(self.album_art_holder)
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

        clear_children(self.album_actions)
        missing = [t for t in tracks if not t.on_ipod]
        if missing and self.mount_point:
            add_all = Gtk.Button(label=f"Queue {plural(len(missing), 'track')}")
            add_all.add_css_class("sf-button")
            add_all.add_css_class("accent")
            add_all.connect("clicked", lambda _b, ts=missing: self._queue_tracks(ts))
            self.album_actions.append(add_all)
        # The whole record at once, which is the one place a track-by-track ⋯
        # menu would be tedious: an album is exactly the kind of thing a
        # playlist is built out of.
        to_playlist = Gtk.MenuButton(label="Add to playlist")
        to_playlist.add_css_class("sf-button")
        to_playlist.set_create_popup_func(
            lambda menu, ts=tracks: menu.set_popover(
                self._playlist_menu(
                    "Add to playlist",
                    lambda name: self._add_tracks_to_playlist(name, ts),
                )
            )
        )
        self.album_actions.append(to_playlist)

        fill_tracks(self.album_tracks, tracks)
        self.show_view("album")
