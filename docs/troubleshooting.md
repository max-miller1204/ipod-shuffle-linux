# Troubleshooting

## The iPod is not detected

Confirm that USB can see the device:

```bash
lsusb | grep -i apple
```

A 4th-generation shuffle normally reports USB ID `05ac:1303`. A fully discharged shuffle can need several minutes of charging before it appears.

If more than one iPod is connected, automatic detection stops. Disconnect the other device or pass `--ipod /path/to/mount` to a command-line script.

## The iPod plays no tracks after sync

The database was not rebuilt, or the device was disconnected before all writes reached flash.

Rebuild the database:

```bash
./ipod-sync.sh --rebuild-only --eject
```

Wait until the unmount completes before you disconnect the device.

## Tracks have no VoiceOver names

Install a speech engine and `mutagen`, then rebuild with VoiceOver:

```bash
./install.sh
./ipod-sync.sh --rebuild-only --voiceover --playlist-voiceover
```

A rebuild regenerates spoken names. If a speech engine is missing, existing generated names can be removed and not replaced.

## A YouTube download returns HTTP 403

A missing or unsupported JavaScript runtime commonly causes this failure. Run:

```bash
./install.sh --check
```

The script supports Deno 2.3 or later, Node.js 22 or later, and Bun 1.2.11 through 1.3.14.

If the runtime is available, update `yt-dlp`:

```bash
./ipod-fetch.sh --update
```

See [YouTube downloads](youtube-downloads.md).

## Preview controls are not available

Preview playback needs GStreamer and its audio plugins. Run `./install.sh` and accept the offered packages, or see [Preview playback packages](installation.md#preview-playback-packages).

The rest of the application continues to work without preview playback.

## `udisksctl unmount` reports that the device is busy

Close file-manager windows and terminals that use the iPod volume. Stop media applications that can scan removable storage, then try again.

Do not disconnect the cable to force the operation. An incomplete write can leave an unusable playback database.

## A menu opens and immediately closes on a HiDPI display

GNOME legacy scaling can place a GTK menu outside its parent window on some HiDPI configurations.

Use one of these workarounds:

- Maximize the application window.
- Move the window to a display with scale 1.
- Enable fractional scaling in **Settings > Displays**.

## The installer reports missing capabilities

Run the read-only check for a complete report:

```bash
./install.sh --check
```

Exit code `6` means that one or more capabilities are missing. Use `--check --json` when another program must read the report.

## A script reports exit code 5

The iPod stopped responding, was disconnected, or was replaced at the same mount path during the operation. Reconnect it, verify the device, and start again.

The scripts check identity before changes. They stop instead of continuing on a replacement volume.

## Recover from a damaged state

See [Device notes and recovery](device.md#recover-a-broken-database). Start with `./ipod-sync.sh --rebuild-only`. Use an iTunes restore only when the volume itself is damaged.
