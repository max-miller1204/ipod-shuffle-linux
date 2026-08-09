# Spoken names found by id - test evidence

All of it read off the iPod shuffle that was plugged in while these ran, `MAX_SHUFFLE`, which is the device the report came from: playlists `2000`, `2016`, `More alt shii` and `YN`, each with a recording the real builder wrote.

## The window, before and after

`playlists-2000-before.png` is the Playlists page with the pre-fix `spoken_playlists` put back, against that same device.
Playlist `2000` reads "12 tracks - on the iPod, but with no spoken name, so the device cannot announce it", which is the note in the screenshot that started this.

`playlists-2000-after.png` is the same page, same device, with the fix.
It reads "12 tracks - the device can announce this playlist".

Both were taken by building the real `IpodWindow`, letting its own worker thread probe the device, opening Playlists and selecting `2000`, then saving what GTK painted.

## What the two implementations answer

`real-device-old-vs-new.txt` reads the connected shuffle both ways in one transcript, and lists the id derived for each playlist name beside the WAVs actually on the volume.
Matching stems against names finds none of the four; hashing each name to the id the recording is filed under finds all four.

## The new check

`real-device-spoken-names.json` is `tests/gui-spoken-names.py` against the connected device.

`real-builder-spoken-names.json` is the same check against a device the upstream builder had just written spoken names onto with `--playlist-voiceover`, which is the path the e2e runs.
Its recording for `Party` is `4ff4323b3d174a09.wav`, the name the unit fixture in `tests/gui-actions-smoke.py` hardcodes.

`mutation-old-implementation.txt` is that check run with the pre-fix `spoken_playlists` in place.
It fails, so it cannot pass for the same reason the old code did.
