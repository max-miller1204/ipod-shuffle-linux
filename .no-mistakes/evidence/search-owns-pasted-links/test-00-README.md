# The test pass, driven independently

An independent re-run of the feature end to end, not a reading of the diff.
Same recipe as the change's own evidence, rebuilt from scratch: the fixture
`tools/demo-library.py` builds, inside `Xephyr :9 -screen 1300x860 -dpi 96 -br
-noreset`, with `GDK_BACKEND=x11 GSK_RENDERER=cairo DISPLAY=:9`, so the window
comes up at exactly 1180x760.
Every click is a real pointer click sent with `xdotool` and the clipboard is a
real X selection set with `xsel`.
`yt-dlp` was 2026.07.04 from `install.sh`'s virtualenv, reached over the
network rather than stubbed, so the playlist title, its length and every row
below the header came off YouTube.

The playlist is *Automate the Boring Stuff with Python*,
`PL0-84-yl1fUnRuXGFe_F7qSH1LEnn9LkW`, fifteen videos against a section that
shows three.

## What each artefact shows

`test-01-placeholder-and-tooltip.png` - the field reads **Search or paste a
link**, and the tooltip under the pointer reads *Search your library and
YouTube, or paste a link to look it up*.
Both halves of the discoverability claim in one frame.

`test-02-clipboard-offer.png` - the strip under the header, after a playlist
link was put on the X clipboard and the empty field was clicked.
It names the link and offers **Look it up**; the field is still empty behind
it, so nothing has been decided for the user.

`test-03-playlist-header.png` - **Look it up** pressed.
The header reads *Playlist: Automate the Boring Stuff with Python, 15 tracks,
showing the first 3*, with **Add all** beside it.
Fifteen is what YouTube reported for the list; three is what is on screen.

`test-04-typed-query-no-header.png` - *bohemian rhapsody* typed, three results,
no header.
This is the case the gate exists for: real yt-dlp hands every entry of a text
search a `playlist_title` equal to the query, so without the gate this frame
would carry a header reading *Playlist: bohemian rhapsody*.

`test-05-watch-with-list-header.png` - a `watch?v=1F_OgqRuSdI&list=PL0-84-...`
link, the form the address bar gives you partway through a playlist.
It resolves to the playlist, header and **Add all** and all, exactly as the
README says it does.
Recorded here because the change's own goal note in `.pi/worklist.json` says
the opposite - see the finding from this test pass.

`test-06-add-all-queued.png` - the window after **Add all** finished.
The storage meter reads *+266.3 MB queued to sync*: the whole fifteen-track
list staged for the device from one press, not the three rows on screen.

`test-07-add-all-command.txt` - what that press ran, read off the process table
while it ran rather than off the log pane.
No `--single`, so no `--no-playlist` reaches yt-dlp, which is what the dialog's
**Whole playlist** switch produced.
All fifteen ids are listed.

`test-08-mutations.txt` - ten mutations, each reversing one behaviour the
change introduced, each caught by the check meant to catch it.
The tenth covers a gate that had no check, and this test pass added one.
