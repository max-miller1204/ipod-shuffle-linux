# Adding a song offered only the playlist the app had made

The report: five playlists on the rail, and the `⋯` beside a song offered one of them.

Everything here was produced by driving the real `IpodWindow` - an `Adw.Application`
building the actual window against a real GDK display, with real widgets, a real
playlist folder, real tagged MP3s and a stand-in volume laid out the way a sync
leaves one.
No stand-in window and no patched store.

The same driver was run against the commit before the change (`40ed0b4`) and the
commit under review, on the same fixture, so the pictures differ only by the fix.

## Why only one playlist was offered

A playlist the app made is an M3U in `~/Music/Playlists`, and that folder is the
whole of what a track's menu ever listed.
The other four had reached the iPod another way - a Spotify export wrote them
into the music folder and `ipod-sync.sh` put them on the device - so the window
knew them only as the copies at the volume root, whose entries name files under
`iPod_Control/Music` rather than files in a music folder.
Those are shown but not editable, and nothing said so: the menu simply did not
list them.

The fixture reproduces exactly that shape.
`Inspo` is made in the app; `2000`, `2016` and `YN` exist only on the device.

## What a user sees

| file | what it shows |
| --- | --- |
| `00-before-a-device-playlist-page.png` | Before the fix: `2000` selected, four playlists on the rail. |
| `01-before-2000-offers-only-removal.png` | Before the fix: the page offers **Remove from iPod** and nothing else, and the note says nothing about why there is no **Add songs**. |
| `02-after-2000-offers-a-copy.png` | After the fix: **Copy to this computer** and a `⋯`, laid out the way a playlist made here is, and the note ends *"only on the iPod until you copy it here"*. Removing it from the iPod is under the `⋯`, where this window keeps what choosing again does not undo. |
| `03-after-add-menu-names-what-it-leaves-out.png` | After the fix: the Add to playlist menu names the playlists it is leaving out - *"Only on the iPod: 2000, 2016, YN. Open one to copy it here."* |
| `04-after-2000-copied-and-added-to.png` | After the fix: `2000` copied here, then a song added to it from the library. |
| `05-after-half-a-playlist-asks-first.png` | `YN` lists a song this computer does not hold, so its note counts it - *"with 1 track this computer does not have"* - and the copy asks before it writes: the copy is what a later sync writes back, and what it leaves out would leave the playlist on the device. |

## What the window reported

`window-transcript-before-fix.txt` and `window-transcript-after-fix.txt` are the
rail rows, menu rows, page buttons, toasts and playlist files read back off the
live widgets at each step.

Before, on a track's `⋯`:

    Add to playlist offers: ['ADD TO PLAYLIST', 'Inspo', '＋  New playlist…', ...]

After, once `2000` has been copied here:

    Add to playlist now offers: ['ADD TO PLAYLIST', '2000', '✓', 'Inspo',
        'Only on the iPod: 2016, YN. Open one to copy it here.', '＋  New playlist…', ...]

and the file it wrote holds the three songs in the music folder that the
device's three copies were made from, in the order the device lists them.
Nothing is staged for a sync by the copy itself: the device is already holding
that playlist.

## One thing measured rather than seen

Measuring that page in this state for the first time - `tests/gui-window-minimum.py`
never showed a playlist that is only on the device - found the window
advertising a minimum width eight pixels narrower than that page can be drawn
in.
It is not caused by this change: every row of a playlist whose tracks are on
the iPod carries a **Remove** button where a library track carries **Add**, and
that column is what sets the width, on a playlist made here just as much as on
one that was not.
The window now asks for 660px rather than 640px, and the check measures the
page in both states so it cannot drift back.

## Against the reported machine

`real-device-reading.txt` is the same window run against the actual iPod and the
actual library the report came from - read only, nothing pressed.
All four playlists that were shown but not editable resolve completely:

    2000: 12 entries · 12 here · 0 only on the iPod
    2016: 13 entries · 13 here · 0 only on the iPod
    More alt shii: 33 entries · 33 here · 0 only on the iPod
    YN: 15 entries · 15 here · 0 only on the iPod

So on that machine each is one press, with no confirmation and nothing left
behind.

## Reproducing it

`driver-window-e2e.py` and `driver-real-device.py` are the scripts, kept as they
were run.
The first takes the repository to import from, an output directory and a label,
which is how the same run was pointed at each commit; it builds its own sandbox
`HOME`, tracks and volume and touches nothing outside them.
The second takes the repository and reads whatever iPod is attached.
