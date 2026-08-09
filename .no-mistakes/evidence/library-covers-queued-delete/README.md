# Playlist covers, the queued state, and deleting a song

Shot in the real app - `ipod-gui.py`, its own `IpodApp`, its own scans - against the fixture `tools/demo-library.py` builds, with the same environment that tool prints.
The buttons pressed are the row's own: Add on a track row, the ⋯ menu it carries, the Delete row in that menu, and Delete in the dialog that follows.

`00-before-placeholder-playlist-covers.png` is the same page from the base commit (328ae14), so the two open shots are the same library either side of the change.

| Shot | What it shows |
| --- | --- |
| `00-before-placeholder-playlist-covers.png` | Before: every playlist tile and rail row is the striped placeholder its name generates, and the pill row has four pills |
| `01-after-playlist-covers.png` | After: each tile and rail row wears the first cover its own songs carry, and a fifth pill reads Queued 0 |
| `02-playlists-rail-covers.png` | The Playlists view, whose rail draws the same lists with the same borrowed artwork - and whose detail page deliberately has no cover beside its heading |
| `03-queued-row-marker-and-unqueue.png` | After pressing Add on Last Stop: the row reads Queued with the ringed marker, the button beside it now reads Unqueue, the pill reads Queued 1, and the device card counts +20.1 KB |
| `04-queued-album-card-and-pill.png` | The same state one level up: Nightbus, staged whole, wears the Queued badge and is counted by the grid's Queued pill |
| `05-track-menu-delete-row.png` | The ⋯ menu the row built, with Delete from library… last and behind a separator |
| `06-delete-confirm-dialog.png` | The confirmation over the page it was asked from, stating the wastebasket, the sync it leaves, and the playlist that keeps the line |
| `07-after-delete-toast.png` | Answered: "Last Stop moved to the wastebasket", the table down to five tracks, Queued back to 0, and the Downloads tile repainted onto the cover of the song still in it |
| `08-after-delete-grid.png` | The grid without the record the song was the whole of |

`run-transcript.json` records the same run's state: what the merge decided, the pill counts before and after each press, the dialog's own words, and where the file ended up.

The shots are rendered from the live widget tree with a Cairo renderer, driven under `gtk4-broadwayd`, because this machine has no X display to grab from.
Broadway maps no popup surface with no browser attached, so `05` is the menu the ⋯ button built, lifted into a window of its own to be seen; the rows in it are the ones that were then pressed.
