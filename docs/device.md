# Device notes and recovery

## Supported hardware

The project is built and tested with the 4th-generation iPod shuffle, USB ID `05ac:1303`.

This model is square, has a clip, has a circular control pad, and has no display. Confirm the USB device with:

```bash
lsusb | grep -i apple
```

The 3rd-generation shuffle uses the same database format and will probably work, but it is not tested. Do not use these scripts with an iPod nano or another iPod model. Those devices use different database formats.

Rockbox does not support any iPod shuffle model.

## Why copied files do not play directly

The shuffle firmware reads `iPod_Control/iTunes/iTunesSD` at startup. This binary database lists the playable tracks. A file that is copied without a matching database entry is ignored.

The sync script copies audio and runs a reverse-engineered database builder. The remove and wipe scripts also rebuild the database so that it always matches the files.

## Important paths

| Path | Purpose | Safe to remove |
| --- | --- | --- |
| `iPod_Control/Music/` | Audio files | Yes, through the project scripts |
| `*.m3u` or `*.pls` at the volume root | Device playlists | Yes, through the project scripts |
| `iPod_Control/iTunes/iTunesSD` | Firmware playback database | Yes; the sync tool rebuilds it |
| `iPod_Control/iTunes/iTunesDB` | iTunes metadata | Back it up first |
| `iPod_Control/iTunes/iTunesPrefs` | Previous computer binding | Yes |
| `iPod_Control/Speakable/` | Built-in spoken prompts | **No** |
| `iPod_Control/Device/` | Device identity data | **No** |

`Speakable` contains firmware prompts such as battery-level speech. The open-source tools cannot regenerate these files. For this reason, `ipod-wipe.sh` clears selected directories instead of formatting the volume.

A playlist that you create in the graphical interface remains in `~/Music/Playlists`. Sync writes a device copy at the root of the iPod.

## Rename the device

The device name is the FAT32 volume label. It is not stored in an iPod database.

The safest method is the **Disks** application. Select the iPod by its current label, capacity, and mount point. Unmount its filesystem, then use **Edit Filesystem** to change the label. Do not select a disk only by a name such as `/dev/sda`, because these names can change after a restart.

For a command-line change, start from the iPod mount point that you already use. Do not type a block-device path by hand:

```bash
MOUNT=/run/media/$USER/CURRENT_IPOD_LABEL
findmnt -no SOURCE,TARGET,FSTYPE,LABEL --target "$MOUNT"
DEVICE="$(findmnt -rn -o SOURCE --target "$MOUNT")"
lsblk -o NAME,MODEL,SIZE,FSTYPE,LABEL,MOUNTPOINTS "$DEVICE"
```

Continue only if all printed details identify the iPod and the printed target is the expected `MOUNT`. `DEVICE` must start with `/dev/`. Stop if it is empty or has another form.

Unmount the verified filesystem, change its label, and mount it again:

```bash
udisksctl unmount -b "$DEVICE"
sudo fatlabel "$DEVICE" MAX_SHUFFLE
udisksctl mount -b "$DEVICE"
```

`fatlabel` writes directly to `DEVICE`. A wrong value can change another disk. Verify the source and mount information immediately before you unmount it.

A FAT32 volume label is limited to 11 characters. `blkid` can show a cached old label. Use `udisksctl info -b "$DEVICE"` to read the current value.

## Recover a broken database

The firmware is separate from the FAT volume. A damaged playback database does not normally damage the firmware.

First try a database-only rebuild:

```bash
./ipod-sync.sh --rebuild-only
```

If necessary, remove `iPod_Control/iTunes/iTunesSD` and run the rebuild again.

If the FAT volume itself is damaged, restore the device with iTunes on Windows or macOS. A restore erases the volume and restores the complete device layout, including the built-in `Speakable` files.

## Back up a secondhand device

Before you wipe a used device, select a new backup directory outside the iPod:

```bash
./ipod-wipe.sh --backup ~/ipod-backups/shuffle-before-wipe
```

The wipe script does not verify that the backup is on a different volume. Do not put the backup directory on the iPod.

The audio files use scrambled names such as `AXKU.m4a`. `iTunesDB` can contain the metadata that maps those files to useful artist and title information. Keep it with the audio backup.
