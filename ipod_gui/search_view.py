"""One search field over two sources: the local library and YouTube.

Owns the search field in the header, the strip under it that offers a link the
clipboard already holds, both result sections, the skeleton rows that hold the
YouTube half's space while it loads, the header naming the playlist a pasted
link resolved to, `search_generation` and the debounce timeout that keep an
older query from landing on a newer one, and the thumbnail fetch behind the
rows.

Borrows from the window: `library` to match against, `mount_point`,
`device_identity`, `busy` and `youtube_unavailable` to decide whether a result
can be added, `current_view` and `show_view`, and `preview_result`,
`_start_youtube_download`, `_refresh_current_view` and `_update_now_playing` to
act on a result.
"""

import threading

from gi.repository import Gdk, GLib, Gtk

from .config import ART_CACHE, PREVIEW_CACHE
from .text import human_duration, plural
from .youtube import (
    SEARCH_DEBOUNCE_MS,
    SEARCH_MIN_QUERY,
    YOUTUBE_SEARCH_RESULTS,
    cache_thumbnail,
    cached_thumbnail_for,
    is_downloadable_url,
    linked_playlist,
    search_youtube,
    short_link,
    youtube_art_path,
)
from .previews import cached_preview_path
from .model import local_search_matches
from .widgets import (
    ELLIPSIZE_END,
    clear_children,
    fill_tracks,
    label,
    make_cover,
    row_menu_button,
    track_column_view,
)


class SearchViewMixin:
    def _build_search_entry(self):
        """The one field in the header, over both sources and over links.

        Which source a result came from is said by the section it lands in,
        not by asking the user to choose a source before they have typed
        anything.

        The placeholder names pasting because a field that only offers to
        search reads as a field that only searches, and looking a pasted link
        up here is the whole of what the Add from YouTube dialog was for. The
        sentence naming both sources does not fit in a 26-character field and
        a placeholder is clipped rather than ellipsised, so it goes in the
        tooltip - which is also what a screen reader reads out.
        """
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search or paste a link"
        )
        self.search_entry.set_tooltip_text(
            "Search your library and YouTube, or paste a link to look it up"
        )
        self.search_entry.add_css_class("sf-search")
        # A natural width rather than a hard floor. set_size_request pinned the
        # field at 260px in a header the window promises can reach 640px wide,
        # which made the content's real minimum wider than the window's own and
        # left GTK painting widgets outside the space it had allocated them.
        self.search_entry.set_width_chars(8)
        self.search_entry.set_max_width_chars(26)
        self.search_entry.set_valign(Gtk.Align.CENTER)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", lambda _e: self._clear_search())
        # Any text at all takes the clipboard offer away: what is being typed
        # is what the search is about, and a strip under the field suggesting
        # something else is then in the way of reading the results.
        self.search_entry.connect("changed", lambda _e: self._hide_clipboard_offer())
        # Focus rather than startup, because that is the moment the offer
        # answers: the user has come back from wherever they copied the link
        # and reached for the field they would paste it into.
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _c: self._read_clipboard_link())
        self.search_entry.add_controller(focus)
        return self.search_entry

    # ----------------------------------------------------- clipboard offer

    def _build_clipboard_offer(self):
        """The strip under the header offering a link already on the clipboard.

        Offered rather than typed into the field: a clipboard that happens to
        hold a link must never silently change what the next search is about.

        A strip rather than a popover over the field. A popover is placed by
        the compositor, and the one drop-down this window already has is
        dismissed on sight under GNOME's legacy HiDPI scaling; a suggestion
        that appears and vanishes would read as the window flickering.
        """
        row = Gtk.Box(spacing=10)
        row.add_css_class("sf-clipboard-offer")
        row.set_margin_start(18)
        row.set_margin_end(12)
        row.set_margin_top(7)
        row.set_margin_bottom(7)

        icon = Gtk.Image.new_from_icon_name("edit-paste-symbolic")
        icon.set_valign(Gtk.Align.CENTER)
        row.append(icon)

        # Full contrast at the body size, rather than the dim sf-body: the
        # strip is asking to be read and decided on, and this is the sentence
        # that says which link is being offered.
        self.clipboard_offer_label = label(
            "",
            hexpand=True,
            ellipsize=ELLIPSIZE_END,
            valign=Gtk.Align.CENTER,
        )
        row.append(self.clipboard_offer_label)

        use = Gtk.Button(label="Look it up")
        use.add_css_class("sf-button")
        use.add_css_class("accent")
        use.set_valign(Gtk.Align.CENTER)
        use.connect("clicked", lambda _b: self._use_clipboard_link())
        row.append(use)

        dismiss = Gtk.Button(icon_name="window-close-symbolic")
        dismiss.add_css_class("flat")
        dismiss.set_valign(Gtk.Align.CENTER)
        dismiss.set_tooltip_text("Dismiss this suggestion")
        dismiss.connect("clicked", lambda _b: self._hide_clipboard_offer())
        row.append(dismiss)

        self.clipboard_offer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        strip.append(row)
        strip.append(Gtk.Separator())
        self.clipboard_offer.set_child(strip)
        return self.clipboard_offer

    def _read_clipboard_link(self):
        """Ask what is on the clipboard, now that the field has been reached."""
        # Nothing to offer if the link could not be looked up: yt-dlp is what
        # turns a URL into the row saying what it is.
        if self.youtube_search_unavailable:
            return
        if self.search_entry.get_text().strip():
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().read_text_async(None, self._offer_clipboard_link)

    def _offer_clipboard_link(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        link = self._link_worth_offering(text)
        if link is None:
            return
        self._offered_links.add(link)
        self.clipboard_offer_url = link
        self.clipboard_offer_label.set_text(f"On your clipboard: {short_link(link)}")
        self.clipboard_offer.set_reveal_child(True)

    def _link_worth_offering(self, text):
        """The clipboard link to suggest, or None.

        Once per link. The field is empty every time the window is come back
        to, so an offer that returns after being turned down would be back on
        every visit, and a suggestion that cannot be got rid of is nagging.
        """
        text = (text or "").strip()
        if not is_downloadable_url(text):
            return None
        if self.search_entry.get_text().strip():
            return None
        if text in self._offered_links:
            return None
        return text

    def _use_clipboard_link(self):
        """Put the offered link in the field, which searches for it."""
        link = self.clipboard_offer_url
        self._hide_clipboard_offer()
        if not link:
            return
        # Typed into the field rather than looked up behind it, so what ran is
        # written where every other search the user runs is written, and can
        # be edited or cleared like one.
        self.search_entry.set_text(link)
        self.focus_search()

    def _hide_clipboard_offer(self):
        self.clipboard_offer_url = ""
        self.clipboard_offer.set_reveal_child(False)

    # ---------------------------------------------------------- search view

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
            self, columns=("title", "album", "state", "duration", "action", "menu")
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
        remote.append(self._build_playlist_header())
        self.search_youtube_rows = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        remote.append(self.search_youtube_rows)
        box.append(remote)
        return scroller

    def _build_playlist_header(self):
        """What a pasted playlist link actually is, above its first few rows.

        Three rows of a forty-track playlist look exactly like a forty-track
        playlist with three tracks in it. Capping the rows is right - an album
        link must not flood the section - but presenting what the cap left out
        as the whole thing is not, and Add all is what puts the rest back
        within reach, at the moment the playlist is on screen rather than
        behind a switch on a settings page.
        """
        self.search_playlist_row = Gtk.Box(spacing=12)
        self.search_playlist_row.add_css_class("sf-playlist-header")
        # Hidden until a link resolves to a list. Built visible, the page
        # would carry an empty tinted bar for as long as nothing had been
        # searched for, which is every state but the one it belongs to.
        self.search_playlist_row.set_visible(False)
        self.search_playlist_label = label(
            "", "sf-row-title", hexpand=True, wrap=True, valign=Gtk.Align.CENTER
        )
        self.search_playlist_row.append(self.search_playlist_label)

        self.search_playlist_add = Gtk.Button(label="Add all")
        self.search_playlist_add.add_css_class("sf-button")
        self.search_playlist_add.add_css_class("accent")
        self.search_playlist_add.set_valign(Gtk.Align.CENTER)
        self.search_playlist_add.connect(
            "clicked", lambda _b: self._download_playlist()
        )
        self.search_playlist_row.append(self.search_playlist_add)
        return self.search_playlist_row

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
        # The same ⋯ a library row carries, so adding a song to a playlist is
        # one gesture whether the song is already on this computer or not.
        row.append(
            row_menu_button(
                lambda r=result: self.result_menu(r),
                f"Playlists to add {result.title} to",
            )
        )
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

    def _can_fetch(self):
        """Whether a result could be downloaded at all, iPod or no iPod.

        Add copies a track onto the device, so it needs one attached. Adding
        the same track to a playlist does not: the download lands in a music
        folder and the playlist is a file on this computer, and neither has
        anything to do with what happens to be plugged in.
        """
        return bool(not self.busy and not self.youtube_unavailable)

    def focus_search(self):
        """Put the cursor in the one field that searches both sources.

        A verb, so the playlist page's "Add songs" can send the user to the
        search without reaching into the header's widgets: which field that is
        belongs to this module.
        """
        self.search_entry.grab_focus()

    def result_menu(self, result):
        """The ⋯ on a YouTube result, which downloads before it adds.

        Here rather than beside the track menu it mirrors, because whether a
        result can be fetched at all is this module's question; the list of
        playlists it offers is still the playlist view's.
        """
        if not self._can_fetch():
            return self._menu_popover(
                "Cannot download",
                [
                    label(
                        self.youtube_unavailable or "Something else is running",
                        "sf-body",
                        wrap=True,
                        max_width_chars=32,
                    )
                ],
            )
        return self._playlist_menu(
            "Add to playlist",
            lambda name: self._add_result_to_playlist(name, result),
        )

    # ------------------------------------------------- painting the results

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
        clear_children(self.search_youtube_rows)
        self._paint_playlist_header()

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

    def _paint_playlist_header(self):
        """Show the header whenever a link resolved to a list of tracks.

        Add all joins search_add_buttons like the Add on a row, so a device
        appearing or a script starting reaches it too: it is the same download
        against the same iPod, and a button left sensitive through a sync is
        one that starts a second script over the first.

        Only a list yt-dlp gave a length for is offered whole, though. A Mix or
        a channel reports none because there is no end to report, and one press
        would put a listing of no stated size onto a player with two gigabytes
        on it. The header still names it, which is what explains the three
        rows, and each row keeps its own Add.
        """
        playlist = None if self.search_loading else self.search_playlist
        self.search_playlist_row.set_visible(playlist is not None)
        if playlist is None:
            return
        self.search_playlist_label.set_text(playlist.summary())
        self.search_playlist_add.set_visible(playlist.length_known())
        if not playlist.length_known():
            return
        self.search_playlist_add.set_sensitive(self._can_download())
        self.search_playlist_add.set_tooltip_text(self._youtube_download_tooltip())
        self.search_add_buttons.append(self.search_playlist_add)

    def _set_search_note(self, text):
        self.search_note = text
        self._paint_youtube_section()

    def _cancel_search_timeout(self):
        if self._search_timeout is not None:
            GLib.source_remove(self._search_timeout)
            self._search_timeout = None

    # ----------------------------------------------------- running a search

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
        self.search_playlist = None
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
        # Read from the query rather than passed in: only a link can resolve
        # to a playlist, and the query that ran is the one this generation
        # belongs to, which the check above has already established.
        self.search_playlist = linked_playlist(self.search_query, results)
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
        self.search_playlist = None
        self.search_note = ""
        # Emptied last, so the search-changed this fires finds the state it
        # would have set already in place and stops rather than recursing.
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        if self.current_view() == "search":
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
            on_failure=lambda: self._report_download_failure(result.title),
        )

    def _download_playlist(self):
        """Fetch the whole list a pasted link named, not the rows shown.

        The same download the link dialog's Whole playlist switch ran: without
        --single, yt-dlp takes the list rather than the first video of it.
        """
        playlist = self.search_playlist
        if playlist is None or not self._can_download():
            return
        self._set_search_note("")
        name = playlist.title or "playlist"
        self._start_youtube_download(
            playlist.url,
            single=False,
            busy_message=f"Downloading {name}",
            on_failure=lambda: self._report_download_failure(name),
        )

    def _report_download_failure(self, name):
        """Say in the section that this particular download did not finish.

        The toast is gone by the time the eye returns to what was being added,
        and that row or header is what the user is looking at - unless the
        press was Add to a new playlist, which takes the window to the
        Playlists view while the download runs. Nothing else reports this
        failure, so somewhere it is not on screen the toast is the only word
        there would be.
        """
        if self.current_view() != "search":
            self._toast(f"Could not finish downloading {name}")
            return
        self._set_search_note(
            f"Could not finish downloading {name}. Details has what "
            "yt-dlp reported; ./ipod-fetch.sh --update is the usual fix when "
            "downloads stop working."
        )
