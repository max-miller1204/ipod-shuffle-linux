# ipod-shuffle-linux

Use an iPod shuffle 4th generation on Linux without iTunes.

Scripts to wipe, load, and manage a shuffle 4G from the command line, with the device details that make the process work.

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
./setup.sh
```

`setup.sh` fetches the database builder into `~/ipod-tools/`, verifies it compiles against your Python, and reports any optional extras worth installing.

The only hard requirement is Python 3.
Everything else is optional:

| Package | Gives you |
| --- | --- |
| `python3-mutagen` | Artist and album metadata in the database |
| `libttspico-utils` | Spoken track names via VoiceOver |
| `ffmpeg` | Converting FLAC, OGG, and other unsupported formats |

## Usage

### Load music

```bash
./ipod-sync.sh ~/Music/roadtrip
```

Replace everything currently on the device and unmount when done:

```bash
./ipod-sync.sh --clear --eject ~/Music/albums/*/
```

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

```bash
udisksctl unmount -b /dev/sda
sudo fatlabel /dev/sda "MAX SHUFFLE"
udisksctl mount -b /dev/sda
```

The label is limited to 11 characters, and `fatlabel` rejects anything longer.

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
Install `python3-mutagen` and re-run.

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

`setup.sh` fetches it from upstream rather than vendoring a copy, so it keeps its own history and updates independently.

## Licence

GPL-2.0, matching the upstream tool these scripts drive.
See [LICENSE](LICENSE).
