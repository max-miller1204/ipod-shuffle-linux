"""The now-playing bar, the preview downloads behind it, and the cache card.

Playback is preview only, on this computer's speakers, and never touches the
iPod. Owns the bar in all four of its states, the transport, the download that
fetches a result into the cache before playing it - with `preview_generation`,
the process handle and the lock that let a newer preview disown an older one -
the cache's pruning, and promoting a previewed track into the library.

It also holds the window's shutdown: a pipeline left playing outlives the
window, so closing it comes through here.

Borrows from the window: `player` and `library`, `mount_point` and
`device_identity` to know whether a kept track can be queued, and `_toast`,
`_log`, `_queue_sources`, `_merge_states` and `_refresh_current_view`.
"""

import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from gi.repository import GLib, Gtk

from .config import (
    AUDIO_EXTENSIONS,
    PREVIEW_CACHE,
    PREVIEW_CACHE_LIMIT,
    PREVIEW_INCOMING,
    STATE_LABELS,
    STATE_LIBRARY,
    STATE_PREVIEW,
    YOUTUBE_LIBRARY,
)
from .text import home_relative, human_duration, human_size, plural
from .tags import scan_tracks
from .youtube import fetch_command
from .previews import (
    cached_preview_path,
    preview_cache_entries,
    promote_destination,
    prunable_previews,
)
from .model import Track
from .widgets import ELLIPSIZE_END, StorageMeter, label, make_cover, state_dot
from .player import (
    PLAY_FETCHING,
    PLAY_IDLE,
    PLAY_LOADING,
    PLAY_PLAYING,
    PREVIEW_FAILED,
)


class PlaybackViewMixin:
    # --------------------------------------------------- preview cache card

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

    # ------------------------------------------------------ now-playing bar

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

    # --------------------------------------------------- fetching a preview

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
        records, _complete = scan_tracks(files=[str(destination)])
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

    def _shutdown_previews(self):
        """Stop playing and disown any download, because the window is closing.

        A pipeline left in PLAYING outlives the window it was started from, so
        closing the window would otherwise keep playing audio nothing on screen
        could stop, and a download would go on writing into a cache nothing is
        left to show or prune.
        """
        self.player.shutdown()
        with self._preview_lock:
            self._preview_closed = True
        self._supersede_preview_fetch()

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

    # ----------------------------------------------------- the cache itself

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
        if not self._keep_preview(track):
            return
        kept = f"Kept in {home_relative(YOUTUBE_LIBRARY)}"
        if self.mount_point and self.device_identity is not None:
            queued = self._queue_sources({track.path: [track]}, show_toast=False)
            self._toast(f"{kept} and queued for sync" if queued else kept)
            return
        self._refresh_current_view()
        self._toast(kept)

    def _keep_preview(self, track):
        """Move a previewed file into the library. True once it is there.

        Apart from the Add that usually asks for it, because adding a previewed
        track to a playlist has to keep the file too: an entry pointing into a
        cache that gets pruned would stop resolving without anything having
        been deleted on purpose.
        """
        if track.state != STATE_PREVIEW:
            return False
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
            return False
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
            return False
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
        return True

    # ------------------------------------------------------------ transport

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
