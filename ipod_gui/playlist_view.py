"""The playlist rail, the shelf, and the detail page with its reordering.

Owns the sidebar rail, the Playlists view's own rail and detail, the shelf of
tiles that sits at the top of the library page, and the drag reordering that
rewrites the list on the device rather than only in this window. The three
places a playlist appears are built here, so a change to how one reads is not a
change spread over three files.

Borrows from the window: `playlists` and `spoken` as the probe left them,
`device_tracks` to resolve a listed path back to a track, and `show_view`,
`_run`, `_toast`, `_confirmed_device`, `_sync_options` and `_queue_playlist` to
change what the device holds.
"""

from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .config import REMOVE_SCRIPT, STATE_IPOD, SYNC_SCRIPT
from .text import plural
from .device import playlist_file, write_playlist
from .model import Track
from .widgets import (
    ELLIPSIZE_END,
    PLAYLIST_ROW_COVER,
    PLAYLIST_TILE_COVER,
    TRACK_PAGE_WIDTH,
    fill_tracks,
    label,
    make_cover,
    track_list_view,
)


class PlaylistViewMixin:
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
                "Names exist on the device only as spoken audio",
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
        row.append(make_cover(None, PLAYLIST_ROW_COVER, name, "tiny"))
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
            # Sized to the artwork, like the album cards below: without a cap
            # the longest playlist name sets the width of its own tile alone,
            # and the shelf becomes a row of tiles no two of which match.
            box.set_size_request(PLAYLIST_TILE_COVER, -1)
            # The card's inset is the button's own padding, which is even on
            # all four sides. Adding to it made the tile wide enough that four
            # playlists and the New tile no longer fit the shelf at the
            # window's opening size.
            box.append(make_cover(None, PLAYLIST_TILE_COVER, name))
            title = label(name, "sf-row-title", ellipsize=ELLIPSIZE_END)
            title.set_max_width_chars(16)
            title.set_width_chars(0)
            box.append(title)
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
        # The artwork beside it, so the shelf ends on the width it kept.
        new_tile.set_size_request(PLAYLIST_TILE_COVER, -1)
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

    # -------------------------------------------------- adding and removing

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
