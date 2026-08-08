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
        "library_controls", "monitor", "window_controls",
    ],
    "library_view": [
        "album_flow", "album_filters", "library_table", "library_modes",
        "library_status", "collection_heading", "group_mode",
        "mode_buttons",
        "album_view", "album_tracks", "album_heading", "album_subheading",
        "album_actions", "album_art_holder", "library_view",
    ],
    "search_view": [
        "search_entry", "search_view", "search_local_table",
        "search_youtube_rows", "search_local_note", "search_youtube_note",
        "search_local_count", "search_youtube_count",
        "search_playlist_row", "search_playlist_label", "search_playlist_add",
        "clipboard_offer", "clipboard_offer_label",
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


class FakeClipboard:
    """A clipboard holding one string, answered the async way GTK answers.

    The real read is a Gio async call, and driving one from here would mean
    pumping the loop from inside the callback that is already running on it.
    What the window does with the answer is the subject, so the answer is
    handed over directly.
    """

    def __init__(self, text):
        self.text = text

    def read_text_finish(self, _result):
        return self.text


def menu_text(widget):
    """Everything a menu says, as one string."""
    return " / ".join(
        found.get_text()
        for found in walk(widget)
        if isinstance(found, Gtk.Label)
    )


def tooltip_shown(widget):
    """Whether this widget would put its tip on screen right now.

    Asking the widget the question GTK asks it when the pointer settles, rather
    than reading a property: a tip that is withheld is one whose handler
    answered False for where the pointer is, and nothing else records that.
    """
    return widget.emit("query-tooltip", 0, 0, False, Gtk.Tooltip())


def inspect(window):
    # Where the window says it is the moment it opens, read before anything
    # below calls show_view and answers the question for it. The stack shows
    # the library because that is what was added first, so the sidebar has to
    # agree: a window whose rows are all unmarked has no current location on
    # screen until the user clicks one.
    if window.current_view() != "library":
        failures.append(
            f"a freshly built window opened on {window.current_view()!r}"
        )
    if "selected" not in window.nav_buttons["library"].get_css_classes():
        failures.append(
            "a freshly built window shows the library with no sidebar row marked"
        )

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

    # The list a row is in is the one place it cannot be moved to, so with only
    # that one made the move menu has nothing to offer - and must not say "no
    # playlists yet" while the user is standing in one it names two lines down.
    only_one = window.track_menu(track, "Built")
    said = menu_text(only_one.get_child())
    if "No other playlists" not in said:
        failures.append(f"the move menu with one playlist reads: {said}")
    if "No playlists yet" in said:
        failures.append(f"the move menu called a playlist the user is in none: {said}")
    # The add menu is a different question and keeps its own wording, with the
    # one playlist there is on offer rather than a sentence about having none.
    if "No" in menu_text(window.track_menu(track).get_child()):
        failures.append("the add menu claimed there were no playlists")

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

    # The Album/Artist drop-down, moved the way the user moves it. The grouping
    # is not held anywhere but in the widget, so what the grid draws and what
    # the next launch reopens on both have to follow the selection rather than
    # a copy of it kept beside it. Repeats are in the sequence on purpose:
    # re-choosing what is already chosen emits no notify at all, and must
    # leave both of those where they were rather than reading as a change.
    for chosen in ("artist", "album", "artist", "artist", "album", "album"):
        window.group_mode.set_selected(gui.GROUP_MODES.index(chosen))
        if window.group_mode.get_selected() != gui.GROUP_MODES.index(chosen):
            failures.append(
                f"choosing {chosen!r} left the grouping control showing "
                f"{gui.GROUP_MODES[window.group_mode.get_selected()]!r}"
            )
            break
        if window._grouped_by_artist() != (chosen == "artist"):
            failures.append(
                f"choosing {chosen!r} left the grid grouped the other way"
            )
            break
        # What the next launch reopens on. The user's report was that this
        # never survived being closed, so the write is read back rather than
        # assumed from the button.
        saved, _view = gui.library_layout()
        if saved != chosen:
            failures.append(
                f"choosing {chosen!r} saved the grouping as {saved!r}"
            )
            break

    # What the option says has to be the grouping it gives. The control is read
    # by index, so a label drifting from the mode it sits beside would group the
    # library by the other one, save that, and never raise a thing. Read off the
    # widget's own model, which is what the user is picking from.
    options = window.group_mode.get_model()
    for index in range(options.get_n_items()):
        window.group_mode.set_selected(index)
        shown = options.get_string(index)
        if window._group_mode_name() != shown.lower():
            failures.append(
                f"the option reading {shown!r} grouped the library by "
                f"{window._group_mode_name()!r}"
            )

    # A tooltip on a control that opens a list has to get out of the way of the
    # list. A tip is a surface of its own and GTK goes on showing it while the
    # popover maps underneath, so one left up is drawn over the options and
    # takes the click meant for the one below it - the menu closes having
    # chosen nothing, and the tip blinking as the pointer moves reads as the
    # window flickering. Both controls that ask for this are driven, because
    # each finds its popover by a different route: a menu button hands its one
    # over, while a drop-down keeps its list in a popover it offers no accessor
    # for and is walked for it, which holds only while GtkDropDown parents that
    # popover directly under itself.
    row_menu = gui.row_menu_button(lambda: window.track_menu(track), "Menu")
    window.library_controls.append(row_menu)
    for name, control, show_list, hide_list in (
        (
            "the Album/Artist drop-down",
            window.group_mode,
            lambda: window.group_mode.get_first_child().set_active(True),
            lambda: window.group_mode.get_first_child().set_active(False),
        ),
        ("a row's menu button", row_menu, row_menu.popup, row_menu.popdown),
    ):
        if not tooltip_shown(control):
            failures.append(f"{name} withheld its tooltip with no list open")
        show_list()
        if gui._open_popover(control) is None:
            failures.append(
                f"{name} opened a list the tooltip cannot see, so the tip stays "
                "up on top of it"
            )
        if tooltip_shown(control):
            failures.append(
                f"{name} kept its tooltip over the list it had just opened, "
                "where it takes the click meant for an option beneath it"
            )
        hide_list()
        if not tooltip_shown(control):
            failures.append(f"{name} never got its tooltip back once closed")
    window.library_controls.remove(row_menu)

    # What a grouping click costs the settings beside it. One file holds the
    # layout and the music folders, and every click rewrites the whole of it,
    # so a save that dropped keys or landed half written would take the folder
    # list with it - and an unreadable config reads as no config at all, which
    # is the folders silently back to their default with nothing said.
    roots = [Path(_SANDBOX, "Music"), Path(_SANDBOX, "Second Library")]
    before_group, _before_view = gui.library_layout()
    gui.save_music_roots(roots)
    after_group, _after_view = gui.library_layout()
    if after_group != before_group:
        failures.append(
            f"saving the music folders turned the grouping from "
            f"{before_group!r} into {after_group!r}"
        )
    # Whichever grouping is not the one showing, so this stays a real change
    # however the checks above left the control: re-choosing what is already
    # chosen emits no notify, nothing would be saved, and the folders below
    # would then be read back from a write that never happened.
    moved_to = (window.group_mode.get_selected() + 1) % len(gui.GROUP_MODES)
    window.group_mode.set_selected(moved_to)
    if gui.library_layout()[0] != gui.GROUP_MODES[moved_to]:
        failures.append(
            f"choosing {gui.GROUP_MODES[moved_to]!r} saved nothing, so the "
            "music folders below are not being read back from a save"
        )
    if gui.music_roots() != roots:
        failures.append(
            f"saving the grouping dropped the music folders: {gui.music_roots()}"
        )
    # And a save that cannot finish costs nothing at all. The file is written
    # beside itself and renamed into place, so the way to stop one half way is
    # to make that sibling unwritable - a directory in its name, standing in
    # for the disk filling up or the session ending mid-write. A writer that
    # truncated the real file first would have nothing to put back.
    layout_before = gui.library_layout()
    blocked = gui.CONFIG_FILE.with_name(f".{gui.CONFIG_FILE.name}.tmp")
    blocked.mkdir()
    try:
        gui.save_library_layout("album", "list")
    finally:
        blocked.rmdir()
    if gui.music_roots() != roots:
        failures.append(
            f"a config save that could not finish lost the music folders: "
            f"{gui.music_roots()}"
        )
    if gui.library_layout() != layout_before:
        failures.append(
            f"a config save that could not finish still moved the layout from "
            f"{layout_before} to {gui.library_layout()}"
        )

    # The one field that reaches YouTube has to say that a link goes in it.
    # Pasting one has worked since the search shipped and nobody would know:
    # the placeholder only offered to search, which is why a dialog on the
    # settings page went on being reached for instead.
    placeholder = window.search_entry.get_placeholder_text() or ""
    if "paste" not in placeholder.lower():
        failures.append(
            f"the search field's placeholder never mentions pasting a link: "
            f"{placeholder!r}"
        )
    # The field is far too narrow for the whole sentence, so the tooltip is
    # where both sources and the link are actually named - and it is what a
    # screen reader reads out, since the field carries no label of its own.
    tip = window.search_entry.get_tooltip_text() or ""
    for word in ("library", "YouTube", "link"):
        if word.lower() not in tip.lower():
            failures.append(f"the search field's tooltip never says {word}: {tip!r}")

    # The strip offering a link off the clipboard, driven the way the user
    # drives it. Built at construction and revealed later, so nothing else
    # here would notice a label appended to the wrong box or a button whose
    # handler cannot run.
    window._offered_links.clear()
    window._offer_clipboard_link(FakeClipboard("  https://youtu.be/abc  "), None)
    if not window.clipboard_offer.get_reveal_child():
        failures.append("a link on the clipboard was never offered")
    if "youtu.be/abc" not in window.clipboard_offer_label.get_text():
        failures.append(
            f"the offer does not name the link: "
            f"{window.clipboard_offer_label.get_text()!r}"
        )
    # Offered rather than typed in: nothing has run until the offer is taken.
    if window.search_entry.get_text():
        failures.append(
            f"a clipboard link filled the field by itself: "
            f"{window.search_entry.get_text()!r}"
        )
    use = find_button(window.clipboard_offer, "Look it up")
    if use is None:
        failures.append("the clipboard offer has no button to take it up")
    else:
        use.emit("clicked")
        if window.search_entry.get_text() != "https://youtu.be/abc":
            failures.append(
                f"taking the offer left the field reading "
                f"{window.search_entry.get_text()!r}"
            )
        if window.clipboard_offer.get_reveal_child():
            failures.append("the offer stayed up after it had been taken")
    window.search_entry.set_text("")
    # And the same link is not offered a second time: the field is empty again
    # every time the window is come back to, so an offer that returned after
    # being turned down would be back on every visit.
    window._offer_clipboard_link(FakeClipboard("https://youtu.be/abc"), None)
    if window.clipboard_offer.get_reveal_child():
        failures.append("a link that had already been offered came back")

    # The header above the YouTube results, which is the whole of what tells
    # three rows of a forty-track playlist from a three-track playlist.
    window.search_results = [result]
    window.search_playlist = gui.LinkedPlaylist(
        "Road Trip", 40, "https://www.youtube.com/playlist?list=PL1", 3
    )
    window._paint_youtube_section()
    if not window.search_playlist_row.get_visible():
        failures.append("a resolved playlist showed no header above its rows")
    said = window.search_playlist_label.get_text()
    if "Road Trip" not in said or "40 tracks" not in said:
        failures.append(f"the playlist header reads {said!r}")
    if not window.search_playlist_add.get_visible():
        failures.append("a playlist of a stated length offered no Add all")
    if window.search_playlist_add not in window.search_add_buttons:
        failures.append(
            "Add all is not one of the buttons a device appearing makes "
            "sensitive, so it would stay disabled with an iPod plugged in"
        )

    # A Mix, a channel or a /videos tab reports no length, because it is
    # paginated rather than finite. The header still names it - that is what
    # explains the three rows - but there is no whole of it to offer in one
    # press onto a device with two gigabytes on it, and each row keeps its Add.
    window.search_playlist = gui.LinkedPlaylist(
        "Bohemian Rhapsody Radio",
        0,
        "https://www.youtube.com/watch?v=fJ9rUzIMcZQ&list=RDfJ9rUzIMcZQ",
        3,
    )
    window._paint_youtube_section()
    if not window.search_playlist_row.get_visible():
        failures.append("a listing of no stated length showed no header at all")
    said = window.search_playlist_label.get_text()
    if "Bohemian Rhapsody Radio" not in said:
        failures.append(f"the header did not name the listing: {said!r}")
    if window.search_playlist_add.get_visible():
        failures.append(
            "Add all was offered for a listing yt-dlp gave no length for, "
            "which is one press to fetch a mix or a channel with no end to it"
        )
    if window.search_playlist_add in window.search_add_buttons:
        failures.append(
            "a hidden Add all is still on the list a device appearing makes "
            "sensitive, so plugging in would light up a button that is not there"
        )

    window.search_playlist = None
    window._paint_youtube_section()
    if window.search_playlist_row.get_visible():
        failures.append("the playlist header outlived the playlist it named")

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
