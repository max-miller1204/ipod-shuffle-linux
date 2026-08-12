# Driving the running window from outside it

The app that produced everything here is the one `./ipod-gui.sh` launches, opened
once against the demo library `tools/demo-library.py` builds - real MP3s with real
tags, and a stand-in shuffle that `ipod-sync.sh` has really written to.
Nothing in the driver imports the package or touches a widget.
Every step is the `gdbus call` written in [docs/machine-interface.md](../../../docs/machine-interface.md), run as a subprocess against the application's own `org.gtk.Actions` interface.

`gdbus-session.md` is the whole session: each command as it was typed, the answer
D-Bus gave back, and the photograph of the window taken afterwards.

Reproduce it with `./run.sh` (it builds the demo, starts a display and a session
bus of its own, and writes these files).

## What each file shows

| File | What it is evidence of |
| --- | --- |
| `gdbus-session.md` | The whole session: real `gdbus` commands, real answers, photographs |
| `01-opened.png` + `dump-01-opened.json` | The window as it opens, and `dump-state` reporting that page and those counts |
| `02-navigate-playlists.png` | `navigate playlists` followed the sidebar row from outside the window |
| `03-navigate-refused.png` | `navigate bogus` left the page it was on, rather than half-following a name the window has no page under |
| `04-search-ridge.png` | `search ridge` opened the search page with the query in the field, both result sections filled |
| `05-search-inline-error.png` + `dump-02-inline-error.json` | The note the search page shows in place of results, and `inlineError` reporting it word for word while it is on screen |
| `06-queued.png` + `dump-03-staged.json` | `queue` staged an album: the pill, the badge and the sidebar's queued line, beside the `staged` document - sources, tracks, changes, bytes, and the iPod it is held against |
| `07-refresh.png` + `dump-04-refreshed.json` | `refresh` started a rescan (the spinner in the header) and the counts after it landed |
| `08-device-page.png` | The device page, reached by `navigate settings`, with the album staged |
| `09-syncing.png` + `dump-05-syncing.json` | The sync bar mid-run, and the `sync` half of the dump reading `active: true` with the stage the bar is showing |
| `10-synced.png` + `dump-06-synced.json`, `dump-07-rescanned.json` | The run finished, everything staged now on the iPod and the queue empty. Two dumps, because the window re-reads the device it has just written to: the first is honestly from before that landed, the second after |
| `track-shape-comparison.json` | The staged track as `dump-state` writes it and as `python3 -m ipod_gui.cli library` writes it - the same fields, and the same values apart from where the track lives |
| `dump-08-final.json` | The last reading, with every field of the document |

## The two things not driven by an action

Starting a sync is a click on the window, dispatched into the browser holding the
broadway display, because it is deliberately not one of the exported actions.
Three hundred extra tracks are staged before it: the demo's own albums are copied,
catalogued and spoken in under a quarter of a second on this disk, which is less
time than one screenshot takes.

Preview playback is not driven either - there is no action for it - so `nowPlaying`
is reported here in its idle shape.

## What was used for a display

`gtk4-broadwayd`, which serves the window over HTTP, with a headless browser
looking at it as the camera. CI runs the window checks under xvfb; this machine
has none, and a nested server would open a window on the screen of whoever is
sitting in front of it.
