# A custom cover for a playlist, and nothing of it on the iPod

Shot in the real app - `ipod-gui.py`'s own `IpodWindow`, its own scans, its own toasts - against the fixture `tools/demo-library.py` builds: four albums of real MP3s carrying real embedded art, two M3U playlists, and a volume the shipped `ipod-sync.sh` has actually written to.
Every step is a press on the widget the pointer would be over: the Playlists view, the playlist on the rail, the `⋯` on its own page, the rows inside the menu it opens, and the buttons in the dialogs those rows put up.

The one seam that is not a press is the file chooser's answer.
A file cannot be picked inside a chooser from a script, so the chooser the product opens is photographed as it stands and the `Gio.File` it would have returned is handed to `_playlist_cover_chosen`, the same callback `Gtk.FileDialog` calls.
Everything from that point on is the product's own code.

The three images chosen during the run are drawn by the driver and say what they are, so a tile that changed is a tile you can see changed: a PNG, a WebP and a JPEG, one per format the store holds.

| Shot | What it shows |
| --- | --- |
| `01-playlist-wears-its-songs-artwork.png` | Before: Downloads wears the first cover its own songs carry, and there is no `.covers` folder beside the playlists |
| `02-menu-offers-a-custom-cover.png` | The `⋯` on the playlist's page, now leading with **Choose custom cover…** and with no way to remove a cover it does not have |
| `03-the-file-chooser-offers-images.png` | The chooser that press opens, filtered to "Images": the three pictures are offered and `sleeve-notes.txt`, sitting in the same folder, is not |
| `04-the-chosen-cover-on-the-playlist.png` | The chosen PNG on both rails, and "Playlist cover saved on this computer" |
| `05-the-shelf-tiles-wear-them-too.png` | The library page: both playlist tiles wearing custom images - a PNG and a WebP - above album cards still wearing their songs' own art |
| `06-renaming-it.png` | Rename…, answered with "Late Night Drive" |
| `07-renamed-and-still-wearing-it.png` | Renamed, and still wearing the same image: the store now holds `Late Night Drive.m3u.png` and nothing under the old name |
| `08-menu-offers-use-song-artwork.png` | The `⋯` with a cover to remove, which is the only place **Use song artwork** appears |
| `09-back-to-the-songs-own-artwork.png` | Pressed: the row is painted from its first song again, the store is empty, and the menu behind it no longer offers the row |
| `10-a-file-that-is-not-an-image-is-refused.png` | A text file chosen as a cover: "Choose a JPEG, PNG or WebP image", and nothing stored |
| `11-deleting-it.png` | A JPEG chosen for it - the third format - and the deletion asked for over it |
| `12-deleted-and-the-cover-with-it.png` | Deleted: the playlist and its image are both gone, and Morning Ride's WebP is untouched beside them |
| `13-synced-with-the-cover-still-here.png` | After a real sync: the playlist's songs read "On iPod", the device holds four tracks, and the rail row still wears the cover this computer kept |
| `14-the-cover-comes-and-goes.gif` | The same run as one loop: song artwork, chosen cover, renamed, taken off, deleted |

`transcript.txt` is that run in words: what each press said, what the cover store held at each step, and which file the playlist was painted from.
It also records the thing a picture cannot show - that choosing a cover changed neither the queued sources nor the device log, so nothing about it reached the sync.

## Nothing of it reaches the device

`driver-nothing-reaches-the-ipod.py` runs the other half.
A cover is chosen in the window, the playlist is queued with **Send to iPod**, **Sync** is pressed, and the shipped `ipod-sync.sh` copies for real - a real `iTunesSD`, real spoken-name recordings.
Then the volume is walked file by file.

- `device-inventory.txt` - every file on the iPod afterwards, the playlist the device got (`#EXTM3U` and two track paths, no image), and the same walk again after `ipod-sync.sh` is run from a terminal against the whole music folder, which is the folder the cover store sits inside. No image reaches the device by suffix or by content: the bytes of the chosen picture are looked for as well, in case a copy arrived under another name.
- `sync-log-from-the-window.txt` - what the script printed while the window drove it.
- `sync-the-whole-music-folder.txt` - the terminal run, which skips the cover as an unsupported file rather than copying it.
- `songs-own-embedded-art.txt` - the same question asked of a song rather than a playlist: a tagged MP3 synced to a stand-in volume arrives byte for byte, ID3 picture frame included. No artwork the app manages reaches the device and no image file is written there, but a cover already inside a user's own file travels inside that file, because the sync copies a track exactly as it found it.

## The tests, and the goal

- `the-tests-bite.txt` - `tests/gui-playlists.py` run once per deliberately broken behaviour: a deletion that leaves the cover, covers matched on the playlist's stem instead of its whole filename, a rename that leaves the image under the old name, and an unsupported image reported as a folder that refused the write. Each one is caught; the unbroken code passes.
- `goal-and-docs.txt` - the Stepstone goal read back through its own CLI, showing `images-in-songs-playlists` as done, and the README paragraphs that document the feature for users.

## Running it again

This machine has no X server, so the window runs under `gtk4-broadwayd` and the pictures are rendered off the live widget tree with a Cairo renderer.
A headless browser is kept pointed at the broadway display for the whole run: a surface nobody is looking at gets no frames, and without them a dialog that has been answered never finishes closing and stays painted over every later picture.

```sh
gtk4-broadwayd :7 &
google-chrome --headless=new --no-sandbox --user-data-dir=/tmp/covers-chrome \
    http://127.0.0.1:8087/ &
python3 tools/demo-library.py /tmp/covers-demo
IPOD_DB_TOOL=~/ipod-tools/IPod-Shuffle-4g/ipod-shuffle-4g.py \
GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 python3 \
    .no-mistakes/evidence/playlist-custom-covers/driver-custom-cover-e2e.py \
    --repo . --demo /tmp/covers-demo --out .no-mistakes/evidence/playlist-custom-covers
```

`driver-nothing-reaches-the-ipod.py` takes the same three arguments and wants a demo of its own, since it syncs the device it is handed.
