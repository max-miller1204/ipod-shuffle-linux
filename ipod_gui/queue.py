"""What is staged for the next sync, and how it gets there.

Nothing is copied when a track is added: it joins a queue held against one
device identity, and the sync is a separate, explicit act. Owns `pending`,
`pending_sources` and `pending_records`, the accounting that turns them into a
count and a byte total, `source_generation` for the scans that enrich them, the
sync launch, and the YouTube download whose result is queued rather than the
folder it wrote into.

Borrows from the window: `mount_point` and `device_identity`, which the queue
is only ever valid against, `library` for tracks it already knows,
`_sync_options` to say what the sync it launches is asked for, and `_run`,
`_set_busy`, `_toast`, `_merge_states`, `_populate_device_summary`,
`_update_device_controls` and `_refresh_current_view`.
"""

import os
import tempfile
import threading
from pathlib import Path

from gi.repository import GLib

from .config import (
    AUDIO_EXTENSIONS,
    PLAYLIST_EXTENSIONS,
    STATE_LIBRARY,
    SYNC_SCRIPT,
    YOUTUBE_LIBRARY,
)
from .text import plural
from .tags import scan_tracks, walk_following_links
from .youtube import fetch_command, fetched_sources
from .model import Track, local_playlist_tracks, read_local_playlist_tracks


class QueueMixin:
    # ----------------------------------------------------------- accounting

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

    def _pending_copy_tracks(self):
        return self._pending_accounting()[0]

    @staticmethod
    def _source_gone(source):
        """Whether a queued source has gone, rather than become unreadable.

        One rule for both boundaries that ask, because they disagreed once and
        the same file took opposite paths through them. A link whose target is
        not mounted is not gone: the file is still in the folder and reads
        again once the drive is back, which is what the editing side already
        says about it. exists() follows the link and answers False for a
        deleted file and that one alike, so it cannot be asked on its own.
        """
        path = Path(source)
        return not path.exists() and not path.is_symlink()

    def is_queued(self, source):
        """Whether this source is already staged for the next sync.

        A verb rather than letting the playlist view read the queue's own
        bookkeeping: what is staged is this module's to know, and a playlist
        page only needs the answer.
        """
        return str(source) in self.pending_sources

    def staged_by(self, track):
        """The sources that staged this track, when the track is not one.

        Asked by the row's Unqueue button, which takes whole sources out: what
        a press would cost is whatever named this track, and a song queued on
        its own is its own source and answers with nothing at all.

        A pass over the sources rather than an index the queue would have to
        keep in step through every stage, prune and sync: each source is one
        set lookup, and this is the same collection the sync itself walks.
        """
        path = str(getattr(track, "path", track))
        return tuple(
            sorted(
                source
                for source, members in self.pending_sources.items()
                if source != path and path in members
            )
        )

    def is_staged(self, track):
        """Whether the queue holds this file, whatever put it there.

        The question `is_queued` does not answer: that one is about a source,
        and a song named inside a queued folder or playlist is staged without
        being one. Asked by the deletion, which drops the path out of every
        member set holding it and says so before it happens - a state is not
        what to ask there, because a staged track the iPod already holds reads
        "On iPod" and the sync still loses it.
        """
        path = str(getattr(track, "path", track))
        return any(path in members for members in self.pending_sources.values())

    def unqueue_source(self, source):
        """Drop a source from the queue, members and all.

        For a playlist that has just been deleted or renamed: the file it named
        is gone, and a sync that still held it would copy nothing under a name
        nothing here uses any more.

        Taken out directly rather than staged as a source holding nothing: the
        queue outlives an unplug and a playlist can be deleted with no iPod
        attached, so asking to queue would refuse for want of a device and
        leave a file that is already gone staged for the next sync to fail on.
        """
        if self.pending_sources.pop(str(source), None) is not None:
            self._prune_pending()

    def _prune_pending(self):
        """Forget whatever no source claims any more, and repaint."""
        self._drop_unclaimed()
        self._merge_states()
        self._populate_device_summary()
        self._refresh_current_view()

    def _drop_unclaimed(self):
        """The bookkeeping half of `_prune_pending`, for a caller that repaints.

        The queue is its sources: a track is staged because something staged
        it, so dropping the last source that named one drops the track with it.

        Apart from the repaint because a caller that changes more than the
        queue - the deletion, which takes a song out of the library, the queue
        and the player in one press - paints once at the end over everything it
        changed, rather than once here in the middle and again after.
        """
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

    def _pending_change_count(self):
        return self._pending_accounting()[1]

    # ------------------------------------------------------------- queueing

    def _queue_sources(
        self,
        sources,
        show_toast=True,
        metadata_complete=False,
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
                )

            threading.Thread(target=worker, daemon=True).start()
            return None
        return self._commit_queue_sources(sources, show_toast=show_toast)

    def _scan_pending_tracks(self, paths, generation):
        paths = set(str(path) for path in paths)
        enriched = {}
        records, complete = scan_tracks(
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
        # Reading tags takes seconds, and a playlist can be deleted or renamed
        # in them. Whatever it was called, it was never in pending_sources to
        # be taken out of, so a source that is no longer there is dropped here
        # instead: staged, it would name a file the next sync cannot re-read,
        # and every press of Sync from then on would be cancelled outright.
        resolved = {
            source: [enriched.get(track.path, track) for track in tracks]
            for source, tracks in sources.items()
            if not self._source_gone(source)
        }
        if not resolved:
            return False
        self._commit_queue_sources(resolved, show_toast=show_toast)
        return False

    def _commit_queue_sources(
        self,
        sources,
        show_toast=True,
        replace=False,
    ):
        if not self.mount_point:
            self._toast("Connect an iPod to queue tracks")
            return 0
        if self.device_identity is None:
            self._toast("Could not identify this iPod, so nothing was queued")
            return 0
        before = self._pending_change_count()
        if replace:
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
        if not self.pending_sources:
            self.pending_device_identity = self.device_identity
        elif self.pending_device_identity != self.device_identity:
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
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
        self._prune_pending()
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

    def _queue_playlists(self, paths, show_toast=True):
        """Stage playlist files and the tracks they list, one source each.

        The file itself is a member, so an emptied playlist is still a queued
        change: the sync is what removes its copy from the device, and a queue
        holding nothing would leave the old list there.

        Plural because an edit can touch two lists at once, and a queueing that
        has to read tags supersedes the one before it: staging them one after
        the other would cancel the first and leave it out of the queue it was
        told it had joined.
        """
        sources = {}
        for path in paths:
            tracks = [
                self._pending_track(item) for item in local_playlist_tracks(path)
            ]
            tracks.append(self._pending_track(path))
            sources[str(path)] = tracks
        return self._queue_sources(sources, show_toast=show_toast)

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
            removed_directory = removed_directory or source not in members
        self._prune_pending()
        if removed_directory:
            self._toast("The whole folder was removed from the queue")

    def unqueue_deleted_path(self, path):
        """Drop one file from the queue and leave what staged it staged.

        For a song deleted off this computer. A source is a list of members - a
        playlist and the tracks it names, a folder and the files under it - and
        losing one of them is not a reason to stop syncing the rest, which is
        what `_unqueue_track` would do here: that one is the row's Unqueue
        button, where the whole source is what the press is about. So the path
        leaves every member set holding it and the sources stay. One left
        naming nothing goes with it, the way `_prune_pending` already decides.

        Members are edited rather than re-queued, because the queue outlives an
        unplug and a song can be deleted with no iPod attached: asking to queue
        would refuse for want of a device and leave the deleted file staged for
        the next sync to fail on.

        The caller repaints. A deletion is the library, the queue and the
        player at once, and the window it leaves behind is the one its own last
        repaint paints.
        """
        key = str(path)
        for source, members in list(self.pending_sources.items()):
            if key not in members:
                continue
            members.discard(key)
            if not members:
                self.pending_sources.pop(source, None)
        self._drop_unclaimed()

    # ------------------------------------------------------ the sync itself

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
            sources, dropped, complete = self._scan_queued_sources(
                paths, generation
            )
            GLib.idle_add(
                self._finish_pending_source_scan,
                generation,
                device_identity,
                sources,
                dropped,
                complete,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _scan_queued_sources(self, sources, generation):
        """Re-read every queued source: what they hold now, and what has gone.

        Returns (sources, dropped, complete). What was dropped is handed back
        rather than only skipped, because the caller has to say it out loud:
        the sync that follows counts what survived, and a run reported as a
        clean success while a folder of staged tracks was quietly forgotten is
        worse than the cancellation this replaced.
        """
        refreshed = {}
        dropped = []
        for source in sources:
            path = Path(source)
            if self._source_gone(source):
                # Both a playlist folder other programs write and a music
                # folder on a stick invite a source disappearing between
                # staging and Sync. Failing the whole scan over one would
                # leave it staged and cancel every press after it, naming
                # nothing to go and put right; dropped instead, the queue is
                # rebuilt without it.
                dropped.append(source)
                continue
            if path.is_dir():
                records, complete = scan_tracks(
                    path,
                    cancelled=lambda: generation != self.source_generation,
                )
                tracks = [
                    Track(path / record["path"], record, STATE_LIBRARY)
                    for record in records
                ]
            elif path.suffix.lower() in PLAYLIST_EXTENSIONS:
                members, complete = read_local_playlist_tracks(path)
                if complete:
                    records, complete = scan_tracks(
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
                records, complete = scan_tracks(
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
                return {}, [], False
            if tracks:
                refreshed[source] = tracks
            else:
                # Still there, but holding nothing the sync would copy: the
                # folder was emptied by hand since it was staged. The queue is
                # rebuilt without it either way, so it counts as dropped for
                # the same reason a missing one does - the user staged it and
                # it is not going to happen.
                dropped.append(source)
        return refreshed, dropped, True

    def _finish_pending_source_scan(
        self,
        generation,
        device_identity,
        sources,
        dropped,
        complete,
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
        self._commit_queue_sources(sources, show_toast=False, replace=True)
        # Said on its own here rather than folded into what the sync reports:
        # a run that succeeds replaces that message with _clear_pending's, and
        # one that fails never shows it at all, so this is the only point both
        # outcomes pass through. What was dropped is the part of what the user
        # staged that will not happen, and a count of the rest reads as
        # success on its own.
        gone = (
            f"Dropped {plural(len(dropped), 'queued source')} with nothing "
            "left to sync"
            if dropped
            else ""
        )
        self._set_busy(False)
        if not self.pending_sources:
            self._toast(gone or "Nothing remains in the queued sources")
            return False
        if gone:
            self._toast(gone)
        self._launch_pending_sync()
        return False

    def _launch_pending_sync(self):
        paths = sorted(self.pending_sources)
        copy_tracks, changes, _queued_bytes = self._pending_accounting()
        self.sync_total = len(copy_tracks)
        self._run(
            [
                str(SYNC_SCRIPT),
                "--ipod",
                self.mount_point,
                # Spoken names among them, which is what makes a playlist
                # findable on a device with no screen. Fixed for every run, so
                # a sync that carries a playlist needs nothing added here.
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
        """Forget the queue the sync has just carried out.

        The merge re-derives everything the queue leaves behind rather than
        this rebuilding it: the index the accounting reads, the state on every
        track that had been staged, and the tracks that were in the window only
        because they were queued - a downloaded file is never in a music root,
        so its songs leave with the queue that named them. Written out here
        instead, that would be the merge's rule kept in two places, and this
        one would be the copy that goes stale.
        """
        self.pending.clear()
        self.pending_sources.clear()
        self.pending_records.clear()
        self.pending_device_identity = None
        self._merge_states()
        return "Sync complete"

    # ---------------------------------------------------------- new sources

    def _discover_music_folder(self, path):
        """Read a folder's tags off the main loop, then queue what it holds.

        Takes the path rather than choosing one, so the folder can come from
        anywhere: the library's own music roots today, and whatever drives this
        window from outside it later.
        """
        self.source_generation += 1
        generation = self.source_generation
        device_identity = self.device_identity
        self.discovering_sources = True
        self._update_device_controls()

        def worker():
            tracks, complete = self._scan_source_tracks(path, generation)
            GLib.idle_add(
                self._finish_music_folder_discovery,
                generation,
                device_identity,
                path,
                tracks,
                complete,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _scan_source_tracks(self, path, generation):
        records, complete = scan_tracks(
            path,
            cancelled=lambda: generation != self.source_generation,
        )
        tracks = [
            Track(Path(path, record["path"]), record, STATE_LIBRARY)
            for record in records
        ]
        return tracks, complete

    def _finish_music_folder_discovery(
        self,
        generation,
        device_identity,
        path,
        tracks,
        complete,
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
            self._toast("No supported audio found in that folder")
            return False
        self._queue_sources({str(path): tracks}, metadata_complete=True)
        return False

    def _start_youtube_download(
        self,
        url,
        single,
        busy_message,
        on_failure=None,
        playlist=None,
        video_id="",
    ):
        """Fetch one link and queue whatever that run produced.

        Shared by a search result, by adding one to a playlist and by the Add
        all beside a pasted playlist, so each of them queues exactly the tracks
        the download reported rather than the folder it wrote into. Add all is
        the only caller that hands over `single=False`, making it the one
        download that takes a whole list rather than the video a link names.
        `playlist` names the playlist a result was added to, which is the one
        case where the download is a step towards something else rather than
        the whole of what was asked for.
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
            then=lambda: self._sync_downloaded(new_tracks, playlist, video_id),
            on_failure=failed,
        ):
            # Refused before anything ran, so the list file it would have read
            # is left behind unless it is cleaned up here.
            failed()
        return fetch

    def _sync_downloaded(self, new_tracks, playlist=None, video_id=""):
        """Queue what the download produced, or say why there is nothing to."""
        sources = fetched_sources(new_tracks, YOUTUBE_LIBRARY)
        try:
            os.unlink(new_tracks)
        except OSError:
            pass

        if playlist is not None:
            # Adding to a playlist is what was asked for, and the playlist is
            # what stages its own tracks, so this download does not queue
            # anything of its own on top of that.
            return self._add_download_to_playlist(playlist, video_id, sources)
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

    @staticmethod
    def _audio_files(path):
        found = []
        for root, files in walk_following_links(path):
            for name in files:
                candidate = Path(root, name)
                if candidate.suffix.lower() in AUDIO_EXTENSIONS:
                    found.append(str(candidate))
        return found
