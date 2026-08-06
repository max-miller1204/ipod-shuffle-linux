# What was exercised, and what each shot is of

Every screenshot here is of the real GTK4 window, running against a demo
library built by `tools/demo-library.py`, inside the nested X server that tool
documents (`Xephyr :9 -screen 1300x860 -dpi 96`).
The window comes up at exactly 1180x760 there, as the tool says it does.

## The screenshot is reproducible again

`tools/demo-library.py` was run twice into two directories that had never held
a demo, the app was launched against each with the command the tool prints, and
the window was grabbed both times.
Both grabs are byte-identical to the `docs/screenshot.png` this branch commits:

```
$ tools/demo-library.py /tmp/shuffle-demo      # first build
$ tools/demo-library.py /tmp/shuffle-demo2     # a second, from scratch
$ md5sum docs/screenshot.png first-grab.png second-grab.png
61942036fec6f3404a941ac19393b1fa  docs/screenshot.png
61942036fec6f3404a941ac19393b1fa  first-grab.png
61942036fec6f3404a941ac19393b1fa  second-grab.png
```

`library-retaken.png` is the first of those grabs.
It is the fresh-launch view as well: the window opens on the library with the
sidebar row marked, which is what `window.py` now says on construction.

The guard that keeps a rebuild from emptying a folder it did not build, at the
command line:

```
$ tools/demo-library.py /tmp/tmp.M2KuKHC8Ou/Music    # a folder the tool did not build
Refusing to build in /tmp/tmp.M2KuKHC8Ou/Music: it holds something this tool did
not build, so there is no .demo-library in it saying the contents are expendable.
Build the demo somewhere new, or empty that directory yourself first.
  (exit 1)

$ find /tmp/tmp.M2KuKHC8Ou/Music -type f
/tmp/tmp.M2KuKHC8Ou/Music/Pixies/Doolittle/Debaser.mp3
```

## Finding 1: a stale row is no longer reported as a write failure

Another program shortened `Downloads.m3u` to one entry while both its rows were
on screen, then the second row was dragged onto the first with a real
pointer drag (`xdotool mousedown` / `mousemove` / `mouseup`).

- `reorder-stale-source-before-fix.png` - the pre-change code, restored for the
  shot: "Could not write Downloads", and the row the file has lost still on
  screen. The folder is perfectly writable.
- `reorder-stale-source-toast.png` - this branch: "That track is no longer in
  Downloads", and the list has repainted to the one track the file now holds.
- `reorder-stale-target-toast.png` - the same playlist shortened under the
  other end of the drag, so the track being dragged is still listed and only
  the destination has gone: "Downloads has changed - drag that again".

## Finding 2: an import collision lands on the next free number

The window was refreshed so its shelf held `Downloads` and `Morning Ride`, then
another program wrote a `Road Trip.m3u` into the playlist folder, and Import was
pressed twice against a foreign `Road Trip.m3u`.
`import-collision-driver.py` is what drove it: the app's Import button opens the
desktop's own file chooser, which needs a portal this nested server has none of,
so the driver calls what the chooser calls with the path it would have returned.
Everything after that is the app's.

- `import-collision-before-fix.png` - the pre-change code: "There is already a
  playlist called Road Trip", identically on the second press, with nothing the
  user can do to the dialog that changes a name they never chose.
- `import-collision-lands-next-free.png` - this branch: "Imported Road Trip 2 ·
  2 tracks · queued for sync", with the squatter still in the shelf at its own
  one track.
- `import-collision-second-press.png` - pressing again lands on Road Trip 3
  rather than repeating an answer.

The playlist folder afterwards, read back by the driver - the squatter's single
entry is untouched:

```
Downloads.m3u:    ['.../Nightbus/01 - Last Stop.mp3', '.../Slow Copper/01 - Slow Copper.mp3']
Morning Ride.m3u: ['.../Warm Ridge/01 - Low Sun.mp3', '.../Warm Ridge/02 - Ridge Line.mp3']
Road Trip 2.m3u:  ['.../Field Notes/01 - Paper Boats.mp3', '.../Field Notes/02 - Coastal Road.mp3']
Road Trip 3.m3u:  ['.../Field Notes/01 - Paper Boats.mp3', '.../Field Notes/02 - Coastal Road.mp3']
Road Trip.m3u:    ['.../Warm Ridge/01 - Low Sun.mp3']
```
