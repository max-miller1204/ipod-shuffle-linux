# ipod-shuffle-linux

Use an iPod shuffle 4th generation on Linux without iTunes.

A GTK4 app and a set of scripts to wipe, load, and manage a shuffle 4G, with the device details that make the process work.

## Why this is not just drag and drop

The shuffle 4G does not play files you copy onto it.

It plays only what is listed in `iPod_Control/iTunes/iTunesSD`, a binary database that the firmware reads at startup.
Copy an MP3 onto the volume without updating that database and the player ignores it completely.
iTunes normally writes this file, which is why the device appears to be locked to Apple's software.

`iTunesSD` has been reverse engineered, so a small Python tool can write it instead.
That is the entire trick: copy audio, rebuild the database, unmount.

Rockbox is not an alternative here.
It does not support any iPod shuffle model, so replacing the firmware is not an option and the database has to be written in Apple's format.

## Supported hardware

Built and tested against the iPod shuffle 4th generation, USB ID `05ac:1303`.

That is the small square model with a clip on the back, a circular control pad, and no screen at all.
If your device has a display it is a nano, not a shuffle, and it uses a different database format that these scripts do not write.

Confirm what you have before starting:

```bash
lsusb | grep -i apple
```

The 3rd generation shuffle uses the same database format and will probably work, but has not been tested.

## Setup

```bash
git clone https://github.com/max-miller1204/ipod-shuffle-linux.git
cd ipod-shuffle-linux
./install.sh
```

That handles the core installation.
It fetches the database builder into `~/ipod-tools/`, creates a virtualenv for the Python dependencies, and offers to install compatible missing system packages.
A missing JavaScript runtime is reported with a link to its manual setup guide instead; see [Downloading from YouTube](#downloading-from-youtube).

The only hard requirements are Python 3 and git.
Everything else is installed or reported for you:

| Component | Where it goes | Gives you |
| --- | --- | --- |
| `mutagen` | virtualenv | Artist and album metadata, including tag-based playlists |
| `yt-dlp` | virtualenv | Downloading music from YouTube via `ipod-fetch.sh` |
| `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1` | system | The graphical interface |
| `libttspico-utils` | system | Spoken track and playlist names via VoiceOver (the Flatpak bundles espeak-ng instead, see below) |
| `ffmpeg` | system | Converting FLAC, OGG, and other unsupported formats, including YouTube's Opus |
| [Supported JavaScript runtime](#downloading-from-youtube) | system | Solving YouTube's signature challenge |

Run `./install.sh --no-system` to set up the virtualenv only and be told what to install by hand.
System packages are never installed without asking, and the privileged step goes through `pkexec` so it prompts through the desktop rather than needing a terminal.

### Why not put everything in the virtualenv

It is a fair question, and the answer differs per dependency.

`mutagen` is pure Python, so it goes in the virtualenv and needs no privileges at all.

PyGObject cannot.
Building it from pip requires the gobject-introspection and cairo development headers, so pip-installing it would mean adding three `-dev` system packages in order to avoid adding one runtime package.
GTK4 itself is a C library that no virtualenv can contain.
The distro's `python3-gi` is smaller, already built, and better tested.

`pico2wave` and `ffmpeg` are plain binaries rather than Python packages, so they have no virtualenv to go in either.

This split is why `ipod-gui.sh` looks for an interpreter that can import GTK instead of assuming the one on PATH, and why the GUI reads tags by calling into the virtualenv as a subprocess.

### Why mutagen goes in a virtualenv rather than apt

`mutagen` supplies the artist and album metadata written into the database, and `install.sh` installs it into `~/ipod-tools/venv` rather than through apt.

That is deliberate.
The `python3` first on your PATH is not necessarily the one apt installs into.
If you use uv, pyenv, conda, or Homebrew, your `python3` cannot see `/usr/lib/python3/dist-packages` at all, so `sudo apt install python3-mutagen` completes successfully while the database builder still reports `No mutagen found`.
Owning a virtualenv sidesteps both that and PEP 668's externally-managed-environment error.

## Graphical interface

```bash
./ipod-gui.sh
```

![The iPod Shuffle app showing device information, actions, and the track list](docs/screenshot.png)

The window detects the iPod automatically, and appears and updates as the device is plugged in or unmounted.
It shows real song titles and artists rather than the scrambled filenames stored on the device, and every operation streams its output into the Output pane so nothing happens invisibly.

Each track in the list has a delete button, which removes that one song and rebuilds the database.
**Add from YouTube** asks for a link, offering whatever is already on the clipboard, and downloads it as MP3 into `~/Music/youtube` before copying it onto the device.
When `yt-dlp` can report the files it fetched, only the tracks that download produced are copied, so pasting a second link does not push a growing library back onto a 2GB device, and pasting a link you have already fetched reports that there is nothing new rather than doing it again.

That button is insensitive when a download could not succeed, and says which piece is missing: `yt-dlp`, `ffmpeg`, or a JavaScript runtime.
Checking beforehand is worth the trouble because every one of those failures otherwise appears several steps later as something else, most memorably as `HTTP Error 403` on every track but the oldest.

The buttons map onto the same scripts documented below, so the two interfaces cannot drift apart.

## Flatpak

There is a Flatpak manifest under `flatpak/`, for distributing the application rather than the scripts.

```bash
./flatpak/build.sh
flatpak run io.github.max_miller1204.IpodShuffle
```

It needs `org.flatpak.Builder` and the GNOME SDK, both from Flathub.
Install the GNOME 50 build dependencies together:

```bash
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//50
```

The build takes the repository root as a source directory, so the build tree is kept in the cache directory rather than inside the checkout.

Flatpak is a better fit here than an AppImage.
GTK4 and libadwaita come from the GNOME runtime, so the application itself stays small instead of carrying a hundred megabytes of bundled libraries that most systems already have.

### How it works inside the sandbox

Managing a removable device from a sandbox sounds like it should be the hard part, and it turns out not to be.

Every privileged operation goes through UDisks2 over the system bus, so the sandbox needs no elevated rights of its own:

```yaml
- --system-talk-name=org.freedesktop.UDisks2
- --filesystem=/media
- --filesystem=/run/media
- --filesystem=home
```

Polkit still arbitrates each request and grants mount, unmount, and relabel on removable media to the logged-in user without a password, exactly as it does outside the sandbox.
Mounting, unmounting, renaming, and reading tags were all verified working from inside the sandbox against a real device.

One thing does change.
The GNOME runtime ships `gdbus` but not `udisksctl`, so `lib.sh` prefers `udisksctl` when it exists and falls back to raw D-Bus calls when it does not, and the GUI talks to UDisks2 through `Gio` directly.
Both routes reach the same daemon and the same polkit check.

The three `IPOD_TOOLS_DIR`, `IPOD_DB_TOOL`, and `IPOD_VENV_PYTHON` variables exist for the same reason.
Inside the Flatpak the database builder is baked into `/app` and mutagen belongs to the runtime interpreter, so there is no virtualenv and no `~/ipod-tools`.

### Why the Flatpak speaks with a different voice

The native install uses `pico2wave`, which sounds more natural.
The Flatpak bundles espeak-ng instead, which is more robotic.

This is not a preference.
SVOX Pico is unmaintained code from 2013, and built against the GNOME runtime it produces non-deterministic output: the same text yields different audio on every run, which is the signature of reading uninitialised memory.
Eight synthesis runs of one unchanging string produced seven different files.
That reaches the device as playlists whose spoken names are garbage or silence, and because the database builder ignores the exit status while Pico reports success either way, nothing detects it.

Building with `-O0 -fno-strict-aliasing -fwrapv` did not help, and Debian's working package is built from a different patch set than the fork available to package here.
espeak-ng is maintained, builds cleanly, and the database builder already supports it.

The build asserts the property that failed rather than assuming it, synthesising the same text twice and comparing the results, so a regression fails the build instead of reaching a device.
The builder hardcodes `-v english_rp`, a voice name espeak-ng renamed to `en-gb-x-rp` and now rejects, so the old name is installed as an alias of the same voice definition rather than forking the builder to change one string.

## Usage

### Load music

```bash
./ipod-sync.sh ~/Music/roadtrip
```

Replace everything currently on the device and unmount when done:

```bash
./ipod-sync.sh --clear --eject ~/Music/albums/*/
```

Rebuild the database without copying anything, which is the fix when tracks are on the device but will not play:

```bash
./ipod-sync.sh --rebuild-only
```

Source folders are mirrored rather than flattened, so two albums that both contain a track called `01.mp3` will not overwrite one another.

A single file works as well as a folder, and lands in a folder named after the one it came from:

```bash
./ipod-sync.sh ~/Music/roadtrip/01-highway.mp3
```

That puts it in `iPod_Control/Music/roadtrip/`, exactly where syncing the whole folder would have put it, so adding one track later does not create a second copy of the album or leave a stray file that `--dir-playlists` cannot group.

The iPod is found automatically.
Pass `--ipod /path/to/mount` if autodetection picks the wrong volume, and note that the script refuses to guess when several iPods are connected.

### Remove individual tracks

```bash
./ipod-remove.sh --list
./ipod-remove.sh 'roadtrip/01-highway.mp3'
```

Tracks are named by their path under `iPod_Control/Music`, which is what `--list` prints.
Passing a folder removes everything in it, so `./ipod-remove.sh roadtrip` clears the album.

Deleting the file is only half the job.
The firmware plays what `iTunesSD` lists, so a track deleted without a rebuild is still offered by the player, which then stops dead when it tries to play it.
`ipod-remove.sh` rebuilds the database itself, reusing the playlist and voiceover options the last sync saved on the device.

Folders left empty are removed too.
With `--dir-playlists` an empty folder becomes a playlist that plays nothing, and on a device with no screen there is no way to tell that is what happened.

The script refuses any path that resolves outside the music folder, and points at `ipod-wipe.sh` if you aim it at the whole library.

### Wipe the device

```bash
./ipod-wipe.sh --backup ~/ipod-backup
```

This removes every track and clears stale iTunes state, then writes a fresh empty database.

Always pass `--backup` on a secondhand device.
Filenames on an iPod are scrambled four-character codes such as `AXKU.m4a`, and the only thing mapping them back to real artist and title metadata is `iTunesDB`.
The backup copies that database alongside the audio, so the music stays recoverable.

### Playlists

The shuffle 4G does support playlists, with one catch that shapes everything about how they work.

**Playlist names are stored only as spoken audio, never as text.**
There is no name field in the database, because there is no screen to print a name on.
The name exists solely as a short WAV clip under `iPod_Control/Speakable/Playlists/`, generated by the text-to-speech engine.

That makes `--playlist-voiceover` effectively mandatory.
Without it you get playlists the device can switch between but cannot name, and no way to tell which one you have landed on.
The sync script warns if you ask for playlists without it.

On the device, hold the VoiceOver button to enter the playlist menu, use next and previous to move between playlists, and click to choose one.

There are four ways to create them.

**One playlist per folder:**

```bash
./ipod-sync.sh --dir-playlists --playlist-voiceover ~/Music
```

Add a depth limit if your library nests deeply, where `1` is the artist level and `2` the album level:

```bash
./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music
```

**Grouped by tag,** which needs mutagen and defaults to grouping by artist:

```bash
./ipod-sync.sh --id3-playlists --playlist-voiceover ~/Music
./ipod-sync.sh --id3-playlists='{genre}' --playlist-voiceover ~/Music
./ipod-sync.sh --id3-playlists='{artist} - {album}' --playlist-voiceover ~/Music
```

**By hand,** by putting a `.m3u` or `.pls` file anywhere on the device.
Paths inside it are relative to the playlist file, and the filename becomes the playlist name:

```
# iPod_Control/Music/Roadtrip.m3u
Beach Boys/QKXQ.m4a
Aaron Neville/AXKU.m4a
```

Then rebuild:

```bash
./ipod-sync.sh --rebuild-only --playlist-voiceover
```

**From the GUI,** using the Playlists dropdown under Options.
Choosing any grouping switches spoken playlist names on automatically, for the reason above.

Every track always stays reachable through the built-in "All songs" playlist, whatever else you create.

**Your choices are remembered on the device.**
The database is regenerated from scratch on every run, so a later rebuild that omitted these flags would silently discard every playlist.
To prevent that, the options are saved to `iPod_Control/.sync-options` and reused automatically when you run a rebuild without specifying any:

```bash
./ipod-sync.sh --rebuild-only
==> Reusing saved options: --auto-id3-playlists {artist} --playlist-voiceover
```

Passing any playlist or voiceover flag replaces what was saved.
To go back to a plain database with neither:

```bash
./ipod-sync.sh --rebuild-only --forget-options
```

### Supported formats

`.mp3`, `.m4a`, `.m4b`, `.m4p`, `.aa`, `.wav`

Anything else is skipped with a warning.
Convert first:

```bash
ffmpeg -i input.flac -c:a libmp3lame -b:a 256k output.mp3
```

MP3 is the recommendation on this hardware, and not because it is the better codec.
AAC is specified to work and store-bought AAC from iTunes does work, but AAC produced by ffmpeg's native encoder crackles continuously on the 4G, because its frames pack close to the 1536-byte AAC-LC stereo ceiling and the firmware's decoder cannot sustain that.
There is more on how that was pinned down under [Downloading from YouTube](#downloading-from-youtube).

`.wav` is the only lossless option on the list and it does play cleanly, but it costs about 42MB per four-minute track against 7.7MB for 256k MP3, which is 3 hours of music on the device against 16.
It also carries no tags the database builder can read: `mutagen` returns nothing at all for a `.wav`, so tracks arrive anonymous, tag-based playlists have nothing to group by, and VoiceOver falls back to reading the filename.
On a device with no screen, that is the whole interface.

Note that Apple Lossless will not play.
ALAC lives in an `.m4a` container, so it passes the extension check and copies onto the device happily, but the shuffle 4G cannot decode it and the track will skip.
The nano supported ALAC; the shuffle never did.

### Downloading from YouTube

```bash
./ipod-fetch.sh 'https://www.youtube.com/watch?v=...'
```

The GUI's **Add from YouTube** button runs this script, so the two share every setting below.

The Flatpak is the exception: it ships the GUI, the sync, the removal, and the wipe, but neither this script nor `yt-dlp`.
Downloading inside the sandbox would mean granting it network access and bundling a JavaScript runtime, which is a large addition for a feature that works perfectly well from a native install.
The button is therefore insensitive there and says so rather than failing when pressed.

This wraps `yt-dlp` with the settings the shuffle needs, saving into `~/Music/youtube` with one folder per artist so the result is ready for `--dir-playlists`.
Downloaded video IDs are recorded in `<output>/.fetched` and skipped on later runs, so re-running a playlist URL collects only what is new.
The ID is also included in each filename so separate videos with the same artist and title cannot overwrite one another; the embedded artist and title tags stay clean.

`--sync` copies straight onto the iPod when the download finishes:

```bash
./ipod-fetch.sh --single --sync 'https://www.youtube.com/watch?v=...'
./ipod-fetch.sh -o ~/Music/mixtape 'https://www.youtube.com/playlist?list=...'
```

It copies the tracks this run downloaded, not the contents of the output folder.
That folder is a growing library, so the difference is between adding one song and pushing a year of downloads back onto a 2GB device.
`yt-dlp` names each file it fetched, and `--new-tracks FILE` writes those paths out for another tool to act on, which is how the GUI knows what to copy.
A `yt-dlp` too old to report them says so and falls back to syncing every artist folder, deleting that file rather than leaving a stale one behind for its reader to trust.

To sync existing downloads later while keeping each artist at the playlist level:

```bash
./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music/youtube/*/
```

You are responsible for having the right to download whatever you point this at.

**Three of its flags exist because the obvious version produces files the device cannot play, or cannot play cleanly.**

YouTube normally serves its best stereo audio as Opus, which the shuffle cannot decode at all, so one re-encode is always needed.
256k is deliberate headroom over the roughly 160k source: encoding lossy to lossy loses a little every time, and giving the second encoder room is the cheapest way to keep that inaudible.

The first flag picks MP3 rather than AAC, which is not the obvious choice and was arrived at the hard way.
AAC is the more modern codec, the shuffle 4G is specified to decode it up to 320k, and store-bought AAC from iTunes plays on this hardware perfectly.
Our AAC did not.
It crackled continuously, on every track, unrelated to how loud the music was.

That was bisected by putting the same 45-second chorus on a real device seven ways and listening to each:

| Encoding | Result |
| --- | --- |
| AAC 256k, 48kHz | crackles |
| AAC 256k, 44.1kHz | crackles |
| AAC 128k, 44.1kHz | crackles less |
| MP3 256k, 44.1kHz | clean |
| WAV, 44.1kHz | clean |
| Synthetic tone as AAC 256k | clean |

That pattern rules out almost everything.
Not the hardware or the headphones, because a synthetic tone through the same decoder at the same bitrate is clean.
Not the sample rate, because 48kHz and 44.1kHz crackle identically.
Not the source material, because MP3 and WAV of that exact same audio are clean.
What is left is frame density: our AAC frames run to 1422 bytes against the 1536-byte AAC-LC stereo ceiling, and the firmware's decoder cannot sustain that, which is why halving the bitrate helps and why a tone that packs frames nowhere near full is fine.
A better AAC encoder would likely also fix it, but ffmpeg ships only its native `aac` encoder and `libfdk_aac` is not generally available, whereas MP3 is proven clean on the device itself.

The second flag restricts selection to stereo.
Plain `bestaudio` picks YouTube's 5.1 AAC at 388k because it ranks highest on bitrate, which means a 30MB download, 1.5% of the whole device, to be downmixed back to two channels anyway.

The third flag is a limiter.
Commercial pop is mastered brickwalled: across a sample of ten YouTube sources, every single one already pinned its sample peak to full scale, with inter-sample peaks reaching +2.9 dBFS.
Re-encoding adds the encoder's own ringing on top, so one track came back out at +4.3 dBFS with 69,921 samples stuck at full scale.
Each of those is a hard clamp in the shuffle's fixed-point decoder.
Limiting to -4 dBFS before the encoder brought that worst case to zero clipped samples while still landing at -9.8 LUFS, far louder than any streaming target, so nothing sounds quiet.
Because a limiter only engages above its threshold, sources that are not brickwalled pass through untouched.

On its own the limiter did not fix the crackling, and the codec was the larger problem, but the unlimited version was audibly worse than the limited one on the device, so both changes earn their place.
Note also that this only undoes damage the pipeline was adding, not damage already in the master.
Sources that clip before anyone downloads them still clip, and no download setting can undo that.
For the same reason, switching to `.wav` fixes nothing on its own: it preserves the clipped waveform exactly, at roughly five times the size.

There is deliberately no `--trim-filenames`.
It limits the length of the whole path rather than the filename, so a long `--output` directory eats the budget and truncates the song title itself, silently collapsing different tracks onto the same name.
YouTube titles stop at 100 characters and vfat allows 255, so it protects against nothing.

`--windows-filenames` does stay, because YouTube titles routinely contain `?`, `|`, and `:`, which vfat rejects outright.
Sanitising at download time means a sync cannot fail halfway through copying.

**Most YouTube downloads require a JavaScript runtime.**

YouTube hides most media URLs behind a signature challenge that has to be solved in JavaScript, and `yt-dlp` enables only `deno` by default.
On a machine with `node` or `bun` but no `deno`, every part of a run looks healthy right up until the download: titles, artists, and formats all resolve, then each track fails with `HTTP Error 403: Forbidden`.

What makes this genuinely confusing is that old unrestricted uploads still work.
The failure therefore looks specific to the videos you happen to want rather than to the machine, which sends you investigating the wrong thing entirely.

`ipod-fetch.sh` probes, in order, for Deno >= 2.3, Node >= 22, and Bun 1.2.11-1.3.14, then passes the first usable runtime it finds.
If none is usable, `install.sh` says so and points at [yt-dlp's EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS), but deliberately does not install one for you.
This is the one dependency it reports rather than offers, because Ubuntu can provide `nodejs` version 18, below the floor of 22.
Installing it would spend a privileged apt transaction on a runtime the probe then rejects, leaving downloads failing with the same `HTTP 403` while looking like the problem had been dealt with.
If any supported runtime is already present, the installer leaves it alone.
If none is usable the script warns before downloading instead of failing opaquely partway through.

When downloads start failing for no apparent reason, YouTube has changed something and `yt-dlp` needs updating:

```bash
./ipod-fetch.sh --update
```

## Renaming the device

The shuffle's name is not stored in any of its databases.
It lives only in the FAT32 volume label, so renaming is a filesystem operation and no iPod-specific tool is involved.

The tidiest way is to ask udisks, which needs no root at all.
Polkit grants label changes on removable media to the logged-in user, so this succeeds without a password:

```bash
udisksctl unmount -b /dev/sda
gdbus call --system --dest org.freedesktop.UDisks2 \
  --object-path /org/freedesktop/UDisks2/block_devices/sda \
  --method org.freedesktop.UDisks2.Filesystem.SetLabel "MAX_SHUFFLE" "{}"
udisksctl mount -b /dev/sda
```

`fatlabel` does the same thing if you would rather, but it writes to the block device directly and therefore needs root:

```bash
sudo fatlabel /dev/sda "MAX_SHUFFLE"
```

The label is limited to 11 characters, and both methods reject anything longer.

FAT32 stores the label in two places, the boot sector and a root directory entry, and they can disagree.
`fatlabel` 4.2 and later write both.
A mismatch is harmless but explains why different tools sometimes report different names for the same device.

Note that `blkid` caches results and will keep reporting the old label until its cache expires.
`udisksctl info -b /dev/sda` reads the device directly and is the reliable check.

## What is actually on the device

Useful when deciding what is safe to delete.

| Path | What it is | Safe to delete |
| --- | --- | --- |
| `iPod_Control/Music/` | The audio files | Yes, this is the music |
| `iPod_Control/iTunes/iTunesSD` | The database the firmware reads | Yes, rebuilt by the tool |
| `iPod_Control/iTunes/iTunesDB` | iTunes' own metadata copy | Yes, but back it up first |
| `iPod_Control/iTunes/iTunesPrefs` | Previous owner's library binding | Yes, and worth clearing |
| `iPod_Control/Speakable/` | Apple's built-in spoken prompts | **No** |
| `iPod_Control/Device/` | Device identity data | **No** |

`Speakable/` holds the system voice prompts for things like battery level.
They ship with the firmware, nothing in the open-source toolchain can regenerate them, and on a device with no screen they are the only feedback the hardware gives you.

This is why the wipe script clears directories rather than reformatting the volume.
Reformatting would take `Speakable/` with it.

A secondhand shuffle also carries the previous owner's username and computer name in `iTunesPrefs`.
`ipod-wipe.sh` removes it.

## Troubleshooting

**The iPod plays nothing after syncing.**
The database was not rebuilt, or the device was unplugged before the write reached the flash.
Re-run the sync and let the script unmount with `--eject`.

**Tracks play but have no names under VoiceOver.**
`mutagen` is missing, so the database was written without metadata.
Re-run `./install.sh`, or install `mutagen` into the virtualenv it creates.

**`udisksctl unmount` says the device is busy.**
Something still has a file open on the volume, often a file manager.
Close it and retry, and never pull the cable to force the issue, since a half-written `iTunesSD` leaves the player unable to start.

**The device is not detected.**
Confirm it appears in `lsusb` as `05ac:1303`.
A shuffle with a fully flat battery will not enumerate until it has charged for a few minutes.

## Recovering from a bad state

The firmware lives in flash separate from the FAT volume, so a broken database is not fatal.
Delete `iPod_Control/iTunes/iTunesSD` and re-run the sync to rebuild it from scratch.

If the volume itself is damaged, a restore through iTunes on Windows or macOS rebuilds the whole layout.
That erases the device, including `Speakable/`, which is restored from the firmware during the process.

## Tests

```bash
bash tests/product-e2e.sh
```

The suite runs against a synthetic iPod directory tree with a stand-in for the database builder, so it needs no hardware, no audio, and a few seconds.
Set `EVIDENCE_DIR` to keep the artefacts it writes; otherwise they go to a temporary directory.

It covers the failures that actually happened rather than the code that was easiest to assert against.
Each of these was a real bug, and reintroducing any one of them fails the suite when it runs as an unprivileged user:

- Playlist flags passed without explicit values, letting `argparse` consume the iPod path as its own argument
- A bare rebuild discarding the playlists a previous run created
- A wipe leaving `.sync-options` behind, so configuration reappeared afterwards
- A wipe destroying Apple's `Speakable` prompts, which nothing can regenerate
- Mount detection using `findmnt` raw mode, which escapes a space as `\x20` and so cannot find an iPod whose name contains one
- The GUI choosing between several connected iPods rather than refusing, when Add Music and Wipe both act destructively on the choice
- Options persisted without reporting a failed write, which would silently resurrect the playlist loss
- `yt-dlp` selecting YouTube's 5.1 AAC stream, a 30MB download on a 2GB device that has to be downmixed to stereo anyway
- Downloading as AAC, which the shuffle 4G is specified to decode but whose firmware crackles continuously on frames packed near the AAC-LC ceiling, as a 256k encode of dense music produces
- Re-encoding brickwalled masters without headroom, so the decoder clamps every peak
- `--trim-filenames` truncating song titles, because it limits the whole path rather than the filename
- `ipod-fetch.sh --sync` handing the parent directory to the sync instead of the artist folders, burying every track a level deeper than playlists expect
- A track path from the GUI or the shell escaping the music folder through `../`, on the way into an `rm -rf`
- A removal leaving the folder it emptied behind, which `--dir-playlists` turns into a playlist that plays nothing
- A removal rebuilding the database without the saved options, silently taking every playlist on the device with it
- `--sync` copying the whole download folder rather than what the run downloaded, so one new song dragged the entire library onto the device
- A `--new-tracks` file left stale by a `yt-dlp` that could not fill it, which reads as a definite answer to whoever picks it up next

The failed-write check is skipped when the suite runs as root because root ignores permission bits; CI refuses to run the suite as root so that coverage cannot disappear silently.

The GUI checks call its methods unbound against a stand-in, so they exercise the real logic without needing a display.
PyGObject still has to be importable, because the module imports it at load time.

`.github/workflows/tests.yml` runs the suite, `shellcheck`, and a Python syntax check on every push and pull request.

## Credits

The hard part, reverse engineering the `iTunesSD` format and writing it correctly, is [nims11/IPod-Shuffle-4g](https://github.com/nims11/IPod-Shuffle-4g).
This repository is a set of wrappers around that tool, plus the device notes.

`install.sh` fetches it from upstream rather than vendoring a copy, so it keeps its own history and updates independently.

## Licence

GPL-2.0-only, matching the upstream tool these scripts drive.
See [LICENSE](LICENSE).
