"""The widgets more than one view builds, so they look the same in each."""

from pathlib import Path

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .config import STATE_LABELS, STATE_PREVIEW, YOUTUBE_LIBRARY
from .text import home_relative, human_duration
from .theme import PALETTE, cover_class


ELLIPSIZE_END = Pango.EllipsizeMode.END


ALBUM_COVER = 140

# A playlist tile stands in the same page as the album grid, one section above
# it, so it takes the grid's artwork size. Two tile widths a few pixels apart
# on one page read as a mistake rather than as two kinds of thing.
PLAYLIST_TILE_COVER = ALBUM_COVER

# One size for a playlist row wherever it appears, so the sidebar and the
# Playlists rail are the same row rather than two versions of it - the
# Playlists view shows both at once, which is where two sizes showed.
PLAYLIST_ROW_COVER = 32

# Where a page whose body is a track table stops widening. A table that keeps
# stretching with the window leaves the title against one edge and the state,
# duration and action cluster against the other, with the whole width between
# them; past about this much, the row stops reading as one line.
TRACK_PAGE_WIDTH = 900


def clear_children(container):
    """Empty a container, one child at a time.

    The next child is held before this one is removed: a widget's sibling links
    go with it when it leaves, so the loop that reads get_next_sibling
    afterwards clears the first child and stops.
    """
    child = container.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        container.remove(child)
        child = nxt


def cover_pixel_size(width, height, size):
    """How large to draw artwork so it fills a square of this size.

    Gtk.Image fits a paintable inside the square it is given, which letterboxes
    anything that is not square - and a YouTube thumbnail is 16:9, so it would
    be a strip of artwork floating in a band of placeholder colour. Scaling by
    the long edge instead lands the short edge exactly on the square and lets
    the frame clip what hangs over, which is the centre crop every music player
    shows a video thumbnail as.
    """
    if width <= 0 or height <= 0:
        return size
    long_edge, short_edge = max(width, height), min(width, height)
    # Rounded up, because a pixel short of the square would show a hairline of
    # the frame's own background down one edge.
    return (size * long_edge + short_edge - 1) // short_edge


def make_cover(art_path, size, seed, extra_class=""):
    """A rounded square cover, from embedded art or a generated placeholder."""
    # An Overlay rather than a Box because artwork wider than the square is
    # what fills it: an overlay child is not measured, so a cropped cover
    # cannot widen the row it sits in, while a Box would grow to its child. It
    # is left without a main child for the same reason - the size request below
    # is the whole of the square, and an Overlay measures nothing else.
    frame = Gtk.Overlay()
    frame.add_css_class("sf-cover")
    if extra_class:
        frame.add_css_class(extra_class)
    # border-radius alone paints rounded corners but does not clip children,
    # so the artwork would square them off again.
    frame.set_overflow(Gtk.Overflow.HIDDEN)
    frame.set_size_request(size, size)
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.CENTER)

    texture = None
    if art_path and Path(art_path).exists():
        try:
            texture = Gdk.Texture.new_from_filename(str(art_path))
        except GLib.Error:
            texture = None

    if texture is not None:
        # Gtk.Image rather than Gtk.Picture: a Picture reports the texture's
        # intrinsic size as its natural width, so a 600px cover would make
        # every cell of the album grid 600px wide and collapse it to two
        # columns. Image honours pixel-size instead.
        image = Gtk.Image.new_from_paintable(texture)
        image.set_pixel_size(
            cover_pixel_size(texture.get_width(), texture.get_height(), size)
        )
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        frame.add_overlay(image)
    else:
        frame.add_css_class(cover_class(seed))

    return frame


def state_dot(state):
    dot = Gtk.Box(valign=Gtk.Align.CENTER)
    dot.add_css_class("sf-dot")
    dot.add_css_class(state)
    return dot


def label(text, *classes, **kwargs):
    kwargs.setdefault("xalign", 0)
    widget = Gtk.Label(label=text, **kwargs)
    for name in classes:
        widget.add_css_class(name)
    return widget


class TrackItem(GObject.Object):
    """A Track in a form Gio.ListStore will hold."""

    __gtype_name__ = "ShuffleTrackItem"

    def __init__(self, track, number):
        super().__init__()
        self.track = track
        self.number = number


def playable_cover(window, track, view):
    """A track's artwork, which becomes a play button under the pointer.

    On the cover rather than in a column of its own: a row already carries an
    Add or Remove button, and a second permanently visible button beside it
    would make the more consequential one harder to pick out. Hovering the
    artwork is also where every other music player puts this.
    """
    cover = make_cover(track.art, 36, track.album, "small")
    if view is None:
        return cover

    overlay = Gtk.Overlay(valign=Gtk.Align.CENTER)
    overlay.set_child(cover)
    play = Gtk.Button(icon_name="media-playback-start-symbolic")
    play.add_css_class("sf-cover-play")
    play.set_size_request(36, 36)
    if window.preview_unavailable:
        play.set_sensitive(False)
        play.set_tooltip_text(window.preview_unavailable)
    else:
        play.set_tooltip_text(f"Preview {track.title} on this computer")
    play.connect("clicked", lambda _b, t=track: window.play_from(view, t))
    overlay.add_overlay(play)
    return overlay


def _open_popover(widget):
    """The popover this widget is currently showing, or None.

    A menu button hands its one over. A drop-down keeps its list in a popover
    it has no accessor for, so that one is found by walking: it is a child of
    the widget like anything else it draws.
    """
    if isinstance(widget, Gtk.MenuButton):
        popover = widget.get_popover()
        return popover if popover is not None and popover.get_visible() else None
    stack = [widget]
    while stack:
        current = stack.pop()
        child = current.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Popover) and child.get_visible():
                return child
            stack.append(child)
            child = child.get_next_sibling()
    return None


def tooltip_beside_popover(widget, text):
    """A tooltip that gets out of the way of the menu it sits on.

    A tooltip is its own surface, and GTK goes on showing it while the widget
    under the pointer opens a popover. The tip is then drawn over the list it
    was describing and takes the click meant for the option beneath it, so the
    menu closes having selected nothing - the control reads as simply not
    working, and the tip blinking in and out as the pointer moves reads as the
    window flickering.

    Withheld while the popover is up rather than dropped altogether: that is
    the one moment it is not wanted, because whatever it would have explained
    is already open and on screen.
    """
    widget.set_has_tooltip(True)

    def query(target, _x, _y, _keyboard, tooltip):
        if _open_popover(target) is not None:
            return False
        tooltip.set_text(text)
        return True

    widget.connect("query-tooltip", query)


def row_menu_button(build_popover, tooltip, dim=True):
    """The ⋯ that opens a row's menu.

    The popover is built when it opens rather than when the row is drawn: what
    it lists is the set of playlists, which changes under the row, and building
    one per row would build several hundred menus to show at most one of them.

    Dimmed at rest on a row, where there is one per line and a column of them
    at full contrast competes with the titles beside it. A page that carries a
    single one is the other case: there it is one control among two or three,
    and half-strength reads as disabled.
    """
    button = Gtk.MenuButton(icon_name="view-more-symbolic")
    button.add_css_class("flat")
    if dim:
        button.add_css_class("sf-row-menu")
    button.set_valign(Gtk.Align.CENTER)
    tooltip_beside_popover(button, tooltip)
    button.set_create_popup_func(lambda menu: menu.set_popover(build_popover()))
    return button


def track_cell(window, track, number, column, view=None, playlist=None):
    """One cell of a track row, for whichever column asked for it.

    `playlist` names the playlist the row is being shown inside, which is what
    turns its menu from adding to moving and lets it offer a removal.
    """
    if column == "number":
        return label(str(number), "sf-caption", "sf-mono", width_chars=3, xalign=1.0)

    if column == "title":
        row = Gtk.Box(spacing=12)
        row.append(playable_cover(window, track, view))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(label(track.title, "sf-row-title", ellipsize=ELLIPSIZE_END))
        subtitle = track.artist if track.tagged else "No tags — filename shown"
        text.append(label(subtitle, "sf-body", ellipsize=ELLIPSIZE_END))
        row.append(text)
        return row

    if column == "album":
        return label(track.album, "sf-body", ellipsize=ELLIPSIZE_END, hexpand=True)

    if column == "state":
        marker = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        marker.append(state_dot(track.state))
        marker.append(label(STATE_LABELS[track.state], "sf-caption"))
        return marker

    if column == "duration":
        return label(
            human_duration(track.duration),
            "sf-caption",
            "sf-mono",
            width_chars=5,
            xalign=1.0,
        )

    if column == "menu":
        return row_menu_button(
            lambda: window.track_menu(track, playlist),
            f"Playlists holding {track.title}",
        )

    action = Gtk.Button()
    action.add_css_class("sf-button")
    action.set_valign(Gtk.Align.CENTER)
    if track.state == STATE_PREVIEW:
        # Add still means "I want this track", but a previewed file is sitting
        # in a cache that gets pruned, so here it has to move the file first.
        # Offered with no iPod attached, unlike every other Add: keeping a
        # download is something you do to your own music folder.
        action.set_label("Add")
        action.add_css_class("accent")
        action.set_tooltip_text(
            f"Move into {home_relative(YOUTUBE_LIBRARY)} and out of the "
            "preview cache"
        )
        action.connect("clicked", lambda _b, t=track: window._promote_preview(t))
        return action
    if track.on_ipod:
        action.set_label("Remove")
        action.connect(
            "clicked", lambda b, t=track: window.on_remove_track(b, t.relpath)
        )
        action.set_sensitive(bool(window.mount_point))
    elif track.path in window.pending:
        action.set_label("Queued")
        action.connect("clicked", lambda _b, t=track: window._unqueue_track(t))
    else:
        action.set_label("Add")
        action.add_css_class("accent")
        action.connect("clicked", lambda _b, t=track: window._queue_tracks([t]))
        action.set_sensitive(bool(window.mount_point))
    return action


TRACK_COLUMNS = (
    # key, title, expand, sort key
    ("number", "#", False, None),
    ("title", "Title", True, lambda t: t.title.lower()),
    ("album", "Album", True, lambda t: t.album.lower()),
    ("state", "", False, lambda t: t.state),
    ("duration", "Time", False, lambda t: t.duration),
    ("action", "", False, None),
    ("menu", "", False, None),
)


def track_sorter(get):
    """A column sorter keyed on one field of the track.

    A closure rather than a lambda with a default argument: GtkCustomSorter
    calls the comparison with user_data as a third positional argument, which
    would land on that default and replace the key function with None.
    """

    def compare(left, right, _user_data=None):
        first, second = get(left.track), get(right.track)
        return (first > second) - (first < second)

    return Gtk.CustomSorter.new(compare)


def track_column_view(window, columns=None):
    """A sortable track table.

    GtkColumnView rather than a box of rows: it recycles widgets, so a flat
    view of a whole library stays responsive, and each column can carry a
    sorter without the rows knowing anything about it.
    """
    store = Gio.ListStore.new(TrackItem)
    sort_model = Gtk.SortListModel.new(store, None)
    view = Gtk.ColumnView.new(Gtk.NoSelection.new(sort_model))
    view.add_css_class("sf-tracks")
    sort_model.set_sorter(view.get_sorter())

    wanted = columns or [key for key, *_ in TRACK_COLUMNS]
    for key, title, expand, sort_key in TRACK_COLUMNS:
        if key not in wanted:
            continue
        factory = Gtk.SignalListItemFactory()

        def bind(_factory, item, key=key):
            entry = item.get_item()
            item.set_child(track_cell(window, entry.track, entry.number, key, view))

        factory.connect("bind", bind)
        column = Gtk.ColumnViewColumn.new(title, factory)
        column.set_expand(expand)
        if sort_key is not None:
            column.set_sorter(track_sorter(sort_key))
        view.append_column(column)
    view.store = store
    return view


def track_list_view(window, on_reorder):
    """An ordered, drag-reorderable track list.

    A GtkListView rather than the sortable table above, because a playlist's
    order is the whole point of it: offering to sort one by title would throw
    away the only thing the user put there by hand.
    """
    store = Gio.ListStore.new(TrackItem)
    view = Gtk.ListView.new(Gtk.NoSelection.new(store), Gtk.SignalListItemFactory())
    view.add_css_class("sf-tracks")
    factory = view.get_factory()

    def setup(_factory, item):
        row = Gtk.Box(spacing=12)
        row.add_css_class("sf-track-row")

        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda _s, _x, _y, i=item: Gdk.ContentProvider.new_for_value(
                GObject.Value(GObject.TYPE_UINT, i.get_position())
            ),
        )
        row.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_UINT, Gdk.DragAction.MOVE)
        target.connect(
            "drop",
            lambda _t, value, _x, _y, i=item: on_reorder(value, i.get_position()),
        )
        row.add_controller(target)
        item.set_child(row)

    def bind(_factory, item):
        row = item.get_child()
        clear_children(row)
        entry = item.get_item()
        # Which playlist is on screen is read at bind time rather than captured
        # when the view was built, because one view shows every playlist in
        # turn and a row recycled by a scroll binds long after the switch.
        playlist = window.current_playlist
        for key in ("number", "title", "state", "duration", "action", "menu"):
            row.append(
                track_cell(window, entry.track, entry.number, key, view, playlist)
            )
        if entry.track.state == STATE_PREVIEW:
            row.add_css_class("previewed")
        else:
            row.remove_css_class("previewed")

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    view.store = store
    return view


def fill_tracks(view, tracks):
    view.store.remove_all()
    for number, track in enumerate(tracks, start=1):
        view.store.append(TrackItem(track, number))


class StorageMeter(Gtk.Box):
    """Used, queued and free space as one bar.

    A CSS gradient with hard colour stops rather than a drawn widget: Cairo
    drawing from Python needs python3-gi-cairo, which is a system package this
    project would otherwise never ask for, and the segments have to meet
    exactly at a fraction that no box layout expresses cleanly.

    Every meter shares one provider, rebuilt whenever any of them changes.
    There are three in the window, so regenerating the lot is cheaper than
    tracking which one moved.
    """

    _provider = None
    _registry = {}
    _counter = 0

    def __init__(self, height=5):
        super().__init__()
        StorageMeter._counter += 1
        self._id = f"sfmeter{StorageMeter._counter}"
        self.set_name(self._id)
        self.add_css_class("sf-meter")
        self.set_size_request(-1, height)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("destroy", lambda *_a: self._forget())
        self.set_fractions(0.0, 0.0)

    def _forget(self):
        StorageMeter._registry.pop(self._id, None)

    def set_fractions(self, used, queued, over=False):
        used = max(0.0, min(1.0, used))
        queued = max(0.0, min(1.0 - used, queued))
        StorageMeter._registry[self._id] = (used, queued, over)
        StorageMeter.restyle()

    @classmethod
    def restyle(cls):
        if cls._provider is None:
            cls._provider = Gtk.CssProvider()
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    cls._provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
                )
        rules = []
        for theme, selector in (
            ("dark", "window.shuffle"),
            ("light", "window.shuffle.light"),
        ):
            colours = PALETTE[theme]
            for name, (used, queued, over) in cls._registry.items():
                # Only the queued segment reddens when the queue will not fit.
                # Reddening the used segment too would erase the one thing the
                # bar is being asked: how much of this is already on the device
                # and how much is the part that overflows.
                filled = colours["accent"]
                pending = colours["danger"] if over else colours["queued"]
                first = used * 100
                second = (used + queued) * 100
                rules.append(
                    f"{selector} #{name} {{ background-image: linear-gradient("
                    f"to right, {filled} 0%, {filled} {first:.2f}%, "
                    f"{pending} {first:.2f}%, {pending} {second:.2f}%, "
                    f"{colours['meter']} {second:.2f}%, "
                    f"{colours['meter']} 100%); }}"
                )
        cls._provider.load_from_data("\n".join(rules), -1)
