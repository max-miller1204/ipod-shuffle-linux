# A deleted playlist, answered apart from one that could not be read

Everything here was produced by driving the real `IpodWindow` - an `Adw.Application`
building the actual window against a real GDK display, with real widgets, a real
playlist folder and a real `unlink`.
No stand-in window and no patched store.

The same two driver scripts were run against the commit before the change
(`1e29ad8`) and the commit under review (`93a3e00`), on the same situations, so
the pictures below differ only by the fix.

## What a user does

1. Two playlists are on the rail: `Road Trip` and `On A Drive`, plus `Gym`.
2. Another program deletes `Road Trip.m3u` while its rows are still on screen.
3. The user presses Add to playlist on `Road Trip`.

## What they see

| file | what it shows |
| --- | --- |
| `00-playlists-before-anything-happens.png` | The starting point: `Road Trip` on the rail with its two tracks. |
| `01-before-fix-deleted-playlist.png` | Before the fix: the toast reads **"Could not read Road Trip"** and the rail is still offering the playlist that is gone. |
| `02-after-fix-deleted-playlist.png` | After the fix: the toast reads **"There is no playlist called Road Trip"** and the rail has lost the row. |
| `03-after-fix-unreadable-playlist-still-says-so.png` | The other half, after the fix: `On A Drive` is a playlist still sitting in the folder pointing at a drive that is not plugged in. Its read fails with the same `FileNotFoundError` a deleted one fails with, and it still says **"Could not read On A Drive"** and stays on the rail. |
| `04-side-by-side-deleted-playlist.png` | The first two, side by side. |
| `05-after-fix-drag-onto-deleted-playlist.png` | Dragging a track out of `Gym` onto `Road Trip` after it has been deleted. The same sentence, and `Gym` still holds its track: a move writes the target first, so a target refusal that is misread empties the source for nothing. The transcript records the source's contents either side of the drag. |

## What the window reported

`window-transcript-before-fix.txt` and `window-transcript-after-fix.txt` are the
rail contents and toast text read back off the live widgets at each step.

Before the fix, the rail after the edit still holds `Road Trip`.
After the fix it does not, and the playlist is not written back either way.

## One layer down

`store-answers-before-and-after.txt` runs every way a playlist can stop reading
past `playlist_contents` and `add_entries` on both commits.

Exactly one row changes: the playlist another program deleted now answers
`PLAYLIST_GONE`.
A playlist on an unplugged drive, one in a folder that cannot be listed, one in
a folder with no read permission, one in a folder that is not there at all and
one in a Music folder on an unplugged drive all keep the old `False` / `None`
answer, which is what keeps them on the rail and keeps them saying "could not
read".

The same file also shows `add_entries`, `remove_entry` and `move_entry` all
answering `PLAYLIST_GONE` for the one deleted playlist, and the playlist staying
deleted rather than being written back by the edit.

## Reproducing it

`driver-window-e2e.py` and `driver-store-answers.py` are the scripts, kept as
they were run.
Both take the repository to import from as their first argument, which is how
the same run was pointed at each commit.

The window driver needs a display.
It was run here under `gtk4-broadwayd`, which is why the window is 1024x768:

    gtk4-broadwayd :7 &
    GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 \
        python3 driver-window-e2e.py <repo> <output dir> after

    python3 driver-store-answers.py <repo>
