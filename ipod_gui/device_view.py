"""Everything that answers "what iPod is this, and what is on it".

Owns the Device & Settings page, the probe that reads the device on a worker
thread and the summaries it paints - connected, absent, still looking - the
sidebar's device card, the walk that reads the device's own tags with
`tag_generation` guarding it, and the flags every sync is run with.

Borrows from the window: `busy` to know when a script is changing the device,
the pending queue for the figures it shows, and `_toast`,
`_populate_playlist_rail`, `_refresh_current_view` and `_merge_states` to
repaint what a probe changed. The buttons on this page belong to the parts that
act on them - the queue, the commands, the playlist view - so it wires them up
and asks each whether it is currently allowed to run.
"""

import threading
from pathlib import Path

from gi.repository import GLib, Gtk, Pango

from .config import STATE_IPOD, save_music_roots
from .text import home_relative, human_size, plural
from .tags import scan_tracks
from .device import DEVICE_IO_LOCK, music_folder, probe_device
from .model import Track
from .widgets import ELLIPSIZE_END, StorageMeter, clear_children, label


class DeviceViewMixin:
    # ------------------------------------------------------- sidebar card

    def _build_device_card(self):
        """The device summary in the sidebar footer, which is always on screen.

        Built here rather than in the sidebar it sits in, because every label
        on it is repainted by _populate_device_summary and the absent
        summaries beside it: a card assembled somewhere else would leave the
        two halves of one widget in two files.
        """
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
        self.device_name = label(
            "No iPod", "sf-row-title", hexpand=True, ellipsize=ELLIPSIZE_END
        )
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
        # Wrapped rather than ellipsised, and rather than held to sentences
        # short enough to fit: this card sits in a sidebar pinned to 236px, of
        # which about 178px reach this label, and every sentence written here
        # is longer than that - the queued size beside the iPod to reconnect,
        # what a scan is checking, why a staged change copies nothing. An
        # ellipsis would cut whichever half came second, and the second half
        # is the one saying what to do about it.
        self.queued_label = label("", "sf-caption", "sf-accent", wrap=True)
        self.queued_row.append(self.queued_label)
        self.queued_row.set_visible(False)
        inner.append(self.queued_row)
        return self.device_card

    # ------------------------------------------------------- settings page

    def _build_settings_view(self):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_top(18)
        box.set_margin_bottom(20)
        scroller.set_child(box)
        # Deliberately not kept as an attribute the way the other four views
        # are: those go into _busy_widgets whole, because their rows carry
        # buttons. Every control on this page is in that list by name already.
        box.append(self._build_device_summary())
        # Built only when there is nothing to speak names with. Every sync asks
        # for spoken track and playlist names, so an engine that is installed
        # is the ordinary case and needs no line of its own. Read once here
        # rather than repainted, because window.py works the answer out at
        # startup and nothing this window does can change it.
        if not self.speech_engine_available:
            box.append(self._build_speech_warning())
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

        left.append(self._build_folders_card())
        left.append(self._build_cache_card())
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

    def _build_speech_warning(self):
        """What a machine with no speech engine costs the iPod, whatever is pressed.

        Its own card rather than a note beside a control, because there is no
        control to sit beside: the flags a sync is run with are fixed, so this
        is a statement about the machine rather than about a choice made here.
        Stated as the condition rather than as the list of actions that meet
        it, so an action nobody has written yet is not read as exempt.
        """
        warn = Gtk.Box(spacing=9)
        warn.add_css_class("sf-warn-card")
        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_margin_start(11)
        icon.set_margin_top(11)
        icon.set_valign(Gtk.Align.START)
        warn.append(icon)
        text = label(
            "No speech engine installed, so this computer cannot record the "
            "names every sync asks for. Anything it does that rebuilds the "
            "iPod's database drops every spoken name already on the device, "
            "including ones another computer recorded, and cannot put them "
            "back. Install pico2wave, espeak or say first: the device has no "
            "screen, so a name it cannot read out is no name at all, and "
            "playlists stay on this computer until one is there.",
            "sf-caption",
            wrap=True,
            hexpand=True,
        )
        text.set_margin_end(11)
        text.set_margin_top(11)
        text.set_margin_bottom(11)
        warn.append(text)
        return warn

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

    # ------------------------------------------------------------ the probe

    def refresh(self):
        """Re-detect the iPod and repaint. Safe to call from the main loop.

        Nothing here touches the device: detection is a subprocess and reading
        the device is a walk over USB, so both happen on a worker thread and
        _apply_probe paints what it brings back.
        """
        if self.busy:
            return False

        self.probe_generation += 1
        generation = self.probe_generation
        if not self.probe_answered:
            # The empty page would claim there is no iPod, which is not what
            # is known yet; the device page says what is actually happening
            # and leaves the library, which needs no device, on screen.
            self._populate_searching_summary()
            self._populate_playlist_rail()
            self.stack.set_visible_child_name("device")

        def worker():
            probe = probe_device(
                cancelled=lambda: generation != self.probe_generation
            )
            GLib.idle_add(self._apply_probe, generation, probe)

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _apply_probe(self, generation, probe):
        """Paint what the worker read off the device."""
        if generation != self.probe_generation:
            return False
        if self.busy:
            # A command started while this was reading. It ends with a refresh
            # of its own, so dropping this one keeps the invariant that no
            # device reading overlaps a script that is changing the device.
            return False
        self.probe_answered = True

        if probe.mount_point is None:
            self._select_mount(None, None)
            self.playlists = []
            self.spoken = set()
            # Only a playlist that came off the device goes with it. One made
            # here is a file of your own that outlives the unplug, and clearing
            # the selection would hand the Playlists view to whichever list
            # sorts first - on every refresh, which is every plug, unplug and
            # finished command.
            if self._local_playlist(self.current_playlist) is None:
                self.current_playlist = None
            if len(probe.candidates) > 1:
                self._populate_disconnected_summary(
                    "Multiple iPods connected. Disconnect all but the one you "
                    "want to manage.",
                    False,
                )
            elif not probe.readable:
                self._populate_disconnected_summary(
                    "The iPod stopped responding while it was being read. "
                    "Reconnect it, or mount it if it is still attached.",
                    True,
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

        self._select_mount(probe.mount_point, probe.identity)
        self.stack.set_visible_child_name("device")
        self.playlists = probe.playlists
        self.spoken = probe.spoken
        self.device_track_count = probe.track_count
        self.device_usage = probe.usage
        self._populate_device_summary()
        self._populate_playlist_rail()
        self._load_device_tracks_async()
        return False

    def _select_mount(self, mount_point, identity):
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
        self.device_track_count = 0
        self.device_usage = None
        self._device_scan_active = False
        self._update_refresh_spinner()
        self._device_snapshot_ready = False
        if (
            mount_point is not None
            and self.pending_sources
            and self.pending_device_identity != identity
        ):
            self.pending.clear()
            self.pending_sources.clear()
            self.pending_records.clear()
            self.pending_device_identity = None
            self._toast(
                "Queued changes were discarded because a different iPod was connected"
            )
        self._merge_states()
        self._refresh_current_view()

    def _populate_searching_summary(self):
        """The card before any probe has answered.

        Not the disconnected summary: "No iPod" is a claim, and at this point
        nothing has looked yet. Worth distinguishing because findmnt alone
        waits five seconds on a device that has stopped answering, which is
        long enough for the difference to be read and believed.
        """
        self._populate_absent_summary(
            "Looking…",
            "Looking for an iPod",
            "Checking connected drives",
            "Checking the drives that are connected.",
            False,
        )

    def _populate_disconnected_summary(self, message, offer_mount):
        self._populate_absent_summary(
            "No iPod", "No iPod connected", "Not connected", message, offer_mount
        )

    def _populate_absent_summary(
        self, name, settings_name, status, message, offer_mount
    ):
        self.device_name.set_text(name)
        self.settings_name.set_text(settings_name)
        self.settings_path.set_text(message)
        self.device_dot.remove_css_class("ipod")
        self.device_dot.add_css_class("library")
        self.settings_dot.remove_css_class("ipod")
        self.settings_dot.add_css_class("library")
        self.device_free.set_text(status)
        self.device_count.set_text("")
        self.wipe_note.set_text("Connect an iPod before using device controls.")
        for meter in (self.sidebar_meter, self.settings_meter):
            meter.set_fractions(0, 0, False)
        self._set_settings_figures(None, 0, 0, False)

        clear_children(self.device_banner)
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

        self._populate_absent_queue()

    def _populate_absent_queue(self):
        """What is staged for an iPod that is not attached.

        Apart from the rest of the absent summary, because the queue outlives
        an unplug and can still change while one is: a playlist deleted or a
        track taken back out of it are both edits this window allows with
        nothing plugged in, and each ends in the prune that repaints from
        here. Everything else on that card describes a device, and the device
        has not changed.
        """
        _tracks, changes, queued_bytes = self._pending_accounting()
        if self.pending_sources:
            self.queued_row.set_visible(True)
            # A size of zero is a reading that failed to be worked out rather
            # than an amount, the same way round as the attached card: what is
            # staged here is a playlist every song of which the device already
            # holds, and the sync copies no bytes for it.
            self.queued_label.set_text(
                f"{human_size(queued_bytes)} queued "
                "— reconnect the same iPod"
                if queued_bytes
                else "No new tracks to copy — reconnect the same iPod"
            )
            self.sync_button.set_label(
                f"Sync {plural(changes, 'change')}"
            )
        else:
            self.queued_row.set_visible(False)
            self.sync_button.set_label("Nothing queued")
        self._update_device_controls()

    def _populate_device_summary(self):
        # Every line below describes an attached device, and this runs from
        # the queue's own prune as well as from a probe - so it can be reached
        # with nothing plugged in, by an edit made while the iPod is away.
        # What that changed is the queue, which the absent card carries its
        # own line for.
        if self.mount_point is None:
            self._populate_absent_queue()
            return
        name = Path(self.mount_point).name
        self.device_name.set_text(name)
        self.settings_name.set_text(name)
        self.settings_path.set_text(self.mount_point)
        self.device_dot.remove_css_class("library")
        self.device_dot.add_css_class("ipod")
        self.settings_dot.remove_css_class("library")
        self.settings_dot.add_css_class("ipod")
        self.device_banner.set_visible(False)

        # From the last probe rather than from the device: this runs again
        # every time a scan lands a batch, and counting the files on a 2GB
        # device over USB is not something a repaint can afford.
        total_tracks = self.device_track_count
        self.device_count.set_text(plural(total_tracks, "track"))
        self.wipe_note.set_text(
            f"Removes all {plural(total_tracks, 'track')}. Filenames on the device are "
            "scrambled codes, so back up first."
        )

        _tracks, changes, queued_bytes = self._pending_accounting()
        usage = self.device_usage
        if usage is not None:
            used_fraction = usage.used / usage.total if usage.total else 0
            queued_fraction = queued_bytes / usage.total if usage.total else 0
            over = queued_bytes > usage.free
            self.device_free.set_text(f"{human_size(usage.free)} free")
            for meter in (self.sidebar_meter, self.settings_meter):
                meter.set_fractions(used_fraction, queued_fraction, over)
            self._set_settings_figures(usage, queued_bytes, total_tracks, over)
        else:
            self.device_free.set_text("size unknown")
            self._set_settings_figures(None, queued_bytes, total_tracks, False)

        if self.pending_sources:
            self.queued_row.set_visible(True)
            # The row reports what the sync would add to the device, and a
            # change that adds nothing is routine rather than a figure of
            # zero: editing a playlist every song of which is already over
            # there stages the list and not one byte, and "+0 B queued to
            # sync" reads as a size that failed to be worked out.
            self.queued_label.set_text(
                f"+{human_size(queued_bytes)} queued to sync"
                if queued_bytes
                else "Queued to sync · no new tracks to copy"
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
        # Collected as they are built rather than repainted from here: a
        # repaint would discard the skeleton mid-search, and a device
        # appearing while YouTube is still answering is exactly when that
        # happens.
        downloadable = self._can_download()
        for button in self.search_add_buttons:
            button.set_sensitive(downloadable)
            button.set_tooltip_text(self._youtube_download_tooltip())
        # Making a playlist writes a file in a folder of your own, so it waits
        # for neither an iPod nor a speech engine. What those are needed for is
        # putting one on the device, which the playlist's own page says in
        # place of disabling the way you make it.
        self.new_playlist_button.set_sensitive(not self.busy)
        self.rebuild_button.set_sensitive(enabled)
        self.wipe_button.set_sensitive(enabled)
        self.eject_button.set_sensitive(enabled)
        self.sidebar_eject.set_sensitive(enabled)
        accounting_ready = (
            not self._device_scan_active or self._device_snapshot_ready
        )
        self.sync_button.set_sensitive(
            queue_enabled and accounting_ready and bool(self.pending_sources)
        )

    def _set_settings_figures(self, usage, queued_bytes, total_tracks, over):
        clear_children(self.settings_figures)

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

    # -------------------------------------------- reading the device's tags

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
        self._update_refresh_spinner()
        self._update_device_controls()
        if self.pending_sources and not self._device_snapshot_ready:
            self.queued_label.set_text(
                "Checking which queued tracks are already on this iPod"
            )
            self.sync_button.set_label("Checking iPod…")

        def worker():
            music = music_folder(mount_point)
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

            if music is None:
                complete = True
            else:
                with DEVICE_IO_LOCK.read():
                    _records, complete = scan_tracks(
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
            self._device_scan_active = False
            self._update_refresh_spinner()
            self._device_snapshot_ready = True
            self._merge_states()
            self._request_refresh(scan_complete=scan_complete)
            # A device playlist's entries name files on the iPod, so its cover
            # is artwork this walk is what read. The rail was painted when the
            # probe answered, which is before any of this existed.
            self._populate_playlist_rail()
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
        self._update_refresh_spinner()
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

    # -------------------------------------------------------- music folders

    def _populate_folders(self):
        clear_children(self.folder_list)

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

    # -------------------------------------------------------- the sync flags

    def _sync_options(self):
        """The flags handed to every ipod-sync.sh run this window starts.

        Fixed rather than chosen, and the same on every machine. The device
        stores a name only as spoken audio, so a name it cannot say is no name
        at all and both voiceovers are always worth their space; and with no
        automatic grouping, the playlists on the device are the ones that were
        put there rather than ones a sync invented out of folder or tag names.

        Asked for even where nothing can record them. A missing speech engine
        makes the builder write no recordings rather than fail, and a rebuild
        run from such a machine leaves the device with none at all: the builder
        empties iPod_Control/Speakable/Tracks and Playlists at the start of
        every run and only refills what it can speak, so the names an
        engine-equipped computer recorded do not survive a rebuild from one
        that cannot - whichever action reached the builder, here or through
        ipod-remove.sh. Nothing here can prevent that, which is what the
        warning on the Device & Settings page is for. What asking anyway does
        buy is the way back: the flags are also what is written into the
        options saved on the iPod, so the next rebuild from a machine that can
        speak records every name again. A run that named none of them would
        clear that file too, and leave the device mute with nothing recording
        the intent to fix it.

        Passing them explicitly is also what retires a grouping an earlier
        version was told to use: ipod-sync.sh replays iPod_Control/.sync-options
        only when it is given no flags of its own, and writes what it was given
        back over that file, so a device still carrying --auto-dir-playlists
        stops grouping on its next sync without a migration to go and find it.
        """
        return ["--voiceover", "--playlist-voiceover"]
