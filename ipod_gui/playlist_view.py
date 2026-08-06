"""Playlists: the ones you make here, and the ones already on the device.

A playlist you make is a file of your own - one M3U per list under
~/Music/Playlists, which playlists.py owns. That is what makes creating one a
matter of typing a name: there is no iPod to wait for, nothing to export from
another program first, and the list exists the moment it is named. Syncing
hands that file to ipod-sync.sh exactly as choosing a playlist file used to, so
nothing about how a playlist reaches the device has changed.

The device's own playlists are shown beside them - a folder or tag grouping the
sync generated, or a list made on another computer. Those are read-only here,
because their entries name scrambled four-letter files on the iPod and there is
nothing local to write down in their place.

Owns the sidebar rail, the Playlists view's rail and detail, the shelf of tiles
at the top of the library page, the ⋯ menu every track row carries, and the
drag reordering that rewrites the list rather than only this window.

Borrows from the window: `playlists` and `spoken` as the probe left them,
`device_tracks` and `library` to resolve an entry into a track, `mount_point`,
`device_identity`, `busy` and `speech_engine_available` to know what can reach
the device, and `show_view`, `_run`, `_toast`, `_confirmed_device`,
`_sync_options`, `_queue_playlists`, `is_queued`, `unqueue_source`,
`focus_search` and `_keep_preview` to act on what an edit changed.
"""

from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .config import (
    AUDIO_EXTENSIONS,
    PLAYLIST_LIBRARY,
    REMOVE_SCRIPT,
    STATE_IPOD,
    STATE_LIBRARY,
    STATE_PREVIEW,
    SYNC_SCRIPT,
    YOUTUBE_LIBRARY,
)
from .text import home_relative, plural
from .device import playlist_file, write_playlist
from .youtube import downloaded_file
from .model import Track
from .playlists import (
    add_entries,
    create_local_playlist,
    default_name,
    delete_local_playlist,
    import_playlist_file,
    local_playlists,
    merge_with_device,
    move_entry,
    name_problem,
    remove_entry,
    rename_local_playlist,
)
from .widgets import (
    ELLIPSIZE_END,
    PLAYLIST_ROW_COVER,
    PLAYLIST_TILE_COVER,
    TRACK_PAGE_WIDTH,
    clear_children,
    fill_tracks,
    label,
    make_cover,
    row_menu_button,
    state_dot,
    track_list_view,
)


class PlaylistViewMixin:
    # ------------------------------------------------------- what there is

    def _load_local_playlists(self):
        """Re-read the playlist folder.

        The files are the playlists, so this is how one edited in another
        program, or moved, or deleted by hand, arrives - and why nothing here
        keeps an index that could disagree with them.
        """
        self.local_playlists = local_playlists(PLAYLIST_LIBRARY)

    def _shown_playlists(self):
        return merge_with_device(self.local_playlists, self.playlists)

    def _local_playlist(self, name):
        folded = (name or "").casefold()
        for playlist in self.local_playlists:
            if playlist.name.casefold() == folded:
                return playlist
        return None

    def _playlist_on_device(self, name):
        folded = (name or "").casefold()
        return any(other.casefold() == folded for other, _entries in self.playlists)

    def _playlist_state(self, playlist):
        return (
            STATE_IPOD if self._playlist_on_device(playlist.name) else STATE_LIBRARY
        )

    def _playlist_tracks(self, playlist):
        """A playlist's entries as tracks, in the order it lists them.

        A local entry is a path in a music folder, so it is looked up in the
        library; a device playlist's entry is a path on the iPod, so it is
        looked up in what was read off the device. Either way an entry nothing
        knows about still becomes a row, named after its file: a playlist that
        silently lost a line would be worse than one showing a track whose tags
        could not be read.
        """
        if not playlist.editable:
            by_relpath = {track.relpath: track for track in self.device_tracks}
            return [
                by_relpath.get(entry)
                or Track(entry, {"title": Path(entry).stem}, STATE_IPOD, relpath=entry)
                for entry in playlist.entries
            ]
        by_path = {track.path: track for track in self.library.all_tracks()}
        return [
            by_path.get(entry)
            or Track(entry, {"title": Path(entry).stem}, STATE_LIBRARY)
            for entry in playlist.entries
        ]

    # ------------------------------------------------------------ the rails

    def _build_sidebar_rail(self):
        """The always-visible list of playlists down the side of the window.

        Built here rather than in the sidebar, so the widget and the
        _populate_playlist_rail that fills it stay in one file.
        """
        self.playlist_rail = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, vexpand=True
        )
        self.playlist_rail.set_margin_start(10)
        self.playlist_rail.set_margin_end(10)
        rail_scroll = Gtk.ScrolledWindow(vexpand=True)
        rail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rail_scroll.set_child(self.playlist_rail)
        return rail_scroll

    def _build_shelf_section(self):
        """The row of playlist tiles at the top of the library page.

        It sits on the library view but holds playlists, so it is built and
        filled here and the library page only makes room for it.
        """
        shelf_head = Gtk.Box(spacing=10)
        shelf_head.append(label("Playlists", "sf-section-heading"))
        shelf_head.append(
            label(
                "Made here, synced to the iPod when you press Sync",
                "sf-body",
                valign=Gtk.Align.BASELINE,
                wrap=True,
            )
        )
        self.shelf_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        self.shelf_section.append(shelf_head)
        # The album grid further down the same page uses this column spacing,
        # so the two sections separate their tiles by the same gap.
        self.playlist_shelf = Gtk.Box(spacing=16)
        shelf_scroll = Gtk.ScrolledWindow()
        shelf_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        shelf_scroll.set_child(self.playlist_shelf)
        self.shelf_section.append(shelf_scroll)
        return self.shelf_section

    # ------------------------------------------------------- playlists view

    def _build_playlists_view(self):
        outer = Gtk.Box(spacing=0, vexpand=True)
        self.playlists_view = outer

        rail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rail_box.set_size_request(240, -1)
        # Explicitly, because both the heading and every row expand to push
        # something to their right edge, and GTK propagates that upwards: the
        # rail would take a share of every extra pixel the window gained and
        # hold a 240px list in half the view.
        rail_box.set_hexpand(False)
        rail_box.set_margin_start(22)
        rail_box.set_margin_end(12)
        rail_box.set_margin_top(20)
        rail_box.set_margin_bottom(20)
        head = Gtk.Box(spacing=8)
        head.append(label("Playlists", "sf-section-heading", hexpand=True))
        self.new_playlist_button = Gtk.Button(label="＋ New")
        self.new_playlist_button.add_css_class("sf-button")
        self.new_playlist_button.set_valign(Gtk.Align.CENTER)
        self.new_playlist_button.set_tooltip_text("Make an empty playlist")
        self.new_playlist_button.connect("clicked", self.on_new_playlist)
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
        self.playlist_actions = Gtk.Box(spacing=8)
        detail.append(self.playlist_actions)

        # The list and the sentence that stands in for an empty one share the
        # space, so a playlist with nothing in it says what to do next rather
        # than showing an empty table with a header and no rows.
        self.playlist_body = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True
        )
        self.playlist_tracks = track_list_view(self, self._reorder_playlist)
        detail_scroll = Gtk.ScrolledWindow(vexpand=True)
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_child(self.playlist_tracks)
        self.playlist_body.add_named(detail_scroll, "tracks")
        self.playlist_empty = label("", "sf-body", wrap=True, valign=Gtk.Align.START)
        self.playlist_body.add_named(self.playlist_empty, "empty")
        detail.append(self.playlist_body)
        # Held to the same width as the album page, whose rows are the same
        # rows: a playlist beside a 240px rail has even more window to stretch
        # across, and stretches the gap between a title and its own controls.
        detail_clamp = Adw.Clamp(
            maximum_size=TRACK_PAGE_WIDTH,
            tightening_threshold=TRACK_PAGE_WIDTH,
            hexpand=True,
        )
        detail_clamp.set_child(detail)
        outer.append(detail_clamp)
        return outer

    # ------------------------------------------------------------- painting

    def _populate_playlist_rail(self):
        self._load_local_playlists()
        for container in (self.playlist_rail, self.playlist_list):
            clear_children(container)

        shown = self._shown_playlists()
        for playlist in shown:
            self.playlist_rail.append(self._rail_row(playlist, compact=True))
            self.playlist_list.append(self._rail_row(playlist, compact=False))

        names = {playlist.name for playlist in shown}
        if not shown:
            self.current_playlist = None
            self._clear_playlist_detail()
        elif self.current_playlist not in names:
            self.current_playlist = shown[0].name
        if self.current_playlist is not None:
            self._show_playlist(self.current_playlist)
        self._populate_playlist_shelf()

    def _rail_row(self, playlist, compact):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-nav-row")
        row = Gtk.Box(spacing=9)
        row.append(make_cover(None, PLAYLIST_ROW_COVER, playlist.name, "tiny"))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(label(playlist.name, ellipsize=ELLIPSIZE_END))
        if not compact:
            text.append(label(plural(len(playlist.entries), "track"), "sf-caption"))
        row.append(text)
        # The same dot a track carries, meaning the same thing: this list is on
        # the iPod, or it is here and waiting to be. The spoken-name marker the
        # rail used to show says nothing at all about a playlist that has never
        # been synced, and every new one starts out as exactly that.
        state = self._playlist_state(playlist)
        marker = state_dot(state)
        marker.set_tooltip_text(
            "On the iPod" if state == STATE_IPOD else "Not on the iPod yet"
        )
        row.append(marker)
        button.set_child(row)
        button.connect(
            "clicked", lambda _b, n=playlist.name: self._select_playlist(n)
        )
        return button

    def _populate_playlist_shelf(self):
        clear_children(self.playlist_shelf)

        for playlist in self._shown_playlists()[:5]:
            tile = Gtk.Button()
            tile.add_css_class("flat")
            tile.add_css_class("sf-card")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
            # Sized to the artwork, like the album cards below: without a cap
            # the longest playlist name sets the width of its own tile alone,
            # and the shelf becomes a row of tiles no two of which match.
            box.set_size_request(PLAYLIST_TILE_COVER, -1)
            # The card's inset is the button's own padding, which is even on
            # all four sides. Adding to it made the tile wide enough that four
            # playlists and the New tile no longer fit the shelf at the
            # window's opening size.
            box.append(make_cover(None, PLAYLIST_TILE_COVER, playlist.name))
            title = label(playlist.name, "sf-row-title", ellipsize=ELLIPSIZE_END)
            title.set_max_width_chars(16)
            title.set_width_chars(0)
            box.append(title)
            note = Gtk.Box(spacing=6)
            note.append(state_dot(self._playlist_state(playlist)))
            note.append(
                label(plural(len(playlist.entries), "track"), "sf-caption")
            )
            box.append(note)
            tile.set_child(box)
            tile.connect(
                "clicked", lambda _b, n=playlist.name: self._select_playlist(n)
            )
            self.playlist_shelf.append(tile)

        new_tile = Gtk.Button()
        new_tile.add_css_class("sf-new-tile")
        # The artwork beside it, so the shelf ends on the width it kept.
        new_tile.set_size_request(PLAYLIST_TILE_COVER, -1)
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=7,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        inner.append(label("＋", xalign=0.5))
        inner.append(label("New playlist", "sf-row-title", xalign=0.5))
        new_tile.set_child(inner)
        new_tile.connect("clicked", self.on_new_playlist)
        # Making a playlist needs nothing but a name, so this is offered with
        # no iPod attached and no speech engine installed. What those are
        # needed for is putting it on the device, which the detail page says.
        new_tile.set_sensitive(not self.busy)
        self.playlist_shelf.append(new_tile)
        # Only the New tile means an empty section, which the library page has
        # nothing to say with; the Playlists view is where one is made.
        self.shelf_section.set_visible(bool(self._shown_playlists()))

    def _show_playlist(self, name):
        playlist = None
        for candidate in self._shown_playlists():
            if candidate.name == name:
                playlist = candidate
                break
        if playlist is None:
            self._clear_playlist_detail()
            return

        self.playlist_heading.set_text(playlist.name)
        self._fill_playlist_note(playlist)
        self._fill_playlist_actions(playlist)

        tracks = self._playlist_tracks(playlist)
        fill_tracks(self.playlist_tracks, tracks)
        self.playlist_body.set_visible_child_name("tracks" if tracks else "empty")
        if not tracks:
            self.playlist_empty.set_text(
                "Nothing in this playlist yet. Search your library or YouTube, "
                "then use the ⋯ beside a track to add it here."
                if playlist.editable
                else "This playlist is on the iPod but lists no tracks the "
                "device still holds."
            )

    def _fill_playlist_note(self, playlist):
        clear_children(self.playlist_voice_note)

        state = self._playlist_state(playlist)
        self.playlist_voice_note.append(state_dot(state))
        parts = [plural(len(playlist.entries), "track")]
        # A playlist that has reached the device is described by what the
        # device can do with it, which is the one thing this window cannot see
        # for itself: with no screen, a name it cannot say is no name at all.
        if state == STATE_IPOD:
            spoken = playlist.name.lower() in self.spoken
            parts.append(
                "the device can announce this playlist"
                if spoken
                else "on the iPod, but with no spoken name, so the device "
                "cannot announce it"
            )
        elif self.playlist_unavailable:
            parts.append(f"{self.playlist_unavailable}, so it cannot be synced")
        else:
            parts.append("kept on this computer until you sync")
        self.playlist_voice_note.append(label(" · ".join(parts), "sf-body", wrap=True))

    def _fill_playlist_actions(self, playlist):
        """The two things a playlist is for, and a menu holding the rest.

        Two buttons rather than four: a row of four set a minimum width the
        whole window then had to honour, 90px past the point where the sidebar
        has already folded away to make room. Renaming and deleting are also
        the two you reach for least, and the ⋯ is where this window already
        keeps what a row can do.
        """
        clear_children(self.playlist_actions)

        def action(text, handler, *classes, tooltip=None, sensitive=True):
            button = Gtk.Button(label=text)
            button.add_css_class("sf-button")
            for name in classes:
                button.add_css_class(name)
            button.set_tooltip_text(tooltip)
            button.set_sensitive(sensitive and not self.busy)
            button.connect("clicked", lambda _b: handler())
            self.playlist_actions.append(button)
            return button

        if not playlist.editable:
            action(
                "Remove from iPod",
                lambda: self.on_remove_playlist(playlist.name),
                sensitive=bool(self.mount_point) and self.device_identity is not None,
            )
            return

        action(
            "Add songs",
            self._start_adding_songs,
            "accent",
            tooltip="Search your library and YouTube for tracks to add",
        )
        queued = self.is_queued(playlist.path)
        action(
            "Queued for sync" if queued else "Send to iPod",
            lambda: self._send_playlist_to_ipod(playlist.name),
            tooltip=self._send_tooltip(playlist, queued),
            sensitive=not queued and self._can_send_playlist(playlist),
        )
        more = row_menu_button(
            lambda: self._playlist_actions_menu(playlist.name),
            f"More for {playlist.name}",
            dim=False,
        )
        more.set_sensitive(not self.busy)
        self.playlist_actions.append(more)

    def _playlist_actions_menu(self, name):
        popover = Gtk.Popover()
        return self._menu_popover(
            name,
            [
                self._menu_row(
                    popover, "Rename…", lambda: self.on_rename_playlist(name)
                ),
                self._menu_row(
                    popover, "Delete…", lambda: self.on_remove_playlist(name)
                ),
            ],
            popover,
        )

    def _can_send_playlist(self, playlist):
        return bool(
            playlist.entries
            and self.mount_point
            and self.device_identity is not None
            and self.speech_engine_available
            and not self.discovering_sources
        )

    def _send_tooltip(self, playlist, queued):
        if queued:
            return "Press Sync to copy these tracks and write the playlist"
        if not playlist.entries:
            return "Add some songs first"
        if not self.mount_point or self.device_identity is None:
            return "Connect an iPod to put this playlist on it"
        if self.playlist_unavailable:
            return self.playlist_unavailable
        return "Stage this playlist and its tracks for the next sync"

    def _clear_playlist_detail(self):
        self.playlist_heading.set_text("No playlists")
        clear_children(self.playlist_voice_note)
        self.playlist_voice_note.append(
            label(
                f"Playlists you make are kept in {home_relative(PLAYLIST_LIBRARY)}, "
                "and go onto the iPod when you sync.",
                "sf-body",
                "sf-dim",
                wrap=True,
            )
        )
        clear_children(self.playlist_actions)
        fill_tracks(self.playlist_tracks, [])
        self.playlist_body.set_visible_child_name("empty")
        self.playlist_empty.set_text("Press ＋ New to make one.")

    def _select_playlist(self, name):
        self.current_playlist = name
        self._show_playlist(name)
        self.show_view("playlists")

    def _start_adding_songs(self):
        """Send the user to the one field that searches both sources.

        Rather than a picker of its own: the search field already lists the
        library and YouTube together, and every row it produces carries the ⋯
        that adds it here. A second list of the same tracks in a dialog would
        be a worse copy of the view behind it.
        """
        self.focus_search()

    # ---------------------------------------------------------- the ⋯ menu

    def track_menu(self, track, playlist_name=None):
        """The menu on a track row, built as it opens.

        Inside a playlist it moves and removes; anywhere else it adds. Both are
        the same list of playlists, so both are built from one popover.
        """
        inside = self._local_playlist(playlist_name)
        if self._device_only_track(track):
            return self._menu_popover(
                "This track is only on the iPod",
                [
                    label(
                        "Playlists made here list files in your music folders, "
                        "and this one has no copy on this computer.",
                        "sf-body",
                        wrap=True,
                        max_width_chars=32,
                    )
                ],
            )
        if inside is not None:
            return self._playlist_menu(
                "Move to playlist",
                lambda name: self._move_track_between(inside.name, name, track),
                exclude=inside.name,
                extra=[
                    (
                        f"Remove from {inside.name}",
                        lambda: self._remove_track_from_playlist(inside.name, track),
                    )
                ],
            )
        return self._playlist_menu(
            "Add to playlist",
            lambda name: self._add_tracks_to_playlist(name, [track]),
            holding={
                playlist.name
                for playlist in self.local_playlists
                if track.path in playlist.entries
            },
        )

    def _playlist_menu(self, heading, on_pick, holding=(), exclude=None, extra=()):
        popover = Gtk.Popover()
        rows = []
        for playlist in self.local_playlists:
            if exclude is not None and playlist.name == exclude:
                continue
            rows.append(
                self._menu_row(
                    popover,
                    playlist.name,
                    lambda name=playlist.name: on_pick(name),
                    ticked=playlist.name in holding,
                )
            )
        if not rows:
            rows.append(
                label(
                    "No playlists yet", "sf-caption", "sf-dim", margin_start=6
                )
            )
        rows.append(
            self._menu_row(popover, "＋  New playlist…", lambda: on_pick(None))
        )
        for text, handler in extra:
            rows.append(Gtk.Separator(margin_top=4, margin_bottom=4))
            rows.append(self._menu_row(popover, text, handler))
        return self._menu_popover(heading, rows, popover)

    def _menu_row(self, popover, text, handler, ticked=False):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-menu-item")
        row = Gtk.Box(spacing=8)
        row.append(label(text, hexpand=True, ellipsize=ELLIPSIZE_END))
        if ticked:
            row.append(label("✓", "sf-accent"))
        button.set_child(row)

        def clicked(_button):
            # Down before the action, because the action repaints the view the
            # row was in and a popover anchored to a widget that is being
            # removed leaves a menu floating over the window.
            popover.popdown()
            handler()

        button.connect("clicked", clicked)
        return button

    @staticmethod
    def _menu_popover(heading, rows, popover=None):
        popover = Gtk.Popover() if popover is None else popover
        popover.add_css_class("sf-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_size_request(190, -1)
        box.append(label(heading.upper(), "sf-section-label", margin_bottom=2))
        listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        for row in rows:
            listing.append(row)
        # Capped rather than left to grow: twenty playlists would otherwise
        # make a menu taller than the window it opens in.
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(280)
        scroller.set_child(listing)
        box.append(scroller)
        popover.set_child(box)
        return popover

    def _device_only_track(self, track):
        """Whether this track names nothing on this computer to list.

        Two shapes of the same thing. Its path is a file under the mount point,
        and writing that into a playlist would ask the next sync to copy the
        device's own files back onto itself under scrambled names. Or it is not
        a path on this computer at all: a device playlist stores its entries
        relative to the iPod's music folder, and a row for one the device scan
        has not resolved - a stale line, or every line until the scan finishes
        - carries that bare relative name and nothing else. Written into a
        playlist here it would be resolved against the playlist folder, find
        nothing, and be dropped by the next sync without a word.
        """
        try:
            path = Path(track.path)
        except TypeError:
            return False
        if not path.is_absolute():
            return True
        if not self.mount_point:
            return False
        try:
            return path.is_relative_to(Path(self.mount_point))
        except (TypeError, ValueError):
            return False

    # -------------------------------------------------------------- editing

    def _add_tracks_to_playlist(self, name, tracks):
        """Put tracks in a playlist, making it first if the menu asked to."""
        if name is None:
            self.on_new_playlist(
                None,
                then=lambda new: self._add_tracks_to_playlist(new, tracks),
            )
            return
        playlist = self._local_playlist(name)
        if playlist is None:
            self._toast(f"There is no playlist called {name}")
            return
        paths = []
        device_only = 0
        for track in tracks:
            # A track that is only on the iPod has no local file to list, and
            # its device path in a playlist would ask the next sync to copy the
            # device's own files back onto it under scrambled names. Checked
            # here rather than beside each menu, because a row, an album and a
            # search result all arrive through this one door.
            if self._device_only_track(track):
                device_only += 1
                continue
            # A previewed file lives in a cache that gets pruned, so a playlist
            # entry pointing into it would stop resolving without anything
            # having been deleted on purpose. Keeping it first is what Add does
            # elsewhere.
            if track.state == STATE_PREVIEW and not self._keep_preview(track):
                continue
            paths.append(track.path)
        # An album holds what is only on the device beside what is here, and
        # Add to playlist takes the whole record, so a partial refusal is the
        # ordinary case rather than the corner one: counting only what landed
        # would report adding an album that arrived three tracks short.
        refused = (
            f" · {plural(device_only, 'track')} only on the iPod, with no copy "
            "here to add"
            if device_only
            else ""
        )
        if not paths:
            if device_only:
                self._toast(f"Nothing added to {name}{refused}")
            return

        added = add_entries(playlist.path, paths)
        if added is None:
            self._toast(f"Could not write {name}")
            return
        if not added:
            self._toast(f"Already in {name}{refused}")
            return
        self._after_playlist_change(
            name, f"{plural(added, 'track')} added to {name}{refused}"
        )

    def _remove_track_from_playlist(self, name, track):
        playlist = self._local_playlist(name)
        if playlist is None:
            return
        removed = remove_entry(playlist.path, track.path)
        if removed is None:
            self._toast(f"Could not write {name}")
            return
        if not removed:
            # The file no longer lists it, so nothing failed and there is
            # nothing to write: the row was painted from an older reading of a
            # playlist something else has edited since. Re-read so the window
            # catches up with the file rather than sending the user to check
            # permissions on one that is perfectly writable.
            self._load_local_playlists()
            self._populate_playlist_rail()
            self._toast(f"That track is no longer in {name}")
            return
        # Said plainly, because the track itself is untouched: it is still in
        # the library, and still on the iPod if it was already copied there.
        self._after_playlist_change(name, f"Removed from {name}")

    def _move_track_between(self, source_name, target_name, track):
        if target_name is None:
            self.on_new_playlist(
                None,
                then=lambda new: self._move_track_between(source_name, new, track),
            )
            return
        source = self._local_playlist(source_name)
        target = self._local_playlist(target_name)
        if source is None or target is None:
            self._toast("That playlist is no longer there")
            return
        if add_entries(target.path, [track.path]) is None:
            self._toast(f"Could not write {target_name}")
            return
        # Only after the track has landed in the target: the opposite order
        # loses it entirely if the second write fails.
        if remove_entry(source.path, track.path) is None:
            # Half a move is a copy, and it is reported as one: both lists now
            # hold the track and both are staged, so a move called finished
            # here would put it on the device twice without a word. A source
            # that simply no longer listed it is not this - that is the move
            # arriving at the state it was asked for.
            self._after_playlist_change(
                target_name,
                f"Copied to {target_name}, but could not remove it from "
                f"{source_name}",
                also=source_name,
            )
            return
        self._after_playlist_change(
            target_name, f"Moved to {target_name}", also=source_name
        )

    def _after_playlist_change(self, name, message, also=None):
        """Re-read, re-queue and repaint after an edit, then say what happened."""
        self._load_local_playlists()
        names = [name] if also is None or also == name else [name, also]
        note = self._stage_playlists(names)
        self._populate_playlist_rail()
        self._toast(message + note)

    def _stage_playlist(self, name):
        return self._stage_playlists([name])

    def _stage_playlists(self, names):
        """Queue playlists and their tracks for the next sync, if they can be.

        Returns what to add to the toast about the edit, so one sentence says
        both what changed here and whether the device will hear about it.
        Editing with no iPod attached is not a failure: the list is kept on
        this computer either way, and syncing it is a separate, explicit act.

        Both ends of a move go in one call rather than one each: staging can
        have to read tags first, and the second reading supersedes the first,
        so a playlist staged and then left behind would be reported as queued
        while the queue never heard of it.
        """
        playlists = [
            playlist
            for playlist in (self._local_playlist(name) for name in names)
            if playlist is not None
        ]
        if not playlists:
            return ""
        if not self.mount_point or self.device_identity is None:
            return ""
        # An emptied playlist still has to reach the sync, which is what
        # removes its copy from the device; one that was never there has
        # nothing to say, and what is already staged names a list the sync
        # would now find nothing in.
        wanted = []
        for playlist in playlists:
            if not playlist.entries and not self._playlist_on_device(playlist.name):
                self.unqueue_source(playlist.path)
            else:
                wanted.append(playlist)
        if not wanted:
            return ""
        if self.playlist_unavailable:
            return f" · not queued: {self.playlist_unavailable.lower()}"
        # A named playlist implies wanting the name read aloud, exactly as
        # choosing a grouping under Sync options does: with no screen, the
        # spoken name is the only way to find the playlist again.
        self.playlist_voiceover.set_active(True)
        queued = self._queue_playlists(
            [playlist.path for playlist in wanted], show_toast=False
        )
        if queued is None:
            return " · reading track details before queueing"
        return " · queued for sync"

    def _send_playlist_to_ipod(self, name):
        note = self._stage_playlist(name)
        self._populate_playlist_rail()
        self._toast(f"{name}{note}" if note else f"{name} could not be queued")

    # -------------------------------------------------- adding from YouTube

    def _add_result_to_playlist(self, name, result):
        if name is None:
            self.on_new_playlist(
                None, then=lambda new: self._add_result_to_playlist(new, result)
            )
            return
        if self._local_playlist(name) is None:
            # Said rather than passed over: a download that never starts and a
            # menu that closes on nothing are the same thing to look at, and
            # the local add says this much when a playlist has gone.
            self._toast(f"There is no playlist called {name}")
            return
        self._start_youtube_download(
            result.url,
            single=True,
            busy_message=f"Downloading {result.title}",
            on_failure=lambda: self._report_download_failure(result),
            playlist=name,
            video_id=result.video_id,
        )

    def _add_download_to_playlist(self, name, video_id, sources):
        """Put what a download produced into the playlist that asked for it.

        Found by video id rather than by what the run reported, because the
        same press has to work for a video already in the music folder: that
        download reports nothing new, and the file it would have written is
        already sitting there.
        """
        playlist = self._local_playlist(name)
        if playlist is None:
            return f"Downloaded, but {name} is no longer there"
        path = downloaded_file(video_id, YOUTUBE_LIBRARY)
        if path is None:
            files = [
                source
                for source in sources
                if Path(source).suffix.lower() in AUDIO_EXTENSIONS
            ]
            path = files[0] if len(files) == 1 else None
        if path is None:
            return f"Downloaded, but could not tell which track to add to {name}"
        added = add_entries(playlist.path, [str(path)])
        if added is None:
            return f"Downloaded, but could not write {name}"
        self._load_local_playlists()
        note = self._stage_playlist(name)
        self._populate_playlist_rail()
        return (f"Added to {name}" if added else f"Already in {name}") + note

    # ----------------------------------------------------- making a playlist

    def _name_dialog(self, heading, body, response, verb, initial, taken, answered):
        """Ask for a playlist's name, and refuse the ones that will not do.

        Naming a new playlist and renaming an existing one are the same
        question asked twice, so they are one dialog: what a name may be is
        decided by name_problem either way, and a rule about it that only half
        the app enforced would be a rule the user meets on the second try.

        `answered` is handed the dialog and the field to connect what to do
        with the name, because what that is - create a file, move one - is the
        only part that differs once the name has been accepted. The dialog is
        returned so a check can open one and read what it offers.
        """
        entry = Adw.EntryRow(title="Name")
        entry.set_text(initial)
        entry.set_activates_default(True)
        problem = label("", "sf-caption", "sf-alert", wrap=True)
        problem.set_visible(False)

        fields = Adw.PreferencesGroup()
        fields.add(entry)

        dialog = Adw.AlertDialog(heading=heading, body=body)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(fields)
        box.append(problem)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response(response, verb)
        dialog.set_response_appearance(response, Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response(response)
        dialog.set_close_response("cancel")

        def revalidate(*_args):
            # While it is being typed rather than after the button is pressed:
            # the name becomes a filename on a FAT volume and the sentence
            # saying why one will not do belongs beside the field it is about.
            reason = name_problem(entry.get_text(), taken)
            problem.set_text(reason or "")
            problem.set_visible(bool(reason))
            dialog.set_response_enabled(response, reason is None)

        entry.connect("changed", revalidate)
        revalidate()
        answered(dialog, entry)
        dialog.present(self)
        return dialog

    def on_new_playlist(self, _button=None, then=None):
        """Ask for a name, and nothing else.

        The whole of making a playlist: the file is written when this dialog
        closes, so there is nothing to choose, nothing to wait for, and nothing
        that needs an iPod attached.
        """
        taken = [playlist.name for playlist in self._shown_playlists()]
        return self._name_dialog(
            "New playlist",
            "Kept in "
            f"{home_relative(PLAYLIST_LIBRARY)}. Add songs to it from your "
            "library or from YouTube, then sync to put it on the iPod.",
            "create",
            "Create",
            default_name(taken),
            taken,
            lambda dialog, entry: dialog.connect(
                "response", self._on_new_playlist_response, entry, then
            ),
        )

    def _on_new_playlist_response(self, _dialog, response, entry, then):
        if response != "create":
            return
        name = entry.get_text().strip()
        if name_problem(name, [p.name for p in self._shown_playlists()]):
            return
        if create_local_playlist(PLAYLIST_LIBRARY, name) is None:
            self._toast(f"Could not create {name}")
            return
        self._load_local_playlists()
        if then is not None:
            then(name)
        else:
            self._populate_playlist_rail()
            self._toast(f"{name} created")
        self._select_playlist(name)

    def on_rename_playlist(self, name):
        playlist = self._local_playlist(name)
        if playlist is None:
            return None
        taken = [
            other.name for other in self._shown_playlists() if other.name != name
        ]
        return self._name_dialog(
            "Rename playlist",
            "The name is what the iPod says out loud, so renaming a "
            "playlist that is already on the device removes the old one "
            "and syncs the new name."
            if self._playlist_on_device(name)
            else "The name is what the iPod will say out loud.",
            "rename",
            "Rename",
            name,
            taken,
            lambda dialog, entry: dialog.connect(
                "response", self._on_rename_response, name, entry
            ),
        )

    def _on_rename_response(self, _dialog, response, old_name, entry):
        if response != "rename":
            return
        new_name = entry.get_text().strip()
        taken = [
            other.name for other in self._shown_playlists() if other.name != old_name
        ]
        if name_problem(new_name, taken) or new_name == old_name:
            return
        playlist = self._local_playlist(old_name)
        if playlist is None:
            return
        # The queue names the file, and the file is about to be called
        # something else, so what is staged has to go with the old name.
        self.unqueue_source(playlist.path)
        if rename_local_playlist(playlist.path, new_name) is None:
            self._toast(f"Could not rename {old_name}")
            return
        self._load_local_playlists()
        self.current_playlist = new_name
        note = self._stage_playlist(new_name)
        self._populate_playlist_rail()
        self._toast(f"Renamed to {new_name}{note}")
        # The device knows the old name and nothing will remove it: the sync
        # writes the playlist its file is called, so the renamed copy would
        # arrive beside the one it replaces.
        if self._playlist_on_device(old_name):
            self._remove_device_playlist(old_name)

    # ------------------------------------------------ importing and removing

    def on_import_playlist(self, _button):
        """Take a playlist another program wrote, and adopt it as one of ours.

        The compatibility path: an M3U or PLS exported from Rhythmbox,
        Strawberry or anything else is copied into the playlist folder with its
        entries resolved, so from then on it is an ordinary playlist here
        rather than a file that has to be found again to change anything.
        """
        dialog = Gtk.FileDialog(title="Import a playlist file")
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
            self._import_playlist(path)

        dialog.open(self, None, chosen)

    def _import_playlist(self, source):
        taken = [playlist.name for playlist in self._shown_playlists()]
        path, tracks, problem = import_playlist_file(PLAYLIST_LIBRARY, source, taken)
        if path is None:
            self._toast(problem)
            return
        self._load_local_playlists()
        note = self._stage_playlist(path.stem)
        self.current_playlist = path.stem
        self._populate_playlist_rail()
        self._toast(f"Imported {path.stem} · {plural(tracks, 'track')}{note}")

    def on_remove_playlist(self, name):
        playlist = self._local_playlist(name)
        on_device = self._playlist_on_device(name)
        if playlist is None and not on_device:
            return
        if playlist is None and (
            not self.mount_point or self.device_identity is None
        ):
            self._toast("Connect an iPod before removing its playlists")
            return
        if playlist is not None and on_device:
            body = (
                f"{name}\n\n"
                "The playlist is deleted here and removed from the iPod. The "
                "songs it lists stay in your library and on the iPod."
            )
        elif playlist is not None:
            body = (
                f"{name}\n\n"
                "The playlist is deleted. The songs it lists stay in your "
                "library."
            )
        else:
            body = (
                f"{name}\n\n"
                "Only the playlist is removed and the database is rebuilt. "
                "The songs it lists stay on the iPod."
            )
        dialog = Adw.AlertDialog(heading="Delete this playlist?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Delete")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", self._on_playlist_remove_response, name, self.device_identity
        )
        dialog.present(self)

    def _on_playlist_remove_response(
        self, _dialog, response, name, device_identity
    ):
        if response != "remove":
            return
        playlist = self._local_playlist(name)
        on_device = self._playlist_on_device(name)
        if playlist is not None:
            self.unqueue_source(playlist.path)
            if not delete_local_playlist(playlist.path):
                self._toast(f"Could not delete {name}")
                return
            self._load_local_playlists()
            if self.current_playlist == name:
                self.current_playlist = None
            self._populate_playlist_rail()
            if not on_device:
                self._toast(f"{name} deleted")
                return
        if not self._confirmed_device(device_identity):
            return
        self._remove_device_playlist(name)

    def _remove_device_playlist(self, name):
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

    # ----------------------------------------------------------- reordering

    def _reorder_playlist(self, source, target):
        """Move a track within the playlist and write the new order out.

        The order is the playlist, so it has to survive the app closing. For a
        playlist made here that means rewriting its file and staging it, like
        any other edit; for one that exists only on the device it means
        rewriting the list on the device and rebuilding, because there is no
        local file to hold the order in the meantime.
        """
        if self.busy or self.current_playlist is None:
            return False
        if source == target:
            return True

        playlist = self._local_playlist(self.current_playlist)
        if playlist is not None:
            if not move_entry(playlist.path, source, target):
                self._toast("Could not rewrite the playlist")
                return False
            self._after_playlist_change(playlist.name, "Playlist reordered")
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
