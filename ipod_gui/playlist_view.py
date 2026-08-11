"""Playlists: the ones you make here, and the ones already on the device.

A playlist you make is a file of your own - one M3U per list under
~/Music/Playlists, which playlists.py owns. That is what makes creating one a
matter of typing a name: there is no iPod to wait for, nothing to export from
another program first, and the list exists the moment it is named. Syncing
hands that file to ipod-sync.sh exactly as choosing a playlist file used to, so
nothing about how a playlist reaches the device has changed.

The device's own playlists are shown beside them - a folder or tag grouping the
sync generated, or a list made on another computer. Those are not edited in
place, because their entries name scrambled four-letter files on the iPod
rather than files in your music folders. Copying one here is the way out of
that: the library has already matched every copy on the device to the file it
was made from, so the same list is written down again as an M3U of our own,
and from then on it is an ordinary playlist.

Owns the sidebar rail, the Playlists view's rail and detail, the shelf of tiles
at the top of the library page, the ⋯ menu every track row carries, and the
drag reordering that rewrites the list rather than only this window.

A playlist can have a custom cover kept only in the local playlist library.
Without one, it uses the first cover its tracks carry; only a list holding
nothing with artwork falls back to the placeholder its name generates.

Borrows from the window: `playlists` and `spoken` as the probe left them,
`device_tracks` and `library` to resolve an entry into a track, `mount_point`,
`device_identity`, `busy`, `speech_engine_available` and the
`playlist_unavailable` reason derived beside it to know what can reach the
device, `_library_scan_running` and `_device_snapshot_ready` to know
whether those two readings can be quoted yet, and `show_view`, `_run`,
`_toast`, `_confirmed_device`, `_sync_options`, `_queue_playlists`,
`is_queued`, `unqueue_source`, `focus_search`, `on_delete_track` and
`_keep_preview` to act on what an edit changed.
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
from .text import SPOKEN_NAMES_LOST, home_relative, plural
from .device import playlist_file, write_playlist
from .youtube import downloaded_file
from .model import Track
from .playlists import (
    add_entries,
    create_local_playlist,
    default_name,
    delete_local_playlist,
    IMAGE_GONE,
    IMAGE_UNSUPPORTED,
    playlist_custom_cover,
    remove_playlist_cover,
    import_playlist_file,
    local_playlists,
    merge_with_device,
    move_entry,
    name_problem,
    playlist_contents,
    PLAYLIST_COVER_SUFFIXES,
    PLAYLIST_GONE,
    remove_entry,
    rename_local_playlist,
    set_playlist_cover,
    TARGET_GONE,
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

    def _shown_playlist(self, name):
        """The one playlist a name means on this page, or None.

        The first of them, and asked here so that everything working from a
        name picks the same row: two shown playlists can carry one name, since
        `merge_with_device` only keeps the local names apart and a volume root
        holding both "Gym.m3u" and "Gym.pls" is two device playlists keyed on
        the same stem. A page reading one and a press reading the other is a
        dialog quoting a count the page never showed.
        """
        for playlist in self._shown_playlists():
            if playlist.name == name:
                return playlist
        return None

    def _local_playlist(self, name):
        folded = (name or "").casefold()
        for playlist in self.local_playlists:
            if playlist.name.casefold() == folded:
                return playlist
        return None

    def _playlist_on_device(self, name):
        folded = (name or "").casefold()
        return any(other.casefold() == folded for other, _entries in self.playlists)

    def _playlists_only_on_device(self):
        """The shown playlists that no file here backs, in the order shown.

        The one thing a playlist made here can do that these cannot is be
        edited, so this is who every menu offering a list of playlists is
        leaving out - and, once copied here, who it would go on to offer.
        """
        return [
            playlist
            for playlist in self._shown_playlists()
            if not playlist.editable
        ]

    def _still_reading(self):
        """Which of the two readings a copy is worked out from is outstanding.

        Named rather than merely counted, because the page says while refusing
        what it is waiting for, and the two waits are not the same wait: the
        library scan is this computer's music folders, while the device
        snapshot is the tag read over USB that every plug-in starts and a
        failed read leaves undone. Telling someone with a fully scanned
        library that their music folders are being read is answering wrong in
        a quieter way.

        None once both have landed, which is the only state a figure is given
        in.
        """
        if self._library_scan_running:
            return "your music folders"
        if not self._device_snapshot_ready:
            return "the tracks on the iPod"
        return None

    def _entries_here_for(self, playlist):
        """A device playlist's entries as files this computer holds.

        Returns those paths in the order the device lists them, and how many
        entries nothing here answers for - or None while either reading this
        is drawn from is still going on.

        An entry names a copy under iPod_Control/Music, and which of this
        computer's files that copy was made from is a question the library has
        already answered: a track claimed by the device carries the device's
        path as its relpath, because that is what the row it draws points at.
        So the mapping is read back off the library rather than worked out
        again here, and a copy this computer cannot account for - a song added
        from another machine, or one since deleted here - is counted instead
        of guessed at.

        Both of those readings arrive late, and both repaint this page while
        they are still arriving: a library scan republishes `library.tracks`
        in batches of 25, and `device_tracks` is empty from the probe until
        the tag read over USB finishes, so no track is claimed at all. A count
        taken from either is a strict undercount of what is here, and it is
        the count a copy is offered on - so there is no figure to give until
        both have finished, and None is that answer rather than a number the
        caller cannot tell apart from a real one. A press is refused for the
        same second the note refuses to quote it: the file this writes is what
        the next sync puts back on the device, and `create_local_playlist`
        will not overwrite it afterwards.
        """
        if self._still_reading() is not None:
            return None
        here = {}
        for track in (*self.library.tracks, *self.library.queued_only):
            if track.on_ipod:
                here.setdefault(track.relpath, track.path)
        found, missing = [], 0
        for entry in playlist.entries:
            path = here.get(entry)
            if path is None:
                missing += 1
            else:
                found.append(path)
        return found, missing

    def _copy_here_offer(self, playlist, resolved):
        """What copying this device playlist here would do: one answer, thrice.

        Whether the press is on offer, what the note above the buttons says
        about it, and what the button's own tooltip says - decided together
        because they were decided apart, and disagreed: a playlist the device
        holds nothing in read as one with nothing missing, so the note invited
        a press ("until you copy it here") the button was already refusing
        ("nothing here to make a list out of").

        `resolved` is what `_entries_here_for` answered, None included.
        """
        if resolved is None:
            reading = self._still_reading()
            return (
                False,
                f"only on the iPod · still reading {reading}",
                f"Still reading {reading}, so what a copy would carry is not "
                "known yet",
            )
        if not playlist.entries:
            return (
                False,
                "empty on the iPod, so there is nothing here to copy",
                "This playlist lists no tracks on the iPod, so there is "
                "nothing here to make a list out of",
            )
        here, missing = resolved
        note = (
            "only on the iPod until you copy it here"
            if not missing
            else "only on the iPod, with "
            f"{plural(missing, 'track')} this computer does not have"
        )
        if not here:
            return (
                False,
                note,
                "None of the songs in this playlist are on this computer, so "
                "there is nothing here to make a list out of",
            )
        return True, note, "Make this a playlist you can edit and add songs to"

    def _playlists_listing(self, track):
        """The names of the playlists made here that list this file.

        Both the tick beside a playlist in a track's menu and the sentence
        warning what a deletion leaves behind are this same question, so it is
        asked once: an entry is a path, and a playlist holds a track when it
        names it.
        """
        return [
            playlist.name
            for playlist in self.local_playlists
            if track.path in playlist.entries
        ]

    def _playlist_state(self, playlist):
        return (
            STATE_IPOD if self._playlist_on_device(playlist.name) else STATE_LIBRARY
        )

    def _playlist_index(self, editable):
        """What an entry is resolved against, built once for several playlists.

        Handed to `_playlist_tracks` by the paints that resolve every playlist
        at once - a rail of them, and the shelf beside it - because building
        this per playlist walks the whole library once per row.
        """
        if editable:
            return {track.path: track for track in self.library.all_tracks()}
        return {track.relpath: track for track in self.device_tracks}

    def _playlist_tracks(self, playlist, index=None):
        """A playlist's entries as tracks, in the order it lists them.

        A local entry is a path in a music folder, so it is looked up in the
        library; a device playlist's entry is a path on the iPod, so it is
        looked up in what was read off the device. Either way an entry nothing
        knows about still becomes a row, named after its file: a playlist that
        silently lost a line would be worse than one showing a track whose tags
        could not be read.
        """
        if index is None:
            index = self._playlist_index(playlist.editable)
        if not playlist.editable:
            return [
                index.get(entry)
                or Track(entry, {"title": Path(entry).stem}, STATE_IPOD, relpath=entry)
                for entry in playlist.entries
            ]
        return [
            index.get(entry)
            or Track(entry, {"title": Path(entry).stem}, STATE_LIBRARY)
            for entry in playlist.entries
        ]

    def _playlist_art(self, playlist, index=None):
        """A computer-only custom cover, then the first cover in its tracks."""
        if playlist.editable:
            custom = playlist_custom_cover(playlist.path)
            if custom is not None:
                return str(custom)
        for track in self._playlist_tracks(playlist, index):
            if track.art:
                return track.art
        return None

    def _playlist_covers(self, playlists):
        """The artwork for each of these playlists, by name.

        One index per kind rather than one per playlist: a rail of twenty
        lists would otherwise walk the library twenty times to draw twenty
        covers, on every repaint.
        """
        indexes = {}
        covers = {}
        for playlist in playlists:
            if playlist.editable not in indexes:
                indexes[playlist.editable] = self._playlist_index(playlist.editable)
            covers[playlist.name] = self._playlist_art(
                playlist, indexes[playlist.editable]
            )
        return covers

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
        # The stack hands every page the width of its widest one, so this rail
        # sets the floor for the whole window: at 240 it put the content pane's
        # minimum above the 660 the window advertises, and a window narrower
        # than its own contents lays out wrong rather than clipping.
        rail_box.set_size_request(200, -1)
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
        # Resolved once for both rails and the shelf below them, which are
        # three drawings of the same playlists.
        covers = self._playlist_covers(shown)
        for playlist in shown:
            art = covers[playlist.name]
            self.playlist_rail.append(self._rail_row(playlist, True, art))
            self.playlist_list.append(self._rail_row(playlist, False, art))

        names = {playlist.name for playlist in shown}
        if not shown:
            self.current_playlist = None
            self._clear_playlist_detail()
        elif self.current_playlist not in names:
            self.current_playlist = shown[0].name
        if self.current_playlist is not None:
            self._show_playlist(self.current_playlist)
        self._populate_playlist_shelf(shown, covers)

    def _rail_row(self, playlist, compact, art=None):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("sf-nav-row")
        row = Gtk.Box(spacing=9)
        row.append(make_cover(art, PLAYLIST_ROW_COVER, playlist.name, "tiny"))
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

    def _populate_playlist_shelf(self, shown=None, covers=None):
        """The tiles at the top of the library page.

        The playlists and their artwork are passed in by the rail, which has
        just resolved both; asked for on its own - by a check, or by a caller
        that only wants this section - it works them out for itself.
        """
        clear_children(self.playlist_shelf)
        if shown is None:
            shown = self._shown_playlists()
        if covers is None:
            covers = self._playlist_covers(shown)

        for playlist in shown[:5]:
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
            box.append(
                make_cover(
                    covers.get(playlist.name), PLAYLIST_TILE_COVER, playlist.name
                )
            )
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
        self.shelf_section.set_visible(bool(shown))

    def _show_playlist(self, name):
        playlist = self._shown_playlist(name)
        if playlist is None:
            self._clear_playlist_detail()
            return

        self.playlist_heading.set_text(playlist.name)
        # The note and the Copy button ask the library the same question about
        # a device playlist, and this page is repainted on every batch a
        # library scan publishes, so it is asked once for the pair.
        resolved = None if playlist.editable else self._entries_here_for(playlist)
        self._fill_playlist_note(playlist, resolved)
        self._fill_playlist_actions(playlist, resolved)

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

    def _fill_playlist_note(self, playlist, resolved):
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
        # Said outright rather than left to be inferred from a page with no Add
        # songs on it: this is the whole difference between the two kinds of
        # playlist, and the button below it is what closes it. What a copy
        # could not carry is said here as well, rather than beside the buttons
        # where it would be a third thing in a row that has to fit the window's
        # minimum width - this line wraps, and that row does not.
        if not playlist.editable:
            _offered, note, _tooltip = self._copy_here_offer(playlist, resolved)
            parts.append(note)
        self.playlist_voice_note.append(label(" · ".join(parts), "sf-body", wrap=True))

    def _fill_playlist_actions(self, playlist, resolved):
        """What this playlist is for, in buttons, and a menu holding the rest.

        Two of them for a playlist made here, one for a playlist only the
        device has, and the ⋯ beside either.

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

        def more_menu():
            button = row_menu_button(
                lambda: self._playlist_actions_menu(playlist.name),
                f"More for {playlist.name}",
                dim=False,
            )
            button.set_sensitive(not self.busy)
            self.playlist_actions.append(button)

        if not playlist.editable:
            # One button and the ⋯, the way a playlist made here is laid out,
            # and for the same two reasons. Taking a playlist off the device is
            # the thing here that choosing again does not undo, which is where
            # this window keeps that; and a third control in this row added
            # 25px to the minimum width the whole window has to advertise,
            # which every other page would then be held to as well.
            # tests/gui-window-minimum.py measures that rather than eyeing it.
            #
            # The copy leads the row because everything this page cannot offer
            # - adding songs, reordering, being one of the playlists a track's
            # ⋯ lists - is on the other side of it.
            offered, _note, tooltip = self._copy_here_offer(playlist, resolved)
            action(
                "Copy to this computer",
                lambda: self.on_copy_playlist_here(playlist.name),
                "accent",
                tooltip=tooltip,
                sensitive=offered,
            )
            more_menu()
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
        more_menu()

    def _playlist_actions_menu(self, name):
        """What a playlist's ⋯ holds, which is not the same for both kinds.

        A playlist only the device has cannot be renamed - the name is the
        filename, and there is no file here to rename - so what is left is
        taking it off the iPod, which is exactly what this menu is for.
        """
        popover = Gtk.Popover()
        playlist = self._local_playlist(name)
        if playlist is None:
            rows = [
                self._menu_row(
                    popover,
                    "Remove from iPod…",
                    lambda: self.on_remove_playlist(name),
                )
            ]
        else:
            rows = [
                self._menu_row(
                    popover,
                    "Choose custom cover…",
                    lambda: self.on_choose_playlist_cover(name),
                )
            ]
            if playlist_custom_cover(playlist.path) is not None:
                rows.append(
                    self._menu_row(
                        popover,
                        "Use song artwork",
                        lambda: self.on_remove_playlist_cover(name),
                    )
                )
            rows.extend(
                [
                    self._menu_row(
                        popover, "Rename…", lambda: self.on_rename_playlist(name)
                    ),
                    self._menu_row(
                        popover, "Delete…", lambda: self.on_remove_playlist(name)
                    ),
                ]
            )
        return self._menu_popover(name, rows, popover)

    def on_choose_playlist_cover(self, name):
        """Choose an image to copy into the computer's playlist library.

        Filtered to the formats the store can hold, so the folder offers the
        files that can actually be used rather than every file in it and a
        refusal afterwards.
        """
        dialog = Gtk.FileDialog(title=f"Choose a cover for {name}")
        images = Gtk.FileFilter()
        images.set_name("Images")
        for suffix in PLAYLIST_COVER_SUFFIXES:
            images.add_suffix(suffix.lstrip("."))
        dialog.set_default_filter(images)

        def chosen(file_dialog, result):
            try:
                image = file_dialog.open_finish(result)
            except GLib.Error:
                return
            self._playlist_cover_chosen(name, image)

        dialog.open(self, None, chosen)
        return dialog

    def _playlist_cover_chosen(self, name, image):
        """What the file dialog's answer means, once it has one.

        A file on a share the desktop has mounted over the network has no path
        on this computer, and the copy is a copy of a local file. Said rather
        than passed over, or the press does nothing and explains nothing.
        """
        path = image.get_path()
        if not path:
            self._toast("That location is not a local file")
            return False
        return self._set_playlist_cover(name, path)

    def _set_playlist_cover(self, name, image):
        """Store a chosen image without adding it to the iPod sync queue.

        Each way of failing is its own sentence: the store tells a format it
        cannot hold from a folder it could not write, and only the first of
        those is answered by choosing a different image.
        """
        playlist = self._local_playlist(name)
        if playlist is None:
            self._playlist_gone(name)
            return False
        saved = set_playlist_cover(playlist.path, image)
        if saved is PLAYLIST_GONE:
            self._playlist_gone(name)
            return False
        if saved is IMAGE_UNSUPPORTED:
            self._toast("Choose a JPEG, PNG or WebP image")
            return False
        if saved is IMAGE_GONE:
            self._toast("That image is no longer there")
            return False
        if saved is None:
            self._toast(
                f"Could not save the cover into {home_relative(PLAYLIST_LIBRARY)}"
            )
            return False
        self._populate_playlist_rail()
        self._select_playlist(name)
        self._toast("Playlist cover saved on this computer")
        return True

    def on_remove_playlist_cover(self, name):
        """Return a local playlist to its first song's artwork."""
        playlist = self._local_playlist(name)
        if playlist is None:
            self._playlist_gone(name)
            return False
        if not remove_playlist_cover(playlist.path):
            self._toast(f"Could not remove the custom cover for {name}")
            return False
        self._populate_playlist_rail()
        self._select_playlist(name)
        self._toast("Using artwork from the songs in this playlist")
        return True

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
            # Nothing here can be written into another playlist: the entry
            # names a file on the iPod, or a path only the folder this list
            # sits in resolves. Taking it out of a list this app owns writes no
            # path anywhere, though, so that stays on offer - otherwise a line
            # another program left behind could never be removed at all.
            if inside is None:
                return self._menu_popover(
                    "This track is only on the iPod",
                    [self._unaddable_note()],
                )
            popover = Gtk.Popover()
            return self._menu_popover(
                inside.name,
                [
                    self._unaddable_note(),
                    Gtk.Separator(margin_top=4, margin_bottom=4),
                    self._menu_row(
                        popover,
                        f"Remove from {inside.name}",
                        lambda: self._remove_track_from_playlist(inside.name, track),
                    ),
                ],
                popover,
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
                    ),
                    *self._delete_entry(track),
                ],
                # The list this row is in is the one place it cannot move to,
                # so it is filtered out - and with only that one made, "no
                # playlists yet" is a sentence the user can see is false while
                # standing in one the menu names two lines further down.
                empty_note="No other playlists",
            )
        return self._playlist_menu(
            "Add to playlist",
            lambda name: self._add_tracks_to_playlist(name, [track]),
            holding=set(self._playlists_listing(track)),
            extra=self._delete_entry(track),
        )

    def _delete_entry(self, track):
        """The Delete row a track's menu ends with, or nothing.

        Offered for a song this computer holds and nothing else: a device-only
        entry names a file on the iPod, which the row's own Remove takes off
        it, and a previewed one sits in a cache Device & Settings empties in
        one press. Last in the menu behind a separator, because it is the one
        thing in there that choosing again does not undo.
        """
        if self._device_only_track(track) or track.state == STATE_PREVIEW:
            return ()
        return ((
            "Delete from library…",
            lambda: self.on_delete_track(track),
        ),)

    @staticmethod
    def _named_briefly(playlists, most=3):
        """These playlists by name, as a sentence can carry them.

        Named rather than counted, because the reader is looking for one
        playlist in particular and a number cannot tell them whether it is in
        there. Capped all the same: a device holding twenty lists would
        otherwise put twenty names inside a menu.
        """
        names = [playlist.name for playlist in playlists]
        if len(names) <= most:
            return ", ".join(names)
        return ", ".join(names[:most]) + f" and {len(names) - most} more"

    @staticmethod
    def _unaddable_note():
        return label(
            "Playlists made here list files in your music folders, and this "
            "entry names none: it is on the iPod, or under a name only its "
            "own playlist resolves.",
            "sf-body",
            wrap=True,
            max_width_chars=32,
        )

    def _playlist_menu(
        self,
        heading,
        on_pick,
        holding=(),
        exclude=None,
        extra=(),
        empty_note="No playlists yet",
    ):
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
                label(empty_note, "sf-caption", "sf-dim", margin_start=6)
            )
        # What this list is leaving out, named rather than left silent. The
        # playlists on the rail and the playlists a track can be added to are
        # not the same set, and a menu that simply omits the difference reads
        # as a list that has lost the playlist the user is looking for.
        left_out = self._playlists_only_on_device()
        if left_out:
            rows.append(
                label(
                    f"Only on the iPod: {self._named_briefly(left_out)}. Open "
                    "one to copy it here.",
                    "sf-caption",
                    "sf-dim",
                    wrap=True,
                    max_width_chars=26,
                    margin_start=6,
                    margin_top=4,
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

    @staticmethod
    def _edit_step(playlist):
        """Which half of an edit failed, as a verb: "read" or "write".

        An edit is a read and a rewrite, and the two send the reader to two
        different places: a list symlinked onto a drive that is not plugged in
        is unreadable in a folder that is perfectly writable, and "could not
        write" would have them checking the permissions on the wrong thing.

        Only asked about an edit that answered None, so the playlist is one
        that is there: a file that has gone answers PLAYLIST_GONE and is
        repainted rather than blamed on the folder it used to sit in.
        """
        return "write" if playlist_contents(playlist.path)[1] else "read"

    def _playlist_gone(self, name):
        """Catch up with a playlist something else deleted, and say so.

        An edit is a read and a rewrite, so a playlist deleted while its rows
        are on screen is what the edit's read finds rather than anything
        watching the folder. Nothing failed and there is nothing to write: the
        window is painted from an older listing of the folder, and re-reading
        it is what takes the playlist off the rails - the answer a row the
        file has lost already gets, given here about the file itself.
        """
        self._populate_playlist_rail()
        self._toast(f"There is no playlist called {name}")

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
            self._playlist_gone(name)
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
        if added is PLAYLIST_GONE:
            self._playlist_gone(name)
            return
        if added is None:
            self._toast(f"Could not {self._edit_step(playlist)} {name}")
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
            # The menu only offers this for a playlist that was in the list
            # when it opened, so getting here means the list has moved on
            # since - the same thing the edit below finds one step later, and
            # answered the same way rather than passed over in silence.
            self._playlist_gone(name)
            return
        removed = remove_entry(playlist.path, track.path)
        if removed is PLAYLIST_GONE:
            self._playlist_gone(name)
            return
        if removed is None:
            self._toast(f"Could not {self._edit_step(playlist)} {name}")
            return
        if not removed:
            # The file no longer lists it, so nothing failed and there is
            # nothing to write: the row was painted from an older reading of a
            # playlist something else has edited since. Repainting re-reads the
            # folder, so the window catches up with the file rather than
            # sending the user to check permissions on one that is perfectly
            # writable.
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
            # Which of the two it is changes nothing to do: either way the
            # window is painted from a listing of the folder that has moved
            # on, so it re-reads it rather than leaving both on the rail.
            self._populate_playlist_rail()
            self._toast("That playlist is no longer there")
            return
        added = add_entries(target.path, [track.path])
        if added is PLAYLIST_GONE:
            self._playlist_gone(target_name)
            return
        if added is None:
            self._toast(f"Could not {self._edit_step(target)} {target_name}")
            return
        # Only after the track has landed in the target: the opposite order
        # loses it entirely if the second write fails.
        removed = remove_entry(source.path, track.path)
        if removed is PLAYLIST_GONE:
            # There is nothing left to take the track out of, so the move
            # arrived where it was asked to go. What is worth saying is that
            # the list it came from has gone, which is also why the rail is
            # about to lose a row.
            self._after_playlist_change(
                target_name,
                f"Moved to {target_name} · {source_name} is no longer there",
            )
            return
        if removed is None:
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

    def _staging_wanted(self, playlist, on_device=None):
        """Whether the next sync has anything to do about this playlist.

        An emptied playlist still has to reach the sync, which is what removes
        its copy from the device; one that was never there has nothing to say.

        `on_device` is whether the device holds the name the sync would find
        this playlist under, and is passed by a caller asking about a name the
        file has not taken yet. A rename passes False and means it: the dialog
        refuses every name the page already shows, the device's own playlists
        are among them, so no name a rename can end at is one over there.

        The exception is a rename that only changes case. The names a rename
        is refused drop the old one case-sensitively while the device is
        asked case-folded, so "Gym" -> "GYM" is allowed through and lands on
        the same folded name the iPod is already holding. What that costs is
        bounded to a sentence: an empty playlist renamed that way is told
        nothing is staged to replace it while the queue does take it, and the
        device copy comes off under either answer, so what the iPod ends up
        holding is the same.
        """
        if on_device is None:
            on_device = self._playlist_on_device(playlist.name)
        return bool(playlist.entries or on_device)

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
        # Unstaged where nothing is wanted, because what was queued names a
        # list the sync would now find nothing in: a change that has stopped
        # being one.
        wanted = []
        for playlist in playlists:
            if self._staging_wanted(playlist):
                wanted.append(playlist)
            else:
                self.unqueue_source(playlist.path)
        if not wanted:
            return ""
        if self.playlist_unavailable:
            return f" · not queued: {self.playlist_unavailable.lower()}"
        # Nothing to ask for beyond the queue itself: every sync is run with
        # spoken playlist names, so a list staged here already carries the one
        # thing that makes it findable on a device with no screen.
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
            # menu that closes on nothing are the same thing to look at. The
            # answer the local add gives, because this is the same playlist
            # having gone, met before the download rather than after it.
            self._playlist_gone(name)
            return
        self._start_youtube_download(
            result.url,
            single=True,
            busy_message=f"Downloading {result.title}",
            on_failure=lambda: self._report_download_failure(result.title),
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
        if added is PLAYLIST_GONE:
            # The same sentence the missing playlist above gets, and repainted
            # for the reason _playlist_gone repaints: the rail is still
            # showing a list the download was going into. Said rather than
            # toasted, because this runs where a download reports back and the
            # caller is what puts one sentence on screen.
            self._populate_playlist_rail()
            return f"Downloaded, but {name} is no longer there"
        if added is None:
            return f"Downloaded, but could not {self._edit_step(playlist)} {name}"
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
        self.current_playlist = name
        # The playlist exists from here on, so the rail, the list and the shelf
        # show it from here on too. What `then` goes on to do with it - start a
        # download, refuse an album that is all on the iPod - is its own errand
        # and none of them is under any obligation to paint a window.
        self._populate_playlist_rail()
        if then is not None:
            then(name)
        else:
            self._toast(f"{name} created")
        self._select_playlist(name)

    def _rename_refusal(self, playlist):
        """Why renaming this playlist would strand it on the iPod, or None.

        A rename the device holds is two halves: the old name comes off the
        iPod now, and the new one goes on at the next sync. The whole move
        therefore rests on the new name reaching the queue, and where it
        cannot the press is a playlist taken off the device with nothing able
        to put it back - Send to iPod wants exactly what staging wants, so it
        is refused for the same reason, and the list stays gone until that
        reason is fixed and the user works out they have to send it again.

        The empty playlist the device holds is why this is not the note
        _stage_playlists hands back. Nothing is staged for that one under
        either name and nothing needs to be, so the note is the same "" a
        refusal to stage would leave - while the removal is the whole of what
        its rename does over there, and the only thing that will ever take
        the old empty list off the iPod.

        Returns the sentence to say rather than a flag, because what is
        refused and how the refusal reads are one decision and both call
        sites say the same thing. Asked before the dialog opens and again
        before the file moves: a probe can put the playlist on the device
        while the dialog is standing open.

        The sentence names the reason and the consequence and no way out.
        The reason string already says what is missing, and every instruction
        this app could give here would be wrong or destructive: Delete on a
        playlist that is both here and on the device deletes the file here
        too, and a row the two share has no "Remove from iPod" of its own.
        """
        if not self._playlist_on_device(playlist.name):
            return None
        if not self.mount_point or self.device_identity is None:
            return None
        if not self._staging_wanted(playlist, on_device=False):
            return None
        if not self.playlist_unavailable:
            return None
        return (
            f"{self.playlist_unavailable}, so renaming {playlist.name} would "
            "take it off the iPod with nothing able to put the new name there"
        )

    def on_rename_playlist(self, name):
        playlist = self._local_playlist(name)
        if playlist is None:
            return None
        refused = self._rename_refusal(playlist)
        if refused:
            self._toast(refused)
            return None
        taken = [
            other.name for other in self._shown_playlists() if other.name != name
        ]
        if self._playlist_on_device(name):
            asked = (
                "The name is what the iPod says out loud, so renaming a "
                "playlist that is already on the device removes the old one"
            )
            if self._staging_wanted(playlist, on_device=False):
                asked += " and stages the new name for the next sync."
            else:
                # The empty playlist, which is also the only one that reaches
                # here with nothing able to speak a name: the removal is the
                # whole of what this press does over there, so a sentence
                # about staging would be describing a sync that has no reason
                # to mention this playlist at all.
                asked += (
                    ". This playlist lists no tracks, so nothing is staged "
                    "to replace it."
                )
            if not self.speech_engine_available:
                asked += f" {SPOKEN_NAMES_LOST}"
        else:
            asked = "The name is what the iPod will say out loud."
        return self._name_dialog(
            "Rename playlist",
            asked,
            "rename",
            "Rename",
            name,
            taken,
            # Which iPod this is, as the dialog opens: renaming a playlist the
            # device holds removes the old one from it, and a probe can swap
            # the mount underneath an open dialog.
            lambda dialog, entry, identity=self.device_identity: dialog.connect(
                "response", self._on_rename_response, name, entry, identity
            ),
        )

    def _on_rename_response(
        self, _dialog, response, old_name, entry, device_identity
    ):
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
        # Again, and before anything moves: the dialog was answered against a
        # window the last probe painted, and one landing while it stood open
        # can have put this playlist on the device since.
        refused = self._rename_refusal(playlist)
        if refused:
            self._toast(refused)
            return
        if rename_local_playlist(playlist.path, new_name) is None:
            self._toast(f"Could not rename {old_name}")
            return
        self._load_local_playlists()
        self.current_playlist = new_name
        # The queue named the file, and the file is called something else now,
        # so what was staged goes with the old name. After the move rather than
        # before it: a rename that could not happen would otherwise cancel a
        # sync the user was never told had stopped carrying this playlist.
        self.unqueue_source(playlist.path)
        note = self._stage_playlist(new_name)
        self._populate_playlist_rail()
        self._toast(f"Renamed to {new_name}{note}")
        # The device knows the old name and nothing will remove it: the sync
        # writes the playlist its file is called, so the renamed copy would
        # arrive beside the one it replaces. Unconditional because the refusal
        # above is what makes it safe - by here the new name is staged, or the
        # playlist lists nothing for a sync to stage, and either way this
        # takes nothing off the iPod that will not be replaced or is not
        # wanted there. A track the library has not read yet only defers the
        # first of those: staging goes away to read tags and commit later, so
        # this runs before the queue has heard anything, and a scan superseded
        # while it reads returns without queueing or saying so. What that
        # leaves behind is reachable, which is the whole difference - a
        # machine that got this far has the engine, so Send to iPod is on
        # offer and puts the playlist back, and the rename refused above is
        # the one with no such way back. Against the iPod the rename was
        # started on, though - this deletes a playlist and rebuilds a
        # database, and the one under the mount now may be a different device
        # that happens to carry a list of the same name.
        if self._playlist_on_device(old_name):
            if not self._confirmed_device(device_identity):
                return
            self._remove_device_playlist(old_name)

    # ------------------------------------------------------- copying it here

    def on_copy_playlist_here(self, name):
        """Make a playlist made somewhere else into one of ours.

        A list that reached the device from another program, another computer,
        or a sync run from the terminal is shown here but cannot be touched,
        because its entries name copies on the iPod and a playlist made here
        lists files in your music folders. The songs are usually still sitting
        in one: the library has already matched every copy on the device to
        the file it was made from, so the same list can be written down again
        in terms of this computer, and from then on it is an ordinary playlist.

        Confirmed only when something would be left behind. A copy that
        carries every track leaves the list on the device exactly as it was,
        so the press itself changes nothing over there; one that cannot is a
        decision, because the file written here becomes what the next sync
        writes back. Deleting the copy is not a way back out of that: a
        playlist that is both here and on the device is deleted from both,
        which is what its own dialog says before it does it.

        Refused outright while either reading it would be worked out from is
        still going on, and says which of the two it is waiting for, because a
        count from a half-read library is an undercount and this file is not
        offered twice.
        """
        playlist = self._shown_playlist(name)
        if playlist is None or playlist.editable:
            return None
        resolved = self._entries_here_for(playlist)
        if resolved is None:
            self._toast(
                f"Still reading {self._still_reading()} · try {name} again "
                "in a moment"
            )
            return None
        here, missing = resolved
        if not playlist.entries:
            self._toast(f"{name} is empty on the iPod, so there is nothing to copy")
            return None
        if not here:
            self._toast(f"None of the songs in {name} are on this computer")
            return None
        if not missing:
            self._copy_playlist_here(name, here, 0)
            return None
        dialog = Adw.AlertDialog(
            heading="Copy this playlist here?",
            body=(
                f"{name}\n\n"
                f"This computer holds {len(here)} of the "
                f"{plural(len(playlist.entries), 'track')} in {name}. The copy "
                f"lists those, and the next sync writes it back to the iPod, "
                f"so {plural(missing, 'track')} would go from the playlist "
                f"there. The songs themselves stay on the iPod."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("copy", "Copy")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", self._on_copy_here_response, name, here, missing
        )
        dialog.present(self)
        return dialog

    def _on_copy_here_response(self, _dialog, response, name, here, missing):
        if response != "copy":
            return
        self._copy_playlist_here(name, here, missing)

    def _copy_playlist_here(self, name, entries, missing):
        """Write the copy, then show the playlist it has become.

        Nothing is staged for a sync. The device is holding this playlist
        already, so copying it asks for no change over there - and where the
        copy is shorter than the list it came from, staging it would be the
        window quietly sending that shortening to the device on the strength
        of a press that said "copy".
        """
        path = create_local_playlist(PLAYLIST_LIBRARY, name, entries)
        if path is None:
            self._toast(
                f"Could not copy {name} into {home_relative(PLAYLIST_LIBRARY)}"
            )
            return
        self._load_local_playlists()
        self._populate_playlist_rail()
        self._select_playlist(path.stem)
        left = f" · {plural(missing, 'track')} left on the iPod" if missing else ""
        self._toast(
            f"{path.stem} copied here · {plural(len(entries), 'track')}{left}"
        )

    # ------------------------------------------------ importing and removing

    def _import_playlist(self, source):
        """Take a playlist another program wrote, and adopt it as one of ours.

        The compatibility path: an M3U or PLS exported from Rhythmbox,
        Strawberry or anything else is copied into the playlist folder with its
        entries resolved, so from then on it is an ordinary playlist here
        rather than a file that has to be found again to change anything.

        Nothing in the window reaches it: the file dialog that opened it went
        with the Device & Settings card, so meanwhile a foreign list is adopted
        by hand, by copying an .m3u into the playlist folder - which resolves
        none of its entries and reads no .pls at all. Kept as the seam the
        headless CLI and the Gio actions will hand a path to.

        Takes the file rather than choosing one, so what adopts it can be
        anything that has a path in hand.
        """
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
        spoken_cost = (
            "" if self.speech_engine_available else f"{SPOKEN_NAMES_LOST} "
        )
        if playlist is not None and on_device:
            body = (
                f"{name}\n\n"
                "The playlist is deleted here and removed from the iPod. "
                f"{spoken_cost}"
                "The songs it lists stay in your library and on the iPod."
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
                f"{spoken_cost}"
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
            if not delete_local_playlist(playlist.path):
                self._toast(f"Could not delete {name}")
                return
            self._load_local_playlists()
            if self.current_playlist == name:
                self.current_playlist = None
            # Only once the file has gone: a delete that failed would otherwise
            # leave the playlist sitting there, staged for a sync it has
            # quietly been taken out of.
            self.unqueue_source(playlist.path)
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
            moved = move_entry(playlist.path, source, target)
            if moved is PLAYLIST_GONE:
                self._playlist_gone(playlist.name)
                return False
            if moved is None:
                self._toast(
                    f"Could not {self._edit_step(playlist)} {playlist.name}"
                )
                return False
            if moved is TARGET_GONE:
                # The playlist was shortened past where the drag was aimed
                # rather than past the track being dragged, so that track is
                # still listed and only the destination has gone: what to say
                # is that the list moved under the drag, not that the track
                # left it.
                self._populate_playlist_rail()
                self._toast(f"{playlist.name} has changed - drag that again")
                return False
            if not moved:
                # The row was painted from an older reading of a playlist
                # something else has shortened since, so nothing failed and
                # there is nothing to write. Repainting re-reads the folder, so
                # the window catches up with the file rather than sending the
                # user to check the permissions on one that is perfectly
                # writable - the same answer removing a vanished row gives.
                self._populate_playlist_rail()
                self._toast(f"That track is no longer in {playlist.name}")
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
