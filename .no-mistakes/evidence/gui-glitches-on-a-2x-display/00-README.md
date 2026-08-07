# GUI glitches on a 2x display - test evidence

How these were produced, so a reviewer can tell what the screenshots are of.

The screenshots numbered 02, 03, 04, 09 and 10 are of a **nested GNOME Shell
running as a Wayland compositor on a 2x-scaled built-in panel**, which is the
environment the bug was reported from:

    MUTTER_DEBUG_DUMMY_MODE_SPECS=2400x1600 MUTTER_DEBUG_DUMMY_MONITOR_SCALES=2 \
      gnome-shell --nested --wayland

which the compositor reports as `LVDS1 / Built-in display, 2400x1600, scale 2.0`.
The window is **floating**, not maximised, in every one of them - the reported
failure needed both.
Screenshots 01, 05, 06, 07 and 08 are of the same application on a plain X11
server, where the widths and the spinner timing are the same and easier to
grab frame by frame.

The library in every shot is the one `tools/demo-library.py` builds: four
albums with generated cover art, two playlists, and a stand-in iPod that the
shipped `ipod-sync.sh` has really written to.

## What each file shows

| File | Shows |
| --- | --- |
| `01-header-before-after.png` | The header before (top) and after (bottom): a `Gtk.DropDown` and no window buttons at all, against two linked toggles plus minimise, maximise and close. |
| `02` / `03` | One click on "Artist", on a floating window on the 2x panel, regroups the library. No other window had to be brought forward first. |
| `04` | The application relaunched after being closed, still grouped by artist. |
| `05` / `06` | The refresh button while a rescan runs: the icon becomes a spinner and is held there for its 600ms minimum. |
| `07` / `08` | The window dragged to the 640px minimum it advertises, before and after. Before: covers, mode buttons and the "Preview on this computer" label are painted past the right edge. After: the sidebar has folded away and everything fits. |
| `09` | The window being dragged by its title bar. |
| `10` | Maximise, restore and minimise, each driven by clicking the button in the title bar. Closing it with the close button ended the process. |
| `11` | Grid rebuilds during one startup scan of a 600-track library, before and after. |
| `12` | `tests/gui-window-minimum.py` failing on the base commit and passing here. |
| `13` | The configuration file after clicking the grouping and view controls. |

## What is not here

The original fault - a `Gtk.DropDown` popup dismissed by the compositor about
10ms after it maps - **did not reproduce** in this nested compositor. Clicking
the base build's "Album" drop-down on the floating 2x window opened the menu
and it stayed open. The branch describes the fault as being in the compositor
rather than in this application, established against stock GTK on the
reporter's own machine, so this is a difference between two mutter setups
rather than a contradiction. What is shown instead is that the replacement
control works in that environment.
