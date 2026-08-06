#!/usr/bin/env python3
"""Builds the real window, which is the one thing the other GUI checks cannot.

Every other GUI check calls methods unbound against a stand-in, deliberately:
that is what lets them run without a display and assert on argument vectors
rather than on pixels. The cost is that none of them would notice a builder
that stopped being called, a widget appended to the wrong parent, or an
attribute that does not exist yet by the time __init__ collects _busy_widgets.
Those are exactly the failures splitting one window class into mixins can
introduce, and constructing it is the only thing that finds them.

Needs a display, so CI runs it under xvfb. It refuses to run without one rather
than skipping: a check that quietly does nothing is worse than one that fails,
because it reads as coverage that is not there.

Hermetic: HOME is a temporary directory, set before the package is imported,
so the scan started during construction reads an empty folder rather than the
real music library and the caches point somewhere disposable.
"""

import os
import sys
import tempfile
import traceback
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="ipod-gui-build-")
os.environ["HOME"] = _SANDBOX
os.environ["XDG_CACHE_HOME"] = str(Path(_SANDBOX, "cache"))
os.environ["XDG_CONFIG_HOME"] = str(Path(_SANDBOX, "config"))
Path(_SANDBOX, "Music").mkdir(parents=True, exist_ok=True)

from harness import gui  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

# Gtk.init_check() answers True with no display at all - it reports that GTK
# itself started, not that it found a windowing system - so the display is
# asked for directly. Without this the first symptom is four Gtk-CRITICAL
# lines and a RuntimeError from deep inside a constructor, which reads as the
# window being broken rather than as the machine having no screen.
Gtk.init_check()
if Gdk.Display.get_default() is None:
    raise SystemExit(
        "no display: run this under `xvfb-run -a`, or on a desktop session"
    )

# Detection is gui-detection-smoke's subject; here it only has to be quick and
# to answer the same way every run, so the construction is what is being read.
gui.find_ipods = lambda: []

failures = []

# What each mixin is responsible for putting on the window. Named per module so
# a failure says which half of the split stopped building its own widgets.
EXPECTED = {
    "window": [
        "toasts", "stack", "views", "view_title", "nav_buttons", "split",
        "sidebar_toggle", "refresh_button", "empty_page", "mount_button",
        "library_controls", "monitor",
    ],
    "library_view": [
        "album_flow", "album_filters", "library_table", "library_modes",
        "library_status", "collection_heading", "group_mode", "mode_buttons",
        "album_view", "album_tracks", "album_heading", "album_subheading",
        "album_actions", "album_art_holder", "library_view",
    ],
    "search_view": [
        "search_entry", "search_view", "search_local_table",
        "search_youtube_rows", "search_local_note", "search_youtube_note",
        "search_local_count", "search_youtube_count",
    ],
    "playlist_view": [
        "playlist_rail", "playlist_list", "playlist_shelf", "shelf_section",
        "playlist_tracks", "playlist_heading", "playlist_voice_note",
        "playlist_actions", "playlist_body", "playlist_empty",
        "playlists_view", "new_playlist_button",
    ],
    "playback_view": [
        "playing_art", "playing_title", "playing_artist", "playing_stack",
        "playing_message", "playing_status", "playing_state_dot",
        "transport_buttons", "seek_scale", "seek_elapsed", "seek_total",
        "cache_meter", "cache_figure", "cache_clear",
    ],
    "device_view": [
        "device_card", "device_dot", "device_name", "sidebar_meter",
        "device_free", "device_count", "queued_row", "queued_label",
        "settings_meter", "settings_name", "settings_path", "settings_dot",
        "settings_figures", "device_banner", "playlist_mode", "track_voiceover",
        "playlist_voiceover", "folder_list", "sync_button", "add_button",
        "playlist_button", "youtube_button", "wipe_button", "wipe_note",
    ],
    "commands": [
        "sync_revealer", "sync_spinner", "sync_title", "sync_count",
        "sync_current", "progress", "log_view", "sync_file_list",
        "details_revealer", "details_toggle", "rebuild_button", "eject_button",
    ],
}


def walk(widget):
    """Every widget in a tree, the one it was given included."""
    if widget is None:
        return
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def find_entry(widget):
    """The name field inside a dialog, wherever it was nested.

    Walked rather than reached for by path, so grouping the field differently
    is a change to the dialog's looks rather than a broken check.
    """
    for found in walk(widget):
        if isinstance(found, Adw.EntryRow):
            return found
    return None


def find_button(widget, text):
    """The button in a menu whose row reads like this, or None."""
    for found in walk(widget):
        if isinstance(found, Gtk.Button) and any(
            isinstance(inner, Gtk.Label) and inner.get_text() == text
            for inner in walk(found)
        ):
            return found
    return None


def inspect(window):
    for name in ("library", "search", "album", "playlists", "settings"):
        if window.views.get_child_by_name(name) is None:
            failures.append(f"view {name!r} never reached the view stack")
    for name in ("empty", "device"):
        if window.stack.get_child_by_name(name) is None:
            failures.append(f"page {name!r} never reached the window stack")

    for module, names in EXPECTED.items():
        for name in names:
            if not hasattr(window, name):
                failures.append(f"{module} did not build self.{name}")

    # _set_busy calls set_sensitive on each of these, so a name that resolved
    # to something else would fail only once a script was actually running.
    for widget in window._busy_widgets:
        if not isinstance(widget, Gtk.Widget):
            failures.append(f"_busy_widgets holds a non-widget: {widget!r}")

    for name in ("library", "playlists", "settings", "search", "album"):
        window.show_view(name)
        if window.current_view() != name:
            failures.append(
                f"show_view({name!r}) left {window.current_view()!r} on screen"
            )

    # The repaints every mixin exposes, against an empty library and no device,
    # which is the state the window opens in and the one most likely to have a
    # None nothing checked for.
    for repaint in (
        window._update_now_playing,
        window._populate_cache_card,
        window._populate_playlist_rail,
        window._populate_albums,
        window._populate_folders,
        window._update_device_controls,
        window._refresh_current_view,
        window._merge_states,
        window._populate_searching_summary,
    ):
        try:
            repaint()
        except Exception:  # noqa: BLE001 - any of them failing is the finding
            failures.append(f"{repaint.__name__} raised:\n{traceback.format_exc()}")

    # A playlist made the way the app makes one, painted the way the app
    # paints it. The rail, the detail and the menus are the only widgets built
    # from data rather than at construction, so nothing else here would notice
    # a row or a popover that cannot be built at all.
    gui.create_local_playlist(gui.PLAYLIST_LIBRARY, "Built")
    window._populate_playlist_rail()
    if window.current_playlist != "Built":
        failures.append(
            f"a new playlist was not selected: {window.current_playlist!r}"
        )
    if window.playlist_heading.get_text() != "Built":
        failures.append(
            f"the detail shows {window.playlist_heading.get_text()!r}"
        )
    if window.playlist_body.get_visible_child_name() != "empty":
        failures.append("an empty playlist did not show its empty state")

    # Every popover is built as it opens rather than with the row it hangs off,
    # so a broken one would first show up under the user's pointer.
    track = gui.Track("/music/Artist/Song.mp3", {"title": "Song"}, gui.STATE_LIBRARY)
    result = gui.SearchResult("Result", "Uploader", 0, "https://x.invalid/v", "v")
    for name, build in (
        ("track_menu", lambda: window.track_menu(track)),
        ("track_menu in a playlist", lambda: window.track_menu(track, "Built")),
        ("result_menu", lambda: window.result_menu(result)),
    ):
        try:
            popover = build()
        except Exception:  # noqa: BLE001 - any of them failing is the finding
            failures.append(f"{name} raised:\n{traceback.format_exc()}")
            continue
        if not isinstance(popover, Gtk.Popover):
            failures.append(f"{name} returned {popover!r}")

    # A playlist another program wrote can list a track relative to the folder
    # it sits in. The sync resolves that, so the entry is real - but it names
    # nothing this app can write into a different playlist. Taking it out of
    # the list it is in writes no path anywhere, so that has to stay on offer,
    # or a line like this could never be removed at all.
    relative_list = gui.local_playlist_file(gui.PLAYLIST_LIBRARY, "Built")
    gui.write_playlist_entries(relative_list, ["Somebody Else Wrote This.mp3"])
    window._populate_playlist_rail()
    borrowed = gui.Track(
        "Somebody Else Wrote This.mp3",
        {"title": "Somebody Else Wrote This"},
        gui.STATE_LIBRARY,
    )
    inside = window.track_menu(borrowed, "Built")
    removal = find_button(inside.get_child(), "Remove from Built")
    if removal is None:
        failures.append(
            "a folder-relative entry offered no way out of the playlist it is in"
        )
    else:
        removal.emit("clicked")
        left = gui.read_playlist_entries(relative_list)
        if left:
            failures.append(f"Remove left the entry in the playlist: {left}")
    # Putting it in a different playlist stays refused: resolved against that
    # playlist's folder instead, the same line names nothing at all.
    outside = window.track_menu(borrowed)
    if find_button(outside.get_child(), "Built") is not None:
        failures.append("a folder-relative entry was offered as one to add")

    # Naming a playlist and renaming one are one dialog assembled in one place,
    # so a break in it is a break in both, and neither is built until the user
    # asks for it. What is read back is what the dialog offers: a usable name
    # to accept, and a refusal while a name FAT cannot store is being typed.
    for name, build, response in (
        ("on_new_playlist", lambda: window.on_new_playlist(), "create"),
        ("on_rename_playlist", lambda: window.on_rename_playlist("Built"), "rename"),
    ):
        try:
            dialog = build()
        except Exception:  # noqa: BLE001 - any of them failing is the finding
            failures.append(f"{name} raised:\n{traceback.format_exc()}")
            continue
        if not isinstance(dialog, Adw.AlertDialog):
            failures.append(f"{name} returned {dialog!r}")
            continue
        if not dialog.get_response_enabled(response):
            failures.append(f"{name} opened offering a name it then refused")
        field = find_entry(dialog.get_extra_child())
        if field is None:
            failures.append(f"{name} built no field to type a name into")
        else:
            field.set_text("Road/Trip")
            if dialog.get_response_enabled(response):
                failures.append(
                    f"{name} still offered {response!r} for a name with a slash "
                    "in it, which the sync would mangle into another name"
                )
        dialog.force_close()

    # Closing stops the player and disowns any download; it is a mixin's job
    # now, so a split that lost the wiring would leave audio playing.
    window._on_close_request(window)


def on_activate(app):
    try:
        window = gui.IpodWindow(application=app)
    except Exception:  # noqa: BLE001 - the construction is the subject
        failures.append(f"IpodWindow(...) raised:\n{traceback.format_exc()}")
        app.quit()
        return

    def look():
        # Anything at all, because an exception escaping an idle callback does
        # not end the main loop: it is printed and the loop carries on, so this
        # check would hang rather than fail. That is how a missing widget first
        # showed up while this file was being written.
        try:
            inspect(window)
        except Exception:  # noqa: BLE001
            failures.append(f"inspecting the window raised:\n{traceback.format_exc()}")
        app.quit()
        return False

    # After one main-loop turn, so the widgets are realised rather than merely
    # constructed and a bad parent has had its chance to warn.
    GLib.idle_add(look)


app = Adw.Application(
    application_id="io.github.max_miller1204.IpodShuffle.BuildCheck",
    # Otherwise a second run, or a copy already on the session bus, is handed
    # the existing instance and this one never activates.
    flags=Gio.ApplicationFlags.NON_UNIQUE,
)
app.connect("activate", on_activate)
# A window that never finishes building, or a dialog nobody expected, would
# otherwise hang CI until the job times out with no output saying why.
GLib.timeout_add_seconds(
    60, lambda: failures.append("the window did not finish building") or app.quit()
)
app.run([])

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)

print(f"IpodWindow built; {sum(len(v) for v in EXPECTED.values())} widgets in place")
