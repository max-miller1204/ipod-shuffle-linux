# The search box owns pasted links

Everything here is the real app talking to the real YouTube, running against
the fixture `tools/demo-library.py` builds, inside the nested server that
tool's recipe names: `Xephyr :9 -screen 1300x860 -dpi 96 -br -noreset`, with
`GDK_BACKEND=x11 GSK_RENDERER=cairo DISPLAY=:9`.
The window comes up at exactly 1180x760 there, so these frame the same region
as `docs/screenshot.png`.
Every click is a real pointer click sent with `xdotool`, and the clipboard is
a real X selection set with `xsel`.
`yt-dlp` was 2026.07.04, reached over the network rather than stubbed.

The playlist used throughout is *Automate the Boring Stuff with Python*,
`PL0-84-yl1fUnRuXGFe_F7qSH1LEnn9LkW`, which has fifteen videos.
Three is what the section shows, so it is a list whose length and whose
shortlist cannot be confused for one another.

## The artifacts

`01-placeholder.png` - the field as the window opens, reading **Search or
paste a link**. It said "Search your library and YouTube" before, which is
why the Add from YouTube dialog on the settings page went on being reached
for by people who could have pasted the link here all along. The sentence
naming both sources moved to the field's tooltip, which is also what a screen
reader reads out.

`02-clipboard-offer.png` - the strip that appears under the header when the
field is reached for while empty and the clipboard holds a link. It names the
link, offers **Look it up**, and can be dismissed with the ×. Nothing has been
typed into the field: a clipboard that happens to hold a link must not change
what the next search is about. Each link is offered once, so coming back to an
empty field does not put a turned-down suggestion back.

`03-pasted-playlist.png` - **Look it up** pressed. The header above the
results reads *Playlist: Automate the Boring Stuff with Python, 15 tracks,
showing the first 3*, with **Add all** beside it. Before this, those three
rows were the whole of what a forty-track playlist looked like.

`04-typed-query-no-header.png` - a typed query, *bohemian rhapsody*, with no
header at all. This is the case the feature turns on, and `06` is why.

`05-linked-video-no-header.png` - a bare `watch?v=1F_OgqRuSdI`, with no
`list=` on it, for a video that does sit inside that same playlist. One
result, no header: a link with no list on it carries none of the three fields
a header is built from, which is case 3 of `06`. A link that does carry the
list is the other case entirely - it resolves to the playlist, header and all,
which is case 4 of `06`. No screenshot here is of that form: `03` is the
playlist's own `playlist?list=` link, case 1, which answers with the same
title and count, so the header reads the same either way.

`06-what-yt-dlp-reports.txt` - the five cases as yt-dlp actually answers
them, under the same flags the search runs. Three findings are load-bearing.
`playlist_count` is the real length while `n_entries` is what
`--playlist-items` left behind, so a header built on `n_entries` would report
the truncation as the length. And `ytsearchN:` is a playlist to yt-dlp too:
every entry of a plain search comes back with `playlist_title` set to the
query, which is why `linked_playlist()` refuses to answer unless the query is
a link, and why `04` shows what it shows. And a Mix, a channel and a bare
`@handle` report no `playlist_count` at all, which is what keeps **Add all**
off a listing yt-dlp never named an end for.

`07-add-all-downloading.png` - **Add all** pressed. The bar reads *Downloading
Automate the Boring Stuff with Python* and the window is busy.

`08-add-all-command.txt` - what that press actually ran, read off the process
table rather than off the log pane. No `--single`, so no `--no-playlist`,
which is exactly what the dialog's **Whole playlist** switch produced. It was
stopped once all fifteen streams were down, listed at the end of the file.

`09-light-offer.png`, `10-light-playlist.png` - both new pieces of chrome in
the light theme, since each carries a tint and a text colour of its own.
