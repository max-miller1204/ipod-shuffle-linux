# Command-line tools

The shell scripts are the device-management interface. Run a script with `--help` for its complete option list.

## Sync music

Sync a folder:

```bash
./ipod-sync.sh ~/Music/roadtrip
```

Sync more than one source and eject when complete:

```bash
./ipod-sync.sh --eject ~/Music/albums/*/
```

Replace the current music before you copy new sources:

```bash
./ipod-sync.sh --clear --eject ~/Music/albums/*/
```

Rebuild the database without copying files:

```bash
./ipod-sync.sh --rebuild-only
```

Use `--ipod /path/to/mount` if automatic detection finds the wrong volume. The script refuses to select a device when more than one iPod is connected.

### Source behavior

You can give the sync script:

- An audio file
- A directory
- An M3U playlist
- A PLS playlist

A directory is mirrored under `iPod_Control/Music`. A single file goes into a directory with the name of its source directory. This keeps the same device path when you first sync a track and later sync its complete album.

The script follows file and directory symbolic links. It copies the target data and preserves the visible source layout. It reports broken links to supported audio files and prevents directory-link loops.

A playlist copies the tracks that it references. The script writes a normalized M3U at the root of the device. Later database rebuilds use that playlist automatically.

## Remove tracks or playlists

List track paths:

```bash
./ipod-remove.sh --list
```

Remove a track by its path under `iPod_Control/Music`:

```bash
./ipod-remove.sh 'roadtrip/01-highway.mp3'
```

Remove all tracks in a device directory:

```bash
./ipod-remove.sh roadtrip
```

Remove a playlist but keep its tracks:

```bash
./ipod-remove.sh --playlist mixtape
```

The remove script rebuilds the database after deletion. It also removes deleted tracks from root playlists and removes playlists that become empty. It uses the playlist and VoiceOver options saved by the last sync.

Use `--list --json` when another program needs the complete device report. See [What is on the device](machine-interface.md#what-is-on-the-device).

## Wipe the device

Use a new backup directory outside the iPod when you wipe a secondhand device:

```bash
./ipod-wipe.sh --backup ~/ipod-backups/shuffle-before-wipe
```

Do not put the backup directory on the iPod. The script accepts any path and does not check that the backup is on a different volume.

The wipe removes tracks, playlists, and stale iTunes state. It then writes an empty database. It preserves `iPod_Control/Speakable` and `iPod_Control/Device`.

The scrambled audio filenames do not contain enough information to reconstruct a previous library. `iTunesDB` contains important metadata, so keep a backup before you remove it.

## Safe automation

Sync can write files and rebuild the database. Remove, clear, and wipe operations delete data.

A person at a terminal can omit `--yes` and answer the confirmation prompt. An automated or non-interactive caller must use a plan token for a destructive operation. Any caller that supplies a token must supply the correct token, including for a non-destructive sync.

Use this sequence:

1. Run the exact command with `--dry-run`.
2. Review the returned JSON plan.
3. Read `device.identity` and `confirmationToken` from the plan.
4. Run the same command with `--expect-device`, `--confirm-token`, and `--yes`.

Example:

```bash
plan="$(./ipod-wipe.sh --ipod "$mount" --dry-run)"
identity="$(printf '%s' "$plan" | jq -r '.device.identity')"
token="$(printf '%s' "$plan" | jq -r '.confirmationToken')"

./ipod-wipe.sh --ipod "$mount" --yes \
  --expect-device "$identity" \
  --confirm-token "$token"
```

The token is bound to the action, normalized arguments, mount, and device identity. A changed argument, saved option, mount, or device invalidates it. The scripts check device identity again before each change.

`--yes` answers prompts. It does not replace authorization for a destructive non-interactive run.

See [Planning and authorizing changes](machine-interface.md#planning-and-authorizing-changes) for the full contract.

## Playlists

The shuffle has no screen. It stores playlist names as spoken audio, not as text. Use `--playlist-voiceover` when you create playlists:

```bash
./ipod-sync.sh --playlist-voiceover ~/Music/mixtape.m3u
```

The playlist filename becomes its spoken name. The script supports comments, blank lines, `file://` URIs, relative paths, and Windows path separators. It skips streams and missing local files with a warning. A PLS input becomes an M3U on the device.

On the iPod, hold the VoiceOver button to open the playlist menu. Use the next and previous controls to select a playlist.

### Create playlists from directories

Create one playlist for each directory:

```bash
./ipod-sync.sh --dir-playlists --playlist-voiceover ~/Music
```

Limit the grouping depth:

```bash
./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music
```

A depth of `1` is normally the artist level. A depth of `2` is normally the album level.

### Create playlists from tags

Tag grouping needs `mutagen`. It groups by artist by default:

```bash
./ipod-sync.sh --id3-playlists --playlist-voiceover ~/Music
./ipod-sync.sh --id3-playlists='{genre}' --playlist-voiceover ~/Music
./ipod-sync.sh --id3-playlists='{artist} - {album}' \
  --playlist-voiceover ~/Music
```

### Add a playlist directly to the device

You can put an M3U or PLS anywhere on the device and rebuild the database. Entries are relative to the playlist file.

```text
# iPod_Control/Music/Roadtrip.m3u
Beach Boys/QKXQ.m4a
Aaron Neville/AXKU.m4a
```

```bash
./ipod-sync.sh --rebuild-only --playlist-voiceover
```

### Saved options

The sync script saves playlist and VoiceOver options in `iPod_Control/.sync-options`. A later rebuild uses these options so that playlists do not disappear.

Passing a playlist or VoiceOver option replaces the saved set. Reset to a plain database with:

```bash
./ipod-sync.sh --rebuild-only --forget-options
```

A rebuild with VoiceOver enabled clears and regenerates spoken names. If the computer has no speech engine, existing generated names can be lost. Install speech support before you change a device that already has spoken names.

## Supported formats

The sync script accepts:

- `.mp3`
- `.m4a`
- `.m4b`
- `.m4p`
- `.aa`
- `.wav`

It skips other formats with a warning. Convert an unsupported file before sync:

```bash
ffmpeg -i input.flac -c:a libmp3lame -b:a 256k output.mp3
```

MP3 is the recommended format for this hardware. AAC produced by the native FFmpeg encoder can crackle on a 4th-generation shuffle at high frame density. Apple Lossless in an `.m4a` container does not play on the shuffle.

WAV plays, but it uses much more space and provides no tags that the database builder can use. See [YouTube downloads](youtube-downloads.md#audio-format) for the tested MP3 choice.

## Download audio

Download to `~/Music/youtube`:

```bash
./ipod-fetch.sh 'https://www.youtube.com/watch?v=...'
```

With a current `yt-dlp`, download one video and sync the new file:

```bash
./ipod-fetch.sh --single --sync 'https://www.youtube.com/watch?v=...'
```

See [YouTube downloads](youtube-downloads.md) for dependencies, archive behavior, and audio settings.

## Exit codes and structured output

The device scripts use these stable codes:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Another failure; read the message |
| `3` | No iPod was found |
| `4` | More than one iPod was found |
| `5` | The iPod stopped responding or was replaced |
| `6` | A dependency is missing |
| `7` | Confirmation was declined or authorization was invalid |

For JSON reports, NDJSON progress, and exact field definitions, see the [Machine interface](machine-interface.md).
