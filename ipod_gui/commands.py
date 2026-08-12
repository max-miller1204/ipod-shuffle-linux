"""Running the shell scripts, and the bar that reports what they are doing.

Every device-changing action goes through `_run`, which holds the device write
lock, re-checks that the iPod under the mount point is still the one the action
was aimed at, and streams the script's output into the log. Owns the sync bar
and its details pane, the busy state that makes the window refuse to race
itself, and the destructive and D-Bus actions: rebuild, wipe, remove, eject and
mount.

Borrows from the window: `mount_point` and `device_identity` to aim a command,
`speech_engine_available` to say what a rebuild costs the iPod's spoken names,
`_toast`, `refresh`, `_rescan_library`, `_sync_options`, `_merge_states`,
`_populate_device_summary`, `_populate_cache_card` and
`_update_device_controls`.
"""

import json
import os
import subprocess
import threading
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from .config import REMOVE_SCRIPT, SYNC_SCRIPT, WIPE_SCRIPT
from .text import (
    FILE_STATUS_LABELS,
    PLAYLIST_STATUS_LABELS,
    SPOKEN_NAMES_LOST,
    STAGE_LABELS,
    progress_event,
    strip_ansi,
)
from .device import (
    DEVICE_IO_LOCK,
    resolve_device,
    udisks_filesystem_call,
    unmounted_vfat_devices,
)
from .widgets import ELLIPSIZE_END, clear_children, label

# The scripts that report their progress as JSON, and so the ones that get a
# stream to report it on. Decided by which script is being run rather than by
# each caller asking for it: a bar that appears depending on who started the
# command would be a bar nobody could rely on. ipod-fetch.sh also runs through
# _run and is not one of them - a download reports itself, through yt-dlp.
PROGRESS_SCRIPTS = frozenset(
    str(script) for script in (SYNC_SCRIPT, REMOVE_SCRIPT, WIPE_SCRIPT)
)


def _script_options(argv, *options):
    """Insert script options before the `--` that begins path arguments."""
    command = list(argv)
    index = command.index("--") if "--" in command else len(command)
    command[index:index] = options
    return command


def _is_destructive_script(argv):
    """Whether this command changes what is on the device.

    Read from the options alone, on the same rule `_script_options` inserts
    by: everything after `--` is a name the device gave us, and a track called
    `--list` is a track. Reading one as a flag would leave the window sending
    a removal it never planned or authorized, which the script then refuses.
    """
    script = str(argv[0])
    if script == str(WIPE_SCRIPT):
        return True
    options = argv[: argv.index("--")] if "--" in argv else argv
    if script == str(REMOVE_SCRIPT):
        return "--list" not in options and "-l" not in options
    return script == str(SYNC_SCRIPT) and (
        "--clear" in options or "-c" in options
    )


class CommandsMixin:
    # ------------------------------------------------------------- sync bar

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

    # -------------------------------------------------------- busy plumbing

    def _set_busy(self, busy, message=""):
        if busy:
            self.probe_generation += 1
            self.tag_generation += 1
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
        return False

    def _note_progress(self, event):
        """Show one event of a running script's progress stream.

        Everything the bar knows comes from the script itself: how many items
        the run is going to report on, which one it has just finished, and what
        it is doing during the stretches that are not a file at a time. An
        event this window has no word for is shown as what it says rather than
        dropped, because a run reporting something new is not a reason to leave
        the bar looking stalled.
        """
        kind = event.get("event")
        if kind == "file":
            status = event.get("status", "")
            name = event.get("name", "")
            self.sync_current.set_text(name)
            self._log_progress_row(name, FILE_STATUS_LABELS.get(status, status))
            self._show_progress_counts(event)
        elif kind == "playlist":
            status = event.get("status", "")
            name = event.get("name", "")
            self.sync_current.set_text(name)
            self._log_progress_row(
                name, PLAYLIST_STATUS_LABELS.get(status, status)
            )
            self._show_progress_counts(event)
        elif kind == "stage":
            stage = event.get("name", "")
            if event.get("state") == "start":
                self.sync_current.set_text(STAGE_LABELS.get(stage, stage))
        return False

    def _log_progress_row(self, name, status):
        row = Gtk.Box(spacing=11)
        row.append(label("✓", "sf-caption", width_chars=2, xalign=0.5))
        row.append(label(name, "sf-body", hexpand=True, ellipsize=ELLIPSIZE_END))
        row.append(label(status, "sf-caption"))
        self.sync_file_list.append(row)

    def _show_progress_counts(self, event):
        """Move the bar to where the run says it has got to.

        The counts ride on the event rather than being kept here, so a line
        that never arrives costs the bar nothing: the next one still knows how
        far along the run is.
        """
        done = event.get("done")
        total = event.get("total")
        if not isinstance(done, int) or not isinstance(total, int):
            return
        if total > 0:
            self.progress.set_fraction(min(1.0, done / total))
            self.sync_count.set_text(f"{done} of {total}")
        else:
            self.sync_count.set_text(str(done))

    def _clear_log(self):
        self.log_view.get_buffer().set_text("")
        clear_children(self.sync_file_list)

    def on_copy_log(self, _button):
        buf = self.log_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set_content(
                Gdk.ContentProvider.new_for_value(GObject.Value(str, text))
            )
            self._toast("Output copied")

    # ----------------------------------------------------- running a script

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
            command = list(argv)

            def run_process():
                nonlocal code
                # The script's own descriptor, opened here so the two streams
                # cannot interleave: the log view shows what a person would
                # read in a terminal, and the bar is driven by the JSON on the
                # other one. Passed by number rather than as a fixed 3, which
                # would mean renumbering a descriptor in the child and there is
                # no safe moment to do that in a threaded process.
                progress_read = progress_write = -1
                
                reader = None
                try:
                    if argv[0] in PROGRESS_SCRIPTS:
                        progress_read, progress_write = os.pipe()
                        # Straight after the script, because every command that
                        # names paths ends with `--` and everything after that
                        # is a path however much it looks like a flag - which
                        # is how a track called "-1" reaches the copy, and how
                        # this arrived as a folder nobody could find.
                        command.insert(1, f"--progress-json={progress_write}")
                    proc = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        pass_fds=() if progress_write < 0 else (progress_write,),
                    )
                    if progress_write >= 0:
                        # The child holds the writing end now, so the end of
                        # the stream is the script finishing with it rather
                        # than this process letting go of a copy it is not
                        # using. The cleanup below would close it in any case;
                        # what this decides is when the reader is told.
                        os.close(progress_write)
                        progress_write = -1
                        reader = threading.Thread(
                            target=self._read_progress,
                            args=(progress_read,),
                            daemon=True,
                        )
                        progress_read = -1
                        reader.start()
                    if proc.stdout is not None:
                        for line in proc.stdout:
                            GLib.idle_add(self._log, line)
                    code = proc.wait()
                except (OSError, TypeError, ValueError) as exc:
                    GLib.idle_add(self._log, f"failed to run: {exc}\n")
                finally:
                    for descriptor in (progress_read, progress_write):
                        if descriptor >= 0:
                            os.close(descriptor)
                    if reader is not None:
                        # Joined before the run is called finished, so that
                        # what the script said it did has reached the window
                        # before the window says it is over. Bounded, because
                        # a descriptor some other child of the script inherited
                        # and kept open is not worth hanging the window on.
                        reader.join(5)

            if device_command:
                with DEVICE_IO_LOCK.write():
                    mount_index = argv.index("--ipod") + 1
                    mount_point = str(argv[mount_index])
                    if (
                        mount_point != self.mount_point
                        or resolve_device(mount_point, expected_identity) is None
                    ):
                        GLib.idle_add(self._cancel_device_command)
                        return
                    command = _script_options(
                        command, "--expect-device", str(expected_identity)
                    )
                    if _is_destructive_script(command):
                        plan_command = _script_options(command, "--dry-run")
                        try:
                            plan = subprocess.run(
                                plan_command,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if plan.returncode != 0:
                                GLib.idle_add(self._log, plan.stderr or plan.stdout)
                                code = plan.returncode
                                GLib.idle_add(
                                    self._finish,
                                    code,
                                    done_message,
                                    then,
                                    device_command,
                                    on_failure,
                                )
                                return
                            token = json.loads(plan.stdout)["confirmationToken"]
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            GLib.idle_add(self._log, f"failed to plan: {exc}\n")
                            GLib.idle_add(
                                self._finish,
                                code,
                                done_message,
                                then,
                                device_command,
                                on_failure,
                            )
                            return
                        command = _script_options(
                            command, "--confirm-token", str(token)
                        )
                    run_process()
            else:
                run_process()
            GLib.idle_add(
                self._finish, code, done_message, then, device_command, on_failure
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _read_progress(self, descriptor):
        """Hand the script's progress events to the main loop as they arrive.

        Its own thread, because the log and the progress are two descriptors
        of one process: reading them one after the other would stop the script
        the moment the stream nobody was reading filled up.
        """
        try:
            with os.fdopen(
                descriptor, "r", encoding="utf-8", errors="replace"
            ) as stream:
                for line in stream:
                    event = progress_event(line)
                    if event is not None:
                        GLib.idle_add(self._note_progress, event)
        except OSError:
            # The run itself is what matters and it is still going; the log
            # beside the bar is what will say how it ended.
            pass

    def _cancel_device_command(self):
        self._set_busy(False)
        self._toast("The connected iPod changed, so the action was cancelled")
        return False

    def _invalidate_device_snapshot(self):
        # The track count and free space are deliberately left alone. Every
        # caller of this is followed by a refresh, so they are replaced within
        # a few hundred milliseconds; clearing them would put "0 tracks" on
        # screen the moment a sync that added tracks finished, which is a
        # worse thing to say than the figure from just before it ran.
        self.tag_generation += 1
        self._device_scan_active = bool(self.mount_point)
        self._update_refresh_spinner()
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
        self.refresh()
        self._rescan_library()
        return False

    # ------------------------------------------------------- device actions

    def on_remove_track(self, _button, relpath):
        if not self.mount_point or self.device_identity is None:
            self._toast("Connect an iPod before removing tracks")
            return
        name = self.track_names.get(relpath, Path(relpath).name)
        rebuilt = "It is deleted from the iPod and the database is rebuilt."
        if not self.speech_engine_available:
            rebuilt += f" {SPOKEN_NAMES_LOST}"
        dialog = Adw.AlertDialog(
            heading="Remove this track?",
            body=(
                f"{name}\n\n"
                f"{rebuilt} "
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
        # The count the window is already showing, rather than a fresh walk of
        # the device: a click that has to wait on USB before its dialog opens
        # reads as the button having missed.
        total = self.device_track_count
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
            with DEVICE_IO_LOCK.write():
                current = resolve_device(
                    self.mount_point, expected_identity, require_block=True
                )
                if current is not None:
                    ok, message = udisks_filesystem_call(
                        current.block_device, "Unmount"
                    )
            if current is None:
                GLib.idle_add(
                    self._finish_dbus,
                    False,
                    "",
                    "the connected iPod changed; eject was cancelled",
                )
                return
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
            with DEVICE_IO_LOCK.write():
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
