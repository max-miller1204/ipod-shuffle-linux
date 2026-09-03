# YouTube downloads

> [!NOTE]
> You are responsible for permission to download the content that you select.

The graphical interface and `ipod-fetch.sh` use the same download path. The script saves tagged MP3 files in one directory per artist.

## Basic use

Download a video or playlist:

```bash
./ipod-fetch.sh 'https://www.youtube.com/watch?v=...'
```

Download only the selected video when its URL also contains a playlist:

```bash
./ipod-fetch.sh --single 'https://www.youtube.com/watch?v=...&list=...'
```

Choose an output directory:

```bash
./ipod-fetch.sh --output ~/Music/mixtape 'https://www.youtube.com/playlist?list=...'
```

Sync only the tracks from this download run:

```bash
./ipod-fetch.sh --single --sync 'https://www.youtube.com/watch?v=...'
```

Run `./ipod-fetch.sh --help` for all options.

## Dependencies

A search needs `yt-dlp`. A download also needs:

- `ffmpeg` to convert the selected audio to MP3
- A supported JavaScript runtime for the YouTube signature challenge

The script checks for Deno 2.3 or later, Node.js 22 or later, and Bun 1.2.11 through 1.3.14. See [JavaScript for YouTube](installation.md#javascript-for-youtube).

If downloads suddenly fail after they previously worked, update `yt-dlp`:

```bash
./ipod-fetch.sh --update
```

YouTube changes frequently, so an old downloader can fail even when the rest of the installation is correct.

## Download archive and output

The default output is `~/Music/youtube`. The script creates one folder for each artist. It includes the video ID in each filename so that equal artist and title tags do not overwrite another file.

The script records downloaded video IDs in `<output>/.fetched`. When you run the same playlist again, it downloads only new items.

With a current `yt-dlp`, `--sync` sends only files downloaded by the current run. It does not resend the complete output directory. Use `--new-tracks FILE` when another tool needs the new paths.

An old `yt-dlp` that does not support `--print-to-file` cannot report the new paths. In that case, the script warns, removes the output list instead of leaving stale data, and makes `--sync` send every artist directory in the output folder. This fallback can copy much more music than the current download and can fill the iPod. Update `yt-dlp` before you use `--sync`:

```bash
./ipod-fetch.sh --update
```

To sync existing downloads later and group them by artist:

```bash
./ipod-sync.sh --dir-playlists=1 --playlist-voiceover ~/Music/youtube/*/
```

## Audio format

The script downloads stereo audio and converts it to 256 kbit/s MP3. These settings are deliberate.

YouTube usually provides the best stereo audio as Opus, which the shuffle cannot play. A conversion is therefore required. The higher MP3 output bitrate gives the lossy-to-lossy conversion sufficient headroom.

### Why the script does not use AAC

The 4th-generation shuffle supports some AAC files, including files from iTunes. However, tests with dense music encoded by the native FFmpeg AAC encoder produced continuous crackling on real hardware.

The same test audio gave these results:

| Encoding | Result |
| --- | --- |
| AAC 256 kbit/s at 48 kHz | Crackling |
| AAC 256 kbit/s at 44.1 kHz | Crackling |
| AAC 128 kbit/s at 44.1 kHz | Less crackling |
| MP3 256 kbit/s at 44.1 kHz | Clean |
| WAV at 44.1 kHz | Clean |
| Synthetic tone as AAC 256 kbit/s | Clean |

The evidence indicates that dense AAC frames from this encoder exceed what the firmware can decode reliably. MP3 at 256 kbit/s played cleanly and is the project default.

### Why the script selects stereo

An unrestricted best-audio request can select a 5.1 AAC stream because it has a higher bitrate. The download is larger and must still be mixed down for the shuffle. The script therefore selects stereo input.

### Why the script limits peaks

Many commercial sources are already close to full scale. Re-encoding can add peaks that a fixed-point decoder clips. The script applies a limiter before encoding. It affects high peaks and leaves quieter material unchanged.

The limiter does not repair clipping that already exists in the source. WAV output also cannot repair that damage and uses much more device storage.

## Filename safety

The script uses Windows-compatible filenames because FAT rejects characters that frequently occur in video titles, such as `?`, `|`, and `:`.

It does not use `yt-dlp --trim-filenames`. That option limits the complete path, so a long output directory can remove meaningful title characters and can cause filename collisions.
