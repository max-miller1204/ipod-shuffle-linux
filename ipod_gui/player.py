"""GStreamer, and the pipeline behind the now-playing bar.

Preview playback only, on this computer's speakers: the shuffle has no way to
be told what to play, so nothing here reaches the device beyond reading a file
that happens to be mounted on it.
"""

from pathlib import Path

import gi
from gi.repository import GLib

from .shell import lib_function_succeeds


GSTREAMER_UNAVAILABLE = (
    "GStreamer is not installed - see Preview playback in the README"
)

# Said in the bar, where the track that will not arrive is already named, and
# pointing at the log rather than repeating yt-dlp's own diagnosis badly.
PREVIEW_FAILED = (
    "Could not download that preview. Details has what yt-dlp reported; "
    "./ipod-fetch.sh --update is the usual fix when downloads stop working."
)
_GST = None
_GST_LOADED = False


def gst():
    """The GStreamer bindings, or None when they are not installed.

    Imported here rather than beside Gtk in the package's __init__, because
    gi.require_version raises when the typelib is absent and that exception
    would take away an entire working window to withhold the one feature of it
    that is optional by design. Loaded once and remembered: Gst.init scans the
    plugin registry, which is not work to repeat per track.
    """
    global _GST, _GST_LOADED
    if _GST_LOADED:
        return _GST
    _GST_LOADED = True
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
    except (ImportError, ValueError, GLib.Error):
        return None
    _GST = Gst
    return _GST


def preview_unavailable_reason():
    """Why preview playback is not possible here, or None if it is.

    Asks lib.sh for the same reason the YouTube checks do: the scripts and the
    window have to agree about what counts as installed.
    The shell probe is also the stricter of the two available answers, because
    it makes the elements rather than only importing the module, and a
    GStreamer with no decoders imports perfectly and then plays silence.
    """
    if not lib_function_succeeds("gst_available"):
        return GSTREAMER_UNAVAILABLE
    return None


# What the now-playing bar is showing. "loading" is its own state rather than a
# flavour of playing because the gap is real and visible: a flushing seek or a
# track read over USB from the device leaves the bar with a title and no
# timeline yet, and a seek bar that accepts drags during that gap would
# silently drop them.
#
# "fetching" is the longer gap in front of that one, and separate from it
# because there is no pipeline at all yet: the file is still being downloaded,
# so there is nothing to pause, seek or step past for several seconds.
PLAY_IDLE = "idle"
PLAY_FETCHING = "fetching"
PLAY_LOADING = "loading"
PLAY_PLAYING = "playing"
PLAY_PAUSED = "paused"

# GStreamer has no position-changed signal, so the bar polls. 250ms is the
# coarsest interval at which the timeline still reads as tracking the audio
# rather than stepping along behind it.
PLAYER_POLL_MS = 250

# Polls to leave the sought position alone after a seek. A flushing seek
# usually reports its new position immediately, but a pipeline that has to
# pre-roll first answers with the old one for a frame or two, and the thumb
# springing back to where it was dragged from reads as the seek being refused.
SEEK_SETTLE_POLLS = 2

# Pressing previous this far into a track restarts it instead of stepping back,
# which is what every other player does and what the gesture usually means.
RESTART_WINDOW = 3.0

# What the bar has painted before it has painted anything. A sentinel rather
# than None, because None is also what "idle, showing the placeholder" looks
# like, and the first paint of a freshly built bar is exactly that - so
# comparing against None skipped it and left the artwork slot empty.
UNPAINTED = object()


class PreviewPlayer:
    """playbin3 driving the now-playing bar, on this computer's speakers.

    Preview only, and deliberately so: the shuffle has no way to be told what
    to play, so nothing here reaches the device beyond reading a file that
    happens to be mounted on it.

    The window owns the widgets and this owns the pipeline; the two meet at
    on_change, which fires whenever the bar would look different. Pushing a
    repaint rather than having the window poll keeps the transport, the end of
    a track and a decoding failure on one path.
    """

    def __init__(self, on_change):
        self.on_change = on_change
        self.track = None
        self.state = PLAY_IDLE
        self.position = 0.0
        self.duration = 0.0
        # The last playback failure, shown in the bar until something else is
        # played. Not a toast: the bar is where the user is already looking,
        # and it is the thing that stopped.
        self.error = None
        # The list the current track was started from, so previous and next
        # move through what the user was looking at rather than through the
        # whole library.
        self.queue = []
        self.index = -1
        self._pipeline = None
        self._poll = None
        self._settle = 0
        self._prerolled = False

    @property
    def seekable(self):
        return (
            self._pipeline is not None
            and self._prerolled
            and self.state in (PLAY_PLAYING, PLAY_PAUSED)
            and self.duration > 0
        )

    # --------------------------------------------------------------- control

    def play(self, tracks, index):
        """Start one track, with the rest of its list as the queue."""
        tracks = list(tracks)
        if not 0 <= index < len(tracks):
            return
        self.queue = tracks
        self.index = index
        self._start(tracks[index])

    def fetch(self, track):
        """Show a track that is still being downloaded before it can play.

        The wait belongs in the bar rather than in a spinner elsewhere: the
        download is happening because the user pressed play, and the bar is
        where the result of pressing play appears.
        """
        self._teardown()
        self.track = track
        self.state = PLAY_FETCHING
        self.error = None
        self.position = 0.0
        # From the search result, so the timeline is the right length before
        # there is a file to ask. Corrected once the pipeline opens it.
        self.duration = float(track.duration or 0)
        self.queue = [track]
        self.index = 0
        self._changed()

    def fail(self, track, message):
        """Give up on a track that never became playable at all.

        The queue goes with it: leaving the track in it would leave the play
        button offering to start a file that was never downloaded.
        """
        self.queue = []
        self.index = -1
        self._fail(track, message)

    def toggle(self):
        """Pause what is playing, resume what is paused, restart what ended."""
        if self.state == PLAY_FETCHING:
            # Nothing exists to pause yet. The transport is insensitive while a
            # download runs, so this is only reachable from the keyboard.
            return
        if self.state in (PLAY_PLAYING, PLAY_LOADING):
            # Loading counts as playing here. The pipeline is already on its
            # way to PLAYING, pressing the button again means "no, stop", and
            # a button that visibly does nothing for the second a track takes
            # to open reads as the transport being broken.
            self._set_pipeline_state("PAUSED", PLAY_PAUSED)
        elif self.state == PLAY_PAUSED:
            self._set_pipeline_state("PLAYING", PLAY_PLAYING)
        elif self.queue and 0 <= self.index < len(self.queue):
            self._start(self.queue[self.index])

    def previous(self):
        """Restart the track, or step back if it only just started."""
        if self.position > RESTART_WINDOW or self.index <= 0:
            self.seek(0.0)
            return
        self.index -= 1
        self._start(self.queue[self.index])

    def next(self):
        """Step forward, or stop at the end of the queue rather than wrap.

        Wrapping would make a queue started from one album play forever, and
        the bar gives no hint that it had looped.
        """
        if self.index + 1 >= len(self.queue):
            self.stop()
            return
        self.index += 1
        self._start(self.queue[self.index])

    def seek(self, fraction):
        """Jump to a fraction of the track, 0 to 1."""
        if not self.seekable:
            return
        module = gst()
        if module is None:
            return
        target = max(0.0, min(1.0, fraction)) * self.duration
        self._pipeline.seek_simple(
            module.Format.TIME,
            module.SeekFlags.FLUSH | module.SeekFlags.KEY_UNIT,
            int(target * module.SECOND),
        )
        self.position = target
        self._settle = SEEK_SETTLE_POLLS
        self._changed()

    def forget(self, paths):
        """Drop tracks whose files have gone.

        The queue is what previous and next walk and what the play button
        resumes, so a pruned or cleared preview left in it is a control that
        fails on every press.
        """
        paths = {str(path) for path in paths}
        if self.track is not None and self.track.path in paths:
            self.stop()
        remaining = [track for track in self.queue if track.path not in paths]
        if len(remaining) == len(self.queue):
            return
        current = self.queue[self.index] if 0 <= self.index < len(self.queue) else None
        self.queue = remaining
        if current in remaining:
            self.index = remaining.index(current)
        else:
            # Whatever was current has gone. Play then starts the queue from
            # the top rather than being a button that does nothing.
            self.index = 0 if remaining else -1
        self._changed()

    def stop(self):
        """Return to idle, keeping the queue so play can resume it."""
        self._teardown()
        self.track = None
        self.state = PLAY_IDLE
        self.position = 0.0
        self.duration = 0.0
        self._changed()

    def shutdown(self):
        """Release the pipeline on the way out of the window."""
        self._teardown()
        self._pipeline = None

    # -------------------------------------------------------------- internals

    def _fail(self, track, message):
        """Give up on a track, stopping whatever was playing before it.

        The teardown matters: without it a start that fails halfway leaves the
        previous track audible behind a bar that has already moved on to the
        one that did not open.
        """
        self._teardown()
        self.track = track
        self.state = PLAY_IDLE
        self.position = 0.0
        self.duration = 0.0
        self.error = message
        self._changed()

    def _start(self, track):
        module = gst()
        if module is None:
            self._fail(track, GSTREAMER_UNAVAILABLE)
            return

        pipeline = self._ensure_pipeline()
        if pipeline is None:
            self._fail(track, "GStreamer cannot build a playback pipeline")
            return

        try:
            uri = module.filename_to_uri(str(Path(track.path).absolute()))
        except GLib.Error:
            self._fail(track, "That file's location cannot be opened")
            return

        # NULL before the new URI, not just READY: playbin3 keeps the previous
        # stream's decoders around otherwise, and starting an m4a straight
        # after an mp3 then fails inside the old decoder rather than building
        # the right one.
        self._prerolled = False
        pipeline.set_state(module.State.NULL)
        pipeline.set_property("uri", uri)

        self.track = track
        self.error = None
        self.position = 0.0
        # Whatever the tags claimed, until the pipeline can be asked. Tagged
        # durations and decoded ones disagree often enough that starting from
        # the tag and correcting is better than an empty timeline.
        self.duration = float(track.duration or 0)
        self._settle = 0
        self._set_pipeline_state("PLAYING", PLAY_LOADING)
        self._start_polling()

    def _ensure_pipeline(self):
        module = gst()
        if module is None:
            return None
        if self._pipeline is None:
            pipeline = module.ElementFactory.make("playbin3", "preview")
            if pipeline is None:
                return None
            # Video would open a window of its own for a YouTube preview the
            # user asked to hear, so the sink is refused outright rather than
            # merely left unset.
            pipeline.set_property("video-sink", module.ElementFactory.make("fakesink"))
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)
            bus.connect("message::error", self._on_error)
            bus.connect("message::state-changed", self._on_state_changed)
            bus.connect("message::duration-changed", self._on_duration_changed)
            self._pipeline = pipeline
        return self._pipeline

    def _set_pipeline_state(self, name, state):
        """Move the pipeline, naming its state rather than passing the enum.

        Gst.State cannot be referenced from a call site that has to work on a
        machine where importing Gst fails, which is every call site here.
        """
        module = gst()
        if self._pipeline is None or module is None:
            return
        self._pipeline.set_state(getattr(module.State, name))
        self.state = state
        if state == PLAY_PLAYING:
            self._start_polling()
        elif state == PLAY_PAUSED:
            self._stop_polling()
        self._changed()

    def _start_polling(self):
        if self._poll is None:
            self._poll = GLib.timeout_add(PLAYER_POLL_MS, self._tick)

    def _stop_polling(self):
        if self._poll is not None:
            GLib.source_remove(self._poll)
            self._poll = None

    def _teardown(self):
        module = gst()
        if self._pipeline is not None and module is not None:
            self._pipeline.set_state(module.State.NULL)
        self._prerolled = False
        self._stop_polling()

    def _tick(self):
        if self._pipeline is None or self.state not in (PLAY_LOADING, PLAY_PLAYING):
            self._poll = None
            return False
        module = gst()
        if module is None:
            self._poll = None
            return False

        found, duration = self._pipeline.query_duration(module.Format.TIME)
        if found and duration > 0:
            self.duration = duration / module.SECOND
        if self._settle > 0:
            self._settle -= 1
        else:
            found, position = self._pipeline.query_position(module.Format.TIME)
            if found and position >= 0:
                self.position = position / module.SECOND
        self._changed()
        return True

    def _on_state_changed(self, _bus, message):
        # Every element in the pipeline reports its own transitions; only the
        # pipeline's own says whether audio is actually coming out.
        if message.src is not self._pipeline:
            return
        module = gst()
        if module is None:
            return
        _old, new, _pending = message.parse_state_changed()
        changed = False
        if (
            new in (module.State.PAUSED, module.State.PLAYING)
            and self.state in (PLAY_LOADING, PLAY_PLAYING, PLAY_PAUSED)
            and not self._prerolled
        ):
            self._prerolled = True
            changed = True
        if new == module.State.PLAYING and self.state in (PLAY_LOADING, PLAY_PLAYING):
            self.state = PLAY_PLAYING
            changed = True
        if changed:
            self._changed()

    def _on_duration_changed(self, _bus, _message):
        # Emitted once the demuxer knows better than the tag did. The next
        # poll reads the new value; this only makes sure a paused track still
        # gets a correct timeline.
        module = gst()
        if self._pipeline is None or module is None:
            return
        found, duration = self._pipeline.query_duration(module.Format.TIME)
        if found and duration > 0:
            self.duration = duration / module.SECOND
            self._changed()

    def _on_eos(self, _bus, _message):
        self.next()

    def _on_error(self, _bus, message):
        error, _debug = message.parse_error()
        self._teardown()
        self.state = PLAY_IDLE
        self.position = 0.0
        # GStreamer's own wording, which names the file and the missing
        # decoder. Rewriting it into something friendlier would throw away the
        # only part that says which plugin to install.
        self.error = error.message
        self._changed()

    def _changed(self):
        if self.on_change is not None:
            self.on_change()
