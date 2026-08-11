# Structured sync progress: what was run, and what it showed

Everything here was produced by driving the shipped scripts and the real window
the way a person drives them, on this branch's code.
The three drivers in this directory are the whole of it: `driver-cli.sh` is the
terminal session, `driver-regression.sh` is the same reword made to the commit
this branch started from and to the branch itself, and
`driver-gui-screenshots.sh` builds `IpodWindow`, starts a real sync through it
and photographs the bar while the script reports.

The upstream database builder is not installed on this machine, so every
rebuild below runs through `tests/fake-db-builder.py`, exactly as the suite
does.
The window is photographed under `gtk4-broadwayd` rather than xvfb, which CI
uses, because this machine has no X server: it is a GDK backend like any other,
and what is saved is the surface GTK painted.

| file | what it shows |
| --- | --- |
| `01-cli-walkthrough.txt` | the whole terminal session: every command, its output, the stream it wrote and the code it left with |
| `02-sync-progress.ndjson` | a sync's progress stream, as a caller receives it |
| `03-remove-progress.ndjson` | a removal's, on descriptor 7 |
| `04-wipe-progress.ndjson` | a wipe's, which is stages and a result and no per-file events |
| `05-reword-before-and-after.txt` | one line of `ipod-sync.sh` reworded, before this change and after it |
| `06-terminal-sync.png` | the sync above rendered as a terminal, with its stream underneath |
| `07-terminal-reader-left.png` | a caller that stops reading mid-copy, and the copy carrying on |
| `08-terminal-refusals.png` | a descriptor nobody opened, refused by all three scripts |
| `10-bar-first-file.png` | the window's sync bar after the script reported its first file |
| `11-bar-mid-copy.png` | the same run four files in |
| `12-bar-playlist-written.png` | every item reported, including the playlist, at 9 of 9 |
| `13-bar-rebuilding.png` | the rebuild, which the bar names rather than sitting on the last file copied |
| `15-sync-bar.gif` | those four, in order |

## The protocol

`02-sync-progress.ndjson` is one run of `ipod-sync.sh --progress-json`, whose
whole document is fifteen lines: `start`, the `device` it settled on, the
`plan` it counted before it began, a `stage` either side of the copy and the
rebuild, one event per file with what became of it, the playlist it wrote, and
a `result` carrying the code the caller is about to be given.

The names are the ones this project actually gets, out of tags and YouTube
titles.
`02 - Say "hi", it's \ fine.mp3` holds a quote and a backslash, and
`03 - Line\nbreak.mp3` holds a real newline: each of them would have ended a
record early had the shell been writing the JSON itself, and each arrives
whole.

`done` reaches `total` exactly, one at a time.
The cover art in the same folder is in neither figure, because the firmware
cannot play it and a bar counting it could never reach its end; it is still
reported to the person reading the terminal, as `Skipped 1 unsupported
file(s)`.
The removal in section 3 is the other direction: it planned one track, then
found a playlist naming that track and rewrote it, so the total followed the
count to 2 rather than the count passing a total announced before the work was
known.

## The stream is additional

Section 2 of the transcript runs the same sync onto an identical device with
the flag left off and diffs the two.
The output is byte for byte identical, which is the only convincing way to say
that the terminal reading is untouched: the scripts are the product, and this
is a second stream rather than a replacement.

## What the reword did, before and after

`05-reword-before-and-after.txt` is the failure this change exists to end.
Both trees are the project as committed, with one edit: `printf '  + %s -> %s\n'`
becomes `printf '  copied %s to %s\n'`, which is the same sentence said
differently and which nothing about the run depends on.

Before the change, with the line as it shipped, the bar reached `2 of 3` for
three copied files, having lost the one whose name holds a newline.
With the line reworded, the bar showed nothing at all: no count, no rows, and a
run that exited 0 with the iPod correctly written.
Nothing failed, which is the point.

After the change, the same reword leaves the bar at `3 of 3` with all three
names, the newline one included, and the label reading `Rebuilding the
database`.

The same file shows the counting fix that came with it: before, a sync of three
tracks ended `iPod now holds 4 track(s)`, because `find | wc -l` counts a
filename containing a newline twice; after, it says 3.

## The bar itself

The four pictures are one run of the real window: `IpodWindow` built whole,
pointed at a synthetic iPod, syncing through the same `_run` the Sync button
goes through, with the bar driven by nothing but the JSON the script wrote on
the descriptor the window opened for it.

Two things are arranged for the camera and neither changes what is reported.
The window's two reading threads are slowed to a fifth of a second per line,
because a copy onto a synthetic iPod on a local disk is over in under half a
second; and the progress reader is held at four points while the shutter is
open, with the other thread held with it so the log pane beside the bar shows
what the script had said by that moment.

`12-bar-playlist-written.png` is the one to read closely: nine rows, each
saying what became of that file rather than all of them reading as a copy.
Six `Copied`, one `Already there`, one `Not found` for a playlist entry that is
not on this computer, and `Playlist written` for the playlist itself.
`13-bar-rebuilding.png` is the stretch that used to leave the bar sitting on
the last filename it had scraped.
