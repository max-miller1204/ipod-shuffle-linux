# The Album/Artist drop-down, back and driven by hand

Everything here was taken from the real app running against the fixture
`tools/demo-library.py` builds, inside the nested server that same tool's
recipe names: `Xephyr :9 -screen 1300x860 -dpi 96 -br -noreset`, with
`GDK_BACKEND=x11 GSK_RENDERER=cairo DISPLAY=:9`.
The window comes up at exactly 1180x760 there, so these frame the same region
as `docs/screenshot.png`.
Every click below is a real pointer click, sent with `xdotool`.

That nested server is a 1x display.
The drop-down's dismissal is a compositor fault that needs a real 2x output
and cannot be reproduced here, which is the whole point of the change: it was
never this app's to fix.
The goal `stop-startup-repaints-from-dismissing` carries the diagnosis and the
GNOME setting that resolved it, and this machine is currently running with
that setting on - `gsettings get org.gnome.mutter experimental-features`
answers `['scale-monitor-framebuffer']`.

## The artifacts

`07-choosing-a-grouping.gif` is the whole interaction in one: the pointer
settles on the control and its tooltip appears, the click opens the list with
Album and Artist both on show and the tooltip gone from over them, and
choosing Album turns the Artists grid back into an Albums grid.

`01-dropdown-closed.png` - the header as it opens, with the drop-down reading
"Album" where two toggle buttons used to sit.

`02-dropdown-open-1x.png` - the list open, Album ticked and Artist beneath it.
Nothing is drawn on top of the options: the tooltip is withheld for as long as
the list is up, which is what stops it taking the click meant for an option.

`03-grouped-by-artist.png` - after clicking Artist. The heading reads
"Artists", the cards are the four performers and each says how many albums it
holds. The config the app wrote at that moment:

    {
      "group_mode": "artist",
      "view_mode": "grid"
    }

`04-reopens-on-artist.png` - the app quit and launched again. It comes back on
Artist, with the control showing it, rather than resetting to Album.

`05-accessibility-tree.txt` - the same window read over AT-SPI from another
process, which is what a screen reader sees. The drop-down appears as a combo
box named for its current value and described by the string its tooltip
carries, so the control is still announced now that the tooltip is served by a
handler rather than by `tooltip-text`.

`06-grouping-sequence.txt` - the drop-down moved through
artist, album, artist, artist, album, album against the real window. The
repeats are there on purpose: re-choosing what is already chosen emits no
notify at all, and the transcript shows the grid and the saved layout both
staying put across those steps rather than reading as a change.
