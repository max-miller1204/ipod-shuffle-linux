# Deterministic screenshot harness - test evidence

All shots below were produced by the branch's own code, unmodified, against a fresh
`tools/demo-library.py` fixture on an X11 display.

## What works

`tests/screenshot-harness.py` builds the fixture and renders both canonical shots, and exits ok.

`tools/shoot.py` navigates through the `navigate` Gio action, allocates the content explicitly and
rasterises through `Gsk.CairoRenderer` with no compositor frame involved.
The output PNG is exactly `--width x 760` logical pixels multiplied by `--scale`.

| command | PNG |
| --- | --- |
| `--page library --width 1180 --scale 1` | 1180x760 |
| `--page playlists --width 760 --scale 2` | 1520x1520 |
| `--page settings --width 900 --scale 1` | 900x760 |
| `--page settings --width 900 --scale 2` | 1800x1520 |

The CLI refuses rather than writing a shot it cannot vouch for.
No PNG is left behind by any of these:

```
--scale omitted          -> exit 2  error: the following arguments are required: --scale
--scale 3                -> exit 2  error: argument --scale: invalid choice: 3 (choose from 1, 2)
--width 320              -> exit 1  --width must be at least the window's own minimum, 660
--fixture <not a demo>   -> exit 1  not a tools/demo-library.py fixture: ...
--page nowhere           -> exit 2  error: argument --page: invalid choice: 'nowhere'
```

`library-1180-1x.png` and `playlists-760-2x.png` are the two canonical shots exactly as CI writes
them; `readme-usage-library-1180-2x.png` is the command the README documents.

## Two defects the shots show

### 1. The shot is taken before the window has finished painting

`defect-stale-album-grid.png`

`_apply_device_track_batch` sets `_device_snapshot_ready`, runs `_merge_states`, and then arms a
250 ms coalesced repaint (`_request_refresh(scan_complete=True)`, `REFRESH_COALESCE_MS = 250`).
`tools/shoot.py` settles on the two model flags only, so it snapshots with that repaint still
pending and captures the grid from before the merge.

Instrumenting the exact snapshot moment of a real run:

```
A model state: {"ipod": 2, "queued": 0, "library": 4, "preview": 0}
A pending coalesced repaint timer: 70          <- still armed when the PNG is written
B model state: {"ipod": 2, "queued": 0, "library": 4, "preview": 0}
```

The canonical library shot reads `On iPod 0 / In library 4` with Warm Ridge badged `In library`,
while the window's own `dump_state()` at that instant reports two tracks on the iPod, and
`docs/screenshot.png` of the same fixture reads `On iPod 1 / In library 3`.

Whether the repaint lands first depends on how long the preceding settle loops, so this is also a
flake: the 760px playlists shot loops its breakpoint settle for ~2 s and does land, the 1180px
library shot returns immediately and does not.

`defect-repeat-runs-differ.png` is the same symptom on the header: the shot lands inside the
refresh spinner's minimum-visible window, so three runs of one identical command produced three
different files, each with the spinner frozen at a different angle.

### 2. `--scale 2` changes the layout instead of only the raster density

`defect-scale-changes-layout.png`

Adw breakpoints are evaluated against the window's width, and at `--scale 2` the window keeps
whatever width the display server gave it rather than the requested one:

```
--width 1180 --scale 1: window.get_width()=1180 content.get_width()=1180 split.collapsed=False
--width 1180 --scale 2: window.get_width()=830  content.get_width()=1180 split.collapsed=True
--width 2400 --scale 2: window.get_width()=830  content.get_width()=2400 split.collapsed=True
--width 1000 --scale 1: window.get_width()=1000 content.get_width()=1000 split.collapsed=False
--width  900 --scale 1: window.get_width()=900  content.get_width()=900  split.collapsed=True
```

`SIDEBAR_COLLAPSE_WIDTH` is 940, so 1180 should show the sidebar and 900 should fold it, which is
what the 1x rows do.
At 2x the window is stuck at 830 whatever `--width` says, the collapsed setter fires, and the
content is then stretched to the requested width - a layout no user at that width ever sees.
The window width there is the display server's, so the 2x artifact changes with the runner's
screen size, and `--width` reaches the breakpoints only at 1x.
