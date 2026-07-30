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

That is the whole installation.
It fetches the database builder into `~/ipod-tools/`, creates a virtualenv for the Python dependencies, works out which system packages are missing, and offers to install them.

The only hard requirements are Python 3 and git.
Everything else is handled for you:

| Component | Where it goes | Gives you |
| --- | --- | --- |
| `mutagen` | virtualenv | Artist and album metadata, including tag-based playlists |
| `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1` | system | The graphical interface |
| `libttspico-utils` | system | Spoken track and playlist names via VoiceOver (the Flatpak bundles espeak-ng instead, see below) |
| `ffmpeg` | system | Converting FLAC, OGG, and other unsupported formats |

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

The iPod is found automatically.
Pass `--ipod /path/to/mount` if autodetection picks the wrong volume, and note that the script refuses to guess when several iPods are connected.

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
ffmpeg -i input.flac -c:a aac -b:a 256k output.m4a
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

## Credits

The hard part, reverse engineering the `iTunesSD` format and writing it correctly, is [nims11/IPod-Shuffle-4g](https://github.com/nims11/IPod-Shuffle-4g).
This repository is a set of wrappers around that tool, plus the device notes.

`install.sh` fetches it from upstream rather than vendoring a copy, so it keeps its own history and updates independently.

## Licence

GPL-2.0-only, matching the upstream tool these scripts drive.
See [LICENSE](LICENSE).
