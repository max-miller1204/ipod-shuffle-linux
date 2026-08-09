# Queued tracks no music folder holds

Screenshots of the real GTK window, before and after the change, driven the way a user drives it.

Both runs use the same fixture: the demo library from `tools/demo-library.py` (four albums, one of them synced to a stand-in "MAX SHUFFLE" volume), plus a fifth album of four real tagged MP3s written to `/tmp/shuffle-demo/Elsewhere/Road Trip`, which is outside every music root.
That folder was staged the way the report describes, through the header's "Add music folder..." button and the file chooser it opens, not through "+ Add folder..." in the Music folders card.
The Music folders card still lists only `~/Music` in every shot, which is what makes these tracks the ones nothing scans.

The window runs under a nested X server at 1300x860 with the app's own stylesheet, so the layout is the same on any machine.

## Before the change (base commit 63b0d51)

| Shot | What it shows |
| --- | --- |
| `00-before-fix-sync-4-changes-and-80kb-queued.png` | The device card offers "Sync 4 changes" and reads "80.1 KB queued" after the folder is staged. |
| `01-before-fix-grid-reads-queued-0.png` | The same app, same moment, on the library page: "All 4 / On iPod 1 / Queued 0 / In library 3" beside a sidebar reading "+80.1 KB queued to sync". The four staged tracks are in no view at all. |

That pair is the reported symptom: the pill and the Sync button disagree, and the tracks the next sync is about to copy appear nowhere.

`00-before-fix-...png` and `03-after-fix-...png` are byte-identical, from two separate runs of two different builds.
That is the point of the change rather than an oversight in the evidence: the device page counted these tracks correctly all along, and only the library page disagreed with it.

## After the change (target commit cd3fbec)

| Shot | What it shows |
| --- | --- |
| `02-after-fix-library-before-staging.png` | The starting library: "All 4 / On iPod 1 / Queued 0 / In library 3", nothing staged. |
| `03-after-fix-staged-folder-is-not-a-music-root.png` | After staging: "Sync 4 changes", "80.1 KB queued", and Music folders still listing `~/Music` alone. |
| `04-after-fix-grid-counts-the-staged-album.png` | The grid now reads "All 5 / On iPod 1 / Queued 1 / In library 3", with a fifth card, Road Trip by Low Ferry, wearing the Queued badge and its own embedded artwork. |
| `05-after-fix-queued-filter-shows-the-staged-album.png` | The Queued pill's filter leaves exactly the record it had just counted. |
| `06-after-fix-table-queued-4-matches-sync-4-changes.png` | The table counts tracks rather than records: "Queued 4" beside the button offering "Sync 4 changes", each staged row offering Unqueue. |
| `07-after-fix-unqueue-takes-the-folder-back-out.png` | Unqueue from one of those rows takes the whole folder back out: "The whole folder was removed from the queue", pills back to "All 6 / Queued 0", the sidebar's queued line gone, and no orphan rows left behind. |
