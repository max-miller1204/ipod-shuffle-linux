# Test evidence: adding a song offers every playlist made here

Independent validation of `copy-a-device-playlist-here`, run against
`b24a114` with the parent commit `40ed0b4` measured on the same fixture.

Everything here comes from one script, `driver-user-presses.py`, which builds
the real `IpodWindow` as an `Adw.Application` against a real display and then
only ever emits a press on the widget a pointer would be over: the album card,
the `⋯` on a track row, the row inside the menu that opens, the playlist on the
rail, **Copy to this computer**, the dialog's own response, and the refresh
button in the header.
Nothing about the copy is called by name, so what the pictures and the
transcript show is what the presses did.

The fixture is the machine in the report.
`Inspo` is made in the app and lives in `~/Music/Playlists`.
`2000`, `2016`, `More alt shii` and `YN` were written into the music folder by a
Spotify export and put on the device by `ipod-sync.sh`, so the window knows them
only as the lists at the volume root, whose entries name copies under
`iPod_Control/Music`.
The tracks are real tagged MP3s, and one song on the device - `YN`'s second
entry - was deleted from this computer after being copied over, so one playlist
cannot be copied whole.

Run it with `GDK_BACKEND=x11 python3 driver-user-presses.py <repo> <outdir>`.
On Wayland a compositor stops sending frames to a surface nobody is looking at,
and the snapshots come out empty as soon as the terminal takes focus.

## Before, on the parent commit

The same script against `40ed0b4`, same fixture.

| file | what it shows |
| --- | --- |
| `before-01-add-menu-offers-only-the-app-made-playlist.png` | Five playlists on the rail and the `⋯` beside a song offering one: `Inspo`, the one the app made. The other four are not there and nothing says why. |
| `before-02-a-device-playlist-page-offers-only-removal.png` | The `2000` page: **Remove from iPod** and nothing else, and a note that says nothing about the playlist being unusable from a song's menu. |
| `before-transcript.txt` | The rows read back off the live widgets: `['ADD TO PLAYLIST', 'Inspo', '＋  New playlist…', 'Delete from library…']`. |

## After

| file | what it shows |
| --- | --- |
| `01-five-playlists-on-the-rail.png` | The same five playlists, unchanged. |
| `02-add-menu-names-what-it-leaves-out.png` | The `⋯` beside a song now names what it is leaving out: *"Only on the iPod: 2000, 2016, More alt shii and 1 more. Open one to copy it here."* The cap at three names plus a count is exercised here. |
| `03-a-device-playlist-offers-a-copy.png` | The `2000` page: **Copy to this computer** and a `⋯`, and the note ends *"only on the iPod until you copy it here"*. |
| `04-remove-from-ipod-under-the-dots.png` | **Remove from iPod…** under that `⋯`, still reachable after moving out of the button row. A rename is not offered, which is right: there is no file here to rename. |
| `05-copied-here-and-now-editable.png` | After the press: `2000` copied, the page now carrying **Add songs** and **Send to iPod**, and the toast *"2000 copied here · 3 tracks"*. The file it wrote names the three files in the music folder the device's copies were made from, in the device's order, and nothing was staged for a sync. |
| `06-the-copied-playlist-is-offered.png` | The same `⋯` on a song, now offering `2000`, with the caption down to the three still only on the iPod. |
| `07-sidebar-queued-nothing-to-copy.png` | The song added to `2000` from that menu, and the sidebar for the change it staged: *"Queued to sync · no new tracks to copy"* rather than `+0 B`, because the song is already on the device. |
| `08-a-partial-copy-is-counted-on-the-page.png` | `YN`, whose second song this computer does not hold: the note counts it before anything is pressed, *"with 1 track this computer does not have"*. |
| `09-a-partial-copy-asks-first.png` | The confirmation that press opens, stating 1 of 2 and what the next sync would do, defaulting to **Cancel**. |
| `10-yn-copied-what-it-could.png` | After confirming: the copy holds the one file this computer answers for, the device's own `YN` still lists both, and the toast says *"1 track left on the iPod"*. Cancelling first wrote nothing. |
| `11-every-playlist-copied-here-is-offered.png` | The menu at the end: `2000` and `YN` offered, `2016` and `More alt shii` named as the two still only on the iPod. |
| `12-the-copy-edits-like-any-other-playlist.png` | A song taken back out of the copied playlist from the `⋯` on its own row, so the copy is editable in both directions. |
| `transcript.txt` | Every rail row, menu row, page button, toast, dialog body and playlist file read back off the live widgets at each step. |

## What the copy never does

Both read off the run rather than argued for:

- `staged for sync by the copy: nothing` in the transcript, taken across the press. The device is already holding the playlist, so a copy that is shorter than it is not quietly sent back.
- No entry in either written file names a path under the mount point. A device entry resolves to the file in the music folder it was made from, or it is left out and counted.

## Against the parent commit

The three checks in the repository that cover this were run against `40ed0b4`
with the new test files copied in, to confirm they fail there:

- `tests/gui-window-build.py`: *"the add menu left out the playlist on the iPod without saying so: ADD TO PLAYLIST / Built / ＋ New playlist… / Delete from library…"*, then `AttributeError: 'IpodWindow' object has no attribute 'on_copy_playlist_here'`.
- `tests/gui-playlists.py`: `TypeError: create_local_playlist() takes 2 positional arguments but 3 were given`.
- `tests/gui-window-minimum.py`: *"the playlists page needs 647px while showing a playlist that is only on the iPod, but the window offers to be 640px wide"*, and the unplugged sidebar needing 308px against the 236px it has. Both are the pre-existing problems the change also fixes; on `b24a114` the same page measures 648px against the 660px now advertised, and the sidebar fits at 236px.

All three pass on `b24a114`.
