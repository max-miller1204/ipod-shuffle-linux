# The note correction, checked against the app it describes

This commit changes prose in one field of `.pi/worklist.json`: the Done note on
*Let the search box own pasted YouTube links*.
Nothing else in the change is code, so the thing worth testing is not that the
file was written but that what it now says is true of the app, and that the
worklist the CLI reads still holds it.

Both corrected paragraphs are claims about what the search does with a link.
Each was put to the running window with real `yt-dlp` over the network rather
than reasoned about, which is the failure mode the corrections exist to undo.

`yt-dlp` was 2026.07.04 from `install.sh`'s virtualenv.
The window is the real `IpodWindow` with the real CSS, against the demo library
`tools/demo-library.py` builds, with `MAX SHUFFLE` attached.
Each link went into the search field and the field's own `search-changed` signal
ran the search, so the debounce, the worker thread, the parse and the header
paint are all the ones a paste goes through.
It rendered on GTK's broadway backend, because this machine has no X display to
nest the usual Xephyr inside; the frames are the window's own render nodes
rather than a compositor's screen, so they are GTK's pixels at 1024x760.

## What each artefact shows

`note-01-watch-with-list.png` - a `watch?v=1F_OgqRuSdI&list=PL0-84-...` link,
the form the address bar gives you partway through a playlist.
The header reads *Playlist: Automate the Boring Stuff with Python, 15 tracks,
showing the first 3*, with **Add all** beside it, and the rows are Lessons 1 to
3.
This is the first correction: the note used to say such a link stays the single
video it names.

The same link pasted from the list's *eighth* video,
`watch?v=xJLj6fWfw6k&list=PL0-84-...`, produced a frame identical to it byte for
byte, so it is not kept twice.
That identity is the second half of the claim: the rows are the playlist's first
three, Lessons 1 to 3, and not the Lesson 8 that was pasted.
`note-04-what-happened.txt` records both runs and the rows each produced.

`note-02-mix-no-add-all.png` - a Mix, `watch?v=fJ9rUzIMcZQ&list=RDfJ9rUzIMcZQ`.
The header names it and stops there: no **Add all**, and each of the three rows
keeps its own **Add**.
This is the second correction, the one the note predated.

`note-03-handle-no-add-all.png` - a bare `@3blue1brown` handle, the other shape
of listing with no end to it.
Header, no **Add all**, three rows each with their own **Add**.

`note-04-what-happened.txt` - the run behind those frames.
The `yt-dlp` argv the search built and what came back for each link, then what
the window did with it: whether the header came up, what it said, whether
**Add all** was visible, and which rows landed.
`playlist_count` is 15 for the playlist-carrying link and null for the Mix and
the handle, which is the whole of what decides **Add all**.

`note-05-worklist-cli.txt` - the corrected note read back through
`pi-worklist project show`, the CLI that wrote it, so the record is one the CLI
still parses.
Below it, the same file parsed as a worklist and compared against the commit
before this one: 28 goals before and after, same ids in the same order, one goal
touched, and on it only `description` and `updatedAt`.
The goal is still `done` with its original `completedAt`, and the description
went from 14 paragraphs to 15.

## The checks that already cover this

`tests/gui-actions-smoke.py` and `tests/gui-window-build.py` are where the two
behaviours are held down for CI, and both were run.
The second is the one that paints the header from a `LinkedPlaylist` with a
count and again from one without, and fails if a listing of no stated length is
offered whole.
Neither needs the network; the frames above are what says the same thing is true
of real YouTube.
